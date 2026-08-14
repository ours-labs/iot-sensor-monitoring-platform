"""Ver3受信・ACK・設定境界の単体テスト。"""

import json
import os
import unittest

os.environ["SENSOR_HOST"] = "127.0.0.1"
os.environ["SENSOR_PORT"] = "50000"

import sensor_server  # noqa: E402
from config_errors import ConfigurationError, require_port  # noqa: E402
from protocol_v3 import Ack, ProtocolError  # noqa: E402


class AckTests(unittest.TestCase):
    def test_ack_is_newline_delimited_json(self):
        encoded = sensor_server.ack_bytes(Ack(3, "abc", "inserted"))
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertEqual(json.loads(encoded), {
            "protocol_version": 3, "message_id": "abc", "status": "inserted"
        })

    def test_protocol_error_becomes_rejected_ack(self):
        ack = sensor_server.rejected_ack(
            ProtocolError("MSG-E003", "version mismatch", message_id="abc")
        )
        self.assertEqual(ack.status, "rejected")
        self.assertEqual(ack.error_code, "MSG-E003")
        self.assertEqual(ack.message_id, "abc")

    def test_rejected_ack_uses_parsed_message_id_as_fallback(self):
        ack = sensor_server.rejected_ack(
            ProtocolError("VAL-E003", "out of range"),
            fallback_message_id="request-message-id",
        )
        self.assertEqual(ack.status, "rejected")
        self.assertEqual(ack.error_code, "VAL-E003")
        self.assertEqual(ack.message_id, "request-message-id")


class ConfigurationTests(unittest.TestCase):
    def test_valid_port(self):
        os.environ["TEST_SENSOR_PORT"] = "12345"
        self.assertEqual(require_port("TEST_SENSOR_PORT"), 12345)

    def test_invalid_port_has_stable_code(self):
        os.environ["TEST_SENSOR_PORT"] = "invalid"
        with self.assertRaisesRegex(ConfigurationError, "CFG-C002"):
            require_port("TEST_SENSOR_PORT")


if __name__ == "__main__":
    unittest.main()
