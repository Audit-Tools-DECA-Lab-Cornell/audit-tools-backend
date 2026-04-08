# Audit Tools: YEE Developer Guide

This repository is the FastAPI backend for DECA Lab's Audit Tools platform, with the Youth Enabling Environments (YEE) website as the primary implemented product today.

The platform is not just a survey page. It is a browser-based, role-aware system with:

- authentication and onboarding
- admin, manager, and auditor workspaces
- project, place, auditor, and assignment management
- multi-step YEE audit submission
- scoring and reporting
- raw data export
- privacy-aware auditor identity handling

The frontend and backend live in separate repositories and must stay separate:

- Backend: `/Users/andishasafdariyan/auditTools/audit-tools-backend`
- Frontend: `/Users/andishasafdariyan/auditTools/audit-tools-yee-frontend`

## Project Purpose

The YEE website supports field auditing of youth enabling environments across real projects and places. Managers organize projects and places, invite and assign auditors, and review results. Auditors complete exactly one submitted audit per assigned place. Admins oversee users, workspaces, audits, and approvals across the whole system.

This backend provides:

- product-scoped data access for `/yee/*` and `/playsafe/*`
- REST auth, dashboard, and YEE endpoints
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

### Partially Implemented

- Settings pages and richer profile management
- Admin lifecycle actions beyond approval, such as deny, revoke, deactivate, or suspend
- Verification UX for local/demo environments
- Audit comparison filtering by arbitrary subset of audits within the same place
- Production polish around email delivery, deployment configuration, and final QA

### Intentionally Not Implemented Yet

- Cap score logic

The code is structured so cap scoring can be added later, but no final cap behavior is invented today.

## User Roles And Permissions

### ADMIN

Admins can:

- access all users, managers, auditors, projects, places, and audits
- approve pending users
- view all raw data and comparison reports
- access system-wide settings when that UI is completed

Primary routes in the frontend:

- `/admin`
- `/admin/users`
- `/admin/projects`
- `/admin/places`
- `/admin/audits`
- `/admin/raw-data`
- `/admin/settings`

### MANAGER

Managers can:

- create projects
- create places
- invite auditors
- assign auditors to places
- view audits, reports, and raw data within their own account scope

Primary routes in the frontend:

- `/dashboard`
- `/dashboard/projects`
- `/dashboard/projects/new`
- `/dashboard/projects/[projectId]`
- `/dashboard/places`
- `/dashboard/places/new`
- `/dashboard/places/[placeId]`
- `/dashboard/auditors`
- `/dashboard/auditors/invite`
- `/dashboard/audits`
- `/dashboard/raw-data`
- `/dashboard/reports`
- `/dashboard/settings`

### AUDITOR

Auditors can:

- access only assigned places
- complete profile setup
- start or continue a draft audit
- submit exactly one final audit per assigned place
- view only their own audit history

Primary routes in the frontend:

- `/my-dashboard`
- `/my-dashboard/places`
- `/my-dashboard/audits`
- `/my-dashboard/settings`
- `/yee/introduction`
- `/yee/audit/[placeId]/page/1` through `/page/8`
- `/yee/audit/[placeId]/review`
- `/yee/audit/[placeId]/submitted`

See [roles-and-permissions.md](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/roles-and-permissions.md) for the full matrix.

## Route Structure

### Public / Account Flow

- `/`
- `/login`
- `/signup`
- `/verify-email`
- `/invite/[token]`
- `/waiting-approval`
- `/complete-profile`

### Backend Route Families

- `/yee/auth/*`
- `/playsafe/auth/*`
- `/yee/dashboard/*`
- `/yee/instrument`
- `/yee/audits/score`
- `/yee/audits`
- `/yee/audits/{submission_id}`
- `/yee/my-audits`
- `/health`

### Major Backend Endpoints

Auth:

- `POST /yee/auth/signup`
- `GET /yee/auth/verify-email`
- `POST /yee/auth/resend-verification`
- `POST /yee/auth/login`
- `GET /yee/auth/me`
- `POST /yee/auth/complete-profile`
- `GET /yee/auth/invite/{token}`
- `POST /yee/auth/invite/{token}/accept`

Dashboard:

- `GET /yee/dashboard/overview`
- `GET /yee/dashboard/projects`
- `GET /yee/dashboard/projects/{project_id}`
- `POST /yee/dashboard/projects`
- `GET /yee/dashboard/places`
- `GET /yee/dashboard/places/{place_id}`
- `POST /yee/dashboard/places`
- `GET /yee/dashboard/auditors`
- `GET /yee/dashboard/audits`
- `GET /yee/dashboard/users`
- `POST /yee/dashboard/users/approve`
- `POST /yee/dashboard/auditor-invites`
- `POST /yee/dashboard/assignments`
- `GET /yee/dashboard/my-places`
- `GET /yee/dashboard/reports/place-comparisons`
- `GET /yee/dashboard/raw-data`

YEE:

- `GET /yee/instrument`
- `POST /yee/audits/score`
- `POST /yee/audits`
- `GET /yee/audits/{submission_id}`
- `GET /yee/my-audits`

## Frontend / Backend Architecture

### High-Level Split

Frontend responsibilities:

- routing and layouts
- role-specific dashboards
- form rendering and survey step navigation
- auth state handling in the browser
- API proxy routes and UI presentation

Backend responsibilities:

- auth and session token creation
- verification and invite processing
- database ownership and account scoping
- assignment enforcement
- audit submission validation
- scoring source of truth for question-level mapping
- reporting and export data generation

### Integration Pattern

The frontend talks to the backend through a shared base URL:

