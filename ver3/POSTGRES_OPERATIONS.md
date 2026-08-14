# PostgreSQL operations

PostgreSQL is the only server-side source of truth for Ver3. SQLite is used only for the Raspberry Pi outbox and for explicit offline backup artifacts.

## Initial setup

1. Provision a supported PostgreSQL instance.
2. Create a dedicated database and least-privilege application role.
3. Store the connection URL outside Git as `DATABASE_URL`.
4. Apply migrations in numeric order with `tools/apply_migrations.py`.
5. Register devices and operators with `tools/register_device.py` and `tools/register_operator.py`.
6. Verify schema version `3` before starting server processes.

The application rejects missing or non-PostgreSQL URLs and refuses a schema-version mismatch.

## Migration from an earlier SQLite dataset

`tools/migrate_legacy_sqlite.py` performs an explicit, auditable import. Keep the source database read-only, back it up first, run the migration in a non-production database, compare row counts and representative records, and then repeat in the target environment during a controlled maintenance window.

Never place the source database or an export containing real readings in this repository.

## Backup

Use PostgreSQL-native backups for primary recovery. `tools/backup_postgres_to_sqlite.py` can create a portable SQLite snapshot for secondary inspection or offline recovery exercises. Store all backup files outside Git with restricted permissions and record their SHA-256 checksums separately.

## Restore verification

Restore into an isolated database first. Verify migrations, schema version, row counts, device identities, `message_id` uniqueness, sequence continuity, timestamps, and application queries before selecting the restored database as a production source.

`tools/restore_sqlite_snapshot.py` is intended for controlled recovery workflows. Use dry-run or validation modes where available and never overwrite the active database without a tested rollback path.

## Operational safeguards

- Use TLS and least-privilege roles appropriate to the deployment.
- Do not log full connection URLs or credentials.
- Run migrations with a backup and a rollback plan.
- Monitor connection pool exhaustion, failed commits, retry ACKs, sequence gaps, and database storage.
- Treat a successful process start as insufficient; verify end-to-end insert, ACK, query, and monitoring behavior.
