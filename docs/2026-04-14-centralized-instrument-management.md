# Centralized Audit Instrument Management System

## Status: Complete & Tested

---

## Summary

The audit instrument (structure, logic, and content) was previously duplicated as static files across three repositories. This system centralizes it into a single PostgreSQL JSONB table with REST endpoints, an admin editor UI, and mobile sync-and-cache support.

## Architecture

```
Admin Editor UI ──POST──▶ instruments table (JSONB, versioned)
                                    │
          ┌─────────────────────────┼─────────────────────────┐
          │                         │                         │
          ▼                         ▼                         ▼
     Backend                  Web Frontend               Mobile App
  (audit sessions            (fetches via                (syncInstrument →
   serve DB instrument)       session response)           MMKV cache)
```

## Files Changed

### Backend (`audit-tools-backend/`) — 6 modified, 3 new

| File | Type | Purpose |
|------|------|---------|
| `app/models.py` | M | `Instrument` ORM model |
| `app/products/playspace/schemas/management.py` | M | Response/request schemas |
| `app/products/playspace/services/instrument.py` | **N** | Async CRUD service |
| `app/products/playspace/routes/instrument.py` | M | 4 REST endpoints + legacy preserved |
| `app/products/playspace/services/audit_sessions.py` | M | Serves DB instrument in session response |
| `app/products/playspace/seed_data.py` | M | Seeds canonical instrument |
| `app/seed.py` | M | Clear ordering + `--skip-migrate` flag |
| `alembic/versions/20260414_0012_...py` | **N** | Migration |
| `docs/2026-04-14-centralized-...md` | **N** | This document |

### Web Frontend (`audit-tools-playspace-frontend/`) — 8 modified, 1 new, 3 deleted

| File | Type | Purpose |
|------|------|---------|
| `src/lib/instrument.ts` | **D** | 12,345-line static instrument removed |
| `src/lib/enInstrument.ts` | **D** | English translation overlay removed |
| `src/lib/deInstrument.ts` | **D** | German translation overlay removed |
| `src/lib/api/playspace.ts` | M | API client methods for instrument management |
| `src/lib/instrument-translations.ts` | M | Returns null when no instrument loaded |
| `src/lib/instrument-system-metadata.ts` | M | Uses constants instead of static imports |
| `src/types/audit.ts` | M | Schema no longer defaults to static blob |
| `src/components/app/app-shell.tsx` | M | Admin nav link |
| `src/app/(protected)/admin/instruments/page.tsx` | **N** | Version history, editor, JSON upload |
| `src/app/(protected)/auditor/execute/.../audit-form.tsx` | M | Null guard for instrument |
| `src/app/(protected)/auditor/reports/.../page.tsx` | M | Null guards |
| `messages/en.json` | M | EN translations |
| `messages/de.json` | M | DE translations |

### Mobile (`audit-tools-playspace-mobile/`) — 3 modified, 1 new, 1 deleted

| File | Type | Purpose |
|------|------|---------|
| `lib/instrument.ts` | **D** | 12,353-line static instrument removed |
| `lib/services/instrument-sync.ts` | **N** | API fetch → MMKV cache → null fallback |
| `stores/audit-store.ts` | M | Uses synced instrument, null-safe |
| `lib/audit/types.ts` | M | Schema default removed |
| `lib/i18n/instrument-translations.ts` | M | Returns null when no instrument |

## Net Impact

- **~25,000 lines of static instrument definitions deleted** across frontend and mobile
- **~400 lines of new infrastructure code** across all three repos
- **Single source of truth** in the `instruments` table
- **Admin can edit and publish** without touching code
- **Mobile works offline** via MMKV cache after first sync

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/playspace/instruments/active/{key}?lang=en` | Active instrument for clients |
| `GET` | `/playspace/admin/instruments` | List all versions |
| `POST` | `/playspace/admin/instruments?activate=true` | Create new version |
| `PATCH` | `/playspace/admin/instruments/{id}` | Toggle active status |
| `GET` | `/playspace/instrument` | Legacy (preserved) |
