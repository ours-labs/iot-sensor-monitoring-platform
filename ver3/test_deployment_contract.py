"""Ver3 production deployment contract tests."""

from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parent


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class ProductionWebTests(unittest.TestCase):
    def test_requirements_include_gunicorn(self):
        self.assertIn("gunicorn>=23,<24", read("requirements.txt"))

    def test_user_service_uses_loopback_gunicorn(self):
        unit = read("systemd-user/sensor-v3-app.service")
        self.assertIn("/gunicorn", unit)
        self.assertIn("--bind 127.0.0.1:5001", unit)
        self.assertIn("--workers 2", unit)
        self.assertIn("--threads 4", unit)
        self.assertIn("app:app", unit)
        self.assertNotIn("python app.py", unit)

    def test_system_service_uses_loopback_gunicorn(self):
        unit = read("systemd/sensor-v3-app.service")
        self.assertIn("SYSTEM_BUILD_CHANNEL=production", unit)
        self.assertIn("--bind 127.0.0.1:5001", unit)
        self.assertIn("app:app", unit)

if __name__ == "__main__":
    unittest.main()
