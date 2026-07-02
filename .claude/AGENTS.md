# AGENTS.md — audit-tools-backend

Shared FastAPI backend for **both** products in this workspace. Routing card only
— implementation facts live in this repo's own docs, not here.

## What this repo owns

- shared SQLAlchemy models and product-scoped database access
- Alembic migrations for both `yee` and `playspace` (branched, product-scoped)
- product REST routes under `/yee/*` and `/playspace/*`
- YEE auth, onboarding, invite, reporting, and export flows
- Playspace audit session, assignment, dashboard, and management flows

Full detail: `README.md`, `SCHEMA.md`, `STRUCTURE.md`, `docs/`.

## Product split (the most important boundary here)

- **YEE auth** — the `users` table in `app/auth.py`.
- **Playspace auth** — the same signed `User` session model, with `x-demo-*`
  actor headers kept only as a temporary compatibility fallback in
  `app/core/actors.py`.

A change to one product's auth is not automatically safe for the other — verify
against `docs/client-map.md` before assuming.

## Where facts actually live today

This repo does not yet have its own `.claude/memory/`. Backend facts consumed by
Playspace work are currently recorded in `playspace/.claude/memory/`:

| Topic | File |
|---|---|
| Backend stack, conventions, key files | `../playspace/.claude/memory/backend.md` |
| Database schema, instrument versioning | `../playspace/.claude/memory/database-schema.md` |
| Alembic migration model | `../playspace/.claude/memory/alembic-migrations.md` |
| Deployment topology | `../playspace/.claude/memory/deployment.md` |

If you're doing **YEE-only** backend work, read those files for the parts that
are genuinely product-agnostic (schema conventions, migration mechanics,
`lazy="raise"` rule) but verify YEE-specific behavior against this repo's own
`README.md` / `docs/` rather than assuming Playspace's memory describes YEE.

## Hard rules (full text in workspace-root `AGENTS.md`)

- Never read/print/work around `.env` / `.env.*` / secret files unless the user
  names a specific file in the current request.
- Never commit, push, branch, or amend without explicit user approval.
- All ORM relationships use `lazy="raise"` — any relationship read must be
  covered by `selectinload`/`joinedload` in the query that loaded it.
