"""Ver3 PostgreSQLを原子的なSQLite復旧スナップショットへ保存する。

出力には個人情報と資格情報ハッシュが含まれる。Gitへ追加せず、アクセス制限と
暗号化された保存先を用いること。生のAPIキーやDB接続URLは保存しない。
"""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any
from uuid import UUID


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from config_errors import require_postgres_url  # noqa: E402
from database import _import_psycopg  # noqa: E402
from system_identity import SCHEMA_VERSION, SYSTEM_VERSION  # noqa: E402


TABLES = (
    "devices",
    "operator_identities",
    "api_credentials",
    "device_capabilities",
    "ingest_messages",
    "sensor_readings",
    "manual_input_audit",
    "device_admin_audit",
    "control_commands",
    "control_events",
    "monitor_events",
)


def encode(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def create_backup(database_url: str, destination: Path) -> dict[str, int]:
    psycopg, dict_row, _ = _import_psycopg()
    url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=destination.name + ".", suffix=".tmp", dir=destination.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    counts: dict[str, int] = {}
    try:
        with psycopg.connect(url, row_factory=dict_row) as source, closing(
            sqlite3.connect(temporary)
        ) as target:
            target.execute("PRAGMA journal_mode=DELETE")
            target.execute("PRAGMA synchronous=FULL")
            target.execute(
                "CREATE TABLE backup_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            metadata = {
                "backup_format": "ver3-sqlite-snapshot-v1",
                "system_version": SYSTEM_VERSION,
                "schema_version": str(SCHEMA_VERSION),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "contains_sensitive_data": "true",
            }
            target.executemany(
                "INSERT INTO backup_metadata(key, value) VALUES (?, ?)", metadata.items()
            )
            target.execute(
                """
                CREATE TABLE backup_columns(
                    table_name TEXT NOT NULL,
                    ordinal_position INTEGER NOT NULL,
                    column_name TEXT NOT NULL,
                    udt_name TEXT NOT NULL,
                    PRIMARY KEY(table_name, column_name)
                )
                """
            )

            for table in TABLES:
                definitions = source.execute(
                    """
                    SELECT ordinal_position, column_name, udt_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema() AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (table,),
                ).fetchall()
                if not definitions:
                    raise RuntimeError(f"[BKP-E001] 必須テーブルがありません: {table}")
                columns = [item["column_name"] for item in definitions]
                target.execute(
                    f"CREATE TABLE {_quote(table)}("
                    + ", ".join(f"{_quote(name)} TEXT" for name in columns) + ")"
                )
                target.executemany(
                    """
                    INSERT INTO backup_columns(
                        table_name, ordinal_position, column_name, udt_name
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [
                        (
                            table, item["ordinal_position"],
                            item["column_name"], item["udt_name"],
                        )
                        for item in definitions
                    ],
                )
                rows = source.execute(f"SELECT * FROM {_quote(table)}").fetchall()
                placeholders = ", ".join(["?"] * len(columns))
                target.executemany(
                    f"INSERT INTO {_quote(table)} VALUES ({placeholders})",
                    [[encode(row[name]) for name in columns] for row in rows],
                )
                counts[table] = len(rows)
            target.commit()
        os.replace(temporary, destination)
        if os.name != "nt":
            destination.chmod(0o600)
        return counts
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Ver3 PostgreSQL→SQLite復旧バックアップ")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.destination.exists() and not args.overwrite:
        parser.error("出力先が存在します。上書きには--overwriteが必要です")
    counts = create_backup(require_postgres_url(), args.destination.resolve())
    print(json.dumps(
        {
            "status": "completed",
            "destination": str(args.destination.resolve()),
            "tables": counts,
            "warning": "個人情報と資格情報ハッシュを含むためGitへ追加しないでください",
        },
        ensure_ascii=False,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
