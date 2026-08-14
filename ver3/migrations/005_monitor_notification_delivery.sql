BEGIN;

ALTER TABLE monitor_events
    ADD COLUMN IF NOT EXISTS notification_required BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS notification_attempts INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS next_notification_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS notified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS notification_last_error TEXT;

CREATE INDEX IF NOT EXISTS idx_monitor_events_notification_pending
    ON monitor_events(notification_required, next_notification_at, event_id)
    WHERE notified_at IS NULL;

COMMIT;
