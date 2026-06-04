# Deployment

## Goal

Deploy one backend service that serves both product namespaces:

- `/yee/*`
- `/playspace/*`

The backend still talks to two separate PostgreSQL databases, one per product.

## Deployment Topology

```mermaid
flowchart LR
    Client["Web or Mobile Client"] --> Backend["FastAPI Backend"]
    Backend --> YEE_DB["YEE PostgreSQL"]
    Backend --> PLAYSPACE_DB["Playspace PostgreSQL"]
```

## Required Infrastructure

Backend requirements:

- Python runtime
- ASGI host
- environment variable management
- network access to both product databases

Database requirements:

- one database for `yee`
- one database for `playspace`

The backend expects:

- `DATABASE_URL_YEE`
- `DATABASE_URL_PLAYSPACE`

## Backend Environment Variables

Required:

- `DATABASE_URL_YEE`
- `DATABASE_URL_PLAYSPACE`
- `AUTH_TOKEN_SECRET_KEY`

Recommended:

- `AUTH_ACCESS_TOKEN_TTL_DAYS`
- `AUTH_EMAIL_VERIFY_TTL_HOURS`
- `AUTH_VERIFY_URL_TEMPLATE`

Optional but important in production:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`
- `SMTP_USE_TLS`
- `TURNSTILE_SECRET_KEY`

## Migration Runbook

The backend migration history is product-scoped **and branched**. A shared
`core` base revision (`0001`) holds the tables common to both products, and two
branches descend from it:

- `playspace` branch — Playspace-only tables (`playspace_*`)
- `yee` branch — YEE-only tables (`yee_audit_submissions`)

Each physical database only ever advances along its own branch, so the YEE
database never receives `playspace_*` tables and the Playspace database never
receives `yee_*` tables. Target the product branch head (not the bare `head`,
which is ambiguous with two heads):

```bash
alembic -x product=yee upgrade yee@head
alembic -x product=playspace upgrade playspace@head
```

### One-time cutover from the pre-branch history

If a database was previously migrated under the old single linear history
(through the squashed `0002`), re-point it onto the branched revision IDs with a
**stamp** — this changes only the `alembic_version` bookkeeping and runs **no
DDL**, so existing data is untouched:

```bash
# Playspace PRODUCTION (real data — stamp only, never re-run DDL):
alembic -x product=playspace -x environment=production stamp --purge playspace@head

# YEE is reset-friendly (seed data only): drop/recreate the schema and rebuild:
alembic -x product=yee -x environment=development upgrade yee@head
```

After stamping, `alembic -x product=playspace ... current` should report
`ps_0002 (head)`, and future Playspace changes are normal additive migrations on
the `playspace` branch.

Important notes:

- do not assume a successful code deploy means both databases are migrated
- several compatibility migrations are one-way and should be treated as
  production operations
- back up both databases before applying migration batches in shared-core merge windows

## Render Note

The checked-in `render.yaml` installs dependencies and starts `uvicorn`, but it
does not itself guarantee that Alembic ran first.

If you deploy on Render, make sure your release process includes the two product
migration commands above, either through:

- a release/pre-deploy command
- a CI job that runs before traffic is shifted
- a manual runbook step

If you skip that step, merged code can reach production before the matching
schema exists.

## Production Checklist

1. Provision both PostgreSQL databases
2. Set all backend environment variables
3. Run `alembic -x product=yee upgrade yee@head`
4. Run `alembic -x product=playspace upgrade playspace@head`
5. Start the backend service
6. Verify `/health`
7. Verify one YEE auth flow and one Playspace auth flow
8. Verify one YEE product flow and one Playspace product flow

## Verification Flow

YEE email verification requires:

- real SMTP delivery
- a valid `AUTH_VERIFY_URL_TEMPLATE`

Example:

```env
AUTH_VERIFY_URL_TEMPLATE=https://your-frontend-domain.example/verify-email?token={token}
```

## Suggested Release Validation

Minimum release smoke test:

- `GET /health`
- YEE login or invite acceptance
- Playspace login
- one YEE manager/admin dashboard call
- one Playspace dashboard or instrument call
- one migration status check per database

## Security Notes

- use a strong `AUTH_TOKEN_SECRET_KEY`
- use HTTPS for all public traffic
- never expose database credentials to client-side code
- keep authorization checks in backend services, not only in the UI
- prefer failing a deploy on migration mismatch over serving schema-drifted code
