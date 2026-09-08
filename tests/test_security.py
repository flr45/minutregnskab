import tempfile
import unittest
import hashlib
from pathlib import Path

import app as application

if not hasattr(hashlib, "scrypt"):  # Compatibility with older local Python builds.
    from werkzeug.security import generate_password_hash

    application.generate_password_hash = lambda value: generate_password_hash(
        value, method="pbkdf2:sha256"
    )


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        application.DATABASE = Path(self.temp_dir.name) / "minutregnskab.db"
        application.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = application.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf_token(self):
        with self.client.session_transaction() as session:
            return session["_csrf_token"]

    def register(self):
        self.client.get("/register")
        return self.client.post(
            "/register",
            data={
                "csrf_token": self.csrf_token(),
                "first_name": "Test",
                "last_name": "Bruger",
                "username": "testuser",
                "password": "long-test-password",
            },
        )

    def test_post_without_csrf_is_rejected(self):
        self.client.get("/register")
        response = self.client.post(
            "/register",
            data={"username": "testuser", "password": "long-test-password"},
        )
        self.assertEqual(response.status_code, 400)

    def test_share_code_is_long_and_invalid_attempts_are_limited(self):
        self.assertEqual(self.register().status_code, 302)
        self.client.get("/")
        headers = {"X-CSRF-Token": self.csrf_token()}
        saved = self.client.post(
            "/api/shifts",
            headers=headers,
            json={
                "shift_date": "2026-09-08",
                "start_time": "08:00",
                "station": "Test",
                "payload": {},
            },
        )
        self.assertEqual(saved.status_code, 200)
        shared = self.client.post(
            f"/api/shifts/{saved.get_json()['id']}/share",
            headers=headers,
        )
        self.assertEqual(len(shared.get_json()["code"]), 10)

        for _ in range(10):
            self.assertEqual(
                self.client.post(
                    "/api/shifts/join",
                    headers=headers,
                    json={"code": "AAAAAAAAAA"},
                ).status_code,
                404,
            )
        self.assertEqual(
            self.client.post(
                "/api/shifts/join",
                headers=headers,
                json={"code": "AAAAAAAAAA"},
            ).status_code,
            429,
        )


if __name__ == "__main__":
    unittest.main()
