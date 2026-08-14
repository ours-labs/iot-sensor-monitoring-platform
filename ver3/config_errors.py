"""共有版の実行設定をfail-closedで検証する共通機能。

エラーコードはログ、systemd/journalctl、READMEで共通に参照する。例外文には
不足した設定名だけを含め、秘密値そのものは決して出力しない。
"""

import os

MIN_TCP_PORT = 1
MAX_TCP_PORT = 65535


ERROR_CATALOG = {
    "CFG-C001": "SENSOR_HOSTが未設定です",
    "CFG-C002": "SENSOR_PORTが未設定または不正です",
    "CFG-W001": "API_KEYSが未設定です",
    "CFG-W002": "SECRET_KEYが未設定です",
    "CFG-W003": "ACCESS_TRUSTED_HOSTが未設定です",
    "CFG-W004": "ALLOWED_OPERATOR_IDSが未設定です",
    "CFG-W005": "PAIRING_EMAILSが未設定です",
    "CFG-W006": "FLASK_PORTが未設定または不正です",
    "CFG-D001": "DATABASE_URLが未設定です",
    "CFG-D002": "DATABASE_URLはPostgreSQL接続先でなければなりません",
    "CFG-D003": "TOKEN_HASH_KEYが未設定または32文字未満です",
    "CFG-C003": "DEVICE_IDが未設定または不正です",
    "CFG-C004": "DEVICE_TOKENが未設定です",
    "CFG-C005": "EDGE_QUEUE_PATHが未設定です",
}


class ConfigurationError(RuntimeError):
    """秘密値を含めず、固定コードで不足設定を表す例外。"""

    def __init__(self, code, detail=None):
        self.code = code
        self.detail = detail or ERROR_CATALOG.get(code, "設定が不正です")
        super().__init__(f"[{self.code}] {self.detail}")


def require_env(name, code):
    """空白ではない環境変数を返し、無ければ指定コードで停止する。"""
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(code)
    return value


def require_port(name="SENSOR_PORT", code="CFG-C002"):
    """1～65535の必須ポート番号を返す。"""
    raw = require_env(name, code)
    try:
        port = int(raw)
    except ValueError as exc:
        raise ConfigurationError(code) from exc
    if not MIN_TCP_PORT <= port <= MAX_TCP_PORT:
        raise ConfigurationError(code)
    return port


def require_csv(name, code):
    """カンマ区切り必須設定を空要素を除いて返す。"""
    values = tuple(item.strip() for item in require_env(name, code).split(","))
    values = tuple(item for item in values if item)
    if not values:
        raise ConfigurationError(code)
    return values


def require_postgres_url(name="DATABASE_URL"):
    """PostgreSQL以外への誤接続を拒否して接続URLを返す。"""
    value = require_env(name, "CFG-D001")
    if not value.startswith(("postgresql://", "postgresql+psycopg://")):
        raise ConfigurationError("CFG-D002")
    return value


def require_token_hash_key(name="TOKEN_HASH_KEY"):
    """認証tokenの決定的HMACに使う32文字以上の秘密鍵を返す。"""
    value = require_env(name, "CFG-D003")
    if len(value.encode("utf-8")) < 32:
        raise ConfigurationError("CFG-D003")
    return value
