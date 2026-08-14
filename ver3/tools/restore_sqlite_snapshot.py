"""専用SQLiteスナップショットを空のVer3 PostgreSQLへ復元する。"""

from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from config_errors import require_postgres_url  # noqa: E402
from database import _import_psycopg  # noqa: E402
from system_identity import SCHEMA_VERSION  # noqa: E402
from tools.backup_postgres_to_sqlite import TABLES  # noqa: E402


IDENTITY_TABLES = {
    "sensor_readings": "id",
    "device_admin_audit": "event_id",
    "control_events": "event_id",
    "monitor_events": "event_id",
}


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _placeholder(udt_name: str) -> str:
    casts = {
        "uuid": "%s::uuid",
        "timestamptz": "%s::timestamptz",
        "timestamp": "%s::timestamp",
        "date": "%s::date",
        "jsonb": "%s::jsonb",
        "json": "%s::json",
        "bool": "%s::boolean",
        "int2": "%s::smallint",
        "int4": "%s::integer",
        "int8": "%s::bigint",
        "float4": "%s::real",
        "float8": "%s::double precision",
    }
    return casts.get(udt_name, "%s")


def restore(snapshot: Path, database_url: str) -> dict[str, int]:
    psycopg, _, _ = _import_psycopg()
    with closing(sqlite3.connect(snapshot)) as source:
        source.row_factory = sqlite3.Row
        metadata = dict(source.execute("SELECT key, value FROM backup_metadata"))
        if metadata.get("backup_format") != "ver3-sqlite-snapshot-v1":
            raise ValueError("[RST-E001] Ver3専用バックアップ形式ではありません")
        if metadata.get("schema_version") != str(SCHEMA_VERSION):
            raise ValueError("[RST-E002] バックアップのスキーマ版数が一致しません")
        url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        counts: dict[str, int] = {}
        with psycopg.connect(url) as target, target.transaction():
            target.execute("SELECT pg_advisory_xact_lock(3000003)")
            current = target.execute(
                "SELECT value FROM schema_metadata WHERE key='schema_version'"
            ).fetchone()
            if current is None or str(current[0]) != str(SCHEMA_VERSION):
                raise ValueError("[RST-E003] 復元先がVer3スキーマではありません")
            occupied = {
                table: target.execute(
                    f"SELECT COUNT(*) FROM {_quote(table)}"
                ).fetchone()[0]
                for table in TABLES
            }
            if any(occupied.values()):
                names = ", ".join(name for name, count in occupied.items() if count)
                raise ValueError("[RST-E004] 復元先が空ではありません: " + names)

            for table in TABLES:
                definitions = source.execute(
                    """
                    SELECT column_name, udt_name FROM backup_columns
                    WHERE table_name=? ORDER BY ordinal_position
                    """,
                    (table,),
                ).fetchall()
                columns = [item["column_name"] for item in definitions]
                placeholders = ", ".join(
                    _placeholder(item["udt_name"]) for item in definitions
                )
                overriding = " OVERRIDING SYSTEM VALUE" if table in IDENTITY_TABLES else ""
                query = (
                    f"INSERT INTO {_quote(table)}("
                    + ", ".join(_quote(name) for name in columns)
                    + f"){overriding} VALUES ({placeholders})"
                )
                rows = source.execute(f"SELECT * FROM {_quote(table)}").fetchall()
                for row in rows:
                    target.execute(query, tuple(row[name] for name in columns))
                counts[table] = len(rows)

            for table, column in IDENTITY_TABLES.items():
                target.execute(
                    "SELECT setval(pg_get_serial_sequence(%s, %s), "
                    f"COALESCE((SELECT MAX({_quote(column)}) FROM {_quote(table)}), 1), "
                    f"EXISTS(SELECT 1 FROM {_quote(table)}))",
                    (table, column),
                )
        return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite復旧バックアップ→Ver3 PostgreSQL")
    parser.add_argument("snapshot", type=Path)
    parser.add_argument(
        "--confirm-empty-target",
        action="store_true",
        help="空の復元先DBであることを確認した場合のみ指定",
    )
    args = parser.parse_args()
    if not args.snapshot.is_file():
        parser.error("snapshotが存在しません")
    if not args.confirm_empty_target:
        parser.error("復元には--confirm-empty-targetが必要です")
    counts = restore(args.snapshot, require_postgres_url())
    print("復元完了:", counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
