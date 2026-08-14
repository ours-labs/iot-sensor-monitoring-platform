"""Ver3 ACK・SQLiteキューをPCだけで確認する擬似Piクライアント。"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import random
import socket
import time
from uuid import UUID

from config_errors import ConfigurationError, require_env, require_port
from edge_queue import EdgeQueue
from protocol_v3 import PROTOCOL_VERSION, SENSOR_COLUMNS, build_envelope
from system_identity import SYSTEM_VERSION


try:
    DEFAULT_HOST = require_env("SENSOR_HOST", "CFG-C001")
    DEFAULT_PORT = require_port()
    DEVICE_ID = require_env("DEVICE_ID", "CFG-C003")
    UUID(DEVICE_ID)
    DEVICE_TOKEN = require_env("DEVICE_TOKEN", "CFG-C004")
except ConfigurationError as exc:
    raise SystemExit(str(exc)) from None
except ValueError:
    raise SystemExit("[CFG-C003] DEVICE_IDが未設定または不正です") from None

QUEUE_PATH = os.environ.get(
    "EDGE_QUEUE_PATH", str(Path(__file__).with_name("sim_pending.sqlite3"))
)
QUEUE = EdgeQueue(QUEUE_PATH)


def generate_sensors(danger_probability: float, missing_probability: float) -> dict:
    light_raw = random.randint(100, 3900)
    sensors = {
        "light_raw": light_raw,
        "light_voltage": round(light_raw / 4095 * 3.3, 3),
        "sound_raw": random.randint(100, 2000),
        "joystick_x": round(random.uniform(-1, 1), 3),
        "joystick_y": round(random.uniform(-1, 1), 3),
        "potentiometer_percent": round(random.uniform(0, 100), 1),
        "temp": round(random.uniform(15, 28), 1),
        "hum": round(random.uniform(30, 70), 1),
        "pressure": round(random.uniform(995, 1020), 1),
        "co2": float(random.randint(400, 900)),
    }
    if random.random() < danger_probability:
        name, value = random.choice([
            ("temp", 32.0), ("co2", 1500.0), ("sound_raw", 3500),
            ("light_raw", 50), ("pressure", 980.0),
        ])
        sensors[name] = value
    if random.random() < missing_probability:
        sensors[random.choice(list(SENSOR_COLUMNS))] = None
    return sensors


def send_and_receive(payload: dict, host: str, port: int) -> dict | None:
    try:
        with socket.create_connection((host, port), timeout=5) as sock:
            sock.settimeout(5)
            sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            received = bytearray()
            while not received.endswith(b"\n"):
                chunk = sock.recv(4096)
                if not chunk:
                    break
                received.extend(chunk)
            ack = json.loads(received.decode("utf-8"))
        if ack.get("protocol_version") != PROTOCOL_VERSION:
            raise ValueError("protocol_version不一致")
        if ack.get("message_id") != payload["message_id"]:
            raise ValueError("message_id不一致")
        return ack
    except Exception as exc:
        print(f"ACK未確認: {exc}")
        return None


def deliver(payload: dict, host: str, port: int) -> bool:
    ack = send_and_receive(payload, host, port)
    if ack is None:
        QUEUE.mark_attempt(payload["message_id"], "ACK-E001")
        return False
    QUEUE.acknowledge(payload["message_id"], ack["status"], ack.get("error_code"))
    print(f"ACK: {ack['status']} message_id={payload['message_id']}")
    return ack["status"] in {"inserted", "duplicate"}


def flush(host: str, port: int) -> None:
    for payload in QUEUE.pending(100):
        if not deliver(payload, host, port):
            break


def main() -> int:
    parser = argparse.ArgumentParser(description="Ver3擬似Pi")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--count", type=int)
    parser.add_argument("--danger-probability", type=float, default=0.15)
    parser.add_argument("--missing-probability", type=float, default=0.05)
    args = parser.parse_args()
    print(
        f"Ver.{SYSTEM_VERSION} Development 擬似Pi / protocol={PROTOCOL_VERSION} / "
        f"device={DEVICE_ID[:8]}… / queue=SQLite"
    )
    sent = 0
    while args.count is None or sent < args.count:
        flush(args.host, args.port)
        seq = QUEUE.next_device_seq()
        payload = build_envelope(
            device_id=DEVICE_ID, device_token=DEVICE_TOKEN, device_seq=seq,
            trigger="timer", sensors=generate_sensors(
                args.danger_probability, args.missing_probability
            ), measured_at=datetime.now(timezone.utc),
        )
        QUEUE.enqueue(payload)
        deliver(payload, args.host, args.port)
        sent += 1
        time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
