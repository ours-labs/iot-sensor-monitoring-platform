"""IoT環境監視システム Ver.3 PostgreSQLデバイス別監視・通知。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from database import DatabaseError, PostgresRepository  # noqa: E402
from system_identity import SCHEMA_VERSION, SYSTEM_VERSION  # noqa: E402

HTTP_STATUS_CLASS_DIVISOR = 100
HTTP_SUCCESS_STATUS_CLASS = 2
POLL_INTERVAL_SEC = int(os.environ.get("MONITOR_POLL_INTERVAL_SEC", "5"))
STALE_THRESHOLD_SEC = int(os.environ.get("MONITOR_STALE_THRESHOLD_SEC", "60"))
STALE_REPEAT_LOG_SEC = int(os.environ.get("MONITOR_STALE_REPEAT_LOG_SEC", "900"))
WEBHOOK_URL = os.environ.get("MONITOR_WEBHOOK_URL", "").strip()
WEBHOOK_TIMEOUT_SEC = int(os.environ.get("MONITOR_WEBHOOK_TIMEOUT_SEC", "5"))
NOTIFICATION_MAX_BACKOFF_SEC = int(
    os.environ.get("MONITOR_NOTIFICATION_MAX_BACKOFF_SEC", "900")
)
logger = logging.getLogger("sensor_monitor_v3")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)


class WebhookNotifier:
    def __init__(self, url: str = WEBHOOK_URL):
        self.url = url

    def should_notify(self, severity: str, state: str) -> bool:
        return bool(
            self.url
            and (severity in {"warning", "error", "critical"} or state == "recovered")
        )

    def dispatch_pending(self, repository: PostgresRepository) -> None:
        if not self.url:
            return
        now = datetime.now(timezone.utc)
        with repository.connection() as conn:
            rows = conn.execute(
                """
                SELECT e.event_id, e.event_code, e.severity, e.state, e.detail,
                       e.occurred_at, e.notification_attempts,
                       d.device_id, d.display_name
                FROM monitor_events e
                LEFT JOIN devices d ON d.device_id=e.device_id
                WHERE e.notification_required=TRUE AND e.notified_at IS NULL
                  AND (e.next_notification_at IS NULL OR e.next_notification_at <= %s)
                ORDER BY e.event_id LIMIT 20
                """,
                (now,),
            ).fetchall()
        for row in rows:
            payload = {
                "system": "sensor-monitor-ver3",
                "event_id": row["event_id"],
                "event_code": row["event_code"],
                "severity": row["severity"],
                "state": row["state"],
                "device_id": None
                if row["device_id"] is None
                else str(row["device_id"]),
                "display_name": row["display_name"],
                "occurred_at": row["occurred_at"].isoformat(),
                "detail": row["detail"],
            }
            try:
                request = Request(
                    self.url,
                    data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "sensor-monitor-ver3/3.0",
                    },
                    method="POST",
                )
                with urlopen(request, timeout=WEBHOOK_TIMEOUT_SEC) as response:
                    if (
                        response.status // HTTP_STATUS_CLASS_DIVISOR
                        != HTTP_SUCCESS_STATUS_CLASS
                    ):
                        raise RuntimeError(f"HTTP {response.status}")
                with repository.connection() as conn, conn.transaction():
                    conn.execute(
                        "UPDATE monitor_events SET notified_at=%s, notification_last_error=NULL WHERE event_id=%s",
                        (datetime.now(timezone.utc), row["event_id"]),
                    )
            except (HTTPError, URLError, TimeoutError, OSError, RuntimeError) as exc:
                attempts = int(row["notification_attempts"]) + 1
                delay = min(
                    NOTIFICATION_MAX_BACKOFF_SEC, 30 * (2 ** min(attempts - 1, 5))
                )
                with repository.connection() as conn, conn.transaction():
                    conn.execute(
                        """UPDATE monitor_events SET notification_attempts=%s,
                           next_notification_at=%s, notification_last_error=%s
                           WHERE event_id=%s""",
                        (
                            attempts,
                            datetime.now(timezone.utc) + timedelta(seconds=delay),
                            f"{type(exc).__name__}",
                            row["event_id"],
                        ),
                    )
                logger.warning(
                    "監視Webhook送信失敗: event_id=%s retry_in=%ss",
                    row["event_id"],
                    delay,
                )


class DeviceMonitor:
    STATEFUL_CODES = {"DATA-MISSING", "DEVICE-STALE"}

    def __init__(
        self, repository: PostgresRepository, notifier: WebhookNotifier | None = None
    ):
        self.repository = repository
        self.notifier = notifier or WebhookNotifier()
        self.last_checked_id = 0
        self.last_seq: dict[UUID, int] = {}
        self.missing_state: dict[UUID, tuple[str, ...]] = {}
        self.stale_since: dict[UUID, datetime] = {}
        self.last_stale_log: dict[UUID, float] = {}

    def initialize(self) -> None:
        with self.repository.connection() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS id FROM sensor_readings"
            ).fetchone()
            self.last_checked_id = int(row["id"])
            for item in conn.execute(
                """SELECT r.device_id, MAX(r.device_seq) AS seq
                   FROM sensor_readings r JOIN devices d ON d.device_id=r.device_id
                   WHERE r.device_seq IS NOT NULL AND d.monitoring_enabled=TRUE
                   GROUP BY r.device_id"""
            ).fetchall():
                self.last_seq[item["device_id"]] = int(item["seq"])
            states = conn.execute(
                """SELECT DISTINCT ON (e.device_id,e.event_code)
                          e.device_id,e.event_code,e.state,e.detail,e.occurred_at
                   FROM monitor_events e JOIN devices d ON d.device_id=e.device_id
                   WHERE d.monitoring_enabled=TRUE
                     AND e.event_code IN ('DATA-MISSING','DEVICE-STALE')
                   ORDER BY e.device_id,e.event_code,e.event_id DESC"""
            ).fetchall()
            for item in states:
                if item["state"] != "open":
                    continue
                if item["event_code"] == "DEVICE-STALE":
                    self.stale_since[item["device_id"]] = item["occurred_at"]
                    self.last_stale_log[item["device_id"]] = time.monotonic()
                else:
                    detail = item["detail"]
                    if isinstance(detail, str):
                        detail = json.loads(detail)
                    self.missing_state[item["device_id"]] = tuple(
                        sorted(detail.get("sensors", []))
                    )

    def _record_event(self, conn, *, device_id, code, severity, state, detail) -> bool:
        if code in self.STATEFUL_CODES:
            previous = conn.execute(
                """SELECT state FROM monitor_events WHERE device_id=%s AND event_code=%s
                   ORDER BY event_id DESC LIMIT 1""",
                (device_id, code),
            ).fetchone()
            if previous is not None and previous["state"] == state:
                return False
        conn.execute(
            """INSERT INTO monitor_events(
                   device_id,event_code,severity,state,detail,notification_required
               ) VALUES (%s,%s,%s,%s,%s,%s)""",
            (
                device_id,
                code,
                severity,
                state,
                json.dumps(detail),
                self.notifier.should_notify(severity, state),
            ),
        )
        return True

    def check_new_rows(self) -> None:
        with self.repository.connection() as conn, conn.transaction():
            rows = conn.execute(
                """SELECT r.*, d.display_name
                   FROM sensor_readings r JOIN devices d ON d.device_id=r.device_id
                   WHERE r.id>%s AND d.monitoring_enabled=TRUE ORDER BY r.id""",
                (self.last_checked_id,),
            ).fetchall()
            capabilities: dict[UUID, set[str]] = {}
            for item in conn.execute(
                """SELECT c.device_id,c.sensor_code FROM device_capabilities c
                   JOIN devices d ON d.device_id=c.device_id
                   WHERE c.enabled=TRUE AND c.required=TRUE AND d.monitoring_enabled=TRUE"""
            ).fetchall():
                capabilities.setdefault(item["device_id"], set()).add(
                    item["sensor_code"]
                )
            for row in rows:
                self.last_checked_id = max(self.last_checked_id, int(row["id"]))
                device_id = row["device_id"]
                missing = tuple(
                    sorted(
                        name
                        for name in capabilities.get(device_id, set())
                        if row.get(name) is None
                    )
                )
                previous_missing = self.missing_state.get(device_id, ())
                if missing and missing != previous_missing:
                    if self._record_event(
                        conn,
                        device_id=device_id,
                        code="DATA-MISSING",
                        severity="warning",
                        state="open",
                        detail={
                            "message_id": str(row["message_id"]),
                            "sensors": list(missing),
                        },
                    ):
                        logger.warning(
                            "必須センサー欠損: device=%s sensors=%s",
                            row["display_name"],
                            ",".join(missing),
                        )
                elif not missing and previous_missing:
                    self._record_event(
                        conn,
                        device_id=device_id,
                        code="DATA-MISSING",
                        severity="info",
                        state="recovered",
                        detail={
                            "message_id": str(row["message_id"]),
                            "sensors": list(previous_missing),
                        },
                    )
                    logger.info(
                        "必須センサー欠損から復帰: device=%s", row["display_name"]
                    )
                self.missing_state[device_id] = missing
                seq = row.get("device_seq")
                if seq is not None:
                    previous = self.last_seq.get(device_id)
                    if previous is not None and seq > previous + 1:
                        self._record_event(
                            conn,
                            device_id=device_id,
                            code="SEQ-GAP",
                            severity="warning",
                            state="open",
                            detail={"previous": previous, "current": seq},
                        )
                        logger.warning(
                            "device_seq欠番: device=%s previous=%s current=%s",
                            device_id,
                            previous,
                            seq,
                        )
                    elif previous is not None and seq <= previous:
                        self._record_event(
                            conn,
                            device_id=device_id,
                            code="OUT-OF-ORDER",
                            severity="info",
                            state="open",
                            detail={"previous": previous, "current": seq},
                        )
                    self.last_seq[device_id] = max(previous or 0, int(seq))

    def check_stale_devices(self) -> None:
        now = datetime.now(timezone.utc)
        with self.repository.connection() as conn, conn.transaction():
            devices = conn.execute(
                """SELECT d.device_id,d.display_name,MAX(r.received_at) AS last_seen
                   FROM devices d LEFT JOIN sensor_readings r ON r.device_id=d.device_id
                   WHERE d.status='active' AND d.monitoring_enabled=TRUE
                   GROUP BY d.device_id,d.display_name"""
            ).fetchall()
            for device in devices:
                device_id, last_seen = device["device_id"], device["last_seen"]
                age = None if last_seen is None else (now - last_seen).total_seconds()
                stale = age is None or age > STALE_THRESHOLD_SEC
                if stale and device_id not in self.stale_since:
                    self.stale_since[device_id] = now
                    self.last_stale_log[device_id] = time.monotonic()
                    self._record_event(
                        conn,
                        device_id=device_id,
                        code="DEVICE-STALE",
                        severity="error",
                        state="open",
                        detail={"age_sec": age, "threshold_sec": STALE_THRESHOLD_SEC},
                    )
                    logger.error(
                        "無通信検知: device=%s age=%s",
                        device["display_name"],
                        "unknown" if age is None else int(age),
                    )
                elif (
                    stale
                    and time.monotonic() - self.last_stale_log.get(device_id, 0)
                    >= STALE_REPEAT_LOG_SEC
                ):
                    self.last_stale_log[device_id] = time.monotonic()
                    logger.warning(
                        "無通信継続（通知抑制中）: device=%s", device["display_name"]
                    )
                elif not stale and device_id in self.stale_since:
                    self._record_event(
                        conn,
                        device_id=device_id,
                        code="DEVICE-STALE",
                        severity="info",
                        state="recovered",
                        detail={"last_seen": last_seen.isoformat()},
                    )
                    logger.info("無通信から復帰: device=%s", device["display_name"])
                    self.stale_since.pop(device_id, None)
                    self.last_stale_log.pop(device_id, None)

    def run(self) -> None:
        self.initialize()
        logger.info(
            "IoT環境監視 Ver.%s / schema=%d / webhook=%s",
            SYSTEM_VERSION,
            SCHEMA_VERSION,
            "enabled" if self.notifier.url else "disabled",
        )
        while True:
            try:
                self.check_new_rows()
                self.check_stale_devices()
                self.notifier.dispatch_pending(self.repository)
            except Exception:
                logger.exception("Ver3監視サイクル失敗")
            time.sleep(POLL_INTERVAL_SEC)


def main() -> int:
    try:
        repository = PostgresRepository()
        repository.open()
    except DatabaseError as exc:
        raise SystemExit(str(exc)) from None
    try:
        DeviceMonitor(repository).run()
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
