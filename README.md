## Audit Tools Backend (FastAPI + SQLAlchemy + Postgres)

### Client channels and role intent

- **Playspace mobile app**: mobile workflow for auditors to complete assigned Playspace field audits with typed session payloads and raw score totals.
- **YEE mobile app**: mobile workflow for auditors to complete assigned field audits
  (offline-first).
- **Manager workflows**: web experience for project/place configuration and management.
- Backend role model supports both `MANAGER` and `AUDITOR`, while the mobile UX is designed for auditor field workflows.
- Playspace and YEE live under separate product folders and route namespaces.

### Local setup (macOS + zsh)

Create and activate a virtualenv:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install runtime dependencies:

```bash
python -m pip install -r requirements.txt
```

Install developer tooling (linting/tests/pre-commit):

```bash
python -m pip install -r requirements-dev.txt
pre-commit install
```

### Database (Neon — default)

Create a local `.env` file for the app:

```bash
cp .env.example .env
```

Set product-specific database URLs in `.env`:

- `DATABASE_URL_YEE` (Youth Enabling Environment)
- `DATABASE_URL_PLAYSPACE` (Playspace Play Value + Usability)

You can paste Neon’s standard `postgresql://...` URL directly — `app/database.py` will normalize it for async SQLAlchemy and enable SSL.

### Migrations (Alembic)

Apply migrations to each product database:

```bash
alembic -x product=yee upgrade head
alembic -x product=playspace upgrade head
```

### Seed demo data

Populate the shared-core dashboard hierarchy for both products:

```bash
./.venv/bin/python -m app.seed
```

Seed only one product database:

```bash
./.venv/bin/python -m app.seed --product yee
./.venv/bin/python -m app.seed --product playspace
```

The seed script inserts deterministic accounts, manager profiles, projects,
places, auditors, assignments, and audit records for both products.

For Playspace specifically, the seed flow now:

- builds realistic audit response payloads from the scoring metadata
- hydrates normalized Playspace audit relations from those payloads
- computes stored Playspace score totals from normalized rows
- keeps `responses_json` and `scores_json` populated as transitional compatibility caches

### Playspace storage and scoring notes

Playspace audit execution now uses normalized product-specific tables:

- `playspace_audit_contexts`
- `playspace_pre_audit_answers`
- `playspace_audit_sections`
- `playspace_question_responses`
- `playspace_scale_answers`

The shared `audits.responses_json` and `audits.scores_json` JSONB columns are
still present as compatibility caches, but Playspace writes normalized rows
first and scores directly from those rows.

Current Playspace scoring stores raw totals rather than percent buckets:

- `quantity_total`
- `diversity_total`
- `challenge_total`
- `sociability_total`
- `play_value_total`
- `usability_total`

For Playspace list views, `summary_score` is the compact combined construct
total: `play_value_total + usability_total`.

### Run the API (FastAPI)

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Deploy (Render)

Render requires your web service to **bind to** `0.0.0.0` and the port provided in `$PORT` (not `127.0.0.1`).

- **Start command**:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

- **Health check path**: `/health`
- **Config**: a minimal `render.yaml` is included so you can deploy via a blueprint if you want.
- **Docs**: [Render port binding](https://render.com/docs/web-services#port-binding)

### REST endpoints

- **Global health**: `http://127.0.0.1:8000/health`
- **Playspace API root namespace**: `http://127.0.0.1:8000/playspace/*`
- **YEE namespace status**: `http://127.0.0.1:8000/yee/status`

### Database configuration

For local development, copy `.env.example` to `.env`. The app reads `DATABASE_URL_YEE` and `DATABASE_URL_PLAYSPACE` from that file (or falls back to local defaults in `app/database.py`).

### Useful commands

Run tests:

```bash
pytest
```

Targeted Playspace validation:

```bash
./.venv/bin/ruff check app/products/playspace
./.venv/bin/python -m py_compile app/products/playspace/scoring.py app/products/playspace/services/audit_sessions.py
./.venv/bin/python -m app.seed --product playspace
```

Lint and auto-fix:

```bash
ruff check . --fix
ruff format .
```

