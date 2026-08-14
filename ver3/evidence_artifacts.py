"""検証結果、合格証跡、SHA-256を原子的に保存する共通処理。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def atomic_text(path: Path, text: str) -> None:
    """同一ディレクトリの一時ファイルを原子的に置き換える。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def atomic_json(path: Path, payload: dict[str, object]) -> None:
    atomic_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_success_evidence(
    result_path: Path,
    *,
    status: str,
    checked_at: str,
    proof_path: Path | None = None,
    checksum_path: Path | None = None,
) -> str:
    """合格結果のJSON証跡と標準形式SHA-256を必要な方へ保存する。"""
    if proof_path is not None and proof_path.suffix.lower() != ".json":
        raise ValueError("proof_pathは.jsonで終わる必要があります")
    digest = file_sha256(result_path)
    if proof_path is not None:
        atomic_json(
            proof_path,
            {
                "status": status,
                "checked_at": checked_at,
                "result": result_path.name,
                "sha256": digest,
            },
        )
    if checksum_path is not None:
        atomic_text(checksum_path, f"{digest}  {result_path.name}\n")
    return digest
