"""IoT環境監視システム Ver.3のRaspberry Piクライアント。

注意: 同一ラズパイの同一センサー配線に対して、他のセンサークライアントと同時に
起動しないこと（GPIO/I2C/SPIバスの競合を避けるため）。
"""

import argparse
import json
import os
import socket
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from config_errors import ConfigurationError, require_env, require_port
from edge_queue import EdgeQueue
from protocol_v3 import PROTOCOL_VERSION, SENSOR_COLUMNS, build_envelope
from sensor_plausibility import dht22_matches_bme280
from system_identity import BUILD_CHANNEL, SYSTEM_VERSION

SUPPORTED_SENSOR_BACKENDS = frozenset({"direct", "pi4gpio"})
DEFAULT_PI4GPIO_SOCKET_PATH = "/run/pi4gpio/pi4gpio.sock"
SEND_INTERVAL_SECONDS = 10.0
SOCKET_TIMEOUT_SECONDS = 5.0
SOCKET_RECEIVE_BYTES = 4096
MAX_ACK_BYTES = 64 * 1024
OUTBOX_FLUSH_LIMIT = 100
CLIENT_LOOP_SLEEP_SECONDS = 0.01


@dataclass(frozen=True)
class ClientConfig:
    """環境変数から確定した、変更不能なクライアント設定。"""

    server_host: str
    server_port: int
    device_id: str
    device_token: str
    edge_queue_path: Path
    sensor_backend: str
    pi4gpio_socket_path: Path


def load_client_config() -> ClientConfig:
    """環境変数を検証し、クライアント設定を一か所で確定する。"""
    sensor_backend = os.environ.get("RPI_SENSOR_BACKEND", "direct").strip().lower()
    if sensor_backend not in SUPPORTED_SENSOR_BACKENDS:
        raise SystemExit("[CFG-C006] RPI_SENSOR_BACKENDはdirectまたはpi4gpioです")

    pi4gpio_socket_path = Path(
        os.environ.get("PI4GPIO_SOCKET_PATH", DEFAULT_PI4GPIO_SOCKET_PATH).strip()
    )
    if sensor_backend == "pi4gpio" and not pi4gpio_socket_path.is_absolute():
        raise SystemExit("[CFG-C007] PI4GPIO_SOCKET_PATHは絶対パスが必要です")

    try:
        server_host = require_env("SENSOR_HOST", "CFG-C001")
        server_port = require_port()
        device_id = require_env("DEVICE_ID", "CFG-C003")
        UUID(device_id)
        device_token = require_env("DEVICE_TOKEN", "CFG-C004")
        edge_queue_path = Path(require_env("EDGE_QUEUE_PATH", "CFG-C005"))
    except ConfigurationError as exc:
        raise SystemExit(str(exc)) from None
    except ValueError:
        raise SystemExit("[CFG-C003] DEVICE_IDが未設定または不正です") from None

    return ClientConfig(
        server_host=server_host,
        server_port=server_port,
        device_id=device_id,
        device_token=device_token,
        edge_queue_path=edge_queue_path,
        sensor_backend=sensor_backend,
        pi4gpio_socket_path=pi4gpio_socket_path,
    )


CLIENT_CONFIG = load_client_config()
# rpi_sensorsはimport時にバックエンドを参照するため、その前に正規化値を反映する。
os.environ["RPI_SENSOR_BACKEND"] = CLIENT_CONFIG.sensor_backend
os.environ["PI4GPIO_SOCKET_PATH"] = str(CLIENT_CONFIG.pi4gpio_socket_path)

from rpi_sensors.bme280_pressure import BME280Sensor
from rpi_sensors.grove_mcp3208_sensors import GroveLightSensor, GroveSoundSensor
from rpi_sensors.joystick_mcp3208 import JoystickMCP3208
from rpi_sensors.mh_x19c_co2 import MHZ19C
from rpi_sensors.potentiometer_mcp3208 import PotentiometerMCP3208
from rpi_sensors.robust_dht22 import RobustDHT22
from rpi_sensors.tactile_button import TactileButton

# ==========================================
# 設定（環境変数で上書き可能）
# ファイル自体の既定値は50000のままだが、実際の送信先はsystemdのEnvironment=で
# グループ用サーバーのポート(12345)に上書きして運用している。
# ==========================================
EDGE_QUEUE = EdgeQueue(CLIENT_CONFIG.edge_queue_path)

