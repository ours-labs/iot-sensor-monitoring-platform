# Changelog

## Ver3

- Replaced CSV server persistence with PostgreSQL.
- Added versioned payloads, durable device identity, sequence tracking, `message_id` idempotency, and commit-after-ACK delivery.
- Added a Raspberry Pi SQLite outbox with safe retry behavior.
- Added multi-device querying, manual readings, time-window averages, and monitoring for missing or implausible data.
- Added protected administration and limited device-control workflows with safe defaults.
- Added Pi4gpio and direct hardware backends with explicit cutover and rollback checks.
- Added an Android Ver3 client and isolated continuous integration for Python, PostgreSQL, dependency security, and Android.

Ver1 and Ver2 are not part of this public repository.
