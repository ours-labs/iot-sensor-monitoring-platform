"""PostgreSQL接続、スキーマ境界、認証、冪等INSERTを集約する。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from config_errors import require_postgres_url, require_token_hash_key
from protocol_v3 import Ack, SensorEnvelope, canonical_payload_hash
from protocol_v3 import SENSOR_COLUMNS
from system_identity import PROTOCOL_VERSION, SCHEMA_VERSION


logger = logging.getLogger(__name__)
DISPLAY_NAME_MIN_LENGTH = 1
DISPLAY_NAME_MAX_LENGTH = 80


class DatabaseError(RuntimeError):
    pass


class SchemaBoundaryError(DatabaseError):
    pass


def token_hash(token: str, key: str | None = None) -> str:
    """高エントロピーtokenを環境固有鍵によるHMACとして保存する。"""
    secret = (key or require_token_hash_key()).encode("utf-8")
    return hmac.digest(secret, token.encode("utf-8"), "sha256").hex()


def _import_psycopg():
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as exc:
        raise DatabaseError(
            "[DB-E001] PostgreSQLドライバがありません。requirements.txtを導入してください"
        ) from exc
    return psycopg, dict_row, ConnectionPool


class PostgresRepository:
    def __init__(self, database_url: str | None = None, *, open_pool: bool = False):
        _, dict_row, pool_class = _import_psycopg()
        self.database_url = database_url or require_postgres_url()
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            raise DatabaseError("[DB-E002] PostgreSQL以外の接続先は使用できません")
        psycopg_url = self.database_url.replace(
            "postgresql+psycopg://", "postgresql://", 1
        )
        self.pool = pool_class(
            conninfo=psycopg_url,
            min_size=1,
            max_size=10,
            timeout=5,
            kwargs={"row_factory": dict_row, "autocommit": False},
            open=open_pool,
        )

    def open(self) -> None:
        self.pool.open(wait=True)
        self.ensure_schema_version()

    def close(self) -> None:
        self.pool.close()

    @contextmanager
    def connection(self) -> Iterator[Any]:
        with self.pool.connection() as conn:
            yield conn

    def ensure_schema_version(self) -> None:
        try:
            with self.connection() as conn:
                row = conn.execute(
                    "SELECT value FROM schema_metadata WHERE key = 'schema_version'"
                ).fetchone()
        except Exception as exc:
            raise SchemaBoundaryError(
                "[DB-E003] Ver3スキーマを確認できません。migration 001を適用してください"
            ) from exc
        if row is None or str(row["value"]) != str(SCHEMA_VERSION):
            actual = None if row is None else row["value"]
            raise SchemaBoundaryError(
                f"[DB-E004] DBスキーマ版数が不一致です: expected={SCHEMA_VERSION}, actual={actual}"
            )

    def register_device(
        self,
        *,
        device_id: UUID,
        display_name: str,
        raw_token: str,
        capabilities: list[dict[str, Any]],
    ) -> None:
        if not DISPLAY_NAME_MIN_LENGTH <= len(display_name) <= DISPLAY_NAME_MAX_LENGTH:
            raise ValueError("display_nameは1〜80文字です")
        with self.connection() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO devices(device_id, display_name)
                VALUES (%s, %s)
                """,
                (device_id, display_name),
            )
            conn.execute(
                """
                INSERT INTO api_credentials(
                    credential_id, credential_type, device_id, credential_hash
                ) VALUES (%s, 'device', %s, %s)
                """,
                (uuid4(), device_id, token_hash(raw_token)),
            )
            for item in capabilities:
                conn.execute(
                    """
                    INSERT INTO device_capabilities(
                        device_id, sensor_code, enabled, required, unit,
                        expected_interval_sec, plausible_min, plausible_max,
                        capability_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1)
                    """,
                    (
                        device_id,
                        item["sensor_code"],
                        bool(item.get("enabled", True)),
                        bool(item.get("required", True)),
                        item.get("unit", ""),
                        int(item.get("expected_interval_sec", 10)),
                        item.get("plausible_min"),
                        item.get("plausible_max"),
                    ),
                )

    def _authenticate_device(self, conn: Any, envelope: SensorEnvelope) -> bool:
        row = conn.execute(
            """
            SELECT d.status
            FROM devices d
            JOIN api_credentials c ON c.device_id = d.device_id
            WHERE d.device_id = %s
              AND c.credential_type = 'device'
              AND c.credential_hash = %s
              AND c.revoked_at IS NULL
            """,
            (envelope.device_id, token_hash(envelope.device_token)),
        ).fetchone()
        return row is not None and row["status"] == "active"

    def _unsupported_sensor_values(
        self, conn: Any, envelope: SensorEnvelope
    ) -> list[str]:
        enabled = {
            row["sensor_code"]
            for row in conn.execute(
                """
                SELECT sensor_code FROM device_capabilities
                WHERE device_id = %s AND enabled = TRUE
                """,
                (envelope.device_id,),
            ).fetchall()
        }
        return sorted(
            name
            for name, value in envelope.sensors.items()
            if value is not None and name not in enabled
        )

    def identity_for_email(self, email: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT principal_email, operator_id, display_name
                FROM operator_identities
                WHERE principal_email = %s AND active = TRUE
                """,
                (email.strip().lower(),),
            ).fetchone()
        return None if row is None else dict(row)

    def authenticate_user_token(self, raw_token: str) -> dict[str, Any] | None:
        if not raw_token:
            return None
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT o.principal_email, o.operator_id, o.display_name,
                       c.credential_type
                FROM api_credentials c
                JOIN operator_identities o
                  ON o.principal_email = c.principal_email
                WHERE c.credential_hash = %s
                  AND c.credential_type IN ('browser', 'android')
                  AND c.revoked_at IS NULL AND o.active = TRUE
                """,
                (token_hash(raw_token),),
            ).fetchone()
        return None if row is None else dict(row)

    def issue_user_credential(
        self, email: str, credential_type: str
    ) -> tuple[dict[str, Any], str]:
        if credential_type not in {"browser", "android"}:
            raise ValueError("credential_typeが不正です")
        import secrets

        identity = self.identity_for_email(email)
        if identity is None:
            raise LookupError("登録済み利用者ではありません")
        raw_token = secrets.token_urlsafe(32)
        with self.connection() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO api_credentials(
                    credential_id, credential_type, principal_email,
                    operator_id, credential_hash
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    uuid4(),
                    credential_type,
                    identity["principal_email"],
                    identity["operator_id"],
                    token_hash(raw_token),
                ),
            )
        return identity, raw_token

    def list_devices(self, *, include_retired: bool = False) -> list[dict[str, Any]]:
        where = "" if include_retired else "WHERE status = 'active'"
        with self.connection() as conn:
            return list(
                conn.execute(
                    f"""
                    SELECT device_id, display_name, status, capability_version,
                           created_at, updated_at
                    FROM devices {where}
                    ORDER BY display_name, device_id
                    """
                ).fetchall()
            )

    def list_controllable_devices(
        self, *, online_within_sec: int = 30
    ) -> list[dict[str, Any]]:
        threshold = datetime.now(timezone.utc) - timedelta(
            seconds=max(10, online_within_sec)
        )
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT device_id, display_name, status, capability_version,
                       control_enabled, last_control_poll_at,
                       (last_control_poll_at IS NOT NULL AND last_control_poll_at >= %s)
                           AS control_online
                FROM devices
                WHERE status='active' AND control_enabled=TRUE
                ORDER BY display_name, device_id
                """,
                (threshold,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_device_display_name(
        self, *, device_id: UUID, display_name: str, principal_email: str
    ) -> dict[str, Any] | None:
        name = display_name.strip()
        if not DISPLAY_NAME_MIN_LENGTH <= len(name) <= DISPLAY_NAME_MAX_LENGTH:
            raise ValueError("display_nameは1〜80文字です")
        with self.connection() as conn, conn.transaction():
            current = conn.execute(
                """
                SELECT display_name FROM devices
                WHERE device_id=%s AND status='active'
                FOR UPDATE
                """,
                (device_id,),
            ).fetchone()
            if current is None:
                return None
            updated = conn.execute(
                """
                UPDATE devices SET display_name=%s, updated_at=CURRENT_TIMESTAMP
                WHERE device_id=%s
                RETURNING device_id, display_name, status, capability_version,
                          created_at, updated_at
                """,
                (name, device_id),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO device_admin_audit(
                    principal_email, device_id, action, before_state, after_state
                ) VALUES (%s, %s, 'display_name_updated', %s, %s)
                """,
                (
                    principal_email.lower(),
                    device_id,
                    json.dumps({"display_name": current["display_name"]}),
                    json.dumps({"display_name": name}),
                ),
            )
        return dict(updated)

    def authenticate_device_token(self, device_id: UUID, raw_token: str) -> bool:
        if not raw_token:
            return False
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT d.status FROM devices d
                JOIN api_credentials c ON c.device_id = d.device_id
                WHERE d.device_id=%s AND c.credential_type='device'
                  AND c.credential_hash=%s AND c.revoked_at IS NULL
                """,
                (device_id, token_hash(raw_token)),
            ).fetchone()
        return row is not None and row["status"] == "active"

    def record_security_event(
        self, *, event_code: str, severity: str, detail: dict[str, Any]
    ) -> None:
        with self.connection() as conn, conn.transaction():
            conn.execute(
                """
                INSERT INTO monitor_events(event_code, severity, state, detail)
                VALUES (%s, %s, 'open', %s)
                """,
                (event_code, severity, json.dumps(detail)),
            )

    def list_monitor_events(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT e.event_id,e.event_code,e.severity,e.state,e.detail,e.occurred_at,
                       e.notification_required,e.notification_attempts,e.notified_at,
                       d.device_id,d.display_name
                FROM monitor_events e
                LEFT JOIN devices d ON d.device_id=e.device_id
                ORDER BY e.event_id DESC LIMIT %s
                """,
                (max(1, min(limit, 200)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def queue_control_command(
        self,
        *,
        request_id: UUID,
        device_id: UUID,
        command: str,
        requested_by: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        if command not in {"status", "restart", "shutdown"}:
            raise ValueError("未対応の制御コマンドです")
        with self.connection() as conn, conn.transaction():
            target = conn.execute(
                """SELECT control_enabled,last_control_poll_at FROM devices
                   WHERE device_id=%s AND status='active' FOR SHARE""",
                (device_id,),
            ).fetchone()
            if target is None or not target["control_enabled"]:
                raise LookupError("CTL-E002")
            online_after = datetime.now(timezone.utc) - timedelta(seconds=30)
            if (
                target["last_control_poll_at"] is None
                or target["last_control_poll_at"] < online_after
            ):
                raise LookupError("CTL-E003")
            row = conn.execute(
                """
                INSERT INTO control_commands(
                    request_id, target_device_id, command, status,
                    requested_by, expires_at
                ) VALUES (%s, %s, %s, 'queued', %s, %s)
                ON CONFLICT (request_id) DO UPDATE SET
                    updated_at = control_commands.updated_at
                RETURNING request_id, target_device_id, command, status,
                          requested_by, requested_at, expires_at, updated_at
                """,
                (request_id, device_id, command, requested_by.lower(), expires_at),
            ).fetchone()
        return dict(row)

    def list_control_commands(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT c.request_id, c.target_device_id, d.display_name,
                       c.command, c.status, c.requested_by, c.requested_at,
                       c.expires_at, c.updated_at
                FROM control_commands c
                JOIN devices d ON d.device_id = c.target_device_id
                ORDER BY c.requested_at DESC LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def poll_control_command(
        self, *, device_id: UUID, raw_token: str
    ) -> dict[str, Any] | None:
        if not self.authenticate_device_token(device_id, raw_token):
            raise PermissionError("AUTH-E002")
        now = datetime.now(timezone.utc)
        with self.connection() as conn, conn.transaction():
            enabled = conn.execute(
                """UPDATE devices SET last_control_poll_at=%s, updated_at=CURRENT_TIMESTAMP
                   WHERE device_id=%s AND status='active' AND control_enabled=TRUE
                   RETURNING device_id""",
                (now, device_id),
            ).fetchone()
            if enabled is None:
                raise PermissionError("CTL-E002")
            conn.execute(
                """
                UPDATE control_commands SET status='outcome_unknown',
                    updated_at=CURRENT_TIMESTAMP
                WHERE target_device_id=%s AND status IN ('queued', 'accepted') AND expires_at <= %s
                """,
                (device_id, now),
            )
            row = conn.execute(
                """
                SELECT request_id, target_device_id, command, status, expires_at
                FROM control_commands
                WHERE target_device_id=%s AND status IN ('queued', 'accepted') AND expires_at > %s
                ORDER BY requested_at FOR UPDATE SKIP LOCKED LIMIT 1
                """,
                (device_id, now),
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "queued":
                conn.execute(
                    """
                    UPDATE control_commands SET status='accepted', updated_at=CURRENT_TIMESTAMP
                    WHERE request_id=%s
                    """,
                    (row["request_id"],),
                )
                conn.execute(
                    """
                    INSERT INTO control_events(request_id, event_type, occurred_at, detail)
                    VALUES (%s, 'accepted', %s, '{}'::jsonb)
                    """,
                    (row["request_id"], now),
                )
        result = dict(row)
        result["status"] = "accepted"
        return result

    def record_control_event(
        self,
        *,
        device_id: UUID,
        raw_token: str,
        request_id: UUID,
        client_event_id: UUID,
        event_type: str,
        boot_id: UUID | None,
        occurred_at: datetime,
        detail: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.authenticate_device_token(device_id, raw_token):
            raise PermissionError("AUTH-E002")
        status_map = {
            "status_report": "completed",
            "completed": "completed",
            "shutdown_started": "shutting_down",
            "boot": "completed",
            "rejected": "rejected",
            "outcome_unknown": "outcome_unknown",
        }
        if event_type not in status_map:
            raise ValueError("DATA-E003")
        with self.connection() as conn, conn.transaction():
            current = conn.execute(
                """
                SELECT target_device_id, status FROM control_commands
                WHERE request_id=%s FOR UPDATE
                """,
                (request_id,),
            ).fetchone()
            if current is None or current["target_device_id"] != device_id:
                raise LookupError("DATA-E003")
            conn.execute(
                """
                INSERT INTO control_events(
                    request_id, client_event_id, event_type, boot_id, occurred_at, detail
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (client_event_id) DO NOTHING
                """,
                (
                    request_id,
                    client_event_id,
                    event_type,
                    boot_id,
                    occurred_at,
                    json.dumps(detail),
                ),
            )
            updated = conn.execute(
                """
                UPDATE control_commands SET status=%s, updated_at=CURRENT_TIMESTAMP
                WHERE request_id=%s
                RETURNING request_id, target_device_id, command, status,
                          requested_by, requested_at, expires_at, updated_at
                """,
                (status_map[event_type], request_id),
            ).fetchone()
        return dict(updated)

    def insert_sensor(self, envelope: SensorEnvelope) -> Ack:
        received_at = datetime.now(timezone.utc)
        payload_hash = canonical_payload_hash(envelope)
        with self.connection() as conn, conn.transaction():
            if not self._authenticate_device(conn, envelope):
                return Ack(
                    protocol_version=PROTOCOL_VERSION,
                    message_id=str(envelope.message_id),
                    status="rejected",
                    device_id=str(envelope.device_id),
                    received_at=received_at.isoformat(),
                    error_code="AUTH-E002",
                    detail="デバイス資格情報が無効です",
                )
            unsupported = self._unsupported_sensor_values(conn, envelope)
            if unsupported:
                return Ack(
                    protocol_version=PROTOCOL_VERSION,
                    message_id=str(envelope.message_id),
                    status="rejected",
                    device_id=str(envelope.device_id),
                    received_at=received_at.isoformat(),
                    error_code="CAP-E001",
                    detail="非搭載または無効なセンサー値です: "
                    + ", ".join(unsupported),
                )

            inserted = conn.execute(
                """
                INSERT INTO ingest_messages(
                    message_id, device_id, payload_hash, status, received_at
                ) VALUES (%s, %s, %s, 'processing', %s)
                ON CONFLICT (message_id) DO NOTHING
                RETURNING message_id
                """,
                (envelope.message_id, envelope.device_id, payload_hash, received_at),
            ).fetchone()

            if inserted is None:
                existing = conn.execute(
                    """
                    SELECT device_id, payload_hash, status, received_at, stored_at, error_code
                    FROM ingest_messages WHERE message_id = %s
                    """,
                    (envelope.message_id,),
                ).fetchone()
                if existing is None:
                    raise DatabaseError("[DB-E005] 重複判定行を取得できません")
                if (
                    existing["payload_hash"] != payload_hash
                    or existing["device_id"] != envelope.device_id
                ):
                    return Ack(
                        protocol_version=PROTOCOL_VERSION,
                        message_id=str(envelope.message_id),
                        status="rejected",
                        device_id=str(envelope.device_id),
                        received_at=received_at.isoformat(),
                        error_code="MSG-E002",
                        detail="同じmessage_idに異なる内容が指定されています",
                    )
                if existing["status"] == "inserted":
                    return Ack(
                        protocol_version=PROTOCOL_VERSION,
                        message_id=str(envelope.message_id),
                        status="duplicate",
                        device_id=str(envelope.device_id),
                        received_at=existing["received_at"].isoformat(),
                        stored_at=existing["stored_at"].isoformat(),
                    )
                if existing["status"] == "rejected":
                    return Ack(
                        protocol_version=PROTOCOL_VERSION,
                        message_id=str(envelope.message_id),
                        status="rejected",
                        device_id=str(envelope.device_id),
                        received_at=existing["received_at"].isoformat(),
                        stored_at=(
                            existing["stored_at"].isoformat()
                            if existing["stored_at"]
                            else None
                        ),
                        error_code=existing["error_code"] or "MSG-E013",
                        detail="以前の確定拒否結果を再送します",
                    )
                return Ack(
                    protocol_version=PROTOCOL_VERSION,
                    message_id=str(envelope.message_id),
                    status="retry",
                    device_id=str(envelope.device_id),
                    received_at=received_at.isoformat(),
                    error_code=existing["error_code"] or "DB-E006",
                    detail="同じ要求を処理中です",
                )

            stored_at = datetime.now(timezone.utc)
            values = envelope.sensors
            psycopg, _, _ = _import_psycopg()
            try:
                # savepointを使い、device_seq競合だけを外側transaction内で確定拒否する。
                with conn.transaction():
                    conn.execute(
                        """
                        INSERT INTO sensor_readings(
                            message_id, device_id, device_seq, measured_at, received_at,
                            stored_at, source_type, trigger,
                            light_raw, light_voltage, sound_raw, joystick_x, joystick_y,
                            potentiometer_percent, temp, hum, pressure, co2
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, 'sensor', %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                        )
                        """,
                        (
                            envelope.message_id,
                            envelope.device_id,
                            envelope.device_seq,
                            envelope.measured_at,
                            received_at,
                            stored_at,
                            envelope.trigger,
                            values["light_raw"],
                            values["light_voltage"],
                            values["sound_raw"],
                            values["joystick_x"],
                            values["joystick_y"],
                            values["potentiometer_percent"],
                            values["temp"],
                            values["hum"],
                            values["pressure"],
                            values["co2"],
                        ),
                    )
            except psycopg.errors.UniqueViolation:
                conn.execute(
                    """
                    UPDATE ingest_messages
                    SET status='rejected', stored_at=%s, error_code='MSG-E013'
                    WHERE message_id=%s
                    """,
                    (stored_at, envelope.message_id),
                )
                return Ack(
                    protocol_version=PROTOCOL_VERSION,
                    message_id=str(envelope.message_id),
                    status="rejected",
                    device_id=str(envelope.device_id),
                    received_at=received_at.isoformat(),
                    stored_at=stored_at.isoformat(),
                    error_code="MSG-E013",
                    detail="同じdevice_idとdevice_seqが別要求で保存済みです",
                )
            conn.execute(
                """
                UPDATE ingest_messages
                SET status = 'inserted', stored_at = %s
                WHERE message_id = %s
                """,
                (stored_at, envelope.message_id),
            )

        return Ack(
            protocol_version=PROTOCOL_VERSION,
            message_id=str(envelope.message_id),
            status="inserted",
            device_id=str(envelope.device_id),
            received_at=received_at.isoformat(),
            stored_at=stored_at.isoformat(),
        )

    def fetch_readings(
        self,
        *,
        device_id: UUID | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if device_id is not None:
            clauses.append("r.device_id = %s")
            params.append(device_id)
        if start_at is not None:
            clauses.append("r.measured_at >= %s")
            params.append(start_at)
        if end_at is not None:
            clauses.append("r.measured_at <= %s")
            params.append(end_at)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, min(limit, 5000)))
        query = f"""
            SELECT r.*, d.display_name
            FROM sensor_readings r
            JOIN devices d ON d.device_id = r.device_id
            {where}
            ORDER BY r.measured_at DESC, r.id DESC LIMIT %s
        """
        with self.connection() as conn:
            return list(conn.execute(query, params).fetchall())

    def averages(
        self,
        *,
        sensors: list[str],
        device_id: UUID | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        include_manual: bool = False,
    ) -> list[dict[str, Any]]:
        safe = [name for name in sensors if name in SENSOR_COLUMNS]
        if not safe:
            return []
        clauses = [
            "source_type IN ('sensor', 'legacy', 'manual')"
            if include_manual
            else "source_type IN ('sensor', 'legacy')"
        ]
        params: list[Any] = []
        if device_id is not None:
            clauses.append("device_id = %s")
            params.append(device_id)
        if start_at is not None:
            clauses.append("measured_at >= %s")
            params.append(start_at)
        if end_at is not None:
            clauses.append("measured_at <= %s")
            params.append(end_at)
        expressions = []
        for name in safe:
            expressions.extend(
                [f'AVG("{name}") AS "{name}_avg"', f'COUNT("{name}") AS "{name}_count"']
            )
        query = (
            "SELECT "
            + ", ".join(expressions)
            + " FROM sensor_readings WHERE "
            + " AND ".join(clauses)
        )
        with self.connection() as conn:
            row = conn.execute(query, params).fetchone()
        results = []
        for name in safe:
            average = row[f"{name}_avg"]
            results.append(
                {
                    "sensor": name,
                    "average": None if average is None else round(float(average), 3),
                    "count": int(row[f"{name}_count"]),
                }
            )
        return results

    def insert_manual(
        self,
        *,
        message_id: UUID,
        target_device_id: UUID,
        principal_email: str,
        operator_id: str,
        measured_at: datetime,
        values: dict[str, float | int | None],
        warning_confirmed: bool,
        warning_reasons: list[str],
    ) -> Ack:
        received_at = datetime.now(timezone.utc)
        canonical = {
            "message_id": str(message_id),
            "target_device_id": str(target_device_id),
            "principal_email": principal_email.lower(),
            "operator_id": operator_id.upper(),
            "measured_at": measured_at.astimezone(timezone.utc).isoformat(),
            "values": {name: values.get(name) for name in SENSOR_COLUMNS},
        }
        payload_hash = hashlib.sha256(
            json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        with self.connection() as conn, conn.transaction():
            identity = conn.execute(
                """
                SELECT 1 FROM operator_identities
                WHERE principal_email = %s AND operator_id = %s AND active = TRUE
                """,
                (principal_email.lower(), operator_id.upper()),
            ).fetchone()
            device = conn.execute(
                "SELECT 1 FROM devices WHERE device_id = %s AND status = 'active'",
                (target_device_id,),
            ).fetchone()
            if identity is None or device is None:
                return Ack(
                    PROTOCOL_VERSION,
                    str(message_id),
                    "rejected",
                    device_id=str(target_device_id),
                    received_at=received_at.isoformat(),
                    error_code="AUTH-E003",
                    detail="利用者または対象Piが無効です",
                )
            inserted = conn.execute(
                """
                INSERT INTO ingest_messages(
                    message_id, device_id, payload_hash, status, received_at
                ) VALUES (%s, %s, %s, 'processing', %s)
                ON CONFLICT (message_id) DO NOTHING RETURNING message_id
                """,
                (message_id, target_device_id, payload_hash, received_at),
            ).fetchone()
            if inserted is None:
                existing = conn.execute(
                    "SELECT payload_hash, received_at, stored_at, status FROM ingest_messages WHERE message_id = %s",
                    (message_id,),
                ).fetchone()
                if existing["payload_hash"] != payload_hash:
                    return Ack(
                        PROTOCOL_VERSION,
                        str(message_id),
                        "rejected",
                        device_id=str(target_device_id),
                        received_at=received_at.isoformat(),
                        error_code="MSG-E002",
                        detail="同じmessage_idに異なる内容があります",
                    )
                status = "duplicate" if existing["status"] == "inserted" else "retry"
                return Ack(
                    PROTOCOL_VERSION,
                    str(message_id),
                    status,
                    device_id=str(target_device_id),
                    received_at=existing["received_at"].isoformat(),
                    stored_at=(
                        existing["stored_at"].isoformat()
                        if existing["stored_at"]
                        else None
                    ),
                    error_code=None if status == "duplicate" else "DB-E006",
                )
            stored_at = datetime.now(timezone.utc)
            columns = ", ".join(SENSOR_COLUMNS)
            placeholders = ", ".join(["%s"] * len(SENSOR_COLUMNS))
            conn.execute(
                f"""
                INSERT INTO sensor_readings(
                    message_id, device_id, device_seq, measured_at, received_at,
                    stored_at, source_type, trigger, quality_state, quality_detail,
                    {columns}
                ) VALUES (
                    %s, %s, NULL, %s, %s, %s, 'manual', 'manual', %s, %s,
                    {placeholders}
                )
                """,
                (
                    message_id,
                    target_device_id,
                    measured_at,
                    received_at,
                    stored_at,
                    "warning" if warning_reasons else "valid",
                    json.dumps({"warnings": warning_reasons}),
                    *(values.get(name) for name in SENSOR_COLUMNS),
                ),
            )
            conn.execute(
                """
                INSERT INTO manual_input_audit(
                    message_id, principal_email, operator_id,
                    target_device_id, warning_confirmed
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    message_id,
                    principal_email.lower(),
                    operator_id.upper(),
                    target_device_id,
                    warning_confirmed,
                ),
            )
            conn.execute(
                "UPDATE ingest_messages SET status = 'inserted', stored_at = %s WHERE message_id = %s",
                (stored_at, message_id),
            )
        return Ack(
            PROTOCOL_VERSION,
            str(message_id),
            "inserted",
            device_id=str(target_device_id),
            received_at=received_at.isoformat(),
            stored_at=stored_at.isoformat(),
        )


def apply_migrations(database_url: str | None = None) -> list[str]:
    """未適用SQLをファイル名順に適用し、チェックサム付きで記録する。"""
    psycopg, dict_row, _ = _import_psycopg()
    url = (database_url or require_postgres_url()).replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    migration_dir = Path(__file__).with_name("migrations")
    files = sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql"))
    applied_now: list[str] = []
    with psycopg.connect(url, autocommit=True, row_factory=dict_row) as conn:
        conn.execute("SELECT pg_advisory_lock(3000001)")
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations(
                    migration_name TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
            """)
            existing_schema = conn.execute(
                "SELECT to_regclass('public.devices') IS NOT NULL AS present"
            ).fetchone()["present"]
            recorded = {
                row["migration_name"]: row["sha256"]
                for row in conn.execute(
                    "SELECT migration_name, sha256 FROM schema_migrations"
                ).fetchall()
            }
            for migration in files:
                sql = migration.read_text(encoding="utf-8")
                digest = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                previous = recorded.get(migration.name)
                if previous:
                    if previous != digest:
                        raise SchemaBoundaryError(
                            f"適用済みmigrationが変更されています: {migration.name}"
                        )
                    continue
                if migration.name.startswith("001_") and existing_schema:
                    logger.info("既存Ver3スキーマを001適用済みとして登録します")
                else:
                    conn.execute(sql)
                    applied_now.append(migration.name)
                conn.execute(
                    "INSERT INTO schema_migrations(migration_name,sha256) VALUES(%s,%s)",
                    (migration.name, digest),
                )
                existing_schema = True
        finally:
            conn.execute("SELECT pg_advisory_unlock(3000001)")
    return applied_now


def apply_initial_migration(database_url: str | None = None) -> None:
    """後方互換ラッパー。現在は全未適用migrationを実行する。"""
    apply_migrations(database_url)
