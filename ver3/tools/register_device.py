"""Ver3デバイスをサーバー上で登録し、一度だけ資格情報を表示する。"""

import argparse
import json
from pathlib import Path
import secrets
import sys
from uuid import uuid4


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from database import PostgresRepository  # noqa: E402
from protocol_v3 import PHYSICAL_RANGES, SENSOR_COLUMNS  # noqa: E402


UNITS = {
    "light_raw": "raw", "light_voltage": "V", "sound_raw": "raw",
    "joystick_x": "ratio", "joystick_y": "ratio",
    "potentiometer_percent": "%", "temp": "degC", "hum": "%",
    "pressure": "hPa", "co2": "ppm",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Ver3デバイス登録")
    parser.add_argument("display_name")
    parser.add_argument(
        "--sensors", default=",".join(SENSOR_COLUMNS),
        help="搭載sensor_codeのカンマ区切り",
    )
    args = parser.parse_args()
    selected = tuple(item.strip() for item in args.sensors.split(",") if item.strip())
    unknown = sorted(set(selected) - set(SENSOR_COLUMNS))
    if unknown:
        parser.error("未対応sensor_code: " + ", ".join(unknown))

    device_id = uuid4()
    token = secrets.token_urlsafe(32)
    capabilities = [
        {
            "sensor_code": name,
            "unit": UNITS[name],
            "expected_interval_sec": 10,
            "plausible_min": PHYSICAL_RANGES[name][0],
            "plausible_max": PHYSICAL_RANGES[name][1],
        }
        for name in selected
    ]
    repository = PostgresRepository()
    repository.open()
    try:
        repository.register_device(
            device_id=device_id,
            display_name=args.display_name,
            raw_token=token,
            capabilities=capabilities,
        )
    finally:
        repository.close()
    print("登録完了。次の資格情報は再表示できません。Gitやチャットへ保存しないでください。")
    print(json.dumps({"device_id": str(device_id), "device_token": token}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
