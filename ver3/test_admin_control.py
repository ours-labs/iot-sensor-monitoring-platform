"""Ver3 管理者一時昇格・限定遠隔操作の回帰テスト。"""
from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import unittest
from uuid import uuid4

from werkzeug.security import generate_password_hash

os.environ["SECRET_KEY"] = "test-secret-not-for-production"
os.environ["ACCESS_TRUSTED_HOST"] = "dashboard.example.invalid"
os.environ["ADMIN_PASSWORD_HASH"] = generate_password_hash("test-admin-password")

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("ver3_admin_app", ROOT / "web-app" / "app.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)
mod.app.template_folder = str(ROOT / "web-app" / "templates")
mod.app.static_folder = str(ROOT / "web-app" / "static")

DEVICE_ID = uuid4()
REQUEST_ID = uuid4()
IDENTITY = {
    "principal_email": "member@example.invalid",
    "operator_id": "OP-TEST-001",
    "display_name": "Test",
}


class FakeRepository:
    def __init__(self):
        self.commands = []
        self.events = []
        self.security = []
        self.control_online = True

    def identity_for_email(self, email):
        return IDENTITY if email == IDENTITY["principal_email"] else None

    def authenticate_user_token(self, token):
        return IDENTITY if token == "user-token" else None

    def list_devices(self, include_retired=False):
        return [
            {"device_id": DEVICE_ID, "display_name": "Test Pi", "status": "active"},
            {"device_id": uuid4(), "display_name": "Migration source", "status": "active"},
        ]

    def list_controllable_devices(self, online_within_sec=30):
        return [{
            "device_id": DEVICE_ID,
            "display_name": "Test Pi",
            "status": "active",
            "control_enabled": True,
            "last_control_poll_at": datetime.now(timezone.utc),
            "control_online": self.control_online,
        }]

    def record_security_event(self, **kwargs):
        self.security.append(kwargs)

    def list_control_commands(self, limit=30):
        return list(reversed(self.commands))[:limit]

    def list_monitor_events(self, limit=30):
        return [{
            "occurred_at": datetime(2026, 7, 23, 9, 40, 17, tzinfo=timezone.utc),
            "display_name": "Test Pi",
            "event_code": "DEVICE-STALE",
            "severity": "error",
            "state": "recovered",
            "detail": {"last_seen": "2026-07-23T09:40:17+00:00"},
            "notification_required": False,
            "notified_at": None,
        }]

    def queue_control_command(self, **kwargs):
        if not self.control_online:
            raise LookupError("CTL-E003")
        row = dict(kwargs, status="queued", requested_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc), display_name="Test Pi")
        self.commands.append(row)
        return row

    def poll_control_command(self, *, device_id, raw_token):
        if raw_token != "device-token" or device_id != DEVICE_ID:
            raise PermissionError("AUTH-E002")
        return {"request_id": REQUEST_ID, "target_device_id": DEVICE_ID, "command": "status", "status": "accepted", "expires_at": datetime.now(timezone.utc)}

    def record_control_event(self, **kwargs):
        if kwargs["raw_token"] != "device-token" or kwargs["device_id"] != DEVICE_ID:
            raise PermissionError("AUTH-E002")
        self.events.append(kwargs)
        return {"request_id": kwargs["request_id"], "target_device_id": DEVICE_ID, "command": "status", "status": "completed"}


