## Audit Tools Backend (FastAPI + Strawberry + SQLAlchemy + Postgres)

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

### Run the API (FastAPI)

```bash
uvicorn app.main:app --reload
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

### GraphQL

- **Endpoints**:
  - `http://127.0.0.1:8000/yee/graphql`
  - `http://127.0.0.1:8000/playspace/graphql`
- **Health check**: `http://127.0.0.1:8000/health`

### Database configuration

For local development, copy `.env.example` to `.env`. The app reads `DATABASE_URL_YEE` and `DATABASE_URL_PLAYSPACE` from that file (or falls back to local defaults in `app/database.py`).

### Useful commands

Run tests:

```bash
pytest
```

Lint and auto-fix:

```bash
ruff check . --fix
ruff format .
```

