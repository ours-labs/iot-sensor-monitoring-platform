# Sensor Monitoring Ver3

This directory contains the Ver3 Raspberry Pi client, TCP ingestion server, PostgreSQL persistence layer, Flask dashboard, monitoring logic, and deployment templates.

Ver3 uses system version `3.0.0`, protocol version `3`, and database schema version `3`. A mismatch fails closed. The supported hardware path uses the Pi4gpio backend; the direct backend remains an explicit fallback.

## Core behavior

- PostgreSQL is the server-side source of truth.
- `message_id` and commit-after-ACK provide at-least-once delivery with deduplication.
- Each device has an immutable `device_id`, a mutable display name, declared capabilities, and a persistent `device_seq`.
- The Raspberry Pi stores unsent messages in a SQLite outbox and retries them safely.
- Measurement, receipt, and storage timestamps are recorded separately.
- Browser and Android clients can query readings and time-window averages.
- Manual readings preserve operator attribution, validate physical ranges, and store missing values as `NULL`.
- Monitoring covers implausible values, missing data, communication gaps, and sequence anomalies.

## Data flow

```text
sensor_client_tiered.py + SQLite outbox
  -> protocol_version=3 JSON
sensor_server.py
  -> PostgreSQL commit
  <- inserted / duplicate / rejected / retry ACK
Flask application and Android client
monitor.py
  -> per-device quality and availability checks
```

## Configuration

Real addresses, domains, user identities, API keys, tokens, and local home paths are excluded. A fresh clone intentionally refuses to start until configuration is supplied.

1. Use `env/*.env.example` and `web-app/.env.example` as templates.
2. Store real values outside Git, preferably in deployment-specific environment files.
3. Apply the PostgreSQL migrations and register devices and operators with the administrative tools.
4. Adapt the generic service templates to the deployment environment.
5. Configure the Android host in the user's Gradle properties, never in committed source.

See [ERROR_CODES.md](ERROR_CODES.md) for stable configuration and runtime error codes, [PUBLIC_CONTRACT.md](PUBLIC_CONTRACT.md) for shared interfaces, and [POSTGRES_OPERATIONS.md](POSTGRES_OPERATIONS.md) for database procedures.

## Limited administration

The shared administrative password is stored only as a hash in an external environment file. Elevated sessions expire after inactivity and have an absolute lifetime. Restart and shutdown actions require reauthentication and explicit confirmation.

The device control agent persists request IDs and events in SQLite. `CONTROL_DRY_RUN=true` is the safe default; real power operations require an explicit operating-system allowlist and deployment approval.

## Pi4gpio backend

Set `RPI_SENSOR_BACKEND=direct` or `RPI_SENSOR_BACKEND=pi4gpio`. The Pi4gpio daemon owns GPIO, I2C, SPI, and UART resources and exposes a Unix socket to the client. A deployment should verify queue delivery, ACKs, socket availability, process ownership, and sensor plausibility before accepting a backend change.

## Test

Create and use a project-local virtual environment, then run:

```bash
python -m unittest discover -s . -p "test_*.py"
python tools/verify_ver3_boundary.py
python -m compileall -q .
python -m pip check
```

PostgreSQL integration tests require `TEST_DATABASE_URL`. The CI workflow supplies a disposable PostgreSQL service automatically.
