# Ver3 error codes

Error codes are stable public identifiers. Messages may evolve, but integrations should branch on the code.

## Configuration

| Code | Meaning |
|---|---|
| `CFG-C001` | `SENSOR_HOST` is missing. |
| `CFG-C002` | `SENSOR_PORT` is missing or invalid. |
| `CFG-C003` | `DEVICE_ID` is missing or is not a UUID. |
| `CFG-C004` | `DEVICE_TOKEN` is missing. |
| `CFG-C005` | `EDGE_QUEUE_PATH` is missing. |
| `CFG-C006` | `RPI_SENSOR_BACKEND` is not `direct` or `pi4gpio`. |
| `CFG-C007` | The Pi4gpio socket path is not absolute. |
| `CFG-C010` | The Ver3 virtual-environment interpreter is missing. |
| `CFG-C011` | The required client wheel is missing. |
| `CFG-C012` | The file-descriptor inspection tool is unavailable. |
| `CFG-C014` | The Ver3 Pi4gpio service override is missing. |
| `CFG-W002` | Flask `SECRET_KEY` is missing. |
| `CFG-W003` | `ACCESS_TRUSTED_HOST` is missing. |
| `CFG-W006` | `FLASK_PORT` is missing or invalid. |
| `CFG-W007` | Administration is disabled because `ADMIN_PASSWORD_HASH` is missing. |
| `CFG-W008` | Administrative password confirmation does not match. |
| `CFG-W009` | The administrative password is shorter than 12 characters. |
| `CFG-D001` | `DATABASE_URL` is missing. |
| `CFG-D002` | The database URL is not PostgreSQL. |
| `CFG-D003` | `TOKEN_HASH_KEY` is missing or shorter than 32 bytes. |
| `CFG-A001` | Android `SENSOR_SERVER_HOST` is missing. |
| `CFG-R001` | Required device-control configuration is incomplete. |
| `CFG-R002` | The device-control device ID is not a UUID. |
| `CFG-R003` | The device-control URL is not HTTPS. |
| `CFG-S001` | A required service path or virtual environment is not ready. |

## Authentication and database

| Code | Meaning |
|---|---|
| `AUTH-A001` | An external access layer returned an HTML login page instead of API JSON. |
| `AUTH-E001` | The API key or authenticated session is missing or invalid. |
| `AUTH-E002` | Device identity and credentials do not match. |
| `AUTH-E003` | The operator or target device is unregistered or inactive. |
| `AUTH-E004` | The pairing request did not arrive through the trusted access path. |
| `AUTH-E006` | Administrative or dangerous-operation reauthentication failed. |
| `AUTH-E007` | The administrative authentication failure limit was reached. |
| `DB-E001` | The PostgreSQL driver is unavailable. |
| `DB-E002` | A non-PostgreSQL database was requested. |
| `DB-E003` | The Ver3 schema could not be verified. |
| `DB-E004` | The database schema version does not match. |
| `DB-E005` | The duplicate record could not be retrieved. |
| `DB-E006` | Commit confirmation failed; retry with the same `message_id`. |

## Data, transport, and hardware

| Code | Meaning |
|---|---|
| `DATA-E001` | Manual-reading time, device, or sensor data is invalid. |
| `DATA-E002` | The display name is empty or longer than 80 characters. |
| `DATA-E003` | A device-control request field is invalid. |
| `DATA-E005` | A direct-backend test start time is not timezone-aware ISO 8601. |
| `DATA-E006` | Test duration, interval, or tolerance is invalid. |
| `DATA-E007` | The physical sensor device cannot be identified uniquely. |
| `DEP-E001` | A client wheel SHA-256 does not match. |
| `DEP-E002` | The installed client version does not match. |
| `MSG-E001` | The TCP payload is not valid JSON. |
| `MSG-E002` | One `message_id` was reused with different content. |
| `MSG-E012` | The receive-size limit was exceeded. |
| `MSG-E013` | A device sequence number was already used by another request. |
| `CAP-E001` | A value was supplied for an unavailable or disabled sensor. |
| `ACK-E001` | ACK validation failed; the message remains in the SQLite outbox. |
| `HW-E001` | A physical device remains open after the direct client stops. |
| `HW-E002` | The Ver3 client directly owns a physical-device descriptor under Pi4gpio. |
| `HW-E003` | Pi4gpio socket creation timed out. |
| `CTL-W001` | Device control could not synchronize and will retry from the outbox. |

Additional `CTL-E*` codes identify failed control or service-state preconditions. The accompanying message describes the failed invariant without exposing secret values.