- `API_BASE_URL`
- `NEXT_PUBLIC_API_BASE_URL`

The frontend uses local Next.js API routes as thin proxies so the browser does not call backend URLs directly from every component.

Examples:

- frontend `/api/auth/login` -> backend `/yee/auth/login`
- frontend `/api/dashboard/overview` -> backend `/yee/dashboard/overview`
- frontend `/api/yee/audits` -> backend `/yee/audits`

See [architecture.md](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/architecture.md) for the detailed system map.

## YEE Survey Flow

The YEE audit is intentionally not one long page.

Current expected flow:

1. Auditor lands in `/yee/introduction`
2. Auditor chooses one assigned place
3. Page 1 captures metadata and high-level questions
4. Page 2 captures domain importance weights
5. Pages 3-8 collect domain questions by section
6. Review page shows draft answers and score summary
7. Submitted page confirms completion

Important behavior:

- answers persist between steps
- users can move backward and forward
- selected answers remain visible when returning to earlier steps
- submission is blocked if the place is not assigned to the logged-in auditor
- backend blocks a second submitted audit for the same auditor/place pair

## YEE Scoring Logic

### Layer 1: Question Scoring

Question-level scoring is derived from the source QSF file:

- file: [app/data/yee_instrument.qsf](/Users/andishasafdariyan/auditTools/audit-tools-backend/app/data/yee_instrument.qsf)
- logic: [app/yee_scoring.py](/Users/andishasafdariyan/auditTools/audit-tools-backend/app/yee_scoring.py)

The backend reads Qualtrics grading definitions and preserves the score mappings already defined in the instrument. That includes special mappings and reverse-coded behavior present in the source instrument.

### Layer 2: Aggregate Scoring

For each audit:

1. Raw Domain Score
   - sum of scored item values inside each YEE domain
2. Youth Weighted Domain Score
   - raw domain score multiplied by the auditor’s importance weight for that domain
3. Total Enabling Environment Raw Score
   - sum of all raw domain scores
4. Total Enabling Environment Youth-Weighted Score
   - sum of all weighted domain scores

Weight values:

- `Very important to me = 3`
- `Somewhat important to me = 2`
- `Not really important to me = 1`

See [scoring.md](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/scoring.md) for the exact model and implementation locations.

## Auditor Privacy Rules

The system is designed to avoid exposing personal identity in reporting views.

Rules:

- auditors receive a generated ID such as `AUD-ABC123`
- reports, comparisons, and exports use the generated auditor ID by default
- full names are not used in place-level comparison views
- full names remain internal account/profile data only where permitted
- generated IDs must not encode birth date, full name, or other personal identifiers

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
DATABASE_URL_PLAYSAFE=postgresql+asyncpg://postgres:postgres@localhost:5432/audit_tools_playsafe
AUTH_TOKEN_SECRET_KEY=change-me
AUTH_ACCESS_TOKEN_TTL_DAYS=7
AUTH_EMAIL_VERIFY_TTL_HOURS=24
AUTH_VERIFY_URL_TEMPLATE=http://localhost:3000/verify-email?token={token}
```

4. Run migrations:

```bash
alembic -x product=yee upgrade head
alembic -x product=playsafe upgrade head
```

5. Start the backend:

```bash
uvicorn app.main:app --reload
```

Backend URLs:

- `http://127.0.0.1:8000`
- `http://127.0.0.1:8000/health`

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

## Deployment

Recommended deployment split:

- frontend on a Next.js-capable host
- backend on an ASGI-capable Python host
- PostgreSQL for `DATABASE_URL_YEE` and `DATABASE_URL_PLAYSAFE`

For local review links, `ngrok` has been used successfully to expose the frontend during development.

For full deployment notes, environment variables, and release checklist, see [deployment.md](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/deployment.md).

## Project File Structure

### Backend

```text
audit-tools-backend/
  alembic/
  app/
    auth.py
    auth_security.py
    dashboard_router.py
    database.py
    email_service.py
    main.py
    models.py
    yee_router.py
    yee_scoring.py
    data/
      yee_instrument.qsf
  tests/
  README.md
  .env.example
```

### Frontend

```text
audit-tools-yee-frontend/
  src/
    app/
      admin/
      dashboard/
      my-dashboard/
      login/
      signup/
      verify-email/
      invite/[token]/
      waiting-approval/
      complete-profile/
      yee/
      api/
    components/
      auth/
      dashboard/
      reporting/
      yee/
      ui/
    lib/
      auth/
      dashboard/
      yee-*.ts
  README.md
```

## Integration Points

Core integration contracts future engineers should know:

- Backend auth response drives frontend routing:
  - `role`
  - `email_verified`
  - `approved`
  - `profile_completed`
  - `next_step`
  - `dashboard_path`
- Backend owns assignment validation and submission rules
- Backend owns question-level score mappings from the QSF
- Frontend owns step UI, draft persistence, and page flow
- Backend reporting endpoints provide comparison/export data scoped by account

## Documentation Map

- [Architecture](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/architecture.md)
- [Scoring](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/scoring.md)
- [Roles And Permissions](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/roles-and-permissions.md)
- [Deployment](/Users/andishasafdariyan/auditTools/audit-tools-backend/docs/deployment.md)

## Pending Work / Future Work

Important remaining work:

- complete settings/profile management pages
- add admin deny/revoke/deactivate lifecycle actions
- improve verification and demo UX
- allow report filtering by arbitrary selected audits
- add final production deployment config, monitoring, and QA
- add final cap score logic once the scoring rules are confirmed

The current system is already a real multi-role website foundation, but future engineers should treat the items above as the next priority layer before public launch.
