"""Ver3 FlaskのPostgreSQL境界、平均、手入力ACK UIテスト。"""

from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import unittest
from uuid import uuid4

os.environ["SECRET_KEY"] = "test-secret-not-for-production"
os.environ["ACCESS_TRUSTED_HOST"] = "dashboard.example.invalid"
os.environ["FLASK_PORT"] = "5000"

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "ver3_app", ROOT / "web-app" / "app.py"
)
ver3_app = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ver3_app)
ver3_app.app.template_folder = str(ROOT / "web-app" / "templates")
ver3_app.app.static_folder = str(ROOT / "web-app" / "static")


DEVICE_ID = uuid4()
IDENTITY = {
    "principal_email": "member@example.invalid",
    "operator_id": "TK" + "000001",
    "display_name": "テスト利用者",
}


class FakeRepository:
    def __init__(self):
        self.manual_calls = []

    def identity_for_email(self, email):
        return IDENTITY if email == IDENTITY["principal_email"] else None

    def authenticate_user_token(self, token):
        return IDENTITY if token == "test-api-token" else None

    def issue_user_credential(self, email, credential_type):
        if email != IDENTITY["principal_email"]:
            raise LookupError
        return IDENTITY, "new-one-time-token"

    def list_devices(self, include_retired=False):
        return [
            {
                "device_id": DEVICE_ID,
                "display_name": "テストPi",
                "status": "active",
                "capability_version": 1,
            }
        ]

    def fetch_readings(self, **kwargs):
        return [
            {
                "id": 1,
                "message_id": uuid4(),
                "device_id": DEVICE_ID,
                "display_name": "テストPi",
                "device_seq": 1,
                "measured_at": datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc),
                "received_at": datetime(2026, 7, 23, 1, 0, 1, tzinfo=timezone.utc),
                "stored_at": datetime(2026, 7, 23, 1, 0, 2, tzinfo=timezone.utc),
                "source_type": "sensor",
                "trigger": "timer",
                "light_raw": 100,
                "light_voltage": 1.0,
                "sound_raw": 200,
                "joystick_x": 0.0,
                "joystick_y": 0.0,
                "potentiometer_percent": 50.0,
                "temp": 25.0,
                "hum": 60.0,
                "pressure": 1000.0,
                "co2": 500.0,
            }
        ]

    def averages(self, **kwargs):
        return [
            {"sensor": name, "average": 25.0, "count": 1} for name in kwargs["sensors"]
        ]

    def insert_manual(self, **kwargs):
        from protocol_v3 import Ack

        self.manual_calls.append(kwargs)
        now = datetime.now(timezone.utc).isoformat()
        return Ack(
            3,
            str(kwargs["message_id"]),
            "inserted",
            device_id=str(kwargs["target_device_id"]),
            received_at=now,
            stored_at=now,
        )

    def update_device_display_name(self, **kwargs):
        if not 1 <= len(kwargs["display_name"].strip()) <= 80:
            raise ValueError("display_nameは1〜80文字です")
        if kwargs["device_id"] != DEVICE_ID:
            return None
        return {
            "device_id": DEVICE_ID,
            "display_name": kwargs["display_name"].strip(),
            "status": "active",
        }


