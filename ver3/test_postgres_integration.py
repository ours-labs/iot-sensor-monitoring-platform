"""実PostgreSQLが指定された場合だけ実行するVer3統合・同時書込み試験。

TEST_DATABASE_URLのDB内に一時schemaを作成し、終了時に削除する。
本番DATABASE_URLは参照しない。
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import unittest
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from database import PostgresRepository, _import_psycopg
from protocol_v3 import SENSOR_COLUMNS, build_envelope, parse_envelope


TEST_URL = os.environ.get("TEST_DATABASE_URL", "").strip()


def _schema_url(url: str, schema: str) -> str:
    parts = urlsplit(url.replace("postgresql+psycopg://", "postgresql://", 1))
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["options"] = f"-c search_path={schema}"
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query, quote_via=quote),
        parts.fragment,
    ))


@unittest.skipUnless(TEST_URL, "TEST_DATABASE_URLが未設定のため実PostgreSQL試験を省略")
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.psycopg, _, _ = _import_psycopg()
        from psycopg import sql

        cls.schema = "ver3_test_" + uuid4().hex
        with cls.psycopg.connect(TEST_URL, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(cls.schema)))
            migration = (
                Path(__file__).with_name("migrations") / "001_ver3_postgresql.sql"
            ).read_text(encoding="utf-8")
            conn.execute(migration)
        cls.repository = PostgresRepository(_schema_url(TEST_URL, cls.schema))
        cls.repository.open()

    @classmethod
    def tearDownClass(cls):
        from psycopg import sql

        cls.repository.close()
        with cls.psycopg.connect(TEST_URL, autocommit=True) as conn:
            conn.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema))
            )

    def setUp(self):
        self.device_id = uuid4()
        self.token = secrets.token_urlsafe(32)
        self.repository.register_device(
            device_id=self.device_id,
            display_name="integration-device",
            raw_token=self.token,
            capabilities=[
                {
                    "sensor_code": "temp",
                    "unit": "degC",
                    "expected_interval_sec": 10,
                    "plausible_min": -40,
                    "plausible_max": 80,
                }
            ],
        )

    def _envelope(self, seq=1, message_id=None):
        sensors = {name: None for name in SENSOR_COLUMNS}
        sensors["temp"] = 24.5
        return parse_envelope(build_envelope(
            device_id=str(self.device_id),
            device_token=self.token,
            device_seq=seq,
            trigger="timer",
            sensors=sensors,
            measured_at=datetime.now(timezone.utc),
            message_id=message_id,
        ))

    def test_insert_then_duplicate_ack(self):
        envelope = self._envelope()
        self.assertEqual(self.repository.insert_sensor(envelope).status, "inserted")
        self.assertEqual(self.repository.insert_sensor(envelope).status, "duplicate")

    def test_simultaneous_same_message_is_exactly_once(self):
        envelope = self._envelope()
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = sorted(
                executor.map(lambda _: self.repository.insert_sensor(envelope), range(2)),
                key=lambda ack: ack.status,
            )
        self.assertEqual(sorted(ack.status for ack in statuses), ["duplicate", "inserted"])
        with self.repository.connection() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS count FROM sensor_readings WHERE message_id=%s",
                (envelope.message_id,),
            ).fetchone()["count"]
        self.assertEqual(count, 1)

    def test_same_device_sequence_different_message_is_rejected(self):
        first = self._envelope(seq=20)
        second = self._envelope(seq=20)
        self.assertEqual(self.repository.insert_sensor(first).status, "inserted")
        ack = self.repository.insert_sensor(second)
        self.assertEqual(ack.status, "rejected")
        self.assertEqual(ack.error_code, "MSG-E013")


if __name__ == "__main__":
    unittest.main()
