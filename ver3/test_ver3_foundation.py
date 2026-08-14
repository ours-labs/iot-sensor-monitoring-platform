"""Tests for the Ver3 version and deployment boundary."""

import os
from pathlib import Path
import unittest

import system_identity


ROOT = Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
ANDROID_ROOT = REPO_ROOT / "SensorDataApp-v3"


class Ver3FoundationTests(unittest.TestCase):
    def test_version_protocol_and_schema_are_ver3(self):
        self.assertEqual(system_identity.SYSTEM_VERSION, "3.0.0")
        self.assertEqual(system_identity.EXPECTED_MAJOR, 3)
        self.assertEqual(system_identity.PROTOCOL_VERSION, 3)
        self.assertEqual(system_identity.SCHEMA_VERSION, 3)

    def test_identity_is_explicitly_development(self):
        identity = system_identity.identity()
        self.assertEqual(identity["system_major"], 3)
        self.assertEqual(identity["build_channel"], "development")

    def test_public_snapshot_contains_only_ver3(self):
        self.assertTrue(ROOT.is_dir())
        self.assertTrue(ANDROID_ROOT.is_dir())
        self.assertFalse((REPO_ROOT / "legacy-v1").exists())
        self.assertFalse((REPO_ROOT / "legacy-v2").exists())
        self.assertFalse((REPO_ROOT / "SensorDataApp").exists())
        self.assertFalse((REPO_ROOT / "SensorDataApp-v2").exists())

    def test_android_has_separate_application_identity(self):
        build_file = (ANDROID_ROOT / "app" / "build.gradle.kts").read_text(
            encoding="utf-8"
        )
        self.assertIn('applicationId = "com.websarva.wings.android.sensordataapp.v3"', build_file)
        self.assertIn('versionName = "3.0.0"', build_file)

    def test_systemd_templates_cannot_target_ver2_install_root(self):
        units = list((ROOT / "systemd").glob("*.service"))
        self.assertEqual(len(units), 5)
        for unit in units:
            text = unit.read_text(encoding="utf-8")
            self.assertIn("Ver.3 Production", text)
            self.assertIn("/ver3", text)
            self.assertNotIn("/legacy-v2", text)
            self.assertIn("EXPECTED_SYSTEM_MAJOR=3", text)
            self.assertIn("SYSTEM_BUILD_CHANNEL=production", text)

    def test_version_mismatch_is_rejected(self):
        previous = os.environ.get("EXPECTED_SYSTEM_MAJOR")
        os.environ["EXPECTED_SYSTEM_MAJOR"] = "2"
        try:
            with self.assertRaises(system_identity.VersionBoundaryError):
                system_identity.read_system_version()
        finally:
            if previous is None:
                os.environ.pop("EXPECTED_SYSTEM_MAJOR", None)
            else:
                os.environ["EXPECTED_SYSTEM_MAJOR"] = previous


if __name__ == "__main__":
    unittest.main()
