"""Pi側の未ACK要求と永続device_seqをSQLiteで管理する。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3


class EdgeQueue:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS queue_state (
                    key TEXT PRIMARY KEY,
                    value INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO queue_state(key, value) VALUES ('device_seq', 0);
                CREATE TABLE IF NOT EXISTS pending_messages (
                    message_id TEXT PRIMARY KEY,
                    device_seq INTEGER NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending'
                        CHECK (state IN ('pending', 'retry', 'rejected')),
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_attempt_at TEXT,
                    last_error_code TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_pending_delivery
                    ON pending_messages(state, device_seq);
                """
            )

    def next_device_seq(self) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE queue_state SET value = value + 1 WHERE key = 'device_seq'"
            )
            row = conn.execute(
                "SELECT value FROM queue_state WHERE key = 'device_seq'"
            ).fetchone()
            return int(row[0])

    def enqueue(self, payload: dict) -> None:
        message_id = str(payload["message_id"])
        device_seq = int(payload["device_seq"])
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO pending_messages(
                    message_id, device_seq, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (message_id, device_seq, serialized, datetime.now(timezone.utc).isoformat()),
            )

    def pending(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT payload_json FROM pending_messages
                WHERE state IN ('pending', 'retry')
                ORDER BY device_seq ASC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def mark_attempt(self, message_id: str, error_code: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE pending_messages
                SET attempt_count = attempt_count + 1,
                    last_attempt_at = ?, last_error_code = ?, state = 'retry'
                WHERE message_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), error_code, message_id),
            )

    def acknowledge(self, message_id: str, status: str, error_code: str | None = None) -> bool:
        with self._connect() as conn:
            if status in {"inserted", "duplicate"}:
                cursor = conn.execute(
                    "DELETE FROM pending_messages WHERE message_id = ?", (message_id,)
                )
                return cursor.rowcount == 1
            if status == "rejected":
                cursor = conn.execute(
                    """
                    UPDATE pending_messages SET state = 'rejected', last_error_code = ?
                    WHERE message_id = ?
                    """,
                    (error_code, message_id),
                )
                return cursor.rowcount == 1
            conn.execute(
                """
                UPDATE pending_messages
                SET attempt_count = attempt_count + 1,
                    last_attempt_at = ?, last_error_code = ?, state = 'retry'
                WHERE message_id = ?
                """,
                (datetime.now(timezone.utc).isoformat(), error_code, message_id),
            )
            return False

    def count(self, state: str | None = None) -> int:
        with self._connect() as conn:
            if state is None:
                row = conn.execute("SELECT COUNT(*) FROM pending_messages").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) FROM pending_messages WHERE state = ?", (state,)
                ).fetchone()
        return int(row[0])
