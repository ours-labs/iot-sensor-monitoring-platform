"""SQLite復旧スナップショットの秘密値を含まない変換補助を検証する。"""

from datetime import datetime, timezone
import unittest
from uuid import UUID

from tools.backup_postgres_to_sqlite import encode
from tools.restore_sqlite_snapshot import _placeholder


class BackupFormatTests(unittest.TestCase):
    def test_encode_keeps_null_and_stable_structures(self):
        self.assertIsNone(encode(None))
        self.assertEqual(encode(True), "true")
        self.assertEqual(encode({"b": 2, "a": 1}), '{"a": 1, "b": 2}')
        self.assertEqual(
            encode(datetime(2026, 7, 20, tzinfo=timezone.utc)),
            "2026-07-20T00:00:00+00:00",
        )
        self.assertEqual(
            encode(UUID("00000000-0000-4000-8000-000000000001")),
            "00000000-0000-4000-8000-000000000001",
        )

    def test_restore_uses_explicit_safe_casts(self):
        self.assertEqual(_placeholder("uuid"), "%s::uuid")
        self.assertEqual(_placeholder("jsonb"), "%s::jsonb")
        self.assertEqual(_placeholder("float8"), "%s::double precision")
        self.assertEqual(_placeholder("text"), "%s")


if __name__ == "__main__":
    unittest.main()
