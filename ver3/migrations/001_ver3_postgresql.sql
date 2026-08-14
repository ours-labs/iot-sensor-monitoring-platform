BEGIN;

CREATE TABLE IF NOT EXISTS schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO schema_metadata(key, value)
VALUES ('schema_version', '3')
ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS devices (
    device_id UUID PRIMARY KEY,
    display_name TEXT NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 80),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'retired')),
    capability_version INTEGER NOT NULL DEFAULT 1 CHECK (capability_version >= 1),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS device_capabilities (
    device_id UUID NOT NULL REFERENCES devices(device_id),
    sensor_code TEXT NOT NULL CHECK (sensor_code IN (
        'light_raw', 'light_voltage', 'sound_raw', 'joystick_x', 'joystick_y',
        'potentiometer_percent', 'temp', 'hum', 'pressure', 'co2'
    )),
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    required BOOLEAN NOT NULL DEFAULT TRUE,
    unit TEXT NOT NULL,
    expected_interval_sec INTEGER NOT NULL CHECK (expected_interval_sec > 0),
    plausible_min DOUBLE PRECISION,
    plausible_max DOUBLE PRECISION,
    capability_version INTEGER NOT NULL CHECK (capability_version >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (device_id, sensor_code),
    CHECK (plausible_min IS NULL OR plausible_max IS NULL OR plausible_min <= plausible_max)
);

CREATE TABLE IF NOT EXISTS operator_identities (
    principal_email TEXT PRIMARY KEY,
    operator_id TEXT NOT NULL UNIQUE CHECK (operator_id ~ '^TK[0-9]{6}$'),
    display_name TEXT NOT NULL CHECK (char_length(display_name) BETWEEN 1 AND 80),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (principal_email, operator_id),
    CHECK (principal_email = lower(principal_email))
);

CREATE TABLE IF NOT EXISTS api_credentials (
    credential_id UUID PRIMARY KEY,
    credential_type TEXT NOT NULL CHECK (credential_type IN ('device', 'browser', 'android')),
    device_id UUID REFERENCES devices(device_id),
    principal_email TEXT REFERENCES operator_identities(principal_email),
    operator_id TEXT,
    credential_hash TEXT NOT NULL UNIQUE CHECK (char_length(credential_hash) = 64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMPTZ,
    CHECK ((credential_type = 'device'
            AND device_id IS NOT NULL
            AND principal_email IS NULL
            AND operator_id IS NULL)
        OR (credential_type <> 'device'
            AND device_id IS NULL
            AND principal_email IS NOT NULL
            AND operator_id IS NOT NULL)),
    FOREIGN KEY (principal_email, operator_id)
        REFERENCES operator_identities(principal_email, operator_id)
);

CREATE TABLE IF NOT EXISTS ingest_messages (
    message_id UUID PRIMARY KEY,
    device_id UUID NOT NULL REFERENCES devices(device_id),
    payload_hash TEXT NOT NULL CHECK (char_length(payload_hash) = 64),
    status TEXT NOT NULL CHECK (status IN ('processing', 'inserted', 'rejected')),
    received_at TIMESTAMPTZ NOT NULL,
    stored_at TIMESTAMPTZ,
    error_code TEXT
);

CREATE TABLE IF NOT EXISTS sensor_readings (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message_id UUID NOT NULL UNIQUE REFERENCES ingest_messages(message_id),
    device_id UUID NOT NULL REFERENCES devices(device_id),
    device_seq BIGINT,
    measured_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    stored_at TIMESTAMPTZ NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('sensor', 'manual', 'legacy')),
    trigger TEXT NOT NULL,
    quality_state TEXT NOT NULL DEFAULT 'valid'
        CHECK (quality_state IN ('valid', 'warning', 'missing', 'out_of_order', 'clock_skew')),
    quality_detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    light_raw INTEGER CHECK (light_raw BETWEEN 0 AND 4095),
    light_voltage DOUBLE PRECISION CHECK (light_voltage BETWEEN 0.0 AND 3.3),
    sound_raw INTEGER CHECK (sound_raw BETWEEN 0 AND 4095),
    joystick_x DOUBLE PRECISION CHECK (joystick_x BETWEEN -1.0 AND 1.0),
    joystick_y DOUBLE PRECISION CHECK (joystick_y BETWEEN -1.0 AND 1.0),
    potentiometer_percent DOUBLE PRECISION CHECK (potentiometer_percent BETWEEN 0.0 AND 100.0),
    temp DOUBLE PRECISION CHECK (temp BETWEEN -40.0 AND 80.0),
    hum DOUBLE PRECISION CHECK (hum BETWEEN 0.0 AND 100.0),
    pressure DOUBLE PRECISION CHECK (pressure BETWEEN 300.0 AND 1100.0),
    co2 DOUBLE PRECISION CHECK (co2 BETWEEN 0.0 AND 10000.0),
    CHECK ((source_type = 'manual' AND device_seq IS NULL)
        OR (source_type <> 'manual' AND device_seq IS NOT NULL)),
    UNIQUE (device_id, device_seq)
);

CREATE INDEX IF NOT EXISTS idx_sensor_readings_device_measured
    ON sensor_readings(device_id, measured_at DESC);
CREATE INDEX IF NOT EXISTS idx_sensor_readings_received
    ON sensor_readings(received_at DESC);

CREATE TABLE IF NOT EXISTS manual_input_audit (
    message_id UUID PRIMARY KEY REFERENCES sensor_readings(message_id),
    principal_email TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    target_device_id UUID NOT NULL REFERENCES devices(device_id),
    warning_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS device_admin_audit (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    principal_email TEXT NOT NULL REFERENCES operator_identities(principal_email),
    device_id UUID NOT NULL REFERENCES devices(device_id),
    action TEXT NOT NULL CHECK (action IN ('display_name_updated')),
    before_state JSONB NOT NULL,
    after_state JSONB NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS control_commands (
    request_id UUID PRIMARY KEY,
    target_device_id UUID NOT NULL REFERENCES devices(device_id),
    command TEXT NOT NULL CHECK (command IN ('status', 'restart', 'shutdown')),
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'accepted', 'shutting_down', 'offline_confirmed',
        'completed', 'rejected', 'outcome_unknown'
    )),
    requested_by TEXT NOT NULL,
    requested_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS control_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id UUID NOT NULL REFERENCES control_commands(request_id),
    event_type TEXT NOT NULL,
    boot_id UUID,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS monitor_events (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    device_id UUID REFERENCES devices(device_id),
    event_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'critical')),
    state TEXT NOT NULL CHECK (state IN ('open', 'recovered', 'acknowledged')),
    detail JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

COMMIT;
