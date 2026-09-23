"""Exercise the Vercel WSGI bridge with a disposable local database."""

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import app as vercel_app
from postgres_db import translate
import server


class VercelAuthenticationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        database = Path(self.directory.name) / "calendar.db"
        self.db_patch = patch.object(server, "DB_PATH", database)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        server.init_db()
        @contextmanager
        def sqlite_connect():
            connection = sqlite3.connect(database)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

        self.connect_patch = patch.object(server, "connect_db", sqlite_connect)
        self.connect_patch.start()
        self.addCleanup(self.connect_patch.stop)
        self.env_patch = patch.dict(os.environ, {"DATABASE_URL": "test-only"})
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        vercel_app.app.initialized = True

    def request(self, method, path, payload=None, cookie=""):
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        status = []
        headers = []

        def start_response(code, fields):
            status.append(code)
            headers.extend(fields)

        result = b"".join(vercel_app.app({
            "REQUEST_METHOD": method, "PATH_INFO": path,
            "CONTENT_LENGTH": str(len(body)), "CONTENT_TYPE": "application/json",
            "HTTP_HOST": "calendar.example.test", "HTTP_COOKIE": cookie,
            "wsgi.input": io.BytesIO(body),
        }, start_response))
        return status[0], dict(headers), result

    def test_register_sign_in_and_persist_session(self):
        status, _, html = self.request("GET", "/")
        self.assertTrue(status.startswith("200"))
        self.assertIn(b"registrationForm", html)
        status, headers, content = self.request("POST", "/api/auth/register", {
            "fullName": "Test Viewer", "email": "viewer@example.test", "password": "a-long-password"
        })
        self.assertTrue(status.startswith("201"), content)
        self.assertEqual(json.loads(content)["user"]["role"], "viewer")
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, content = self.request("GET", "/api/auth/me", cookie=cookie)
        self.assertTrue(status.startswith("200"), content)
        self.assertEqual(json.loads(content)["user"]["email"], "viewer@example.test")
        status, _, content = self.request("POST", "/api/auth/login", {
            "username": "viewer@example.test", "password": "a-long-password"
        })
        self.assertTrue(status.startswith("200"), content)

    def test_postgres_queries_used_by_registration_and_calendar(self):
        self.assertIn("LOWER(email) = LOWER(%s)", translate("email = ? COLLATE NOCASE"))
        self.assertIn("data::jsonb ->> 'date'", translate("ORDER BY json_extract(data, '$.date')"))
        self.assertIn("ON CONFLICT (id) DO UPDATE", translate(
            "INSERT OR REPLACE INTO events (id, data) VALUES (?, ?)"
        ))

    def test_public_afisha_and_poster_without_edit_access(self):
        event = {
            "title": "Open exhibition", "date": "2026-10-01", "time": "18:00",
            "institution": "Museum", "place": "Main hall", "category": "Выставка",
            "description": "Welcome", "direction": "Art", "ticketUrl": "",
            "representative": "Private Person", "contact": "private@example.test",
            "poster": "data:image/png;base64,aGVsbG8=",
        }
        with server.connect_db() as connection:
            connection.execute(
                "INSERT INTO events (id, data, owner_id, direction, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("public-1", json.dumps(event), None, "Art", "2026-09-01", "2026-09-01"),
            )
        status, _, content = self.request("GET", "/api/events")
        self.assertTrue(status.startswith("200"), content)
        listing = json.loads(content)[0]
        self.assertEqual(listing["title"], "Open exhibition")
        self.assertTrue(listing["hasPoster"])
        self.assertFalse(listing["canEdit"])
        for private_field in ("contact", "representative", "poster", "ownerId", "ownerName"):
            self.assertNotIn(private_field, listing)
        status, _, content = self.request("GET", "/api/events/public-1")
        self.assertTrue(status.startswith("200"), content)
        self.assertNotIn("private@example.test", content.decode())
        status, headers, content = self.request("GET", "/api/events/public-1/poster")
        self.assertTrue(status.startswith("200"), content)
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertEqual(content, b"hello")
        for method, path in (("POST", "/api/events"), ("PUT", "/api/events/public-1"),
                             ("DELETE", "/api/events/public-1")):
            status, _, _ = self.request(method, path, event if method != "DELETE" else None)
            self.assertTrue(status.startswith("401"), (method, status))
        status, headers, _ = self.request("POST", "/api/auth/register", {
            "fullName": "Viewer", "email": "visitor@example.test", "password": "long-password"
        })
        self.assertTrue(status.startswith("201"))
        cookie = headers["Set-Cookie"].split(";", 1)[0]
        status, _, _ = self.request("POST", "/api/events", event, cookie=cookie)
        self.assertTrue(status.startswith("403"), status)

    def test_places_are_public_and_include_source_and_photo(self):
        status, _, content = self.request("GET", "/api/places")
        self.assertTrue(status.startswith("200"), content)
        places = json.loads(content)
        self.assertGreaterEqual(len(places), 17)
        self.assertEqual(len({place["id"] for place in places}), len(places))
        self.assertIn("Краснодон", {place["city"] for place in places})
        for place in places:
            self.assertTrue(place["image"].startswith("https://"))
            self.assertTrue(place["source"].startswith("https://"))
            self.assertTrue(place["photoCredit"])

    def test_city_and_pushkin_card_survive_save_and_public_listing(self):
        with server.connect_db() as connection:
            admin_id = connection.execute("SELECT id FROM users WHERE role = 'admin'").fetchone()["id"]
        cookie = "session=" + server.create_session(admin_id)
        event = {
            "title": "Museum evening", "date": "2026-10-02", "time": "19:00",
            "institution": "Museum", "place": "Main hall", "city": "Луганск",
            "category": "Выставка", "description": "Welcome", "representative": "Editor",
            "pushkinCard": True,
        }
        status, _, content = self.request("POST", "/api/events", event, cookie=cookie)
        self.assertTrue(status.startswith("201"), content)
        saved = json.loads(content)
        self.assertEqual(saved["city"], "Луганск")
        self.assertTrue(saved["pushkinCard"])
        status, _, content = self.request("GET", "/api/events")
        self.assertTrue(status.startswith("200"), content)
        public = json.loads(content)[0]
        self.assertEqual(public["city"], "Луганск")
        self.assertTrue(public["pushkinCard"])
        event["pushkinCard"] = False
        status, _, content = self.request("PUT", "/api/events/" + saved["id"], event, cookie=cookie)
        self.assertTrue(status.startswith("200"), content)
        status, _, content = self.request("GET", "/api/events")
        self.assertFalse(json.loads(content)[0]["pushkinCard"])


if __name__ == "__main__":
    unittest.main()
