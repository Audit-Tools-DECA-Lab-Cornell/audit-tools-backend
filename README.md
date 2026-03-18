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

Optional auth/email env vars:

```env
AUTH_EMAIL_VERIFY_TTL_HOURS=24
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=your_user
SMTP_PASSWORD=your_password
SMTP_FROM_EMAIL=no-reply@example.com
SMTP_USE_TLS=true
TURNSTILE_SECRET_KEY=
AUTH_VERIFY_URL_TEMPLATE=
```

Notes:
- If SMTP is not configured, verification links are logged in backend console.
- If `TURNSTILE_SECRET_KEY` is set, signup/resend require a valid captcha token.
- `AUTH_VERIFY_URL_TEMPLATE` can be used for frontend verification pages, example:
  `http://localhost:3000/verify-email?token={token}`

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

### Auth REST (YEE and Playsafe)

These exist under both prefixes:
- `/yee/auth/*`
- `/playsafe/auth/*`

Endpoints:
- `POST /auth/signup` creates DB account and sends verification email
- `GET /auth/verify-email?token=...` verifies email
- `POST /auth/resend-verification` resends verification email
- `POST /auth/login` allows login only after verified email

Example:

```bash
curl http://127.0.0.1:8000/yee/instrument
```

### GraphQL

- `http://127.0.0.1:8000/yee/graphql`
- `http://127.0.0.1:8000/playsafe/graphql`

## 8. Authorization: What it does

This backend now supports real account creation instead of dummy auth.

Simple version of the flow:
- A user signs up with email and password.
- The backend creates the user in the database.
- The backend sends an email verification link.
- The user clicks the link.
- After verification, the user can log in.

This works for both audit tools:
- `/yee/auth/*`
- `/playsafe/auth/*`

The account data is stored in the correct database depending on the route prefix:
- requests to `/yee/*` use the YEE database
- requests to `/playsafe/*` use the Playsafe database

## 9. Authorization: How it is implemented

The auth system currently includes:
- password hashing before saving to the database
- email verification tokens
- email verified / not verified tracking on the user
- resend verification support
- failed login attempt tracking
- simple anti-bot protection

Anti-bot protection currently includes:
- a honeypot field called `website`
- optional Cloudflare Turnstile validation if `TURNSTILE_SECRET_KEY` is configured

Email sending works like this:
- if SMTP env vars are configured, the backend sends a real email
- if SMTP is not configured, the verification link is printed in the backend logs so local development still works

## 10. Authorization: Endpoints and how to use them

These endpoints exist for both products:
- `POST /yee/auth/signup`
- `GET /yee/auth/verify-email?token=...`
- `POST /yee/auth/resend-verification`
- `POST /yee/auth/login`
- `POST /playsafe/auth/signup`
- `GET /playsafe/auth/verify-email?token=...`
- `POST /playsafe/auth/resend-verification`
- `POST /playsafe/auth/login`

### Signup

Purpose:
- create a new user in the database
- send verification email

Example request:

```bash
curl -X POST http://127.0.0.1:8000/yee/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "email": "person@example.com",
    "password": "StrongPass123!",
    "name": "Example User",
    "account_type": "MANAGER",
    "website": ""
  }'
```

What happens:
- email is normalized
- password is hashed
- user is created in the database
- verification token is generated
- verification email is sent or logged

### Verify email

Purpose:
- mark the email as verified
- allow future login

Example:

```bash
curl "http://127.0.0.1:8000/yee/auth/verify-email?token=YOUR_TOKEN_HERE"
```

What happens:
- token is checked against the stored token hash
- token expiration is checked
- user is marked as verified

### Resend verification

Purpose:
- send a new verification link if the user has not verified yet

Example request:

```bash
curl -X POST http://127.0.0.1:8000/yee/auth/resend-verification \
  -H "Content-Type: application/json" \
  -d '{
    "email": "person@example.com",
    "website": ""
  }'
```

### Login

Purpose:
- authenticate a verified user
- return a bearer token-style session token in the response

Example request:

```bash
curl -X POST http://127.0.0.1:8000/yee/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "email": "person@example.com",
    "password": "StrongPass123!",
    "website": ""
  }'
```

What happens:
- email is looked up in the correct product database
- password hash is checked
- login is blocked if email is not verified
- login metadata is updated in the database
- access token and user info are returned

## 11. Authorization: How other parts of the app should use it

If another frontend or service wants to use auth, the normal order is:

1. Call `signup`
2. Wait for the user to verify email
3. Call `login`
4. Store the returned token on the frontend
5. Use the same product prefix for all later requests

Important integration rule:
- if the user signs up through `/yee/auth/signup`, then that same user should log in through `/yee/auth/login`
- if the user signs up through `/playsafe/auth/signup`, then that same user should log in through `/playsafe/auth/login`

Frontend integration notes:
- the frontend should show a message after signup like: `Check your email to verify your account`
- if SMTP is not configured in local development, check the backend terminal for the verification link
- the frontend should handle `403` login responses as `email not verified yet`
- the frontend can provide a `Resend verification email` button using `/auth/resend-verification`

## 12. YEE frontend (separate repo)

YEE frontend is intentionally split into its own repository:
- https://github.com/Andisha2004/audit-tools-yee-frontend

Run it separately (while backend is running):

```bash
cd /path/to/audit-tools-yee-frontend
npm install
npm run dev
```

## 13. Common issues

### `role "postgres" does not exist`
Your `.env` is missing product DB URLs, so it falls back to local postgres defaults.

### `Can't locate revision identified by ...`
Your DB `alembic_version` does not match local migration files. Pull latest repo and rerun migrations.

### Frontend shows `Failed to load instrument`
Make sure backend is running on `127.0.0.1:8000` and `GET /yee/instrument` works directly.

### Signup works but no email is received
SMTP is probably not configured. Check backend logs for the verification link.

### Login returns `403`
The account exists, but the email has not been verified yet.

## 14. Useful commands

Run tests:

```bash
pytest
```

Lint/format:

```bash
ruff check . --fix
ruff format .
```

## 15. Deploy (Render)

Start command:

```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Use `/health` as health check path.
