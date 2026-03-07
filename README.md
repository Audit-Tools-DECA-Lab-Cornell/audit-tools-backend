# Audit Tools Backend

FastAPI backend for Audit Tools with product-scoped databases and routes:
- YEE (Youth Enabling Environments)
- Playsafe

This guide is step-by-step for local setup and running.

## 1. Prerequisites

- Python `3.11+`
- `pip`
- PostgreSQL connection strings (Neon recommended)

## 2. Clone and enter project

```bash
git clone https://github.com/pratyush1712/audit-tools-backend.git
cd audit-tools-backend
```

## 3. Create Python environment

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install runtime packages:

```bash
python -m pip install -r requirements.txt
```

Optional dev tools:

```bash
python -m pip install -r requirements-dev.txt
pre-commit install
```

## 4. Configure environment variables

Create `.env`:

```bash
cp .env.example .env
```

Set DB URLs inside `.env`:

```env
DATABASE_URL_YEE="postgresql://<user>:<password>@<host>/<yee_db>?sslmode=require&channel_binding=require"
DATABASE_URL_PLAYSAFE="postgresql://<user>:<password>@<host>/<playsafe_db>?sslmode=require&channel_binding=require"
```

Notes:
- `DATABASE_URL_YEE` powers `/yee/*`
- `DATABASE_URL_PLAYSAFE` powers `/playsafe/*`
- Legacy `DATABASE_URL` still works as a fallback for YEE only

## 5. Run migrations

Run YEE migrations:

```bash
alembic -x product=yee upgrade head
```

Run Playsafe migrations:

```bash
alembic -x product=playsafe upgrade head
```

## 6. Start backend

```bash
uvicorn app.main:app --reload
```

Backend runs at:
- `http://127.0.0.1:8000`
- Health check: `http://127.0.0.1:8000/health`

## 7. Verify API quickly

### YEE REST

- `GET /yee/instrument`
- `POST /yee/audits/score`
- `POST /yee/audits`
- `GET /yee/audits/{id}`

Example:

```bash
curl http://127.0.0.1:8000/yee/instrument
```

### GraphQL

- `http://127.0.0.1:8000/yee/graphql`
- `http://127.0.0.1:8000/playsafe/graphql`

## 8. YEE frontend (separate repo)

YEE frontend is intentionally split into its own repository:
- https://github.com/Andisha2004/audit-tools-yee-frontend

Run it separately (while backend is running):

```bash
cd /path/to/audit-tools-yee-frontend
npm install
npm run dev
```

## 9. Common issues

### `role "postgres" does not exist`
Your `.env` is missing product DB URLs, so it falls back to local postgres defaults.

### `Can't locate revision identified by ...`
Your DB `alembic_version` does not match local migration files. Pull latest repo and rerun migrations.

### Frontend shows `Failed to load instrument`
Make sure backend is running on `127.0.0.1:8000` and `GET /yee/instrument` works directly.

## 10. Useful commands

Run tests:

```bash
pytest
```

Lint/format:

```bash
ruff check . --fix
ruff format .
```

## Deploy (Render)

Start command:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Use `/health` as health check path.
