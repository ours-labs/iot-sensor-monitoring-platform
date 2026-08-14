"""SQLite移行の変換規則をPostgreSQLなしで検証する。"""

from contextlib import closing
from datetime import timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import UUID

from tools.migrate_legacy_sqlite import _source_rows, convert_row


DEVICE_ID = UUID("00000000-0000-4000-8000-000000000001")
JST = timezone(timedelta(hours=9))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "legacy.db"
        columns = (
            "timestamp TEXT NOT NULL, trigger TEXT, light_raw TEXT, light_voltage TEXT,"
            "sound_raw TEXT, joystick_x TEXT, joystick_y TEXT,"
            "potentiometer_percent TEXT, temp TEXT, hum TEXT, pressure TEXT, co2 TEXT"
        )
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(f"CREATE TABLE sensor_readings(id INTEGER PRIMARY KEY, {columns})")
            conn.commit()

    def tearDown(self):
        self.temp.cleanup()

    def _add(self, trigger="timer", co2="450", temp="24.5"):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute(
                "INSERT INTO sensor_readings VALUES(1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "2026-07-20 12:34:56", trigger, "100", "1.2", "200",
                    "0.1", "-0.1", "50", temp, "60", "1000", co2,
                ),
            )
            conn.commit()

    def _first(self):
        return list(_source_rows(self.path, 10))[0][0]

    def test_sensor_row_is_deterministic_and_timezone_aware(self):
        self._add()
        row = self._first()
        first = convert_row(row, DEVICE_ID, JST)
        second = convert_row(row, DEVICE_ID, JST)
        self.assertEqual(first["message_id"], second["message_id"])
        self.assertEqual(first["device_seq"], 1)
        self.assertEqual(first["measured_at"].tzinfo, timezone.utc)
        self.assertEqual(first["source_type"], "legacy")

    def test_manual_identity_is_not_copied(self):
        self._add(trigger="TK" + "000001")
        item = convert_row(
            self._first(), DEVICE_ID, JST
        )
        self.assertEqual(item["source_type"], "manual")
        self.assertEqual(item["trigger"], "manual-legacy")
        self.assertIsNone(item["device_seq"])

    def test_invalid_physical_value_becomes_null_warning(self):
        self._add(co2="99999")
        item = convert_row(
            self._first(), DEVICE_ID, JST
        )
        self.assertIsNone(item["values"]["co2"])
        self.assertEqual(item["quality_state"], "warning")
        self.assertEqual(
            item["quality_detail"]["invalid_values"]["co2"], "outside_physical_range"
        )


if __name__ == "__main__":
    unittest.main()