class FlaskVer3Tests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeRepository()
        ver3_app._repository = self.repository
        ver3_app.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = ver3_app.app.test_client()
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess.update(IDENTITY)

    def csrf(self):
        response = self.client.get("/insert")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as sess:
            return sess["manual_csrf_token"]

    def test_dashboard_identifies_ver3_postgresql(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ver3", response.data)
        self.assertIn(b"PostgreSQL", response.data)

    def test_dashboard_table_uses_display_name_and_dual_scrollbars(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("テストPi".encode(), response.data)
        self.assertNotIn(b"<th>device_id</th>", response.data)
        self.assertIn(b'id="tableScrollTop"', response.data)
        self.assertIn(b'id="tableScrollBottom"', response.data)

    def test_naive_browser_datetime_is_interpreted_as_jst(self):
        parsed = ver3_app._parse_boundary("2026-07-23T18:40")
        self.assertEqual(parsed.isoformat(), "2026-07-23T09:40:00+00:00")

    def test_dashboard_timestamp_is_rendered_in_jst(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"2026-07-23 10:00:00+0900", response.data)

    def test_android_api_without_key_returns_json_401(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.get("/api/v3/status")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json["error_code"], "AUTH-E001")

    def test_android_readings_route_returns_json(self):
        response = self.client.get(
            "/api/v3/readings?format=json",
            headers={"X-API-Key": "test-api-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "success")
        self.assertIn("device_id", response.json["sensor_data"])

    def test_android_manual_route_returns_commit_ack(self):
        response = self.client.post(
            "/api/v3/manual-readings",
            json={
                "message_id": str(uuid4()),
                "target_device_id": str(DEVICE_ID),
                "sensors": {"temp": 25.5},
            },
            headers={"X-API-Key": "test-api-token"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["status"], "inserted")

    def test_json_status_contains_all_versions(self):
        response = self.client.get(
            "/api/v3/status", headers={"X-API-Key": "test-api-token"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["protocol_version"], 3)
        self.assertEqual(response.json["schema_version"], 3)

    def test_manual_uses_authenticated_operator_and_target_device(self):
        response = self.client.post(
            "/insert",
            data={
                "csrf_token": self.csrf(),
                "message_id": str(uuid4()),
                "target_device_id": str(DEVICE_ID),
                "temp": "25.5",
            },
        )
        self.assertEqual(response.status_code, 302)
        call = self.repository.manual_calls[-1]
        self.assertEqual(call["operator_id"], IDENTITY["operator_id"])
        self.assertEqual(call["target_device_id"], DEVICE_ID)

    def test_danger_requires_confirmation_before_insert(self):
        csrf = self.csrf()
        message_id = str(uuid4())
        response = self.client.post(
            "/insert",
            data={
                "csrf_token": csrf,
                "message_id": message_id,
                "target_device_id": str(DEVICE_ID),
                "temp": "35",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.repository.manual_calls)
        self.assertIn("危険値候補".encode(), response.data)

    def test_json_returns_commit_ack(self):
        response = self.client.post(
            "/insert",
            json={
                "message_id": str(uuid4()),
                "target_device_id": str(DEVICE_ID),
                "sensors": {"temp": 25.5},
            },
            headers={"X-API-Key": "test-api-token"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["status"], "inserted")
        self.assertEqual(response.json["protocol_version"], 3)

    def test_non_object_json_is_rejected_without_server_error(self):
        response = self.client.post(
            "/api/v3/manual-readings",
            json=[],
            headers={"X-API-Key": "test-api-token"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["status"], "rejected")
        self.assertEqual(response.json["error_code"], "DATA-E001")

    def test_average_is_device_scoped(self):
        response = self.client.get(
            f"/?format=json&display_mode=average&device_id={DEVICE_ID}&sensors=temp",
            headers={"X-API-Key": "test-api-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["device_id"], str(DEVICE_ID))
        self.assertEqual(response.json["average_results"][0]["count"], 1)

    def test_device_display_name_update_uses_authenticated_identity(self):
        self.csrf()
        response = self.client.post(
            f"/api/v3/devices/{DEVICE_ID}/display-name",
            json={"display_name": "教室Pi"},
            headers={"X-API-Key": "test-api-token"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["device"]["display_name"], "教室Pi")

    def test_empty_device_display_name_is_rejected(self):
        response = self.client.post(
            f"/api/v3/devices/{DEVICE_ID}/display-name",
            json={"display_name": ""},
            headers={"X-API-Key": "test-api-token"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json["error_code"], "DATA-E002")
        self.assertEqual(
            response.json["detail"], "device_idまたはdisplay_nameが不正です。"
        )

    def test_login_rejects_external_next_redirect(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.post(
            "/login?next=https://attacker.example.invalid/collect",
            data={"key": "test-api-token"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")

    def test_login_accepts_local_next_redirect(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.post(
            "/login?next=/insert", data={"key": "test-api-token"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/insert")

    def test_login_rejects_unlisted_local_next_redirect(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.post(
            "/login?next=/unlisted", data={"key": "test-api-token"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")


if __name__ == "__main__":
    unittest.main()
