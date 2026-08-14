# IoT Sensor Monitoring Platform

A production-oriented Ver3 portfolio project for collecting environmental sensor data from Raspberry Pi devices, storing it reliably in PostgreSQL, and presenting it through a Flask dashboard and an Android application.

This repository contains the current Ver3 implementation.

## Architecture

```text
Raspberry Pi sensors
  -> SQLite outbox
  -> versioned JSON over TCP
  -> Python ingestion server
  -> PostgreSQL
       -> Flask dashboard and API
       -> Android client
       -> monitoring and limited device control
```

The delivery protocol uses `message_id`, `device_id`, and `device_seq` for at-least-once delivery, deduplication, and ordering checks. A successful ACK is returned only after the PostgreSQL transaction commits.

## Repository layout

- `ver3/`: Raspberry Pi client, ingestion server, PostgreSQL migrations, Flask application, monitoring, operations templates, and Python tests.
- `SensorDataApp-v3/`: Android client built with Kotlin and Jetpack Compose.
- `.github/workflows/ver3-ci.yml`: isolated PostgreSQL, Python, security, and Android CI.

## Highlights

- Ten sensor fields with measurement, receipt, and storage timestamps.
- Durable Raspberry Pi outbox and retry behavior.
- Multi-device identity and sequence tracking.
- PostgreSQL-backed API and browser dashboard.
- Android views for current readings, alerts, time-window averages, and manual readings.
- Conservative sensor plausibility checks and explicit `NULL` handling.
- API-key, session, and external-access integration boundaries.
- CI load exercise with 85 logical devices.

## Safety boundary

This repository contains no production credentials, device tokens, private hosts, sensor datasets, or generated APKs. A clone is deliberately non-operational until an administrator supplies environment-specific configuration outside Git.

Start with [the Ver3 server guide](ver3/README.md), [the Android guide](SensorDataApp-v3/README.md), and [the security policy](SECURITY.md).

The current dashboard and Android interface are primarily Japanese. A user-selectable language switch is planned; protocol fields, API identifiers, configuration keys, and stored data will remain language-neutral.

## Validation

```bash
cd ver3
python -m unittest discover -s . -p "test_*.py"
python tools/verify_ver3_boundary.py
python -m compileall -q .
python -m pip check
```

The GitHub Actions workflow also runs PostgreSQL integration and concurrency tests, dependency auditing, static security checks, and the Android unit/lint/build pipeline.

## License

Project-authored code and documentation are available under the [MIT License](LICENSE). Third-party dependencies retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
