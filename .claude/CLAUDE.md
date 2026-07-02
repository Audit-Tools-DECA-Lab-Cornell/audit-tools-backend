# CLAUDE — audit-tools-backend quick reference

FastAPI backend serving both `YEE` and `Playspace` from one codebase, two
product-scoped Postgres databases.

## Commands

```bash
.venv/bin/uvicorn app.main:app --reload
.venv/bin/alembic -x product=<yee|playspace> upgrade <product>@head
.venv/bin/ruff check .
.venv/bin/pytest tests/ -p no:cacheprovider
```

## Read first

- `README.md` — product split, what this repo owns
- `SCHEMA.md` — full database schema reference
- `STRUCTURE.md` — module map
- `docs/client-map.md` — which client consumes which `/yee/*` or `/playspace/*` namespace
- `.claude/AGENTS.md` — this repo's routing card (auth split, where facts live today)

## Workspace context

Root routing: `../AGENTS.md` → `.claude/AGENT_ROUTING.md`. If a change affects
Playspace contracts, also read `../playspace/AGENTS.md`; if it affects YEE, also
read `../yee/AGENTS.md`.
