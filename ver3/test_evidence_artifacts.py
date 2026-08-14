"""検証証跡ファイルの形式と整合性を確認する。"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from evidence_artifacts import (
    atomic_json,
    file_sha256,
    write_success_evidence,
)


class EvidenceArtifactTests(unittest.TestCase):
    def test_proof_and_checksum_match_result(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = root / "result.json"
            proof = root / "result.proof.json"
            checksum = root / "result.sha256"
            atomic_json(result, {"status": "passed"})

            digest = write_success_evidence(
                result,
                status="VER3_TEST_OK",
                checked_at="2026-07-24T09:42:00+00:00",
                proof_path=proof,
                checksum_path=checksum,
            )

            self.assertEqual(digest, file_sha256(result))
            self.assertEqual(
                checksum.read_text(encoding="utf-8"),
                f"{digest}  result.json\n",
            )
            proof_payload = json.loads(proof.read_text(encoding="utf-8"))
            self.assertEqual(proof_payload["status"], "VER3_TEST_OK")
            self.assertEqual(proof_payload["result"], "result.json")
            self.assertEqual(proof_payload["sha256"], digest)

    def test_proof_rejects_sha256_extension(self):
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "result.json"
            atomic_json(result, {"status": "passed"})
            with self.assertRaisesRegex(ValueError, "json"):
                write_success_evidence(
                    result,
                    status="VER3_TEST_OK",
                    checked_at="2026-07-24T09:42:00+00:00",
                    proof_path=Path(directory) / "result.sha256",
                )


if __name__ == "__main__":
    unittest.main()
