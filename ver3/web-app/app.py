"""IoT環境監視システム Ver.3 PostgreSQL Web/APIアプリケーション。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import math
import os
import secrets
import sys
import threading
import time
from typing import Any, NamedTuple
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
import pandas as pd
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash


PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from config_errors import ConfigurationError, require_env, require_port  # noqa: E402
from database import DatabaseError, PostgresRepository  # noqa: E402
from protocol_v3 import PHYSICAL_RANGES, PROTOCOL_VERSION, SENSOR_COLUMNS  # noqa: E402
from system_identity import BUILD_CHANNEL, SCHEMA_VERSION, SYSTEM_VERSION  # noqa: E402


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_for=1, x_host=1)
try:
    app.secret_key = require_env("SECRET_KEY", "CFG-W002")
    ACCESS_TRUSTED_HOST = require_env("ACCESS_TRUSTED_HOST", "CFG-W003").lower()
except ConfigurationError as exc:
    raise RuntimeError(str(exc)) from None


def _safe_local_redirect(candidate: str | None) -> str:
    """明示的に許可したendpointだけをlogin後の遷移先として返す。"""
    if candidate == "/insert":
        return url_for("insert_data")
    if candidate == "/admin":
        return url_for("admin_console")
    return url_for("index")

app.config.update(
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)


@app.context_processor
def inject_system_identity():
    return {
        "system_version": SYSTEM_VERSION,
        "build_channel": BUILD_CHANNEL,
    }


SENSOR_LABELS = {
    "light_raw": "照度（raw）",
    "light_voltage": "照度電圧（V）",
    "sound_raw": "音（raw）",
    "joystick_x": "ジョイスティック X",
    "joystick_y": "ジョイスティック Y",
    "potentiometer_percent": "可変抵抗（%）",
    "temp": "温度（℃）",
    "hum": "湿度（%）",
    "pressure": "気圧（hPa）",
    "co2": "CO₂（ppm）",
}
DANGER_THRESHOLDS = {
    "temp_high": 30.0,
    "temp_low": 0.0,
    "co2": 1200.0,
    "pressure_low": 990.0,
    "sound_high": 3000.0,
    "light_low": 100.0,
    "light_high": 3900.0,
}
DANGER_LABELS = {
    "temp": "温度",
    "co2": "CO₂",
    "pressure": "気圧",
    "sound": "音",
    "light": "照度",
}
TABLE_ROW_LIMIT = int(os.environ.get("TABLE_ROW_LIMIT", "100"))
FILTERED_ROW_LIMIT = 5000
DATETIME_MINUTE_LENGTH = 16
DATE_ONLY_LENGTH = 10
DOOR_OPEN_THRESHOLD = 0.8
DISPLAY_MODES = frozenset({"table", "graph", "average"})
AVERAGE_SOURCES = frozenset({"sensor", "all"})
TRUTHY_FORM_VALUES = frozenset({"1", "true", "on", "yes"})
COMMITTED_ACK_STATUSES = frozenset({"inserted", "duplicate"})
JST = ZoneInfo("Asia/Tokyo")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "").strip()
ADMIN_IDLE_SECONDS = int(os.environ.get("ADMIN_IDLE_SECONDS", "600"))
ADMIN_ABSOLUTE_SECONDS = int(os.environ.get("ADMIN_ABSOLUTE_SECONDS", "1800"))
ADMIN_REAUTH_SECONDS = int(os.environ.get("ADMIN_REAUTH_SECONDS", "300"))
ADMIN_FAILURE_WINDOW_SECONDS = int(
    os.environ.get("ADMIN_FAILURE_WINDOW_SECONDS", "900")
)
ADMIN_MAX_FAILURES = int(os.environ.get("ADMIN_MAX_FAILURES", "5"))

_admin_failure_lock = threading.Lock()
_admin_failures: dict[str, list[float]] = {}

_repository: PostgresRepository | None = None
_repository_lock = threading.Lock()


class ManualValueValidation(NamedTuple):
    """手入力センサー値の正規化結果。"""

    values: dict[str, float | int | None]
    display_values: dict[str, str]
    errors: list[str]
    danger_sensors: list[str]


class ManualSubmission(NamedTuple):
    """ブラウザとJSONに共通する手入力要求。"""

    message_id: UUID
    target_device_id: UUID | None
    target_text: str
    measured_at: datetime | None
    measured_text: str
    warning_confirmed: bool
    validation: ManualValueValidation

    @property
    def needs_confirmation(self) -> bool:
        return bool(self.validation.danger_sensors and not self.warning_confirmed)


class DashboardFilters(NamedTuple):
    """ダッシュボードの検索条件。"""

    start_raw: str
    end_raw: str
    start_at: datetime | None
    end_at: datetime | None
    display_mode: str
    average_source: str
    selected_sensors: list[str]
    device_id: UUID | None
    device_text: str


class ReadingPresentation(NamedTuple):
    """DB行から生成したAPI・HTML共通の表示材料。"""

    chart_data: dict[str, list]
    data_html: str
    trigger_list: list[str]
    door_list: list[int]
    danger_list: list[list[str]]
    latest_thi: float | None
    latest_dangers: list[str]


@app.template_filter("jst_datetime")
def jst_datetime(value) -> str:
    """DBのTIMESTAMPTZを日本時間として人間向けに表示する。"""
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if not isinstance(value, datetime):
        return str(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S JST")


def repository() -> PostgresRepository:
    global _repository
    if _repository is None:
        with _repository_lock:
            if _repository is None:
                candidate = PostgresRepository(open_pool=False)
                candidate.open()
                _repository = candidate
    return _repository


def _looks_like_api_client() -> bool:
    user_agent = request.headers.get("User-Agent", "").lower()
    return bool(
        request.path.startswith("/api/v3/")
        or request.is_json
        or request.args.get("format") == "json"
        or "okhttp" in user_agent
        or "kotlin" in user_agent
    )


def _access_authenticated_email() -> str | None:
    if request.host.split(":", 1)[0].lower() != ACCESS_TRUSTED_HOST:
        return None
    email = request.headers.get("Cf-Access-Authenticated-User-Email")
    return email.strip().lower() if email else None


def _set_session_identity(identity: dict) -> None:
    session.permanent = True
    session["authenticated"] = True
    session["principal_email"] = identity["principal_email"]
    session["operator_id"] = identity["operator_id"]
    session["display_name"] = identity["display_name"]


def _session_identity() -> dict | None:
    if not session.get("authenticated"):
        return None
    required = ("principal_email", "operator_id", "display_name")
    if not all(session.get(key) for key in required):
        return None
    return {key: session[key] for key in required}


@app.before_request
def authenticate_request():
    if request.endpoint in {
        "login",
        "static",
        "device_control_poll",
        "device_control_event",
    }:
        return None
    try:
        email = _access_authenticated_email()
        identity = repository().identity_for_email(email) if email else None
        if identity is None:
            raw_token = request.headers.get("X-API-Key", "")
            identity = (
                repository().authenticate_user_token(raw_token) if raw_token else None
            )
        if identity is None:
            identity = _session_identity()
        if identity is not None:
            g.identity = identity
            if email:
                _set_session_identity(identity)
            return None
    except (ConfigurationError, DatabaseError):
        app.logger.exception("Ver3認証DBを確認できません")
        if _looks_like_api_client():
            return jsonify({"status": "retry", "error_code": "DB-E006"}), 503
        return "Ver3 PostgreSQLを確認できません。", 503
    if _looks_like_api_client():
        return jsonify({"status": "rejected", "error_code": "AUTH-E001"}), 401
    return redirect(url_for("login", next=request.path))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        identity = repository().authenticate_user_token(request.form.get("key", ""))
        if identity:
            _set_session_identity(identity)
            return redirect(_safe_local_redirect(request.args.get("next")))
        error = "認証情報が正しくありません。"
    return render_template("login.html", error=error, system_version=SYSTEM_VERSION)


@app.route("/pair")
def pair():
    email = _access_authenticated_email()
    if not email:
        return jsonify({"status": "rejected", "error_code": "AUTH-E004"}), 403
    try:
        identity, raw_token = repository().issue_user_credential(email, "android")
    except LookupError:
        return jsonify({"status": "rejected", "error_code": "AUTH-E003"}), 404
    return jsonify(
        {
            "status": "success",
            "system_version": SYSTEM_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "name": identity["display_name"],
            "operator_id": identity["operator_id"],
            "api_key": raw_token,
        }
    )


def _to_float(value):
    try:
        numeric = float(value)
        return numeric if math.isfinite(numeric) else None
    except (TypeError, ValueError):
        return None


def _parse_boundary(raw: str, *, is_end: bool = False) -> datetime | None:
    if not raw:
        return None
    parsed = pd.to_datetime(raw, errors="coerce")
    if pd.isna(parsed):
        return None
    value = parsed.to_pydatetime()
    if value.tzinfo is None:
        value = value.replace(tzinfo=JST)
    if is_end and len(raw) == DATETIME_MINUTE_LENGTH:
        value += timedelta(minutes=1) - timedelta(microseconds=1)
    elif is_end and len(raw) == DATE_ONLY_LENGTH:
        value += timedelta(days=1) - timedelta(microseconds=1)
    return value.astimezone(timezone.utc)


def evaluate_danger_row(temp=None, co2=None, pressure=None, sound=None, light=None):
    dangers = []
    temp, co2, pressure, sound, light = map(
        _to_float, (temp, co2, pressure, sound, light)
    )
    if temp is not None and (
        temp >= DANGER_THRESHOLDS["temp_high"] or temp <= DANGER_THRESHOLDS["temp_low"]
    ):
        dangers.append("temp")
    if co2 is not None and co2 >= DANGER_THRESHOLDS["co2"]:
        dangers.append("co2")
    if pressure is not None and pressure < DANGER_THRESHOLDS["pressure_low"]:
        dangers.append("pressure")
    if sound is not None and sound >= DANGER_THRESHOLDS["sound_high"]:
        dangers.append("sound")
    if light is not None and (
        light < DANGER_THRESHOLDS["light_low"]
        or light > DANGER_THRESHOLDS["light_high"]
    ):
        dangers.append("light")
    return dangers


def calculate_thi(temp, hum):
    temp, hum = _to_float(temp), _to_float(hum)
    if temp is None or hum is None:
        return None
    return round(0.81 * temp + 0.01 * hum * (0.99 * temp - 14.3) + 46.3, 1)


def _validate_manual_values(raw_values: Mapping[str, Any]) -> ManualValueValidation:
    values, display, errors = {}, {}, []
    for name in SENSOR_COLUMNS:
        raw = "" if raw_values.get(name) is None else str(raw_values.get(name)).strip()
        display[name] = raw
        if not raw:
            values[name] = None
            continue
        value = _to_float(raw)
        if value is None:
            errors.append(f"{SENSOR_LABELS[name]}は有限の数値で入力してください。")
            values[name] = None
            continue
        lower, upper = PHYSICAL_RANGES[name]
        if not lower <= value <= upper:
            errors.append(
                f"{SENSOR_LABELS[name]}は物理範囲{lower}〜{upper}内で入力してください。"
            )
        values[name] = (
            int(value)
            if name in {"light_raw", "sound_raw"} and value.is_integer()
            else value
        )
    dangers = evaluate_danger_row(
        temp=values.get("temp"),
        co2=values.get("co2"),
        pressure=values.get("pressure"),
        sound=values.get("sound_raw"),
        light=values.get("light_raw"),
    )
    return ManualValueValidation(values, display, errors, dangers)


def _request_body(is_json: bool) -> Mapping[str, Any]:
    """要求本文を、入力元に依存しないMappingとして返す。"""
    if not is_json:
        return request.form
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


def _parse_manual_submission(
    body: Mapping[str, Any], *, is_json: bool
) -> ManualSubmission:
    """手入力要求の値、識別子、時刻を正規化する。"""
    raw_values = (
        body.get("sensors", {})
        if is_json
        else {name: body.get(name, "") for name in SENSOR_COLUMNS}
    )
    validation = _validate_manual_values(
        raw_values if isinstance(raw_values, dict) else {}
    )
    errors = validation.errors

    message_id_text = str(body.get("message_id", "") or uuid4())
    try:
        message_id = UUID(message_id_text)
    except ValueError:
        errors.append("message_idが不正です。")
        message_id = uuid4()

    target_text = str(body.get("target_device_id", ""))
    try:
        target_device_id = UUID(target_text)
    except ValueError:
        errors.append("対象Piを選択してください。")
        target_device_id = None

    measured_text = str(body.get("measured_at", ""))
    measured_at = (
        _parse_boundary(measured_text) if measured_text else datetime.now(timezone.utc)
    )
    if measured_at is None:
        errors.append("計測時刻が不正です。")

    warning_confirmed = (
        str(body.get("warning_confirmed", "")).lower() in TRUTHY_FORM_VALUES
    )
    return ManualSubmission(
        message_id=message_id,
        target_device_id=target_device_id,
        target_text=target_text,
        measured_at=measured_at,
        measured_text=measured_text,
        warning_confirmed=warning_confirmed,
        validation=validation,
    )


def _manual_csrf_token() -> str:
    token = session.get("manual_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["manual_csrf_token"] = token
    return token


def _security_event(event_code: str, severity: str, **detail) -> None:
    try:
        repository().record_security_event(
            event_code=event_code,
            severity=severity,
            detail=detail,
        )
    except Exception:
        app.logger.exception("セキュリティ監査イベントを保存できません: %s", event_code)


def _admin_state(*, touch: bool = False) -> dict | None:
    elevated_at = session.get("admin_elevated_at")
    last_active = session.get("admin_last_active")
    if not isinstance(elevated_at, (int, float)) or not isinstance(
        last_active, (int, float)
    ):
        return None
    now = time.time()
    if (
        now - elevated_at > ADMIN_ABSOLUTE_SECONDS
        or now - last_active > ADMIN_IDLE_SECONDS
    ):
        session.pop("admin_elevated_at", None)
        session.pop("admin_last_active", None)
        session.pop("admin_reauthenticated_at", None)
        return None
    if touch:
        session["admin_last_active"] = now
    return {
        "elevated_at": elevated_at,
        "last_active": last_active,
        "reauthenticated_at": session.get("admin_reauthenticated_at"),
        "absolute_remaining": max(0, int(ADMIN_ABSOLUTE_SECONDS - (now - elevated_at))),
        "idle_remaining": max(0, int(ADMIN_IDLE_SECONDS - (now - last_active))),
    }


def _admin_rate_limited(principal: str) -> bool:
    now = time.time()
    with _admin_failure_lock:
        recent = [
            stamp
            for stamp in _admin_failures.get(principal, [])
            if now - stamp < ADMIN_FAILURE_WINDOW_SECONDS
        ]
        _admin_failures[principal] = recent
        return len(recent) >= ADMIN_MAX_FAILURES


def _record_admin_failure(principal: str) -> None:
    now = time.time()
    with _admin_failure_lock:
        recent = [
            stamp
            for stamp in _admin_failures.get(principal, [])
            if now - stamp < ADMIN_FAILURE_WINDOW_SECONDS
        ]
        recent.append(now)
        _admin_failures[principal] = recent


def _clear_admin_failures(principal: str) -> None:
    with _admin_failure_lock:
        _admin_failures.pop(principal, None)


def _check_admin_password(raw_password: str) -> bool:
    return bool(
        ADMIN_PASSWORD_HASH
        and raw_password
        and check_password_hash(ADMIN_PASSWORD_HASH, raw_password)
    )


def _valid_csrf(body) -> bool:
    expected = session.get("manual_csrf_token", "")
    supplied = str(body.get("csrf_token", ""))
    return bool(expected and secrets.compare_digest(supplied, expected))


@app.route("/admin")
def admin_console():
    state = _admin_state(touch=True)
    commands = repository().list_control_commands(limit=30) if state else []
    return render_template(
        "admin.html",
        identity=g.identity,
        devices=repository().list_controllable_devices(online_within_sec=30),
        commands=commands,
        admin_state=state,
        admin_enabled=bool(ADMIN_PASSWORD_HASH),
        csrf_token=_manual_csrf_token(),
        system_version=SYSTEM_VERSION,
        idle_minutes=ADMIN_IDLE_SECONDS // 60,
        absolute_minutes=ADMIN_ABSOLUTE_SECONDS // 60,
    )


@app.route("/admin/monitor-events.json")
def admin_monitor_events_json():
    if _admin_state(touch=False) is None:
        return jsonify({"status": "rejected", "error_code": "AUTH-E008"}), 403
    events = repository().list_monitor_events(limit=30)
    return jsonify(
        {
            "status": "success",
            "events": [
                {
                    "occurred_at": jst_datetime(item["occurred_at"]),
                    "display_name": item["display_name"] or "system",
                    "event_code": item["event_code"],
                    "severity": item["severity"],
                    "event_state": item["state"],
                    "detail": item["detail"],
                    "notification": "sent"
                    if item["notified_at"]
                    else "pending"
                    if item["notification_required"]
                    else "internal",
                }
                for item in events
            ],
        }
    )


@app.route("/admin/commands.json")
def admin_commands_json():
    if _admin_state(touch=False) is None:
        return jsonify({"status": "rejected", "error_code": "AUTH-E008"}), 403
    commands = repository().list_control_commands(limit=30)
    return jsonify(
        {
            "status": "success",
            "commands": [
                {
                    "requested_at": jst_datetime(item["requested_at"]),
                    "display_name": item["display_name"],
                    "command": item["command"],
                    "command_status": item["status"],
                    "requested_by": item["requested_by"],
                    "request_id": str(item["request_id"]),
                }
                for item in commands
            ],
        }
    )


@app.route("/admin/elevate", methods=["POST"])
def admin_elevate():
    if not _valid_csrf(request.form):
        return "不正なフォーム送信です。", 400
    principal = g.identity["principal_email"]
    if not ADMIN_PASSWORD_HASH:
        _security_event("CFG-W007", "warning", principal=principal)
        flash(
            "管理者パスワードがサーバーに設定されていないため、管理機能は無効です。",
            "danger",
        )
        return redirect(url_for("admin_console"))
    if _admin_rate_limited(principal):
        _security_event(
            "AUTH-E007", "warning", principal=principal, reason="rate_limited"
        )
        flash(
            "認証失敗回数が上限に達しました。時間を置いて再試行してください。", "danger"
        )
        return redirect(url_for("admin_console"))
    if not _check_admin_password(request.form.get("password", "")):
        _record_admin_failure(principal)
        _security_event(
            "AUTH-E006", "warning", principal=principal, reason="invalid_admin_password"
        )
        flash("管理者パスワードが正しくありません。", "danger")
        return redirect(url_for("admin_console"))
    _clear_admin_failures(principal)
    now = time.time()
    session["admin_elevated_at"] = now
    session["admin_last_active"] = now
    session["admin_reauthenticated_at"] = now
    _security_event("AUTH-I001", "info", principal=principal, action="admin_elevated")
    flash("管理者権限を一時的に有効化しました。", "success")
    return redirect(url_for("admin_console"))


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    if not _valid_csrf(request.form):
        return "不正なフォーム送信です。", 400
    principal = g.identity["principal_email"]
    session.pop("admin_elevated_at", None)
    session.pop("admin_last_active", None)
    session.pop("admin_reauthenticated_at", None)
    _security_event("AUTH-I002", "info", principal=principal, action="admin_released")
    flash("管理者権限を解除しました。", "success")
    return redirect(url_for("admin_console"))


@app.route("/admin/commands", methods=["POST"])
def admin_command():
    if not _valid_csrf(request.form):
        return "不正なフォーム送信です。", 400
    state = _admin_state(touch=True)
    if state is None:
        flash("管理者権限の有効時間が終了しました。再度昇格してください。", "danger")
        return redirect(url_for("admin_console"))
    command = request.form.get("command", "").strip().lower()
    if command not in {"status", "restart", "shutdown"}:
        flash("未対応の操作です。", "danger")
        return redirect(url_for("admin_console"))
    try:
        device_id = UUID(request.form.get("device_id", ""))
    except ValueError:
        flash("対象Piを選択してください。", "danger")
        return redirect(url_for("admin_console"))
    principal = g.identity["principal_email"]
    if command in {"restart", "shutdown"}:
        if request.form.get("confirm_text", "").strip().upper() != command.upper():
            flash(
                f"危険操作の確認欄に {command.upper()} と入力してください。", "danger"
            )
            return redirect(url_for("admin_console"))
        if not _check_admin_password(request.form.get("password", "")):
            _record_admin_failure(principal)
            _security_event(
                "AUTH-E006",
                "warning",
                principal=principal,
                action=command,
                reason="dangerous_action_reauth_failed",
            )
            flash("危険操作の再認証に失敗しました。", "danger")
            return redirect(url_for("admin_console"))
        session["admin_reauthenticated_at"] = time.time()
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=120 if command in {"restart", "shutdown"} else 60
    )
    try:
        queued = repository().queue_control_command(
            request_id=uuid4(),
            device_id=device_id,
            command=command,
            requested_by=principal,
            expires_at=expires_at,
        )
    except LookupError:
        flash("このPiは遠隔操作対象外、無効、または現在オフラインです。", "danger")
        return redirect(url_for("admin_console"))
    except Exception:
        app.logger.exception("制御コマンド登録失敗")
        flash("操作要求を保存できません。時間を置いて再試行してください。", "danger")
        return redirect(url_for("admin_console"))
    _security_event(
        "CTL-I001",
        "warning" if command != "status" else "info",
        principal=principal,
        device_id=str(device_id),
        command=command,
        request_id=str(queued["request_id"]),
    )
    flash(
        f"{command} をキューへ登録しました。Piの受理・結果イベントを待っています。",
        "warning" if command != "status" else "success",
    )
    return redirect(url_for("admin_console"))


@app.route("/api/v3/device-control/poll", methods=["POST"])
def device_control_poll():
    body = request.get_json(silent=True) or {}
    try:
        device_id = UUID(str(body.get("device_id", "")))
        command = repository().poll_control_command(
            device_id=device_id,
            raw_token=request.headers.get("X-Device-Token", ""),
        )
    except (ValueError, TypeError):
        return jsonify({"status": "rejected", "error_code": "DATA-E003"}), 400
    except PermissionError:
        return jsonify({"status": "rejected", "error_code": "AUTH-E002"}), 401
    except Exception:
        app.logger.exception("制御コマンドpoll失敗")
        return jsonify({"status": "retry", "error_code": "DB-E006"}), 503
    return jsonify(
        {
            "status": "success",
            "protocol_version": PROTOCOL_VERSION,
            "command": command,
        }
    )


@app.route("/api/v3/device-control/events", methods=["POST"])
def device_control_event():
    body = request.get_json(silent=True) or {}
    try:
        occurred_raw = str(body.get("occurred_at", ""))
        occurred_at = datetime.fromisoformat(occurred_raw.replace("Z", "+00:00"))
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        boot_raw = body.get("boot_id")
        updated = repository().record_control_event(
            device_id=UUID(str(body.get("device_id", ""))),
            raw_token=request.headers.get("X-Device-Token", ""),
            request_id=UUID(str(body.get("request_id", ""))),
            client_event_id=UUID(str(body.get("client_event_id", ""))),
            event_type=str(body.get("event_type", "")),
            boot_id=UUID(str(boot_raw)) if boot_raw else None,
            occurred_at=occurred_at,
            detail=body.get("detail", {})
            if isinstance(body.get("detail", {}), dict)
            else {},
        )
    except PermissionError:
        return jsonify({"status": "rejected", "error_code": "AUTH-E002"}), 401
    except LookupError:
        return jsonify({"status": "rejected", "error_code": "DATA-E003"}), 404
    except (ValueError, TypeError):
        return jsonify({"status": "rejected", "error_code": "DATA-E003"}), 400
    except Exception:
        app.logger.exception("制御イベント保存失敗")
        return jsonify({"status": "retry", "error_code": "DB-E006"}), 503
    return jsonify({"status": "success", "command": updated})


@app.route("/api/v3/status")
def api_status():
    return jsonify(
        {
            "status": "success",
            "system_version": SYSTEM_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "schema_version": SCHEMA_VERSION,
            "database": "PostgreSQL",
        }
    )


@app.route("/api/v3/devices")
def api_devices():
    devices = repository().list_devices()
    return jsonify({"status": "success", "devices": devices})


@app.route("/api/v3/devices/<device_id>/display-name", methods=["POST"])
def update_device_name(device_id):
    body = request.get_json(silent=True) or {} if request.is_json else request.form
    if not request.is_json:
        expected = session.get("manual_csrf_token", "")
        if not expected or not secrets.compare_digest(
            str(body.get("csrf_token", "")), expected
        ):
            return "不正なフォーム送信です。", 400
    try:
        target_id = UUID(device_id)
        updated = repository().update_device_display_name(
            device_id=target_id,
            display_name=str(body.get("display_name", "")),
            principal_email=g.identity["principal_email"],
        )
    except (ValueError, TypeError):
        if request.is_json:
            return jsonify(
                {
                    "status": "rejected",
                    "error_code": "DATA-E002",
                    "detail": "device_idまたはdisplay_nameが不正です。",
                }
            ), 400
        flash("device_idまたは表示名が不正です。", "danger")
        return redirect(url_for("index", device_id=device_id))
    if updated is None:
        if request.is_json:
            return jsonify({"status": "rejected", "error_code": "AUTH-E003"}), 404
        flash("対象Piが見つからないか無効です。", "danger")
        return redirect(url_for("index"))
    if request.is_json:
        return jsonify({"status": "success", "device": updated})
    flash("Piの表示名を更新しました。device_idは変更していません。", "success")
    return redirect(url_for("index", device_id=device_id))


@app.route("/api/v3/manual-readings", methods=["POST"])
@app.route("/insert", methods=["GET", "POST"])
def insert_data():
    is_json = request.is_json
    body = _request_body(is_json)
    submission = _parse_manual_submission(body, is_json=is_json)
    validation = submission.validation

    if request.method == "POST" and not is_json:
        expected = session.get("manual_csrf_token", "")
        if not expected or not secrets.compare_digest(
            str(body.get("csrf_token", "")), expected
        ):
            return "不正なフォーム送信です。", 400

    if (
        request.method == "POST"
        and not validation.errors
        and not submission.needs_confirmation
    ):
        identity = g.identity
        try:
            ack = repository().insert_manual(
                message_id=submission.message_id,
                target_device_id=submission.target_device_id,
                principal_email=identity["principal_email"],
                operator_id=identity["operator_id"],
                measured_at=submission.measured_at,
                values=validation.values,
                warning_confirmed=submission.warning_confirmed,
                warning_reasons=validation.danger_sensors,
            )
        except Exception:
            app.logger.exception(
                "Ver3手入力保存失敗: message_id=%s", submission.message_id
            )
            ack = None
        if is_json:
            if ack is None:
                return jsonify(
                    {
                        "protocol_version": PROTOCOL_VERSION,
                        "message_id": str(submission.message_id),
                        "status": "retry",
                        "error_code": "DB-E006",
                    }
                ), 503
            status_code = (
                201
                if ack.status == "inserted"
                else 200
                if ack.status == "duplicate"
                else 400
            )
            return jsonify(ack.to_dict()), status_code
        if ack and ack.status in COMMITTED_ACK_STATUSES:
            session.pop("manual_csrf_token", None)
            flash(
                "PostgreSQLへの保存をACKで確認しました。",
                "warning" if validation.danger_sensors else "success",
            )
            today = datetime.now(JST).strftime("%Y-%m-%d")
            return redirect(
                url_for(
                    "index",
                    start_date=today,
                    end_date=today,
                    display_mode="table",
                    device_id=str(submission.target_device_id),
                )
            )
        validation.errors.append(
            "保存結果を確認できません。message_idを保持して再試行してください。"
        )

    if request.method == "POST" and is_json and submission.needs_confirmation:
        return jsonify(
            {
                "protocol_version": PROTOCOL_VERSION,
                "message_id": str(submission.message_id),
                "status": "confirmation_required",
                "danger_reasons": [
                    DANGER_LABELS.get(name, name) for name in validation.danger_sensors
                ],
            }
        ), 409
    if request.method == "POST" and is_json and validation.errors:
        return jsonify(
            {
                "protocol_version": PROTOCOL_VERSION,
                "message_id": str(submission.message_id),
                "status": "rejected",
                "error_code": "DATA-E001",
                "errors": validation.errors,
            }
        ), 400

    devices = repository().list_devices()
    return render_template(
        "insert.html",
        csrf_token=_manual_csrf_token(),
        sensor_columns=SENSOR_COLUMNS,
        sensor_labels=SENSOR_LABELS,
        plausible_ranges=PHYSICAL_RANGES,
        form_values=validation.display_values,
        errors=validation.errors,
        dangers=validation.danger_sensors,
        requires_confirmation=submission.needs_confirmation,
        message_id=str(submission.message_id),
        selected_device_id=submission.target_text,
        measured_at=submission.measured_text,
        devices=devices,
        identity=g.identity,
        system_version=SYSTEM_VERSION,
    )


def _parse_dashboard_filters() -> DashboardFilters:
    """クエリ文字列をダッシュボード検索条件へ正規化する。"""
    start_raw = request.args.get("start_date", "")
    end_raw = request.args.get("end_date", "")
    display_mode = request.args.get("display_mode", "table")
    if display_mode not in DISPLAY_MODES:
        display_mode = "table"
    average_source = request.args.get("average_source", "sensor")
    if average_source not in AVERAGE_SOURCES:
        average_source = "sensor"

    selected_sensors = [
        name for name in request.args.getlist("sensors") if name in SENSOR_COLUMNS
    ]
    if not selected_sensors:
        selected_sensors = list(SENSOR_COLUMNS)

    device_text = request.args.get("device_id", "")
    try:
        device_id = UUID(device_text) if device_text else None
    except ValueError:
        device_id = None
        device_text = ""

    return DashboardFilters(
        start_raw=start_raw,
        end_raw=end_raw,
        start_at=_parse_boundary(start_raw),
        end_at=_parse_boundary(end_raw, is_end=True),
        display_mode=display_mode,
        average_source=average_source,
        selected_sensors=selected_sensors,
        device_id=device_id,
        device_text=device_text,
    )


def _prepare_reading_presentation(
    rows: list[dict[str, Any]], selected_sensors: list[str]
) -> ReadingPresentation:
    """PostgreSQLの行をAPI・表・グラフ共通の表示材料へ変換する。"""
    chart_data = {"timestamp": [], **{name: [] for name in selected_sensors}}
    empty_table = "<div class='alert alert-warning'>該当するデータがありません。</div>"
    df = pd.DataFrame(rows)
    if df.empty:
        return ReadingPresentation(chart_data, empty_table, [], [], [], None, [])

    df = df.rename(columns={"measured_at": "timestamp"})
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(JST)
    df["door_open"] = (
        pd.to_numeric(df.get("joystick_x"), errors="coerce")
        .abs()
        .gt(DOOR_OPEN_THRESHOLD)
        .astype(int)
    )

    ordered = df.sort_values("timestamp")
    trigger_list = ordered["trigger"].fillna("").astype(str).tolist()
    door_list = ordered["door_open"].astype(int).tolist()
    danger_list = [
        evaluate_danger_row(
            temp=row.get("temp"),
            co2=row.get("co2"),
            pressure=row.get("pressure"),
            sound=row.get("sound_raw"),
            light=row.get("light_raw"),
        )
        for _, row in ordered.iterrows()
    ]
    latest = ordered.iloc[-1]
    latest_thi = calculate_thi(latest.get("temp"), latest.get("hum"))
    latest_dangers = [DANGER_LABELS.get(name, name) for name in danger_list[-1]]

    chart_columns = [
        name
        for name in (
            "timestamp",
            "device_id",
            "display_name",
            "source_type",
            "door_open",
            *selected_sensors,
        )
        if name in ordered.columns
    ]
    chart = ordered[chart_columns].copy()
    chart["timestamp"] = chart["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
    if "device_id" in chart.columns:
        chart["device_id"] = chart["device_id"].astype(str)
    chart_data = chart.where(pd.notna(chart), "Null").to_dict(orient="list")

    table_columns = [
        name
        for name in (
            "timestamp",
            "display_name",
            "source_type",
            "trigger",
            "door_open",
            *selected_sensors,
        )
        if name in df.columns
    ]
    table = (
        df.sort_values("timestamp", ascending=False)
        .head(TABLE_ROW_LIMIT)[table_columns]
        .copy()
    )
    table["timestamp"] = table["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S%z")
    data_html = table.where(pd.notna(table), "Null").to_html(
        classes="table table-striped", index=False, escape=True
    )
    return ReadingPresentation(
        chart_data,
        data_html,
        trigger_list,
        door_list,
        danger_list,
        latest_thi,
        latest_dangers,
    )


@app.route("/api/v3/readings")
@app.route("/")
def index():
    filters = _parse_dashboard_filters()
    devices = repository().list_devices()
    selected_device = next(
        (item for item in devices if str(item["device_id"]) == filters.device_text),
        None,
    )
    averages = []
    if filters.display_mode == "average":
        averages = repository().averages(
            sensors=filters.selected_sensors,
            device_id=filters.device_id,
            start_at=filters.start_at,
            end_at=filters.end_at,
            include_manual=filters.average_source == "all",
        )
        for item in averages:
            item["label"] = SENSOR_LABELS[item["sensor"]]
    rows = repository().fetch_readings(
        device_id=filters.device_id,
        start_at=filters.start_at,
        end_at=filters.end_at,
        limit=(
            FILTERED_ROW_LIMIT
            if filters.start_at or filters.end_at
            else TABLE_ROW_LIMIT
        ),
    )
    presentation = _prepare_reading_presentation(rows, filters.selected_sensors)

    if _looks_like_api_client():
        return jsonify(
            {
                "status": "success",
                "system_version": SYSTEM_VERSION,
                "protocol_version": PROTOCOL_VERSION,
                "schema_version": SCHEMA_VERSION,
                "device_id": filters.device_text or None,
                "devices": devices,
                "sensor_data": presentation.chart_data,
                "average_results": averages,
                "average_source": filters.average_source,
                "trigger": presentation.trigger_list,
                "door_open": presentation.door_list,
                "danger": presentation.danger_list,
                "latest_thi": presentation.latest_thi,
                "is_dangerous": bool(presentation.latest_dangers),
                "danger_reasons": presentation.latest_dangers,
            }
        )

    door_banner = (
        "<div class='alert alert-warning'><strong>ドア開放中</strong></div>"
        if presentation.door_list and presentation.door_list[-1]
        else ""
    )
    danger_banner = (
        "<div class='alert alert-danger'><strong>危険値:</strong> "
        + "、".join(presentation.latest_dangers)
        + "</div>"
        if presentation.latest_dangers
        else ""
    )
    return render_template(
        "index.html",
        data_html=presentation.data_html,
        all_columns=SENSOR_COLUMNS,
        selected_sensors=filters.selected_sensors,
        display_mode=filters.display_mode,
        average_results=averages,
        average_source=filters.average_source,
        chart_data=presentation.chart_data,
        start_date=filters.start_raw,
        end_date=filters.end_raw,
        door_banner=door_banner,
        danger_banner=danger_banner,
        storage_banner="<div class='alert alert-secondary py-1 px-2 small'>データソース: PostgreSQL（Ver3正本）</div>",
        latest_thi=presentation.latest_thi,
        latest_dangerous=bool(presentation.latest_dangers),
        latest_danger_reasons=presentation.latest_dangers,
        devices=devices,
        selected_device_id=filters.device_text,
        selected_device=selected_device,
        identity=g.identity,
        csrf_token=_manual_csrf_token(),
        system_version=SYSTEM_VERSION,
    )


if __name__ == "__main__":
    app.logger.info(
        "IoT環境監視システム Ver.%s %s / PostgreSQL / protocol=%d / schema=%d",
        SYSTEM_VERSION,
        BUILD_CHANNEL,
        PROTOCOL_VERSION,
        SCHEMA_VERSION,
    )
    try:
        repository()
        port = require_port("FLASK_PORT", "CFG-W006")
    except (ConfigurationError, DatabaseError) as exc:
        raise SystemExit(str(exc)) from None
    app.run(
        host=os.environ.get("FLASK_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=port,
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
