import base64
import os
import tempfile
import unittest
from io import BytesIO

from app import _get_item, _row_dict, _store_matches_for_item, create_app
from database.database import query_db


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/"
    "BmkAAAAASUVORK5CYII="
)


class FinditFlowTests(unittest.TestCase):
    def test_app_requires_a_secret_key(self):
        with self.assertRaisesRegex(RuntimeError, "SECRET_KEY is required"):
            create_app("development", {"SECRET_KEY": None})

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = os.path.join(self.temp_dir.name, "test.db")
        self.upload_path = os.path.join(self.temp_dir.name, "uploads")
        self.app = create_app(
            "testing",
            {
                "DATABASE_PATH": self.database_path,
                "UPLOAD_FOLDER": self.upload_path,
                "SECRET_KEY": "test-secret",
            },
        )
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def csrf(self, client=None):
        with (client or self.client).session_transaction() as session:
            return session["_csrf_token"]

    def signup(self, client, name, email):
        client.get("/signup?next=/report/lost")
        return client.post(
            "/signup",
            data={
                "csrf_token": self.csrf(client),
                "name": name,
                "email": email,
                "password": "correct-horse-123",
                "next": "/report/lost",
            },
            follow_redirects=False,
        )

    def create_item(self, client, item_type, title):
        return client.post(
            "/api/items",
            data={
                "csrf_token": self.csrf(client),
                "type": item_type,
                "title": title,
                "category": "Wallet",
                "description": "Brown leather bifold wallet with a stitched corner.",
                "location": "Central Market",
                "event_date": "2026-10-02",
                "image": (BytesIO(PNG_BYTES), f"{item_type}.png"),
            },
            headers={"X-CSRF-Token": self.csrf(client)},
            content_type="multipart/form-data",
        )

    def test_signup_reports_matching_search_detail_dashboard_profile_and_logout(self):
        self.assertEqual(self.client.get("/report/lost").status_code, 302)
        signup_response = self.signup(self.client, "Lost Reporter", "lost@example.test")
        self.assertEqual(signup_response.status_code, 302)
        self.assertIn("/report/lost", signup_response.location)

        lost_response = self.create_item(self.client, "lost", "Brown Leather Wallet")
        self.assertEqual(lost_response.status_code, 201, lost_response.get_json())
        lost_item = lost_response.get_json()["item"]
        self.assertEqual(lost_item["type"], "lost")
        self.assertTrue(lost_item["image_url"].startswith("/static/images/uploads/"))
        self.assertTrue(os.path.exists(os.path.join(self.upload_path, lost_item["image_url"].rsplit("/", 1)[1])))
        self.assertEqual(lost_response.get_json()["matches"], [])

        found_client = self.app.test_client()
        self.assertEqual(self.signup(found_client, "Found Reporter", "found@example.test").status_code, 302)
        found_response = self.create_item(found_client, "found", "Brown Leather Wallet")
        self.assertEqual(found_response.status_code, 201, found_response.get_json())
        found_item = found_response.get_json()["item"]
        matches = found_response.get_json()["matches"]
        self.assertEqual(len(matches), 1)
        self.assertGreaterEqual(matches[0]["score"], 50)
        self.assertEqual(matches[0]["level"], "Strong Match")
        self.assertIn("Same category", matches[0]["reasons"])
        self.assertEqual(matches[0]["item"]["id"], lost_item["id"])
        match_id = matches[0]["id"]

        notifications = self.client.get("/notifications")
        self.assertEqual(notifications.status_code, 200)
        self.assertIn(b"A potential item match was found", notifications.data)
        self.assertEqual(
            found_client.get(f"/api/items/{found_item['id']}/matches").get_json()["matches"][0]["id"],
            match_id,
        )
        public_match = self.app.test_client().get(
            f"/api/items/{found_item['id']}/matches"
        ).get_json()["matches"][0]
        self.assertNotIn("status", public_match)
        user_matches = found_client.get("/api/matches").get_json()["matches"]
        self.assertEqual(len(user_matches), 1)
        self.assertEqual(user_matches[0]["status"], "potential")
        self.assertEqual(user_matches[0]["components"]["category"], 100)
        self.assertNotIn("user_id", user_matches[0]["lost_item"])
        self.assertEqual(
            found_client.get(f"/api/matches/{match_id}").get_json()["match"]["id"],
            match_id,
        )
        self.assertEqual(
            self.app.test_client().get(f"/api/matches/{match_id}").status_code,
            401,
        )
        outsider = self.app.test_client()
        self.signup(outsider, "Unrelated User", "outsider@example.test")
        self.assertEqual(
            outsider.get(f"/api/matches/{match_id}").status_code,
            404,
        )

        csrf = self.csrf(found_client)
        status_response = found_client.patch(
            f"/api/matches/{match_id}",
            json={"status": "reviewed"},
            headers={"X-CSRF-Token": csrf},
        )
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.get_json()["match"]["status"], "reviewed")

        second_found_client = self.app.test_client()
        self.assertEqual(
            self.signup(second_found_client, "Second Finder", "second-found@example.test").status_code,
            302,
        )
        second_found_response = self.create_item(
            second_found_client,
            "found",
            "Brown Leather Wallet",
        )
        self.assertEqual(second_found_response.status_code, 201)
        self.assertEqual(len(self.client.get("/api/matches").get_json()["matches"]), 2)

        with self.app.app_context():
            _store_matches_for_item(
                _row_dict(_get_item(second_found_response.get_json()["item"]["id"])),
                notify=True,
            )
            pair_count = query_db("SELECT COUNT(*) AS total FROM matches", one=True)["total"]
            owner_id = query_db(
                "SELECT id FROM users WHERE email = ?",
                ("lost@example.test",),
                one=True,
            )["id"]
            notification_count = query_db(
                "SELECT COUNT(*) AS total FROM notifications WHERE user_id = ?",
                (owner_id,),
                one=True,
            )["total"]
        self.assertEqual(pair_count, 2)
        self.assertEqual(notification_count, 2)

        homepage = self.client.get("/")
        self.assertEqual(homepage.status_code, 200)
        self.assertIn(b"Strong Match", homepage.data)
        self.assertIn(b"Brown Leather Wallet", homepage.data)

        search = self.client.get(
            "/api/items?q=stitched&category=Wallet&location=Central%20Market"
            "&date=2026-10-02&type=found"
        ).get_json()
        self.assertEqual(
            [item["id"] for item in search["items"]],
            [second_found_response.get_json()["item"]["id"], found_item["id"]],
        )
        self.assertEqual(self.client.get("/api/items?q=not-a-real-report").get_json()["items"], [])

        detail_response = self.client.get(f"/api/items/{found_item['id']}")
        self.assertEqual(detail_response.status_code, 200)
        detail = detail_response.get_json()
        self.assertEqual(detail["item"]["title"], "Brown Leather Wallet")
        self.assertEqual(detail["matches"][0]["item"]["id"], lost_item["id"])
        self.assertNotIn("email", detail["item"])
        self.assertNotIn("user_id", detail["item"])
        html_detail = self.client.get(f"/items/{found_item['id']}")
        self.assertEqual(html_detail.status_code, 200)
        self.assertIn(b"Potential matches", html_detail.data)

        self.assertEqual(self.client.get("/api/dashboard").status_code, 200)
        dashboard = self.client.get("/api/dashboard").get_json()
        self.assertEqual(dashboard["stats"]["lost_reports"], 1)
        self.assertEqual(dashboard["stats"]["potential_matches"], 2)
        profile = self.client.get("/api/profile").get_json()
        self.assertEqual(profile["user"]["name"], "Lost Reporter")
        self.assertEqual(len(profile["lost_items"]), 1)
        self.assertEqual(len(profile["matches"]), 2)
        self.assertNotIn("password_hash", profile["user"])

        self.assertEqual(self.client.post("/logout", data={"csrf_token": self.csrf()}).status_code, 302)
        self.assertEqual(self.client.get("/api/dashboard").status_code, 401)

    def test_login_validation_and_invalid_image_are_rejected(self):
        self.signup(self.client, "Test User", "test@example.test")
        self.client.post("/logout", data={"csrf_token": self.csrf()})
        self.client.get("/login")
        login_response = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf(),
                "email": "test@example.test",
                "password": "correct-horse-123",
                "next": "/report/found",
            },
        )
        self.assertEqual(login_response.status_code, 302)
        self.assertIn("/report/found", login_response.location)

        invalid = self.client.post(
            "/api/items",
            data={
                "csrf_token": self.csrf(),
                "type": "lost",
                "title": "Invalid Upload",
                "category": "Other",
                "description": "Description",
                "location": "Here",
                "event_date": "2026-10-02",
                "image": (BytesIO(b"not an image"), "not-image.png"),
            },
            headers={"X-CSRF-Token": self.csrf()},
            content_type="multipart/form-data",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertIn("valid image", invalid.get_json()["error"])
        items = self.client.get("/api/items").get_json()["items"]
        self.assertEqual(items, [])

        validation = self.client.post(
            "/api/items",
            data={
                "csrf_token": self.csrf(),
                "type": "found",
                "title": "",
                "category": "Keys",
                "description": "Missing title",
                "location": "Here",
                "event_date": "2026-10-02",
            },
            headers={"X-CSRF-Token": self.csrf()},
            content_type="multipart/form-data",
        )
        self.assertEqual(validation.status_code, 400)
        self.assertIn("Item name is required", validation.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
