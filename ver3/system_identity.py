"""Ver3の版数・プロトコル・DBスキーマ識別を一元管理する。"""

from __future__ import annotations

import os
from pathlib import Path


EXPECTED_MAJOR = 3
PROTOCOL_VERSION = 3
SCHEMA_VERSION = 3
VERSION_FILE = Path(__file__).with_name("VERSION")


class VersionBoundaryError(RuntimeError):
    """Ver3領域に別世代のコード・設定が混入した場合の安全停止。"""


def read_system_version() -> str:
    try:
        version = VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise VersionBoundaryError(
            "[VER-E001] Ver3のVERSIONファイルを読み込めません"
        ) from exc
    if not version:
        raise VersionBoundaryError("[VER-E002] Ver3のVERSIONが空です")
    try:
        major = int(version.split(".", 1)[0])
    except (TypeError, ValueError) as exc:
        raise VersionBoundaryError(
            f"[VER-E003] 不正なシステム版数です: {version!r}"
        ) from exc
    if major != EXPECTED_MAJOR:
        raise VersionBoundaryError(
            f"[VER-E004] Ver3領域に別世代の版数が指定されています: {version}"
        )
    configured_major = os.environ.get("EXPECTED_SYSTEM_MAJOR")
    if configured_major and configured_major != str(EXPECTED_MAJOR):
        raise VersionBoundaryError(
            "[VER-E005] 配置先が要求するシステム世代とVer3コードが一致しません"
        )
    return version


SYSTEM_VERSION = read_system_version()
BUILD_CHANNEL = os.environ.get("SYSTEM_BUILD_CHANNEL", "development").strip() or "development"


def identity() -> dict[str, object]:
    return {
        "system_version": SYSTEM_VERSION,
        "system_major": EXPECTED_MAJOR,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": SCHEMA_VERSION,
        "build_channel": BUILD_CHANNEL,
    }
