# Audit Tools Backend

This repository is the FastAPI backend for DECA Lab's Audit Tools platform.

It now combines:

- the shared-core dashboard architecture from `master`
- the real YEE authentication, onboarding, dashboard, invite, submission, scoring, reporting, and export work from the YEE branch

The platform is not just a survey page. It is a browser-based, role-aware system with:

- authentication and onboarding
- admin, manager, and auditor workspaces
- project, place, auditor, and assignment management
- multi-step YEE audit submission
- scoring and reporting
- raw data export
- privacy-aware auditor identity handling

The frontend and backend live in separate repositories and should remain separate:

- Backend: `/Users/andishasafdariyan/auditTools/audit-tools-backend`
- Frontend: `/Users/andishasafdariyan/auditTools/audit-tools-yee-frontend`

## Project Purpose

The YEE website supports field auditing of youth enabling environments across real projects and places. Managers organize projects and places, invite and assign auditors, and review results. Auditors complete exactly one submitted audit per assigned place. Admins oversee users, workspaces, audits, and approvals across the whole system.

This backend provides:

- product-scoped data access for `/yee/*` and `/playspace/*`
- REST auth, dashboard, and YEE endpoints
- shared-core product routes for YEE and Playspace
- YEE instrument loading and question scoring
- scoped reporting and raw-data export
- enforcement of assignment-based access and one-audit-per-place submission rules

## Current Implementation Status

### Implemented

- Real auth flow with signup, login, email verification, resend verification, profile completion, and invite acceptance
- Role-aware session responses including `role`, verification status, approval status, profile-completion status, `next_step`, and `dashboard_path`
- `ADMIN`, `MANAGER`, and `AUDITOR` roles
- Admin approval flow for pending users
- Manager project creation, place creation, auditor invite creation, and place assignment
- Auditor-assigned place scoping
- Multi-step YEE audit flow in the frontend
- Backend enforcement for one submitted audit per auditor per place
- YEE question scoring from the source QSF instrument
- Weighted YEE reporting totals used in comparisons and raw-data export
- Place-level comparisons and CSV-ready raw data output
- Generated auditor IDs used in reporting and exports instead of full names
- Scoped project and place detail endpoints
- Shared-core YEE and Playspace dashboard route wrappers
- Deterministic seed data for the shared-core hierarchy

### Partially Implemented

- Settings pages and richer profile management
- Admin lifecycle actions beyond approval, such as deny, revoke, deactivate, or suspend
- Verification UX for local/demo environments
- Audit comparison filtering by arbitrary subset of audits within the same place
- Production polish around email delivery, deployment configuration, and final QA
- Full architectural reconciliation between the legacy YEE branch model assumptions and the new shared-core model is in progress on the integration branch

### Intentionally Not Implemented Yet

- Cap score logic

## User Roles And Permissions

### ADMIN

Admins can:

- access all users, managers, auditors, projects, places, and audits
- approve pending users
- view all raw data and comparison reports
- access system-wide settings when that UI is completed

### MANAGER

Managers can:

- create projects
- create places
- invite auditors
- assign auditors to places
- view audits, reports, and raw data within their own account scope

### AUDITOR

Auditors can:

- access only assigned places
- complete profile setup
- start or continue a draft audit
- submit exactly one final audit per assigned place
- view only their own audit history

See [docs/roles-and-permissions.md](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/roles-and-permissions.md) for the full matrix.

## Backend Route Families

- `/yee/auth/*`
- `/playspace/auth/*`
- `/yee/dashboard/*`
- `/yee/instrument`
- `/yee/audits/score`
- `/yee/audits`
- `/yee/audits/{submission_id}`
- `/yee/my-audits`
- `/yee/graphql`
- `/playspace/graphql`
- `/health`

## Local Setup

### Backend

1. Create a Python environment:

```bash
cd /Users/andishasafdariyan/auditTools/audit-tools-backend
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

2. Create `.env`:

```bash
cp .env.example .env
```

3. Set environment values:

```env
DATABASE_URL_YEE=postgresql+asyncpg://postgres:postgres@localhost:5432/audit_tools_yee
DATABASE_URL_PLAYSPACE=postgresql+asyncpg://postgres:postgres@localhost:5432/audit_tools_playspace
AUTH_TOKEN_SECRET_KEY=change-me
AUTH_ACCESS_TOKEN_TTL_DAYS=7
AUTH_EMAIL_VERIFY_TTL_HOURS=24
AUTH_VERIFY_URL_TEMPLATE=http://localhost:3000/verify-email?token={token}
```

4. Run migrations:

```bash
alembic -x product=yee upgrade head
alembic -x product=playspace upgrade head
```

5. Seed demo/shared-core data if needed:

```bash
./.venv/bin/python -m app.seed
```

Single product seed:

```bash
./.venv/bin/python -m app.seed --product yee
./.venv/bin/python -m app.seed --product playspace
```

6. Start the backend:

```bash
uvicorn app.main:app --reload
```

Backend URLs:

- `http://127.0.0.1:8000`
- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/yee/graphql`
- `http://127.0.0.1:8000/playspace/graphql`

### Frontend

1. Install dependencies:

```bash
cd /Users/andishasafdariyan/auditTools/audit-tools-yee-frontend
npm install
```

2. Create frontend env values:

```env
API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
```

3. Start the frontend:

```bash
npm run dev
```

Frontend URL:

- `http://localhost:3000`

## File Structure

### Backend

```text
audit-tools-backend/
  alembic/
  app/
    auth.py
    auth_security.py
    core/
    dashboard_router.py
    data/
      yee_instrument.qsf
    database.py
    email_service.py
    main.py
    models.py
    products/
      playspace/
      yee/
    schema.py
    seed.py
    yee_router.py
    yee_scoring.py
  docs/
  tests/
  README.md
  .env.example
```

## Documentation Map

- [docs/architecture.md](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/architecture.md)
- [docs/scoring.md](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/scoring.md)
- [docs/roles-and-permissions.md](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/roles-and-permissions.md)
- [docs/deployment.md](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/deployment.md)

## Pending Work / Future Work

Important remaining work:

- complete settings/profile management pages
- add admin deny/revoke/deactivate lifecycle actions
- improve verification and demo UX
- allow report filtering by arbitrary selected audits
- finish the architectural integration between shared-core dashboard models and the YEE auth/reporting layer
- add final production deployment config, monitoring, and QA
- add final cap score logic once confirmed
