"""Ver3 JSON保存要求とACKの検証・正規化。"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from system_identity import PROTOCOL_VERSION

SENSOR_COLUMNS = (
    "light_raw",
    "light_voltage",
    "sound_raw",
    "joystick_x",
    "joystick_y",
    "potentiometer_percent",
    "temp",
    "hum",
    "pressure",
    "co2",
)
MIN_DEVICE_TOKEN_LENGTH = 24

PHYSICAL_RANGES = {
    "light_raw": (0.0, 4095.0),
    "light_voltage": (0.0, 3.3),
    "sound_raw": (0.0, 4095.0),
    "joystick_x": (-1.0, 1.0),
    "joystick_y": (-1.0, 1.0),
    "potentiometer_percent": (0.0, 100.0),
    "temp": (-40.0, 80.0),
    "hum": (0.0, 100.0),
    "pressure": (300.0, 1100.0),
    "co2": (0.0, 10000.0),
}


class ProtocolError(ValueError):
    def __init__(self, code: str, detail: str, *, message_id: str | None = None):
        self.code = code
        self.detail = detail
        self.message_id = message_id
        super().__init__(f"[{code}] {detail}")


@dataclass(frozen=True)
class SensorEnvelope:
    protocol_version: int
    message_id: UUID
    device_id: UUID
    device_token: str
    device_seq: int
    measured_at: datetime
    trigger: str
    sensors: dict[str, float | int | None]


@dataclass(frozen=True)
class Ack:
    protocol_version: int
    message_id: str | None
    status: str
    device_id: str | None = None
    received_at: str | None = None
    stored_at: str | None = None
    error_code: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_uuid(value: Any, field: str, code: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ProtocolError(code, f"{field}はUUIDで指定してください") from exc


def parse_measured_at(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ProtocolError(
            "MSG-E006", "measured_atはタイムゾーン付きISO 8601文字列が必要です"
        )
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError("MSG-E006", "measured_atの形式が不正です") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProtocolError("MSG-E007", "measured_atにはタイムゾーンが必要です")
    return parsed.astimezone(timezone.utc)


def finite_sensor_value(
    name: str,
    value: Any,
    *,
    message_id: str | None = None,
) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(
            "VAL-E001",
            f"{name}は数値またはnullで指定してください",
            message_id=message_id,
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ProtocolError(
            "VAL-E002",
            f"{name}は有限の数値で指定してください",
            message_id=message_id,
        )
    lower, upper = PHYSICAL_RANGES[name]
    if numeric < lower or numeric > upper:
        raise ProtocolError(
            "VAL-E003",
            f"{name}が物理範囲外です（{lower}〜{upper}）",
            message_id=message_id,
        )
    if name in {"light_raw", "sound_raw"}:
        if not numeric.is_integer():
            raise ProtocolError(
                "VAL-E004",
                f"{name}は整数で指定してください",
                message_id=message_id,
            )
        return int(numeric)
    return numeric


def parse_envelope(payload: Any) -> SensorEnvelope:
    if not isinstance(payload, dict):
        raise ProtocolError("MSG-E001", "JSONオブジェクトが必要です")
    message_id_text = str(payload.get("message_id", "")) or None
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError(
            "MSG-E003",
            f"protocol_version={PROTOCOL_VERSION}が必要です",
            message_id=message_id_text,
        )
    message_id = parse_uuid(payload.get("message_id"), "message_id", "MSG-E004")
    device_id = parse_uuid(payload.get("device_id"), "device_id", "MSG-E005")
    token = payload.get("device_token")
    if not isinstance(token, str) or len(token) < MIN_DEVICE_TOKEN_LENGTH:
        raise ProtocolError("AUTH-E001", "device_tokenが未設定または短すぎます")
    seq = payload.get("device_seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise ProtocolError("MSG-E008", "device_seqは1以上の整数が必要です")
    trigger = payload.get("trigger")
    if trigger not in {"timer", "button"}:
        raise ProtocolError("MSG-E009", "triggerはtimerまたはbuttonが必要です")
    raw_sensors = payload.get("sensors")
    if not isinstance(raw_sensors, dict):
        raise ProtocolError("MSG-E010", "sensorsオブジェクトが必要です")
    unknown = sorted(set(raw_sensors) - set(SENSOR_COLUMNS))
    if unknown:
        raise ProtocolError("MSG-E011", f"未対応センサーです: {', '.join(unknown)}")
    sensors = {
        name: finite_sensor_value(
            name,
            raw_sensors.get(name),
            message_id=str(message_id),
        )
        for name in SENSOR_COLUMNS
    }
    return SensorEnvelope(
        protocol_version=PROTOCOL_VERSION,
        message_id=message_id,
        device_id=device_id,
        device_token=token,
        device_seq=seq,
        measured_at=parse_measured_at(payload.get("measured_at")),
        trigger=trigger,
        sensors=sensors,
    )


def canonical_payload_hash(envelope: SensorEnvelope) -> str:
    canonical = {
        "protocol_version": envelope.protocol_version,
        "message_id": str(envelope.message_id),
        "device_id": str(envelope.device_id),
        "device_seq": envelope.device_seq,
        "measured_at": envelope.measured_at.isoformat(),
        "trigger": envelope.trigger,
        "sensors": envelope.sensors,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def build_envelope(
    *,
    device_id: str,
    device_token: str,
    device_seq: int,
    trigger: str,
    sensors: dict[str, Any],
    measured_at: datetime | None = None,
    message_id: str | None = None,
) -> dict[str, Any]:
    measured = measured_at or utc_now()
    if measured.tzinfo is None:
        measured = measured.replace(tzinfo=timezone.utc)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "message_id": message_id or str(uuid4()),
        "device_id": str(parse_uuid(device_id, "device_id", "MSG-E005")),
        "device_token": device_token,
        "device_seq": device_seq,
        "measured_at": measured.astimezone(timezone.utc).isoformat(),
        "trigger": trigger,
        "sensors": {name: sensors.get(name) for name in SENSOR_COLUMNS},
    }