class AdminControlTests(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepository()
        mod._repository = self.repo
        mod.ADMIN_PASSWORD_HASH = generate_password_hash("test-admin-password")
        mod._admin_failures.clear()
        mod.app.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
        self.client = mod.app.test_client()
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess.update(IDENTITY)

    def csrf(self):
        response = self.client.get("/admin")
        self.assertEqual(response.status_code, 200)
        with self.client.session_transaction() as sess:
            return sess["manual_csrf_token"]

    def elevate(self):
        return self.client.post("/admin/elevate", data={"csrf_token": self.csrf(), "password": "test-admin-password"})

    def test_admin_datetime_filter_converts_utc_to_jst(self):
        value = datetime(2026, 7, 23, 9, 40, 17, tzinfo=timezone.utc)
        self.assertEqual(mod.jst_datetime(value), "2026-07-23 18:40:17 JST")

    def test_password_alone_does_not_bypass_normal_authentication(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.post("/admin/elevate", data={"password": "test-admin-password"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_elevation_and_release(self):
        self.assertEqual(self.elevate().status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertIn("admin_elevated_at", sess)
        token = self.csrf()
        self.assertEqual(self.client.post("/admin/logout", data={"csrf_token": token}).status_code, 302)
        with self.client.session_transaction() as sess:
            self.assertNotIn("admin_elevated_at", sess)

    def test_wrong_password_is_audited(self):
        response = self.client.post("/admin/elevate", data={"csrf_token": self.csrf(), "password": "wrong"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(any(e["event_code"] == "AUTH-E006" for e in self.repo.security))

    def test_status_command_requires_elevation(self):
        response = self.client.post("/admin/commands", data={"csrf_token": self.csrf(), "device_id": str(DEVICE_ID), "command": "status"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.repo.commands)

    def test_status_command_is_queued_after_elevation(self):
        self.elevate()
        response = self.client.post("/admin/commands", data={"csrf_token": self.csrf(), "device_id": str(DEVICE_ID), "command": "status"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.repo.commands[-1]["command"], "status")

    def test_admin_lists_only_control_enabled_actual_pi(self):
        self.elevate()
        response = self.client.get("/admin")
        self.assertIn(b"Test Pi", response.data)
        self.assertNotIn(b"Migration source", response.data)

    def test_history_json_requires_elevation_and_returns_jst(self):
        self.assertEqual(self.client.get("/admin/commands.json").status_code, 403)
        self.elevate()
        self.repo.queue_control_command(
            request_id=REQUEST_ID,
            target_device_id=DEVICE_ID,
            command="status",
            requested_by=IDENTITY["principal_email"],
            expires_at=datetime.now(timezone.utc),
        )
        response = self.client.get("/admin/commands.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["commands"][0]["command_status"], "queued")
        self.assertTrue(response.json["commands"][0]["requested_at"].endswith("JST"))

    def test_monitor_events_json_requires_elevation(self):
        self.assertEqual(self.client.get("/admin/monitor-events.json").status_code, 403)
        self.elevate()
        response = self.client.get("/admin/monitor-events.json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["events"][0]["event_code"], "DEVICE-STALE")
        self.assertEqual(response.json["events"][0]["event_state"], "recovered")

    def test_crafted_offline_target_is_rejected_server_side(self):
        self.elevate()
        self.repo.control_online = False
        response = self.client.post(
            "/admin/commands",
            data={"csrf_token": self.csrf(), "device_id": str(DEVICE_ID), "command": "status"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(self.repo.commands)

    def test_dangerous_command_requires_confirmation_and_reauth(self):
        self.elevate()
        token = self.csrf()
        self.client.post("/admin/commands", data={"csrf_token": token, "device_id": str(DEVICE_ID), "command": "shutdown", "password": "test-admin-password", "confirm_text": "wrong"})
        self.assertFalse(self.repo.commands)
        self.client.post("/admin/commands", data={"csrf_token": token, "device_id": str(DEVICE_ID), "command": "shutdown", "password": "test-admin-password", "confirm_text": "SHUTDOWN"})
        self.assertEqual(self.repo.commands[-1]["command"], "shutdown")

    def test_device_poll_uses_device_token_not_user_session(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        rejected = self.client.post("/api/v3/device-control/poll", json={"device_id": str(DEVICE_ID)}, headers={"X-Device-Token": "wrong"})
        self.assertEqual(rejected.status_code, 401)
        accepted = self.client.post("/api/v3/device-control/poll", json={"device_id": str(DEVICE_ID)}, headers={"X-Device-Token": "device-token"})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json["command"]["status"], "accepted")

    def test_device_event_is_persisted(self):
        with self.client.session_transaction() as sess:
            sess.clear()
        response = self.client.post("/api/v3/device-control/events", json={"device_id": str(DEVICE_ID), "request_id": str(REQUEST_ID), "client_event_id": str(uuid4()), "event_type": "status_report", "occurred_at": datetime.now(timezone.utc).isoformat(), "detail": {"uptime_seconds": 10}}, headers={"X-Device-Token": "device-token"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.repo.events), 1)


if __name__ == "__main__":
    unittest.main()
