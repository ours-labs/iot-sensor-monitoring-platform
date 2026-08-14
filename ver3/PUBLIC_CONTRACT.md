# Ver3 public contract

The public contract is the boundary shared by the Raspberry Pi client, ingestion server, PostgreSQL schema, browser application, and Android client. Changing only one side can break transport, persistence, retries, authentication, or presentation.

## Raspberry Pi payload

The versioned JSON payload includes `protocol_version`, `message_id`, `device_id`, `device_token`, `device_seq`, `measured_at`, `trigger`, `sensors`, and the supported sensor fields.

## Server ACK

The ACK contains `status`, `message_id`, `device_id`, `received_at`, `stored_at`, and `error_code`. Status is one of `inserted`, `duplicate`, `rejected`, or `retry`. A success ACK is sent only after the database commit completes.

## Web and Android API

Stable API paths include `/api/v3/status`, `/api/v3/readings`, `/api/v3/manual-readings`, `/api/v3/devices`, and `/api/v3/device-control/*`. HTTP status codes, JSON field names, and stable error codes are part of the contract.

## Persistence and configuration

Migration-defined table names, columns, types, constraints, `message_id` idempotency, and `device_id` plus `device_seq` ordering are contract elements. Environment variable names and system service names are also operational interfaces.

Internal local variables, private helper functions, dataclasses, named tuples, constants, formatting, comments, and type annotations may change without a protocol version change when externally visible behavior remains identical.

When a public contract changes, define the protocol, schema, and system version plus its migration procedure before changing individual components. Validate the Raspberry Pi, server, Web, Android, database, and retry paths together.