# センサー基盤(MCP3208等)ごと物理的に未接続の場合に見られる「疑わしい既定値」の組み合わせ。
# DHT22/BME280/CO2(I2C・UART)は未接続だと例外になりNoneになるが、
# 照度・音・ジョイスティック・半固定抵抗(MCP3208 ADC経由)は未接続でも例外にならず、
# 「何も繋がっていないチャンネルの値」をあたかも正常値であるかのように返してしまう。
# monitor.py側のPLAUSIBLE_RANGESはこれらの値を「物理的にあり得る範囲内」として
# 見逃してしまうため、ここで組み合わせパターンとして検知し、送信自体を止める。
DISCONNECT_FLOOR_VALUES = {
    "light_raw": 0.0,
    "sound_raw": 0.0,
    "joystick_x": -1.0,
    "joystick_y": -1.0,
    "potentiometer_percent": 0.0,
}
# ==========================================


def send_once(payload, server_host):
    """Ver3 JSONを送信し、message_idが一致するACKだけを返す。"""
    json_data = json.dumps(payload, ensure_ascii=False)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(SOCKET_TIMEOUT_SECONDS)
            sock.connect((server_host, CLIENT_CONFIG.server_port))
            sock.sendall((json_data + "\n").encode("utf-8"))
            received = bytearray()
            while not received.endswith(b"\n"):
                chunk = sock.recv(SOCKET_RECEIVE_BYTES)
                if not chunk:
                    break
                received.extend(chunk)
                if len(received) > MAX_ACK_BYTES:
                    raise ValueError("ACKが大きすぎます")
            ack = json.loads(received.decode("utf-8"))
            if ack.get("protocol_version") != PROTOCOL_VERSION:
                raise ValueError("ACKのprotocol_versionが一致しません")
            if ack.get("message_id") != payload.get("message_id"):
                raise ValueError("ACKのmessage_idが一致しません")
            if ack.get("status") not in {"inserted", "duplicate", "rejected", "retry"}:
                raise ValueError("ACK statusが未対応です")
            return ack
    except Exception as e:
        print(f"❌ ACK未確認: {e}")
        return None


def apply_ack(payload, ack):
    """inserted/duplicateだけを削除し、rejectedは隔離、未確認は保持する。"""
    message_id = payload["message_id"]
    if ack is None:
        EDGE_QUEUE.mark_attempt(message_id, "ACK-E001")
        return False
    status = ack["status"]
    EDGE_QUEUE.acknowledge(message_id, status, ack.get("error_code"))
    if status in {"inserted", "duplicate"}:
        print(f"✅ DB保存確認: {status} message_id={message_id}")
        return True
    if status == "rejected":
        print(f"⛔ 保存拒否・隔離: {ack.get('error_code')} message_id={message_id}")
        return False
    print(f"⏳ 保存未確認・再送待ち: {ack.get('error_code')} message_id={message_id}")
    return False


def flush_buffer(server_host):
    """SQLiteの未ACK要求をdevice_seq順に再送する。"""
    for payload in EDGE_QUEUE.pending(limit=OUTBOX_FLUSH_LIMIT):
        ack = send_once(payload, server_host)
        apply_ack(payload, ack)
        if ack is None or ack.get("status") == "retry":
            break


