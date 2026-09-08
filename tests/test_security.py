import hashlib
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

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
        application.PUBLIC_BASE_URL = "https://minutregnskab.example"
        application.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = application.app.test_client()
        self.sent_reset_messages = []
        self.original_sender = application.send_password_reset_email
        application.send_password_reset_email = (
            lambda recipient, first_name, reset_url: self.sent_reset_messages.append(
                (recipient, first_name, reset_url)
            )
        )

    def tearDown(self):
        application.send_password_reset_email = self.original_sender
        self.temp_dir.cleanup()

    def csrf_token(self, client=None):
        client = client or self.client
        with client.session_transaction() as session:
            return session["_csrf_token"]

    def register(self, client=None):
        client = client or self.client
        client.get("/register")
        return client.post(
            "/register",
            data={
                "csrf_token": self.csrf_token(client),
                "first_name": "Test",
                "last_name": "Bruger",
                "email": "test@example.com",
                "username": "testuser",
                "password": "long-test-password",
            },
        )

    def login(self, client, password="long-test-password"):
        client.get("/login")
        return client.post(
            "/login",
            data={
                "csrf_token": self.csrf_token(client),
                "username": "testuser",
                "password": password,
            },
        )

    def test_post_without_csrf_is_rejected(self):
        self.client.get("/register")
        response = self.client.post(
            "/register",
            data={
                "email": "test@example.com",
                "username": "testuser",
                "password": "long-test-password",
            },
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

    def test_password_reset_is_single_use_and_invalidates_old_sessions(self):
        self.assertEqual(self.register().status_code, 302)

        second_client = application.app.test_client()
        self.assertEqual(self.login(second_client).status_code, 302)
        self.assertEqual(second_client.get("/").status_code, 200)

        self.client.get("/forgot-password")
        response = self.client.post(
            "/forgot-password",
            data={
                "csrf_token": self.csrf_token(),
                "identifier": "TEST@example.com",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.sent_reset_messages), 1)
        recipient, first_name, reset_url = self.sent_reset_messages[0]
        self.assertEqual(recipient, "test@example.com")
        self.assertEqual(first_name, "Test")

        reset_path = urlsplit(reset_url).path
        self.assertEqual(self.client.get(reset_path).status_code, 200)
        response = self.client.post(
            reset_path,
            data={
                "csrf_token": self.csrf_token(),
                "password": "new-long-test-password",
                "confirm_password": "new-long-test-password",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Adgangskoden er", response.data)

        self.assertEqual(self.client.get(reset_path).status_code, 400)
        self.assertEqual(second_client.get("/").status_code, 302)

        fresh_client = application.app.test_client()
        self.assertEqual(self.login(fresh_client, "new-long-test-password").status_code, 302)

    def test_unknown_password_reset_request_is_generic(self):
        self.client.get("/forgot-password")
        response = self.client.post(
            "/forgot-password",
            data={
                "csrf_token": self.csrf_token(),
                "identifier": "nobody@example.com",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.sent_reset_messages, [])
        self.assertIn(b"Hvis oplysningerne matcher", response.data)

    def test_existing_user_can_add_recovery_email(self):
        self.assertEqual(self.register().status_code, 302)
        self.client.get("/account")
        response = self.client.post(
            "/account",
            data={
                "csrf_token": self.csrf_token(),
                "email": "new@example.com",
            },
        )
        self.assertEqual(response.status_code, 200)
        with application.get_db() as db:
            email = db.execute(
                "SELECT email FROM users WHERE username = 'testuser'"
            ).fetchone()["email"]
        self.assertEqual(email, "new@example.com")


if __name__ == "__main__":
    unittest.main()
