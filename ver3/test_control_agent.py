"""Pi限定操作エージェントのSQLite・冪等性・安全既定テスト。"""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("control_agent_tested", ROOT / "control_agent.py")
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


class ControlAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        agent.DB_PATH = Path(self.tmp.name) / "control.db"
        agent.DEVICE_ID = str(uuid4())
        agent.DEVICE_TOKEN = "secret"
        agent.BASE_URL = "https://example.invalid"
        agent.DRY_RUN = True
        self.conn = agent.connect()
        self.original_flush = agent.flush_outbox
        agent.flush_outbox = lambda conn: set()

    def tearDown(self):
        agent.flush_outbox = self.original_flush
        self.conn.close()
        self.tmp.cleanup()

    def command(self, action):
        return {"request_id": str(uuid4()), "command": action}

    def test_status_is_locally_completed_and_event_is_queued(self):
        command = self.command("status")
        agent.execute_command(self.conn, command)
        state = self.conn.execute("SELECT state FROM processed_commands WHERE request_id=?", (command["request_id"],)).fetchone()[0]
        self.assertEqual(state, "completed")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1)

    def test_dangerous_action_is_rejected_in_default_dry_run(self):
        command = self.command("shutdown")
        agent.execute_command(self.conn, command)
        state = self.conn.execute("SELECT state FROM processed_commands WHERE request_id=?", (command["request_id"],)).fetchone()[0]
        self.assertEqual(state, "dry_run_rejected")
        payload = self.conn.execute("SELECT payload FROM outbox").fetchone()[0]
        self.assertIn("CONTROL_DRY_RUN", payload)

    def test_duplicate_dry_run_command_does_not_duplicate_event(self):
        command = self.command("restart")
        agent.execute_command(self.conn, command)
        agent.execute_command(self.conn, command)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1)

    def test_requires_https_and_uuid(self):
        agent.BASE_URL = "http://example.invalid"
        with self.assertRaises(SystemExit):
            agent.validate_config()


if __name__ == "__main__":
    unittest.main()
