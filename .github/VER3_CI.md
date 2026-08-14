# Ver3 CI

`workflows/ver3-ci.yml` validates Ver3 without connecting to production servers, physical Raspberry Pi devices, or external access infrastructure.

## Jobs

### Python / PostgreSQL / 85 devices

- Starts a disposable PostgreSQL 16 service container.
- Runs the Python unit and integration test suite on Python 3.12.
- Checks the Ver3 protocol and schema boundary, Python compilation, dependency consistency, shell syntax, and public-information policy.
- Exercises concurrent writes and reads from 85 logical devices and verifies `message_id` deduplication.

### Dependency and Python security

- Audits published dependencies with `pip-audit`.
- Runs Ruff `F` checks for undefined names and unused imports.
- Fails on high-severity Bandit findings.

### Android unit / lint / build

- Runs `testDebugUnitTest`, `lintDebug`, and `assembleDebug`.
- Uploads test and lint reports as workflow artifacts.

## Security boundary

The workflow has read-only repository permissions and uses disposable database credentials. `TOKEN_HASH_KEY` is a test-only non-secret value containing the workflow run ID; production key material is never passed to GitHub Actions. The workflow requires no repository secrets and must not contact production databases, hosts, external access services, or physical devices.
