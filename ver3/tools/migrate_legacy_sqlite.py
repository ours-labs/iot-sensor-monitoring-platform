"""Ver2 SQLite全履歴をVer3 PostgreSQLへ冪等に移行する。

実アドレスや利用者IDは引数・環境変数から受け取り、ソースへ保持しない。
Ver2の手入力triggerに含まれていた入力者IDはVer3へ複製せず、
source_type=manual / trigger=manual-legacyとして区別だけを残す。
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
from typing import Any
from uuid import UUID, NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from config_errors import require_postgres_url  # noqa: E402
from database import _import_psycopg  # noqa: E402
from protocol_v3 import PHYSICAL_RANGES, SENSOR_COLUMNS  # noqa: E402


LEGACY_COLUMNS = ("timestamp", "trigger", *SENSOR_COLUMNS)
INTEGER_SENSORS = {"light_raw", "sound_raw"}
MANUAL_TRIGGER = re.compile(r"(?i)^TK[0-9]{6}$")


def _source_rows(path: Path, batch_size: int) -> Iterator[list[dict[str, Any]]]:
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        schema_cursor = conn.execute("PRAGMA table_info(sensor_readings)")
        try:
            columns = {row["name"] for row in schema_cursor.fetchall()}
        finally:
            schema_cursor.close()
        missing = set(("id", *LEGACY_COLUMNS)) - columns
        if missing:
            raise ValueError(
                "[MIG-E001] Ver2 SQLiteの列が不足しています: " + ", ".join(sorted(missing))
            )
        cursor = conn.execute(
            "SELECT id, " + ", ".join(f'"{name}"' for name in LEGACY_COLUMNS)
            + " FROM sensor_readings ORDER BY id"
        )
        try:
            while rows := cursor.fetchmany(batch_size):
                # sqlite3.Rowはcursorを参照し続ける実装があるため、接続外へはdictだけを渡す。
                yield [dict(row) for row in rows]
        finally:
            cursor.close()


def _timestamp(value: Any, source_zone: ZoneInfo) -> datetime:
    text = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"[MIG-E002] timestampを解釈できません: row timestamp={text!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=source_zone)
    return parsed.astimezone(timezone.utc)


def _numeric(name: str, value: Any) -> tuple[int | float | None, str | None]:
    if value is None or str(value).strip().lower() in {"", "null", "none", "nan"}:
        return None, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, "not_numeric"
    if number != number or number in {float("inf"), float("-inf")}:
        return None, "not_finite"
    lower, upper = PHYSICAL_RANGES[name]
    if not lower <= number <= upper:
        return None, "outside_physical_range"
    if name in INTEGER_SENSORS:
        if not number.is_integer():
            return None, "not_integer"
        return int(number), None
    return number, None


def convert_row(row: dict[str, Any], device_id: UUID, source_zone: ZoneInfo) -> dict[str, Any]:
    measured_at = _timestamp(row["timestamp"], source_zone)
    values: dict[str, int | float | None] = {}
    invalid: dict[str, str] = {}
    for name in SENSOR_COLUMNS:
        values[name], reason = _numeric(name, row[name])
        if reason:
            invalid[name] = reason
    original_trigger = str(row["trigger"] or "").strip()
    is_manual = bool(MANUAL_TRIGGER.fullmatch(original_trigger))
    canonical = {
        "legacy_row_id": int(row["id"]),
        "measured_at": measured_at.isoformat(),
        "source_type": "manual" if is_manual else "legacy",
        "trigger": "manual-legacy" if is_manual else (
            original_trigger if original_trigger in {"timer", "button"} else "legacy"
        ),
        "values": values,
    }
    stable = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    message_id = uuid5(NAMESPACE_URL, "ver2-sqlite:" + stable)
    return {
        **canonical,
        "measured_at": measured_at,
        "message_id": message_id,
        "device_id": device_id,
        "device_seq": None if is_manual else int(row["id"]),
        "payload_hash": hashlib.sha256(stable.encode("utf-8")).hexdigest(),
        "quality_state": "warning" if invalid else "valid",
        "quality_detail": {"migration": "ver2_sqlite", "invalid_values": invalid},
    }


def _insert(conn: Any, item: dict[str, Any]) -> str:
    device = conn.execute(
        "SELECT status FROM devices WHERE device_id = %s", (item["device_id"],)
    ).fetchone()
    if device is None:
        raise ValueError("[MIG-E003] 移行先device_idが登録されていません")
    if device[0] != "active":
        raise ValueError("[MIG-E004] 移行先device_idがactiveではありません")
    received_at = stored_at = datetime.now(timezone.utc)
    inserted = conn.execute(
        """
        INSERT INTO ingest_messages(
            message_id, device_id, payload_hash, status, received_at, stored_at
        ) VALUES (%s, %s, %s, 'inserted', %s, %s)
        ON CONFLICT (message_id) DO NOTHING RETURNING message_id
        """,
        (
            item["message_id"], item["device_id"], item["payload_hash"],
            received_at, stored_at,
        ),
    ).fetchone()
    if inserted is None:
        existing = conn.execute(
            "SELECT device_id, payload_hash FROM ingest_messages WHERE message_id = %s",
            (item["message_id"],),
        ).fetchone()
        if existing is None or existing[0] != item["device_id"] or existing[1] != item["payload_hash"]:
            raise ValueError("[MIG-E005] 同じmessage_idに異なる既存データがあります")
        return "duplicate"
    columns = ", ".join(SENSOR_COLUMNS)
    placeholders = ", ".join(["%s"] * len(SENSOR_COLUMNS))
    conn.execute(
        f"""
        INSERT INTO sensor_readings(
            message_id, device_id, device_seq, measured_at, received_at, stored_at,
            source_type, trigger, quality_state, quality_detail, {columns}
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, {placeholders}
        )
        """,
        (
            item["message_id"], item["device_id"], item["device_seq"],
            item["measured_at"], received_at, stored_at, item["source_type"],
            item["trigger"], item["quality_state"], json.dumps(item["quality_detail"]),
            *(item["values"][name] for name in SENSOR_COLUMNS),
        ),
    )
    return "inserted"


def main() -> int:
    parser = argparse.ArgumentParser(description="Ver2 SQLiteからVer3 PostgreSQLへの移行")
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("--device-id", required=True, type=UUID)
    parser.add_argument("--source-timezone", default="Asia/Tokyo")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if not args.sqlite_path.is_file():
        parser.error("sqlite_pathが存在しません")
    if not 1 <= args.batch_size <= 5000:
        parser.error("batch-sizeは1〜5000です")
    try:
        source_zone = ZoneInfo(args.source_timezone)
    except Exception:
        parser.error("source-timezoneが不正です")

    stats = {
        "source_rows": 0, "inserted": 0, "duplicates": 0,
        "manual_rows": 0, "warning_rows": 0,
    }
    psycopg = None
    url = None
    if not args.dry_run:
        psycopg, _, _ = _import_psycopg()
        url = require_postgres_url().replace("postgresql+psycopg://", "postgresql://", 1)

    for batch in _source_rows(args.sqlite_path, args.batch_size):
        converted = [convert_row(row, args.device_id, source_zone) for row in batch]
        stats["source_rows"] += len(converted)
        stats["manual_rows"] += sum(item["source_type"] == "manual" for item in converted)
        stats["warning_rows"] += sum(item["quality_state"] == "warning" for item in converted)
        if args.dry_run:
            continue
        with psycopg.connect(url) as conn, conn.transaction():
            for item in converted:
                result = _insert(conn, item)
                stats["inserted" if result == "inserted" else "duplicates"] += 1

    report = {
        "status": "dry-run" if args.dry_run else "completed",
        "device_id": str(args.device_id),
        "source_timezone": args.source_timezone,
        **stats,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