def read_all_sensors(sensors, button_pressed):
    """全センサーを読み取り、(payload, failed_sensors) を返す。
    読み取りに失敗したセンサーはNone（PostgreSQLのNULL）とし、送信はスキップしない。
    """
    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "trigger": "button" if button_pressed else "timer",
    }
    failed = []

    try:
        payload["light"] = {
            "raw": sensors["light"].read_raw(),
            "voltage": round(sensors["light"].read_voltage(), 2),
        }
    except Exception:
        payload["light"] = {"raw": None, "voltage": None}
        failed.append("GroveLightSensor")

    try:
        payload["sound"] = {"raw": sensors["sound"].read_raw()}
    except Exception:
        payload["sound"] = {"raw": None}
        failed.append("GroveSoundSensor")

    try:
        x, y = sensors["joy"].read_xy(ch_x=2, ch_y=3, normalize=True)
        payload["joystick"] = {"x": x, "y": y}
    except Exception:
        payload["joystick"] = {"x": None, "y": None}
        failed.append("JoystickMCP3208")

    try:
        payload["potentiometer"] = {
            "percent": round(sensors["pot"].read_percentage(), 1)
        }
    except Exception:
        payload["potentiometer"] = {"percent": None}
        failed.append("PotentiometerMCP3208")

    dht_reading = None
    try:
        temp, hum = sensors["dht"].read()
        dht_reading = (temp, hum)
        payload["dht22"] = {"temp": temp, "hum": hum}
    except Exception:
        payload["dht22"] = {"temp": None, "hum": None}
        failed.append("RobustDHT22")

    bme_reading = None
    try:
        bme_temp, bme_hum, pres = sensors["bme"].read()
        bme_reading = (bme_temp, bme_hum)
        payload["bme280"] = {"pres": pres}
    except Exception:
        payload["bme280"] = {"pres": None}
        failed.append("BME280Sensor")

    if (
        dht_reading is not None
        and bme_reading is not None
        and not dht22_matches_bme280(*dht_reading, *bme_reading)
    ):
        # 約2倍化のようにチェックサムを通過するDHT22波形破損も、
        # 独立したBME280との不一致で送信前にfail-closedとする。
        payload["dht22"] = {"temp": None, "hum": None}
        failed.append("RobustDHT22 (BME280不整合)")

    try:
        co2 = sensors["co2"].read_co2()
        if co2:
            payload["mh_z19c"] = {"co2": co2}
        else:
            payload["mh_z19c"] = {"co2": None}
            failed.append("MHZ19C (データ無効)")
    except Exception:
        payload["mh_z19c"] = {"co2": None}
        failed.append("MHZ19C")

    return payload, failed


def flatten_sensors(payload):
    """Ver2互換の読み取り結果をVer3の固定センサー列へ正規化する。"""
    values = {
        "light_raw": payload.get("light", {}).get("raw"),
        "light_voltage": payload.get("light", {}).get("voltage"),
        "sound_raw": payload.get("sound", {}).get("raw"),
        "joystick_x": payload.get("joystick", {}).get("x"),
        "joystick_y": payload.get("joystick", {}).get("y"),
        "potentiometer_percent": payload.get("potentiometer", {}).get("percent"),
        "temp": payload.get("dht22", {}).get("temp"),
        "hum": payload.get("dht22", {}).get("hum"),
        "pressure": payload.get("bme280", {}).get("pres"),
        "co2": payload.get("mh_z19c", {}).get("co2"),
    }
    return {name: values.get(name) for name in SENSOR_COLUMNS}


def _is_close(value, target, tol=1e-6):
    """valueがtargetとほぼ等しいか(浮動小数点の誤差を許容した比較)。"""
    return value is not None and abs(value - target) < tol


def looks_disconnected(payload):
    """センサー基盤(MCP3208等)ごと物理的に未接続と疑われる状態かどうかを判定する。

    以下の両方が揃った場合だけ「未接続」と判定する(単独条件だけでは正常値と区別できないため):
      1. DHT22・BME280・CO2(I2C/UART経由)が全て読み取り失敗(None)
      2. 照度・音・ジョイスティックXY・半固定抵抗(MCP3208 ADC経由)が、
         何も繋がっていないチャンネルにありがちな既定値(DISCONNECT_FLOOR_VALUES)と一致

    注意: これは「センサー基盤が丸ごと外れている」ケースを狙った簡易的なヒューリスティックであり、
    一部のセンサーだけ外れている場合(例: ADC基盤だけ外れ、I2C/UARTは接続されたまま)は
    検知できない。その場合は個別のNull値として送信され続ける点に留意すること。
    """
    dht22 = payload.get("dht22", {})
    bme280 = payload.get("bme280", {})
    mh_z19c = payload.get("mh_z19c", {})

    all_digital_sensors_failed = (
        dht22.get("temp") is None
        and dht22.get("hum") is None
        and bme280.get("pres") is None
        and mh_z19c.get("co2") is None
    )
    if not all_digital_sensors_failed:
        return False

    light = payload.get("light", {})
    sound = payload.get("sound", {})
    joystick = payload.get("joystick", {})
    potentiometer = payload.get("potentiometer", {})

    return (
        _is_close(light.get("raw"), DISCONNECT_FLOOR_VALUES["light_raw"])
        and _is_close(sound.get("raw"), DISCONNECT_FLOOR_VALUES["sound_raw"])
        and _is_close(joystick.get("x"), DISCONNECT_FLOOR_VALUES["joystick_x"])
        and _is_close(joystick.get("y"), DISCONNECT_FLOOR_VALUES["joystick_y"])
        and _is_close(
            potentiometer.get("percent"),
            DISCONNECT_FLOOR_VALUES["potentiometer_percent"],
        )
    )


