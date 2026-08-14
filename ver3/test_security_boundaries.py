"""Ver3認証情報の保存境界に関する単体テスト。"""

import os
import unittest

from config_errors import ConfigurationError, require_token_hash_key
from database import token_hash


class TokenHashTests(unittest.TestCase):
    def setUp(self):
        self.previous = os.environ.get("TOKEN_HASH_KEY")

    def tearDown(self):
        if self.previous is None:
            os.environ.pop("TOKEN_HASH_KEY", None)
        else:
            os.environ["TOKEN_HASH_KEY"] = self.previous

    def test_hmac_is_deterministic_per_environment_key(self):
        first = token_hash("high-entropy-token", "a" * 32)
        second = token_hash("high-entropy-token", "a" * 32)
        other_key = token_hash("high-entropy-token", "b" * 32)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_key)
        self.assertEqual(len(first), 64)

    def test_short_environment_key_is_rejected(self):
        os.environ["TOKEN_HASH_KEY"] = "too-short"
        with self.assertRaises(ConfigurationError) as context:
            require_token_hash_key()
        self.assertEqual(context.exception.code, "CFG-D003")


if __name__ == "__main__":
    unittest.main()
