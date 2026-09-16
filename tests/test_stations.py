import tempfile
import unittest
from pathlib import Path

import app as application
import station_app  # noqa: F401 - registers station routes and migration wrapper


class StationAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        application.DATABASE = Path(self.temp_dir.name) / "minutregnskab.db"
        application.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = application.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf_token(self, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            return session["_csrf_token"]

    def register(self, client, *, username, email, first_name="Test", last_name="Bruger"):
        client.get("/register")
        return client.post(
            "/register",
            data={
                "csrf_token": self.csrf_token(client),
                "first_name": first_name,
                "last_name": last_name,
                "email": email,
                "username": username,
                "password": "long-test-password",
            },
        )

    def test_admin_can_create_station_and_assign_user(self):
        self.assertEqual(
            self.register(
                self.client,
                username="adminuser",
                email="admin@example.com",
                first_name="Admin",
                last_name="Bruger",
            ).status_code,
            302,
        )

        admin_page = self.client.get("/admin")
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"Uden station", admin_page.data)

        response = self.client.post(
            "/admin/stations",
            data={"csrf_token": self.csrf_token(), "name": "Slagelse"},
        )
        self.assertEqual(response.status_code, 302)

        with application.get_db() as db:
            station = db.execute(
                "SELECT id, name FROM stations WHERE name = 'Slagelse'"
            ).fetchone()
            user = db.execute(
                "SELECT id, station_id FROM users WHERE username = 'adminuser'"
            ).fetchone()
        self.assertIsNotNone(station)
        self.assertIsNone(user["station_id"])

        response = self.client.post(
            f"/admin/users/{user['id']}/station",
            data={
                "csrf_token": self.csrf_token(),
                "station_id": str(station["id"]),
            },
        )
        self.assertEqual(response.status_code, 302)

        with application.get_db() as db:
            assigned_station = db.execute(
                "SELECT station_id FROM users WHERE id = ?", (user["id"],)
            ).fetchone()["station_id"]
        self.assertEqual(assigned_station, station["id"])

        admin_page = self.client.get("/admin")
        self.assertEqual(admin_page.status_code, 200)
        self.assertIn(b"Slagelse", admin_page.data)
        self.assertIn(b"Admin Bruger", admin_page.data)

    def test_duplicate_station_name_is_not_created(self):
        self.register(
            self.client,
            username="adminuser",
            email="admin@example.com",
        )
        token = self.csrf_token()
        self.client.post(
            "/admin/stations",
            data={"csrf_token": token, "name": "Slagelse"},
        )
        response = self.client.post(
            "/admin/stations",
            data={"csrf_token": self.csrf_token(), "name": "  slagelse  "},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("error=", response.headers["Location"])
        with application.get_db() as db:
            count = db.execute("SELECT COUNT(*) AS count FROM stations").fetchone()["count"]
        self.assertEqual(count, 1)

    def test_normal_user_cannot_open_station_admin(self):
        self.register(
            self.client,
            username="adminuser",
            email="admin@example.com",
        )
        second_client = application.app.test_client()
        self.assertEqual(
            self.register(
                second_client,
                username="normaluser",
                email="normal@example.com",
                first_name="Normal",
                last_name="Bruger",
            ).status_code,
            302,
        )
        self.assertEqual(second_client.get("/admin").status_code, 403)


if __name__ == "__main__":
    unittest.main()
