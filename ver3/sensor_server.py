"""IoT環境監視システム Ver.3 ACK付きTCP受信サーバー。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from contextlib import suppress

from config_errors import ConfigurationError, require_env, require_port
from database import DatabaseError, PostgresRepository, SchemaBoundaryError
from protocol_v3 import Ack, ProtocolError, parse_envelope
from system_identity import (
    BUILD_CHANNEL,
    PROTOCOL_VERSION,
    SCHEMA_VERSION,
    SYSTEM_VERSION,
)

try:
    PORT = require_port()
    DEFAULT_HOST = require_env("SENSOR_HOST", "CFG-C001")
except ConfigurationError as exc:
    raise SystemExit(str(exc)) from None

MAX_LINE_BYTES = 64 * 1024
logger = logging.getLogger("sensor_server_v3")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def ack_bytes(ack: Ack) -> bytes:
    return (json.dumps(ack.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


async def write_ack(writer: asyncio.StreamWriter, ack: Ack) -> None:
    writer.write(ack_bytes(ack))
    await writer.drain()


def rejected_ack(
    error: ProtocolError,
    fallback_message_id: str | None = None,
) -> Ack:
    return Ack(
        protocol_version=PROTOCOL_VERSION,
        message_id=error.message_id or fallback_message_id,
        status="rejected",
        error_code=error.code,
        detail=error.detail,
    )


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    repository: PostgresRepository,
) -> None:
    peer = writer.get_extra_info("peername")
    message_id: str | None = None
    try:
        try:
            data = await reader.readuntil(b"\n")
        except asyncio.LimitOverrunError:
            await write_ack(
                writer,
                Ack(PROTOCOL_VERSION, None, "rejected", error_code="MSG-E012", detail="要求が大きすぎます"),
            )
            return
        except asyncio.IncompleteReadError as exc:
            data = exc.partial

        if not data:
            return
        if len(data) > MAX_LINE_BYTES:
            await write_ack(
                writer,
                Ack(PROTOCOL_VERSION, None, "rejected", error_code="MSG-E012", detail="要求が大きすぎます"),
            )
            return
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await write_ack(
                writer,
                Ack(PROTOCOL_VERSION, None, "rejected", error_code="MSG-E001", detail="JSON形式が不正です"),
            )
            return

        if isinstance(payload, dict):
            message_id = str(payload.get("message_id") or "") or None
        try:
            envelope = parse_envelope(payload)
        except ProtocolError as exc:
            await write_ack(writer, rejected_ack(exc, message_id))
            logger.warning("Ver3要求を拒否: code=%s message_id=%s peer=%s", exc.code, message_id, peer)
            return

        try:
            ack = await asyncio.to_thread(repository.insert_sensor, envelope)
        except Exception:
            logger.exception("PostgreSQL保存失敗: message_id=%s peer=%s", message_id, peer)
            ack = Ack(
                protocol_version=PROTOCOL_VERSION,
                message_id=message_id,
                status="retry",
                device_id=str(envelope.device_id),
                error_code="DB-E006",
                detail="一時的に保存を確認できません",
            )
        await write_ack(writer, ack)
        logger.info(
            "ACK送信: status=%s message_id=%s device_id=%s seq=%s peer=%s",
            ack.status,
            ack.message_id,
            envelope.device_id,
            envelope.device_seq,
            peer,
        )
    except (ConnectionError, asyncio.TimeoutError):
        logger.warning("ACK送信前に接続終了: message_id=%s peer=%s", message_id, peer)
    finally:
        writer.close()
        with suppress(ConnectionError):
            await writer.wait_closed()


async def run_server(host: str, repository: PostgresRepository) -> None:
    server = await asyncio.start_server(
        lambda reader, writer: handle_client(reader, writer, repository),
        host,
        PORT,
        limit=MAX_LINE_BYTES + 1,
    )
    logger.info(
        "IoT環境監視システム Ver.%s %s / protocol=%d / schema=%d",
        SYSTEM_VERSION,
        BUILD_CHANNEL,
        PROTOCOL_VERSION,
        SCHEMA_VERSION,
    )
    logger.info("Ver3 ACKサーバー起動: host=%s port=%s database=PostgreSQL", host, PORT)
    async with server:
        await server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser(description="Ver3 ACK付きセンサー受信サーバー")
    parser.add_argument("-p", dest="ip_address", default=DEFAULT_HOST)
    args = parser.parse_args()
    try:
        repository = PostgresRepository()
        repository.open()
    except (ConfigurationError, DatabaseError, SchemaBoundaryError) as exc:
        raise SystemExit(str(exc)) from None
    try:
        asyncio.run(run_server(args.ip_address, repository))
    finally:
        repository.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
