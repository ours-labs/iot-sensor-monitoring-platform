"""Ver3監視通知の抑制境界に関する単体テスト。"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "ver3_monitor", ROOT / "web-app" / "monitor.py"
)
monitor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(monitor)


class WebhookNotifierTests(unittest.TestCase):
    def test_blank_url_keeps_notifications_internal(self):
        notifier = monitor.WebhookNotifier("")
        self.assertFalse(notifier.should_notify("error", "open"))
        self.assertFalse(notifier.should_notify("info", "recovered"))

    def test_webhook_sends_only_actionable_and_recovery_events(self):
        notifier = monitor.WebhookNotifier("https://notify.example.invalid/hook")
        self.assertTrue(notifier.should_notify("warning", "open"))
        self.assertTrue(notifier.should_notify("error", "open"))
        self.assertTrue(notifier.should_notify("info", "recovered"))
        self.assertFalse(notifier.should_notify("info", "open"))

    def test_stateful_codes_are_limited_to_missing_and_stale(self):
        self.assertEqual(
            monitor.DeviceMonitor.STATEFUL_CODES,
            {"DATA-MISSING", "DEVICE-STALE"},
        )


if __name__ == "__main__":
    unittest.main()
