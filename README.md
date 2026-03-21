# Audit Tools Backend

> FastAPI + SQLAlchemy backend for the audit platform.

This repository owns the shared account/project/place/auditor model plus product-specific backend behavior for:

| Product | Status |
|---|---|
| **Playspace** |
| **YEE** | Separate product track |

---

## Responsibilities

| In scope | Out of scope |
|---|---|
| Authentication/session support for platform users | Auditor mobile UI |
| Shared account, project, place, auditor, and assignment data | Future manager web UI |
| Playspace audit lifecycle (`access`, `draft`, `submit`, reporting payloads) | |
| Playspace normalized audit storage and scoring | |
| Seed/demo data and migrations | |

---

## Documentation Map

This backend uses **one** README. The other root docs are focused references, not extra READMEs:

| File | Purpose |
|---|---|
| `README.md` | Onboarding, setup, responsibilities, and high-level architecture |
| `SCHEMA.md` | Current database/data contract |
| `STRUCTURE.md` | Backend code organization and module boundaries |

---

## Current Playspace Status

Playspace no longer uses a generic blob-only audit persistence path.

**Current implementation:**

- Writes normalized Playspace audit rows first
- Computes raw totals from normalized rows
- Keeps `responses_json` and `scores_json` as compatibility caches
- Serves typed auditor/mobile responses for `meta`, `pre_audit`, `sections`, `scores`, and `progress`

**Recent hardening:**

- Draft patch normalization now reuses existing ORM child rows by natural unique keys instead of delete/reinsert replacement
- This avoids duplicate-key failures on repeated saves for pre-audit answers, section rows, question responses, and scale answers

---

## Local Setup

### 1. Create a virtualenv

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2. Install dependencies

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
pre-commit install
```

### 3. Configure database access

```bash
cp .env.example .env
```

Set the following in `.env`:

| Variable | Description |
|---|---|
| `DATABASE_URL_YEE` | Neon `postgresql://...` URL for YEE |
| `DATABASE_URL_PLAYSPACE` | Neon `postgresql://...` URL for Playspace |

> Neon URLs can be pasted directly — `app/database.py` normalizes them for async SQLAlchemy.

---

## Migrations

Apply migrations per product database:

```bash
alembic -x product=yee upgrade head
alembic -x product=playspace upgrade head
```

---

## Seed Demo Data

```bash
# Seed both products
./.venv/bin/python -m app.seed

# Seed one product only
./.venv/bin/python -m app.seed --product yee
./.venv/bin/python -m app.seed --product playspace
```

The Playspace seed flow builds realistic audit payloads, hydrates normalized relations, computes score totals, and keeps compatibility JSON caches populated.

---

## Run the API

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

| URL | Description |
|---|---|
| `http://127.0.0.1:8000/health` | Health check |
| `http://127.0.0.1:8000/playspace/*` | Playspace namespace |
| `http://127.0.0.1:8000/yee/status` | YEE namespace status |

---

## Playspace Backend Notes

### Normalized Audit Tables

Playspace currently writes to these tables, which back the draft, scoring, and submit flows:

| Table | Description |
|---|---|
| `playspace_audit_contexts` |
| `playspace_pre_audit_answers` |
| `playspace_audit_sections` |
| `playspace_question_responses` |
| `playspace_scale_answers` |

### Score Shape

| Bucket | Type |
|---|---|
| `quantity_total` | Column total |
| `diversity_total` | Column total |
| `challenge_total` | Column total |
| `sociability_total` | Construct total |
| `play_value_total` | Construct total |
| `usability_total` | Construct total |

```
summary_score = play_value_total + usability_total
```

### Mobile Integration Notes

- The Playspace mobile app is offline-first and syncs draft patches to this backend
- Exports are currently generated on the mobile client from submitted audit payloads
- There is no dedicated backend export endpoint yet for manager/project reporting

---

## Useful Commands

### Tests

```bash
# Run all tests
pytest

# Targeted Playspace validation
./.venv/bin/ruff check app/products/playspace
./.venv/bin/python -m py_compile app/products/playspace/scoring.py app/products/playspace/services/audit_sessions.py
./.venv/bin/python -m app.seed --product playspace
```

### Lint & Format

```bash
ruff check . --fix
ruff format .
```

---

## Deployment

Render start command:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```