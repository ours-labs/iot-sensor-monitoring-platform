"""Ver3 Raspberry Pi限定遠隔操作エージェント。

既定はCONTROL_DRY_RUN=true。危険操作はサーバーがイベント保存をACKした後だけ実行する。
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

MIN_CONTROL_INTERVAL_SECONDS = 2
HTTP_STATUS_CLASS_DIVISOR = 100
HTTP_SUCCESS_STATUS_CLASS = 2


BASE_URL = os.environ.get("CONTROL_BASE_URL", "").rstrip("/")
DEVICE_ID = os.environ.get("DEVICE_ID", "")
DEVICE_TOKEN = os.environ.get("DEVICE_TOKEN", "")
DB_PATH = Path(os.environ.get("CONTROL_DB_PATH", "control_agent.db"))
POLL_SECONDS = max(
    MIN_CONTROL_INTERVAL_SECONDS, int(os.environ.get("CONTROL_POLL_SECONDS", "10"))
)
DRY_RUN = os.environ.get("CONTROL_DRY_RUN", "true").lower() not in {"0", "false", "no"}
HTTP_TIMEOUT = max(
    MIN_CONTROL_INTERVAL_SECONDS, int(os.environ.get("CONTROL_HTTP_TIMEOUT", "10"))
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def boot_id() -> str:
    try:
        return str(UUID(Path("/proc/sys/kernel/random/boot_id").read_text().strip()))
    except (OSError, ValueError):
        return str(uuid4())


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS processed_commands(
            request_id TEXT PRIMARY KEY,
            command TEXT NOT NULL,
            state TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS outbox(
            client_event_id TEXT PRIMARY KEY,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metadata(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    return conn


def request_json(path: str, body: dict) -> dict:
    request = Request(
        BASE_URL + path,
        data=json.dumps(body, separators=(",", ":")).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Device-Token": DEVICE_TOKEN,
            "User-Agent": "sensor-v3-control-agent/3.0",
        },
        method="POST",
    )
    with urlopen(request, timeout=HTTP_TIMEOUT) as response:
        payload = json.loads(response.read().decode())
        if response.status // HTTP_STATUS_CLASS_DIVISOR != HTTP_SUCCESS_STATUS_CLASS:
            raise RuntimeError(f"HTTP {response.status}")
        return payload


def enqueue_event(
    conn: sqlite3.Connection, request_id: str, event_type: str, detail: dict
) -> str:
    event_id = str(uuid4())
    payload = {
        "client_event_id": event_id,
        "device_id": DEVICE_ID,
        "request_id": request_id,
        "event_type": event_type,
        "boot_id": boot_id(),
        "occurred_at": utc_now(),
        "detail": detail,
    }
    conn.execute(
        "INSERT INTO outbox(client_event_id,payload,created_at) VALUES(?,?,?)",
        (event_id, json.dumps(payload, separators=(",", ":")), utc_now()),
    )
    conn.commit()
    return event_id


def flush_outbox(conn: sqlite3.Connection) -> set[str]:
    delivered: set[str] = set()
    for row in conn.execute(
        "SELECT client_event_id,payload FROM outbox ORDER BY created_at"
    ):
        try:
            response = request_json(
                "/api/v3/device-control/events", json.loads(row["payload"])
            )
            if response.get("status") != "success":
                break
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, RuntimeError):
            break
        conn.execute(
            "DELETE FROM outbox WHERE client_event_id=?", (row["client_event_id"],)
        )
        conn.commit()
        delivered.add(row["client_event_id"])
    return delivered


def mark(conn: sqlite3.Connection, request_id: str, command: str, state: str) -> None:
    conn.execute(
        """INSERT INTO processed_commands(request_id,command,state,updated_at)
           VALUES(?,?,?,?) ON CONFLICT(request_id) DO UPDATE SET
           state=excluded.state, updated_at=excluded.updated_at""",
        (request_id, command, state, utc_now()),
    )
    conn.commit()


def metadata_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def metadata_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def metadata_delete(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM metadata WHERE key=?", (key,))
    conn.commit()


def report_pending_boot(conn: sqlite3.Connection) -> None:
    request_id = metadata_get(conn, "pending_boot_request_id")
    if not request_id:
        return
    event_key = "boot_event:" + request_id
    event_id = metadata_get(conn, event_key)
    if not event_id:
        event_id = enqueue_event(conn, request_id, "boot", {"result": "boot_confirmed"})
        metadata_set(conn, event_key, event_id)
    flush_outbox(conn)
    pending = conn.execute(
        "SELECT 1 FROM outbox WHERE client_event_id=?", (event_id,)
    ).fetchone()
    if pending is None:
        metadata_delete(conn, "pending_boot_request_id")
        metadata_delete(conn, event_key)


def execute_command(conn: sqlite3.Connection, command: dict) -> None:
    request_id = str(command["request_id"])
    action = str(command["command"])
    previous = conn.execute(
        "SELECT state FROM processed_commands WHERE request_id=?", (request_id,)
    ).fetchone()
    if previous and previous["state"] in {"completed", "executing", "dry_run_rejected"}:
        flush_outbox(conn)
        return

    if action == "status":
        mark(conn, request_id, action, "completed")
        enqueue_event(
            conn,
            request_id,
            "status_report",
            {
                "agent": "online",
                "dry_run": DRY_RUN,
                "outbox_pending": conn.execute(
                    "SELECT COUNT(*) FROM outbox"
                ).fetchone()[0],
            },
        )
        flush_outbox(conn)
        return

    if action not in {"restart", "shutdown"}:
        mark(conn, request_id, action, "rejected")
        enqueue_event(conn, request_id, "rejected", {"reason": "unsupported_command"})
        flush_outbox(conn)
        return

    if DRY_RUN:
        mark(conn, request_id, action, "dry_run_rejected")
        enqueue_event(
            conn,
            request_id,
            "rejected",
            {"reason": "CONTROL_DRY_RUN", "would_execute": action},
        )
        flush_outbox(conn)
        return

    event_key = "prepared_event:" + request_id
    event_id = metadata_get(conn, event_key)
    if not event_id:
        mark(conn, request_id, action, "prepared")
        event_id = enqueue_event(
            conn, request_id, "shutdown_started", {"action": action}
        )
        metadata_set(conn, event_key, event_id)
    flush_outbox(conn)
    pending = conn.execute(
        "SELECT 1 FROM outbox WHERE client_event_id=?", (event_id,)
    ).fetchone()
    if pending is not None:
        mark(conn, request_id, action, "waiting_for_server_ack")
        return

    metadata_delete(conn, event_key)
    metadata_set(conn, "pending_boot_request_id", request_id)
    mark(conn, request_id, action, "executing")
    systemctl_action = "reboot" if action == "restart" else "poweroff"
    subprocess.run(
        ["sudo", "-n", "/usr/bin/systemctl", systemctl_action],
        check=True,
        timeout=15,
    )


def validate_config() -> None:
    missing = [
        name
        for name, value in {
            "CONTROL_BASE_URL": BASE_URL,
            "DEVICE_ID": DEVICE_ID,
            "DEVICE_TOKEN": DEVICE_TOKEN,
        }.items()
        if not value
    ]
    if missing:
        raise SystemExit("[CFG-R001] 必須設定がありません: " + ", ".join(missing))
    try:
        UUID(DEVICE_ID)
    except ValueError as exc:
        raise SystemExit("[CFG-R002] DEVICE_IDがUUIDではありません") from exc
    if not BASE_URL.startswith("https://"):
        raise SystemExit("[CFG-R003] CONTROL_BASE_URLはHTTPS必須です")


def main() -> None:
    validate_config()
    conn = connect()
    report_pending_boot(conn)
    while True:
        flush_outbox(conn)
        try:
            response = request_json(
                "/api/v3/device-control/poll", {"device_id": DEVICE_ID}
            )
            command = response.get("command")
            if command:
                execute_command(conn, command)
        except HTTPError as exc:
            print(
                f"[CTL-W001] 制御サーバーと同期できません: HTTP {exc.code}", flush=True
            )
        except (URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
            print(
                f"[CTL-W001] 制御サーバーと同期できません: {type(exc).__name__}",
                flush=True,
            )
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
