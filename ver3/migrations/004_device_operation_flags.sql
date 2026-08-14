BEGIN;

ALTER TABLE devices
    ADD COLUMN IF NOT EXISTS control_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS monitoring_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS last_control_poll_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_devices_control_enabled
    ON devices(control_enabled, status, last_control_poll_at);
CREATE INDEX IF NOT EXISTS idx_devices_monitoring_enabled
    ON devices(monitoring_enabled, status);

COMMIT;
