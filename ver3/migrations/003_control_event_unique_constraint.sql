BEGIN;

DROP INDEX IF EXISTS uq_control_events_client_event_id;
CREATE UNIQUE INDEX uq_control_events_client_event_id
    ON control_events(client_event_id);

COMMIT;
