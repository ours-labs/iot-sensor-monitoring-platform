"""公開禁止情報スキャンの検出境界を固定する。"""

import unittest

from tools.public_release_scan import (
    ALLOWED_EMAIL_DOMAINS,
    EMAIL,
    PRIVATE_IPV4,
    TEXT_RULES,
)


class PublicReleaseScanTests(unittest.TestCase):
    def test_detects_private_ipv4_ranges(self):
        samples = (
            "10" + ".1.2.3",
            "100" + ".64.0.1",
            "100" + ".127.255.254",
            "172" + ".16.0.1",
            "172" + ".31.255.254",
            "192" + ".168.1.10",
        )
        for value in samples:
            with self.subTest(value=value):
                self.assertIsNotNone(PRIVATE_IPV4.search(value))

    def test_does_not_reject_public_or_documentation_ipv4(self):
        samples = (
            "8.8.8.8",
            "100" + ".63.0.1",
            "100" + ".128.0.1",
            "172.32.0.1",
            "192.0.2.10",
        )
        for value in samples:
            with self.subTest(value=value):
                self.assertIsNone(PRIVATE_IPV4.search(value))

    def test_detects_identity_patterns(self):
        self.assertIsNotNone(TEXT_RULES["student-id"].search("TK" + "123456"))
        self.assertIsNotNone(
            TEXT_RULES["personal-home"].search("/home/" + "localuser")
        )
        self.assertIsNotNone(
            TEXT_RULES["windows-user-home"].search("C:" + r"\Users" + r"\localuser")
        )

    def test_allows_only_reserved_email_domains(self):
        addresses = (
            "operator@example.com",
            "system@example.invalid",
            "12345+bot@users.noreply.github.com",
        )
        for value in addresses:
            with self.subTest(value=value):
                match = EMAIL.search(value)
                self.assertIsNotNone(match)
                domain = match.group(0).rsplit("@", 1)[1].lower()
                self.assertIn(domain, ALLOWED_EMAIL_DOMAINS)

        private_match = EMAIL.search("operator" + "@" + "real-domain.test")
        self.assertIsNotNone(private_match)
        private_domain = private_match.group(0).rsplit("@", 1)[1].lower()
        self.assertNotIn(private_domain, ALLOWED_EMAIL_DOMAINS)


if __name__ == "__main__":
    unittest.main()
