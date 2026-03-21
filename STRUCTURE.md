# Audit System :  Development Structure

> This document explains how `audit-tools-backend` is organized in code.

Use it together with:

| File | Purpose |
|---|---|
| `README.md` | Setup and responsibilities |
| `SCHEMA.md` | Current data model |

The system manages hierarchical audit processes across multiple projects and physical locations.

---

## Table of Contents

- [Repository Shape](#1-repository-shape)
- [Top-Level Responsibilities](#2-top-level-backend-responsibilities)
- [Product Module Layout](#3-product-module-layout)
- [Playspace Module Responsibilities](#4-playspace-module-responsibilities)
- [Request Flow](#5-request-flow)
- [Testing & Migrations](#6-testing--migrations)
- [Design Boundaries](#7-current-design-boundaries)

---

## 1. Repository Shape

```
audit-tools-backend/
├── app/
│   ├── main.py
│   ├── database.py
│   ├── auth.py
│   ├── models.py
│   ├── seed.py
│   ├── core/
│   └── products/
│       ├── playspace/
│       └── yee/
├── alembic/
├── tests/
├── README.md
├── SCHEMA.md
└── STRUCTURE.md
```

---

## 2. Top-Level Backend Responsibilities

| File | Responsibilities |
|---|---|
| `app/main.py` | FastAPI app creation, route registration, health/status exposure |
| `app/database.py` | Database engine/session setup, product-specific URL handling, shared SQLAlchemy integration |
| `app/auth.py` | Shared authentication/session helpers, account-level auth concerns used across products |
| `app/models.py` | Shared SQLAlchemy ORM models, Playspace normalized audit models, relational constraints and cascade behavior |
| `app/seed.py` | Entry point for deterministic seed/demo data, delegates product-specific seeding where needed |
| `app/core/` | Shared helpers not owned by one product :  e.g. actor context and demo/source-material helpers |

---

## 3. Product Module Layout

All products live under `app/products/`.

```
app/products/
├── playspace/
│   ├── __init__.py
│   ├── instrument.py
│   ├── scoring.py
│   ├── scoring_metadata.py
│   ├── audit_state.py
│   ├── seed_data.py
│   ├── routes/
│   ├── services/
│   └── schemas/
└── yee/
    ├── __init__.py
    └── routes.py
```

---

## 4. Playspace Module Responsibilities

### `routes/` :  HTTP Layer

Thin layer responsible only for request handling. Does not contain business logic.

| Concern | Handled here? |
|---|---|
| Request parsing | ✅ |
| Dependency wiring | ✅ |
| Auth / actor enforcement | ✅ |
| Business logic | ❌ :  delegate to services |

**Current route groups:** audits · assignments · dashboard · profile · route dependencies

---

### `services/` :  Business Logic Layer

| Module | Responsibilities |
|---|---|
| `audit.py` | Root service composition and shared helpers |
| `audit_sessions.py` | Auditor/mobile access, draft, submit, list, and session response logic |
| `audit_assignments.py` | Assignment creation, update, and list behavior |
| `dashboard.py` | Manager/dashboard-oriented response building |
| `profile.py` | Current-account and auditor-profile service helpers |

---

### `schemas/` :  Typed API Contract Layer

Defines the backend contract consumed by the mobile client.

| Schema group |
|---|
| Request / response models |
| Audit session models |
| Dashboard models |
| Profile models |
| Base shared schemas |

---

### Core Playspace Files

| File | Responsibilities |
|---|---|
| `instrument.py` | Backend-side Playspace instrument metadata; stable scoring/input structure for backend logic |
| `scoring_metadata.py` | Scoring-specific projection of the instrument :  options, constructs, domains, and scale metadata used by scoring/progress logic |
| `scoring.py` | Pure-ish scoring and progress logic :  execution-mode resolution, question visibility, pre-audit completeness, section progress, raw-total score aggregation. **Must stay focused on deterministic calculations, not request handling.** |
| `audit_state.py` | Normalization bridge between relational rows and runtime payloads *(see below)* |
| `seed_data.py` | Playspace-specific deterministic seed data, realistic audit payload generation, normalized relation hydration during seed setup |

#### `audit_state.py` :  Critical Boundary

This file is the boundary between **mobile draft payloads** and the **normalized database model**.

- Apply draft patches into normalized child rows
- Rebuild compatibility JSON caches from relations
- Read/write execution mode and draft progress
- Synchronize child collections without violating natural unique constraints

---

## 5. Request Flow

```
Route
→ dependency resolution / actor validation
→ service method
→ audit_state / scoring / ORM helpers as needed
→ schema response
```

### Examples

| Operation | Flow |
|---|---|
| Draft save | `route` → `audit_sessions.py` → `audit_state.py` → `scoring.py` → schema response |
| Assigned places | `route` → `audit_sessions.py` → ORM reads + score/progress helpers → schema response |

---

## 6. Testing & Migrations

### `tests/`

Playspace-specific tests live under:

```
tests/products/playspace/
```

| Test area |
|---|
| Audit state normalization regressions |
| Scoring behavior |
| Service-level contract tests |

### `alembic/`

- Schema migration history
- Product-specific migrations selected via `-x product=...`

---

## 7. Current Design Boundaries

| Rule |
|---|
| Keep request handling in **routes** :  not in scoring or normalization helpers |
| Keep schema definitions in **`schemas/`** :  not inline inside services |
| Keep product-specific logic under **`app/products/<product>/`** |
| Keep shared ORM definitions centralized in **`app/models.py`** |
| Treat `responses_json` and `scores_json` as compatibility caches :  not as the primary long-term Playspace write model |