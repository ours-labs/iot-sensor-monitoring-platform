BEGIN;

ALTER TABLE control_events
    ADD COLUMN IF NOT EXISTS client_event_id UUID;

CREATE UNIQUE INDEX IF NOT EXISTS uq_control_events_client_event_id
    ON control_events(client_event_id)
    WHERE client_event_id IS NOT NULL;

COMMIT;
