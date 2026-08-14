"""Ver3 protocolとPi側SQLiteキューのP0/P1単体テスト。"""

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import threading
import unittest
from uuid import uuid4

from config_errors import ConfigurationError, require_postgres_url
from edge_queue import EdgeQueue
from protocol_v3 import (
    Ack,
    ProtocolError,
    SENSOR_COLUMNS,
    build_envelope,
    canonical_payload_hash,
    parse_envelope,
)


DEVICE_ID = str(uuid4())
TOKEN = "test-device-token-with-at-least-24-characters"


def valid_payload(seq: int = 1, message_id: str | None = None) -> dict:
    sensors = {name: None for name in SENSOR_COLUMNS}
    sensors.update({"temp": 25.5, "hum": 60.0, "co2": 500.0, "light_raw": 100})
    return build_envelope(
        device_id=DEVICE_ID,
        device_token=TOKEN,
        device_seq=seq,
        trigger="timer",
        sensors=sensors,
        measured_at=datetime(2026, 7, 23, 1, 2, 3, tzinfo=timezone.utc),
        message_id=message_id,
    )


class ProtocolTests(unittest.TestCase):
    def test_public_envelope_keys_remain_stable(self):
        payload = valid_payload()
        self.assertEqual(
            set(payload),
            {
                "protocol_version",
                "message_id",
                "device_id",
                "device_token",
                "device_seq",
                "measured_at",
                "trigger",
                "sensors",
            },
        )
        self.assertEqual(tuple(payload["sensors"]), SENSOR_COLUMNS)

    def test_public_ack_keys_remain_stable(self):
        ack = Ack(
            protocol_version=3,
            message_id=str(uuid4()),
            status="inserted",
            device_id=DEVICE_ID,
            received_at="2026-07-23T01:02:04+00:00",
            stored_at="2026-07-23T01:02:05+00:00",
        ).to_dict()
        self.assertEqual(
            set(ack),
            {
                "protocol_version",
                "message_id",
                "status",
                "device_id",
                "received_at",
                "stored_at",
            },
        )

    def test_valid_payload_round_trip(self):
        parsed = parse_envelope(valid_payload())
        self.assertEqual(parsed.protocol_version, 3)
        self.assertEqual(parsed.device_seq, 1)
        self.assertEqual(parsed.sensors["temp"], 25.5)

    def test_hash_does_not_include_secret_token(self):
        first = parse_envelope(valid_payload())
        payload = valid_payload(message_id=str(first.message_id))
        payload["device_token"] = "another-valid-device-token-123456"
        second = parse_envelope(payload)
        self.assertEqual(canonical_payload_hash(first), canonical_payload_hash(second))

    def test_protocol_version_mismatch_rejected(self):
        payload = valid_payload()
        payload["protocol_version"] = 2
        with self.assertRaisesRegex(ProtocolError, "MSG-E003"):
            parse_envelope(payload)

    def test_naive_measured_at_rejected(self):
        payload = valid_payload()
        payload["measured_at"] = "2026-07-23T01:02:03"
        with self.assertRaisesRegex(ProtocolError, "MSG-E007"):
            parse_envelope(payload)

    def test_string_sensor_rejected(self):
        payload = valid_payload()
        payload["sensors"]["temp"] = "25.5"
        with self.assertRaisesRegex(ProtocolError, "VAL-E001"):
            parse_envelope(payload)

    def test_nan_rejected(self):
        payload = valid_payload()
        payload["sensors"]["temp"] = float("nan")
        with self.assertRaisesRegex(ProtocolError, "VAL-E002"):
            parse_envelope(payload)

    def test_physical_out_of_range_rejected(self):
        payload = valid_payload()
        payload["sensors"]["hum"] = 101
        with self.assertRaisesRegex(ProtocolError, "VAL-E003") as caught:
            parse_envelope(payload)
        self.assertEqual(caught.exception.message_id, payload["message_id"])

    def test_all_sensor_validation_errors_preserve_message_id(self):
        cases = (
            ("temp", "25.5", "VAL-E001"),
            ("temp", float("nan"), "VAL-E002"),
            ("hum", 101, "VAL-E003"),
            ("light_raw", 1.5, "VAL-E004"),
        )
        for sensor_name, value, code in cases:
            with self.subTest(code=code):
                payload = valid_payload()
                payload["sensors"][sensor_name] = value
                with self.assertRaises(ProtocolError) as caught:
                    parse_envelope(payload)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.message_id, payload["message_id"])

    def test_unknown_sensor_rejected(self):
        payload = valid_payload()
        payload["sensors"]["unknown"] = 1
        with self.assertRaisesRegex(ProtocolError, "MSG-E011"):
            parse_envelope(payload)

    def test_database_url_is_required_and_postgres_only(self):
        previous = os.environ.pop("DATABASE_URL", None)
        try:
            with self.assertRaisesRegex(ConfigurationError, "CFG-D001"):
                require_postgres_url()
            os.environ["DATABASE_URL"] = "sqlite:///wrong.db"
            with self.assertRaisesRegex(ConfigurationError, "CFG-D002"):
                require_postgres_url()
        finally:
            if previous is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = previous


class EdgeQueueTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.queue = EdgeQueue(Path(self.tempdir.name) / "pending.sqlite3")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_sequence_survives_reopen(self):
        self.assertEqual(self.queue.next_device_seq(), 1)
        reopened = EdgeQueue(self.queue.path)
        self.assertEqual(reopened.next_device_seq(), 2)

    def test_concurrent_sequence_is_unique(self):
        results = []
        lock = threading.Lock()

        def allocate():
            value = self.queue.next_device_seq()
            with lock:
                results.append(value)

        threads = [threading.Thread(target=allocate) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(results), list(range(1, 21)))

    def test_inserted_and_duplicate_remove_message(self):
        first = valid_payload(seq=self.queue.next_device_seq())
        self.queue.enqueue(first)
        self.assertTrue(self.queue.acknowledge(first["message_id"], "inserted"))
        second = valid_payload(seq=self.queue.next_device_seq())
        self.queue.enqueue(second)
        self.assertTrue(self.queue.acknowledge(second["message_id"], "duplicate"))
        self.assertEqual(self.queue.count(), 0)

    def test_retry_keeps_message_and_rejected_isolated(self):
        payload = valid_payload(seq=self.queue.next_device_seq())
        self.queue.enqueue(payload)
        self.queue.acknowledge(payload["message_id"], "retry", "DB-E006")
        self.assertEqual(self.queue.count("retry"), 1)
        self.queue.acknowledge(payload["message_id"], "rejected", "VAL-E003")
        self.assertEqual(self.queue.count("rejected"), 1)
        self.assertEqual(self.queue.pending(), [])

    def test_fifo_order(self):
        payloads = []
        for _ in range(3):
            payload = valid_payload(seq=self.queue.next_device_seq())
            payloads.append(payload)
            self.queue.enqueue(payload)
        pending = self.queue.pending()
        self.assertEqual(
            [item["device_seq"] for item in pending],
            [item["device_seq"] for item in payloads],
        )


if __name__ == "__main__":
    unittest.main()