def close_sensors(sensors):
    """初期化済みセンサーを、個別の終了失敗に影響されず閉じる。"""
    for sensor in sensors.values():
        with suppress(Exception):
            sensor.close()


def initialize_sensors():
    """全センサーを初期化し、途中失敗時は初期化済み資源を解放する。"""
    sensors = {}
    try:
        sensors["light"] = GroveLightSensor(channel=0)
        sensors["sound"] = GroveSoundSensor(channel=1)
        sensors["joy"] = JoystickMCP3208(deadzone=150)
        sensors["pot"] = PotentiometerMCP3208(channel=4)
        sensors["btn"] = TactileButton(pin=6)
        sensors["dht"] = RobustDHT22(pin=26)
        sensors["bme"] = BME280Sensor(port=1, address=0x76)
        sensors["co2"] = MHZ19C(serial_device="/dev/serial0")
    except Exception:
        close_sensors(sensors)
        raise
    return sensors


def main(server_host):
    print(
        f"=== グループ用センサークライアント "
        f"Ver.{SYSTEM_VERSION} {BUILD_CHANNEL} 起動 ==="
    )
    print(
        f"protocol={PROTOCOL_VERSION} / "
        f"device_id={CLIENT_CONFIG.device_id[:8]}… / queue=SQLite"
    )
    print(
        f"sensor_backend={CLIENT_CONFIG.sensor_backend} / "
        "pi4gpio_socket="
        f"{CLIENT_CONFIG.pi4gpio_socket_path if CLIENT_CONFIG.sensor_backend == 'pi4gpio' else 'unused'}"
    )
    print(f"送信先: {server_host}:{CLIENT_CONFIG.server_port}")
    print("トリガー: 自動判定 (timer/button)")
    print(f"取得間隔: {SEND_INTERVAL_SECONDS}秒")

    try:
        sensors = initialize_sensors()
    except Exception as e:
        print(f"初期化エラー: {e}")
        sys.exit(1)

    last_send_time = time.time() - SEND_INTERVAL_SECONDS
    button_latched = False

    try:
        while True:
            current_time = time.time()
            just_pressed, _, _ = sensors["btn"].update()
            if just_pressed:
                button_latched = True

            if (current_time - last_send_time) >= SEND_INTERVAL_SECONDS:
                flush_buffer(server_host)

                payload, failed_sensors = read_all_sensors(sensors, button_latched)
                button_latched = False

                if failed_sensors:
                    print(
                        "⚠️ 一部センサーの取得に失敗しました（該当値はNullとして送信します）"
                    )
                    print(f"   [失敗したセンサ]: {', '.join(failed_sensors)}")

                if looks_disconnected(payload):
                    # センサー基盤が丸ごと未接続の疑いが強いため、送信自体をスキップする。
                    # (別プロジェクトでPiを使っている間などに、ゴミ値が本番DBへ
                    #  蓄積され続けるのを防ぐための安全策)
                    print(
                        "🔌 センサー基盤が未接続の疑いを検知しました。このデータの送信をスキップします。"
                    )
                else:
                    device_seq = EDGE_QUEUE.next_device_seq()
                    envelope = build_envelope(
                        device_id=CLIENT_CONFIG.device_id,
                        device_token=CLIENT_CONFIG.device_token,
                        device_seq=device_seq,
                        trigger=payload["trigger"],
                        sensors=flatten_sensors(payload),
                    )
                    # ネットワーク送信より先にSQLiteへcommitし、電源断時の消失を防ぐ。
                    EDGE_QUEUE.enqueue(envelope)
                    print(
                        f"送信準備: message_id={envelope['message_id']} "
                        f"device_seq={device_seq} pending={EDGE_QUEUE.count()}"
                    )
                    apply_ack(envelope, send_once(envelope, server_host))

                last_send_time = current_time

            time.sleep(CLIENT_LOOP_SLEEP_SECONDS)

    finally:
        close_sensors(sensors)
        print("完了しました。")


def parse_args():
    parser = argparse.ArgumentParser(description="グループ用センサークライアント")
    parser.add_argument(
        "-p",
        dest="server_host",
        default=CLIENT_CONFIG.server_host,
        help="送信先サーバーのホスト名またはIPアドレス",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args.server_host)
