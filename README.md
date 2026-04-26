# Audit Tools Backend

FastAPI backend for the Audit Tools platform. This repository serves two product
namespaces from one codebase:

- `YEE`: full `User`-backed authentication, onboarding, approvals, invites,
  dashboard, reporting, and submission workflows
- `Playspace`: shared-core dashboard and audit APIs plus a lightweight
  account-based mobile auth bootstrap used by the current mobile client

## What This Repo Owns

- shared SQLAlchemy models and product-scoped database access
- Alembic migrations for both `yee` and `playspace`
- product REST routes under `/yee/*` and `/playspace/*`
- YEE auth, onboarding, invite, reporting, and export flows
- Playspace audit session, assignment, dashboard, and management flows
- deterministic seed data for local development and integration tests

## Product Split

The most important integration boundary in this repository is auth:

- `YEE auth`: implemented with the `users` table in `app/auth.py`
- `Playspace auth`: uses the same signed `User` session model for
  `/playspace/auth/signup`, `/playspace/auth/login`, `/playspace/auth/me`,
  and downstream Playspace product routes, with `x-demo-*` actor headers kept
  only as a temporary compatibility fallback in `app/core/actors.py`

That split is intentional for now. Do not assume a change in one product's auth
flow is automatically safe for the other.

## Current Status

Implemented today:

- shared-core account, project, place (with `address` field), auditor-profile, assignment, and audit models
- YEE real auth with email verification, approvals, invite acceptance, and session state
- Playspace normalized audit storage and scoring-backed draft/submit flows
- manager/admin Playspace dashboards and management APIs
- manager multi-user auth: each manager profile now gets a dedicated `User` record
- Playspace enum types for structured field values
- YEE instrument metadata enrichment for section intros, comment prompts, and grouped question rendering
- manager-scoped YEE edit APIs for projects and places
- seeded test data and product-scoped migration support

Still evolving:

- richer settings and lifecycle management
- production automation around migrations and release verification
- removal of the remaining `x-demo-*` compatibility fallback once all clients stop sending it
- final reporting polish and cap-score decisions

## Repository Layout

```text
audit-tools-backend/
├── alembic/
├── app/
│   ├── auth.py
│   ├── auth_security.py
│   ├── core/
│   ├── database.py
│   ├── main.py
│   ├── models.py
│   ├── products/
│   │   ├── playspace/
│   │   └── yee/
│   └── seed.py
├── docs/
├── tests/
├── .env.example
├── README.md
├── SCHEMA.md
└── STRUCTURE.md
```

## Local Setup

1. Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

2. Create a local env file:

```bash
cp .env.example .env
```

3. Set product database URLs:

```env
DATABASE_URL_YEE=postgresql+asyncpg://postgres:postgres@localhost:5432/audit_tools_yee
DATABASE_URL_PLAYSPACE=postgresql+asyncpg://postgres:postgres@localhost:5432/audit_tools_playspace
AUTH_TOKEN_SECRET_KEY=change-me
AUTH_ACCESS_TOKEN_TTL_DAYS=7
AUTH_EMAIL_VERIFY_TTL_HOURS=24
AUTH_VERIFY_URL_TEMPLATE=http://localhost:3000/verify-email?token={token}
```

4. Apply migrations to both product databases:

```bash
alembic -x product=yee upgrade head
alembic -x product=playspace upgrade head
```

5. Seed demo data when needed:

```bash
./.venv/bin/python -m app.seed
```

Or seed one product only:

```bash
./.venv/bin/python -m app.seed --product yee
./.venv/bin/python -m app.seed --product playspace
```

6. Start the API:

```bash
uvicorn app.main:app --reload
```

Useful local URLs:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/yee/auth/login`
- `http://127.0.0.1:8000/yee/instrument`
- `http://127.0.0.1:8000/playspace/auth/login`
- `http://127.0.0.1:8000/playspace/instrument`

## Recent YEE API Additions

The latest YEE website iteration depends on a few backend behaviors that are
important for future engineers to preserve:

- `GET /yee/instrument` now returns section metadata used by the frontend to
  render:
  - section intro text
  - per-section optional comment prompts
  - block titles
  - item-kind hints used for grouped presence/condition question display
- `PATCH /yee/dashboard/projects/{project_id}` allows manager-scoped project edits
- `PATCH /yee/dashboard/places/{place_id}` allows manager-scoped place edits,
  including postal code and address updates

Those contracts are consumed directly by the Next.js frontend for:

- spreadsheet-aligned YEE wording and domain instructions
- conditional domain question rendering
- manager project/place edit flows with persistence
- review/report pages that depend on richer score and instrument context

## Seeded Demo Credentials

The current seed flows use the shared demo password:

- `DemoPass123!`

This applies to seeded auth-capable demo accounts used in local development and
integration tests.

## Testing

Fast checks:

```bash
./.venv/bin/python -m py_compile app/auth.py app/seed.py
./.venv/bin/pytest tests/test_auth_security.py
```

Playspace integration coverage uses a dedicated test database:

```bash
TEST_DATABASE_URL_PLAYSPACE=postgresql://... ./.venv/bin/pytest tests/products/playspace
```

## Deployment Notes

- Run migrations for **both** products before serving new code
- The checked-in `render.yaml` starts the app but does not itself guarantee that
  Alembic ran, so your deployment process must include the migration step
- Treat one-way compatibility migrations as production operations: back up first

Recommended release sequence:

1. Deploy code
2. Run `alembic -x product=yee upgrade head`
3. Run `alembic -x product=playspace upgrade head`
4. Verify `/health`, auth, and one product-specific flow per namespace

## Documentation Map

- `docs/architecture.md`: product boundaries, runtime model, and request flows
- `docs/deployment.md`: production setup and migration runbook
- `docs/scoring.md`: YEE scoring behavior
- `docs/roles-and-permissions.md`: role matrix
- `SCHEMA.md`: current schema reference
- `STRUCTURE.md`: code organization

## High-Risk Areas

When changing this repository, double-check:

- `app/auth.py`: product-aware auth behavior
- `app/models.py` and `alembic/versions/`: schema/code alignment
- `app/core/actors.py`: Playspace header-based actor resolution
- `app/products/playspace/seed_data.py` and `app/seed.py`: demo credentials and seeded contracts
- `app/products/playspace/schemas/` and `app/products/playspace/services/`: Playspace API contracts consumed by mobile
- `tests/products/playspace/`: API contract coverage for the mobile-facing surface
