"""隔離されたPostgreSQL test DBで複数Pi・複数読取クライアントを再現する。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import statistics
import sys
import threading
import time
from urllib.parse import urlparse
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from database import PostgresRepository, apply_migrations  # noqa: E402
from protocol_v3 import SENSOR_COLUMNS, build_envelope, parse_envelope  # noqa: E402


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))]


def ensure_test_url(url: str) -> None:
    name = urlparse(url.replace("postgresql+psycopg://", "postgresql://", 1)).path.rsplit("/", 1)[-1]
    if "test" not in name.lower():
        raise SystemExit("[TEST-E001] DB名にtestを含む隔離DBだけ使用できます")
    production = os.environ.get("DATABASE_URL", "")
    if production and production == url:
        raise SystemExit("[TEST-E002] 本番DATABASE_URLと同一です")


def capabilities() -> list[dict]:
    return [{"sensor_code": name, "enabled": True, "required": False,
             "unit": "test", "expected_interval_sec": 10}
            for name in SENSOR_COLUMNS]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--devices", type=int, default=10)
    parser.add_argument("--messages-per-device", type=int, default=300)
    parser.add_argument("--writers", type=int, default=16)
    parser.add_argument("--readers", type=int, default=5)
    parser.add_argument("--measured-step-seconds", type=float, default=10.0)
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url:
        raise SystemExit("[TEST-E003] TEST_DATABASE_URLが未設定です")
    ensure_test_url(url)
    migrations = apply_migrations(url)
    repo = PostgresRepository(url, open_pool=True)
    with repo.connection() as conn, conn.transaction():
        conn.execute("TRUNCATE devices, operator_identities CASCADE")

    devices = []
    for number in range(args.devices):
        device_id, token = uuid4(), secrets.token_urlsafe(32)
        repo.register_device(device_id=device_id, display_name=f"load-test-{number:02d}",
                             raw_token=token, capabilities=capabilities())
        devices.append((device_id, token))

    latencies: list[float] = []
    status_counts: dict[str, int] = {}
    lock = threading.Lock()
    stop = threading.Event()
    read_count = 0
    read_errors = 0

    def write(device_number: int, seq: int):
        device_id, token = devices[device_number]
        measured = datetime.now(timezone.utc) + timedelta(seconds=seq * args.measured_step_seconds)
        payload = build_envelope(
            device_id=str(device_id), device_token=token, device_seq=seq,
            trigger="timer", measured_at=measured,
            sensors={"light_raw": 1000 + device_number, "light_voltage": 1.2,
                     "sound_raw": 500 + (seq % 50), "joystick_x": 0.5,
                     "joystick_y": 0.5, "potentiometer_percent": 50.0,
                     "temp": 20.0 + device_number / 10, "hum": 55.0,
                     "pressure": 1005.0, "co2": 600.0 + device_number},
        )
        started = time.perf_counter()
        ack = repo.insert_sensor(parse_envelope(payload))
        latency = time.perf_counter() - started
        with lock:
            latencies.append(latency)
            status_counts[ack.status] = status_counts.get(ack.status, 0) + 1
        return ack.status

    def read_loop():
        nonlocal read_count, read_errors
        while not stop.is_set():
            try:
                repo.fetch_readings(limit=100)
                with lock:
                    read_count += 1
            except Exception:
                with lock:
                    read_errors += 1
            time.sleep(0.01)

    readers = [threading.Thread(target=read_loop, daemon=True) for _ in range(args.readers)]
    for thread in readers:
        thread.start()
    started_all = time.perf_counter()
    futures = []
    with ThreadPoolExecutor(max_workers=args.writers) as pool:
        for device_number in range(args.devices):
            for seq in range(1, args.messages_per_device + 1):
                futures.append(pool.submit(write, device_number, seq))
        for future in as_completed(futures):
            future.result()
    elapsed = time.perf_counter() - started_all
    stop.set()
    for thread in readers:
        thread.join(timeout=2)

    device_id, token = devices[0]
    duplicate_payload = parse_envelope(build_envelope(
        device_id=str(device_id), device_token=token,
        device_seq=args.messages_per_device + 1, trigger="timer",
        sensors={"temp": 25.0},
    ))
    with ThreadPoolExecutor(max_workers=20) as pool:
        duplicate_statuses = list(pool.map(lambda _: repo.insert_sensor(duplicate_payload).status, range(20)))

    expected = args.devices * args.messages_per_device + 1
    with repo.connection() as conn:
        stored = conn.execute("SELECT COUNT(*) AS n FROM sensor_readings").fetchone()["n"]
        duplicate_rows = conn.execute(
            "SELECT COUNT(*) AS n FROM sensor_readings WHERE message_id=%s",
            (duplicate_payload.message_id,),
        ).fetchone()["n"]
    result = {
        "devices": args.devices,
        "writers": args.writers,
        "readers": args.readers,
        "messages_attempted": args.devices * args.messages_per_device,
        "measured_span_hours_per_device": round(args.messages_per_device * args.measured_step_seconds / 3600, 2),
        "status_counts": status_counts,
        "stored_rows": stored,
        "expected_rows": expected,
        "elapsed_seconds": round(elapsed, 3),
        "throughput_per_second": round((args.devices * args.messages_per_device) / elapsed, 2),
        "ack_latency_ms": {
            "mean": round(statistics.mean(latencies) * 1000, 2),
            "p50": round(percentile(latencies, 0.50) * 1000, 2),
            "p95": round(percentile(latencies, 0.95) * 1000, 2),
            "max": round(max(latencies) * 1000, 2),
        },
        "concurrent_reads": read_count,
        "read_errors": read_errors,
        "duplicate_race": {
            "inserted": duplicate_statuses.count("inserted"),
            "duplicate": duplicate_statuses.count("duplicate"),
            "stored_rows": duplicate_rows,
        },
        "migrations_applied": migrations,
        "passed": (
            status_counts == {"inserted": args.devices * args.messages_per_device}
            and stored == expected and read_errors == 0
            and duplicate_statuses.count("inserted") == 1
            and duplicate_statuses.count("duplicate") == 19
            and duplicate_rows == 1
        ),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with repo.connection() as conn, conn.transaction():
        conn.execute("TRUNCATE devices, operator_identities CASCADE")
    repo.close()
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
