# Backend Client Map

Which clients consume this backend, and over which namespace. One Render service
serves two product namespaces backed by two separate PostgreSQL databases.

```mermaid
flowchart LR
    YF["yee-frontend (Next.js)"] -->|/yee/*| BE["FastAPI backend"]
    YM["yee-mobile (Expo)"] -->|/yee/*| BE
    CF["copa-frontend (Next.js)"] -->|/playspace/*| BE
    CM["copa-mobile (Expo)"] -->|/playspace/*| BE
    CD["copa-desktop (Electron)"] -->|/playspace/* (auditor subset)| BE
    BE --> YDB["YEE PostgreSQL"]
    BE --> PDB["Playspace PostgreSQL"]
```

## Namespaces

| Namespace      | Product                        | Database    | Auth entry          |
| -------------- | ------------------------------ | ----------- | ------------------- |
| `/yee/*`       | YEE web audit platform         | `yee`       | `/yee/auth/*`       |
| `/playspace/*` | Playspace field-audit platform | `playspace` | `/playspace/auth/*` |

No client consumes both namespaces. YEE clients call `/yee/*`; every Playspace
client calls `/playspace/*`.

## Clients

| Client        | Repo                       | Namespace                       | How it calls the backend                                                                         | API base env key                                                |
| ------------- | -------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- |
| yee-frontend  | `yee/yee-frontend/`        | `/yee/*`                        | Next.js route handlers under `src/app/api/**` proxy to the backend                               | `BACKEND_API_URL` / `API_BASE_URL` / `NEXT_PUBLIC_API_BASE_URL` |
| yee-mobile    | `yee/yee-mobile/`          | `/yee/*`                        | Direct HTTP from the app (`lib/yee-api.ts`, `lib/auth/api.ts`)                                   | `EXPO_PUBLIC_API_BASE_URL`                                      |
| copa-frontend | `playspace/copa-frontend/` | `/playspace/*`                  | Direct HTTP (`src/lib/api/playspace.ts`, `src/lib/auth/auth-api.ts`)                             | `NEXT_PUBLIC_API_BASE_URL`                                      |
| copa-mobile   | `playspace/copa-mobile/`   | `/playspace/*`                  | Direct HTTP (`lib/auth/api.ts`, `lib/audit/api.ts`, `lib/notifications/api.ts`)                  | `EXPO_PUBLIC_API_BASE_URL`                                      |
| copa-desktop  | `playspace/copa-desktop/`  | `/playspace/*` (auditor subset) | Main-process client (`src/main/api/playspace-client.ts`); the renderer is CORS-blocked by design | `PLAYSPACE_API_URL`                                             |

Client-side env key names are owned by each client repo. This table is a pointer,
not the source of truth for those values.

## Route mounts

Mounted in `app/main.py`:

| Mount                              | Source                           | Purpose                                                                 |
| ---------------------------------- | -------------------------------- | ----------------------------------------------------------------------- |
| `/yee/auth/*`, `/playspace/auth/*` | `app/auth.py`                    | Product-aware auth (signup, login, `/me`; YEE adds email verification)  |
| `/yee/*`                           | `app/products/yee/routes/`       | YEE status, audit lifecycle, instrument/admin                           |
| `/yee/dashboard/*`                 | `app/dashboard_router.py`        | YEE manager/admin dashboard, invites, assignments, reporting            |
| `/playspace/*`                     | `app/products/playspace/routes/` | Playspace auditor + manager/admin surface, audits, exports, bug reports |
| `/playspace/api/notifications/*`   | `app/notifications_router.py`    | Playspace in-app notifications                                          |
| `/health`, `/`                     | `app/main.py`                    | Health check + root                                                     |

## CORS

`app/main.py` → `_resolve_cors_origins()` allow-lists localhost, the Render
backend host, the Vercel web frontends, the Expo mobile origin, and
`copa-tool.vercel.app`. Extend via `CORS_ALLOWED_ORIGINS` (comma-separated).

## Coordinating a contract change

A change to a route both clients of a product consume must move together with
those clients:

- **YEE:** update the backend route, then the `yee-frontend` `src/app/api/**`
  proxy + `src/lib` helper, and the `yee-mobile` `lib/` API surface.
- **Playspace:** update the backend route, then `copa-frontend`, `copa-mobile`,
  and (for the auditor subset) `copa-desktop`, plus the matching e2e/contract
  coverage required by `playspace/AGENTS.md`.

## Related docs

- `docs/architecture.md` — product boundaries, request flows, module map.
- `docs/deployment.md` — topology, env keys, migration runbook, Render note.
- `docs/roles-and-permissions.md` — role matrix per namespace.
- `playspace/.claude/memory/deployment.md` — Playspace-side deploy facts and CORS detail.
- `yee/AGENTS.md` — YEE product routing and per-repo docs.
