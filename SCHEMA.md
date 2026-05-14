# Audit Backend — Schema Reference

> This document records the **current** backend data model used by `audit-tools-backend`.

| Also see       | Purpose                    |
| -------------- | -------------------------- |
| `README.md`    | Setup and responsibilities |
| `STRUCTURE.md` | Code organization          |

Intentionally split into: shared core tables · Playspace-specific normalized audit tables · compatibility caches.

---

## Table of Contents

- [Shared Core Tables](#1-shared-core-tables)
- [Playspace Normalized Draft Tables](#2-playspace-normalized-draft-tables)
- [Current Score Model](#3-current-playspace-score-model)
- [Dual-Storage Boundary](#4-dual-storage-boundary)
- [Legacy Checklist Data Migration](#5-legacy-checklist-data-migration)
- [Not In The Current Schema](#6-not-in-the-current-schema)

---

## 1. Shared Core Tables

### `accounts`

Workspace/account record shared across products.

| Column          | Notes                             |
| --------------- | --------------------------------- |
| `id`            | UUID primary key                  |
| `name`          | Account or workspace display name |
| `email`         | Unique account email              |
| `password_hash` | Nullable                          |
| `account_type`  | `ADMIN`, `MANAGER`, or `AUDITOR`  |
| `created_at`    |                                   |

---

### `users`

Platform auth identity table for both YEE and Playspace.

Manager workspaces now support multiple manager users (`account_type=MANAGER`)
linked to the same `account_id`, so `users` is the canonical login identity
table for all manager sign-in flows.

| Column                          | Notes                            |
| ------------------------------- | -------------------------------- |
| `id`                            | UUID primary key                 |
| `email`                         | Unique login email               |
| `password_hash`                 | Required hashed password         |
| `account_id`                    | Nullable FK → `accounts`         |
| `account_type`                  | `ADMIN`, `MANAGER`, or `AUDITOR` |
| `name`                          | Nullable display name            |
| `email_verified`                | Boolean                          |
| `email_verification_token_hash` | Nullable                         |
| `email_verification_sent_at`    | Nullable                         |
| `email_verified_at`             | Nullable                         |
| `failed_login_attempts`         | Integer                          |
| `approved`                      | Boolean                          |
| `approved_at`                   | Nullable                         |
| `profile_completed`             | Boolean                          |
| `profile_completed_at`          | Nullable                         |
| `last_login_at`                 | Nullable                         |
| `created_at`                    |                                  |

---

### `manager_profiles`

Manager profile rows owned by a manager account.

| Column                          | Notes                         |
| ------------------------------- | ----------------------------- |
| `id`                            | UUID primary key              |
| `account_id`                    | FK → `accounts`               |
| `user_id`                       | Nullable, unique FK → `users` |
| `full_name` / `email` / `phone` |                               |
| `position` / `organization`     |                               |
| `is_primary`                    |                               |
| `created_at`                    |                               |

---

### `auditor_profiles`

Auditor identity/profile rows owned by auditor accounts.

| Column                                                    | Notes                                                       |
| --------------------------------------------------------- | ----------------------------------------------------------- |
| `id`                                                      | UUID primary key                                            |
| `account_id`                                              | FK → `accounts`                                             |
| `user_id`                                                 | Nullable, unique FK → `users`                               |
| `auditor_code`                                            | Unique public-facing identifier used in reports and exports |
| `email`                                                   | Nullable, unique when present                               |
| `full_name` / `age_range` / `gender` / `country` / `role` |                                                             |
| `created_at`                                              |                                                             |

---

### `auditor_invites`

Invite rows used by the YEE onboarding flow.

| Column               | Notes                             |
| -------------------- | --------------------------------- |
| `id`                 | UUID primary key                  |
| `account_id`         | FK → `accounts`                   |
| `invited_by_user_id` | FK → `users`                      |
| `auditor_id`         | Nullable FK → `auditor_profiles`  |
| `email`              | Invite target email               |
| `token_hash`         | Unique hashed invite token        |
| `created_at`         |                                   |
| `expires_at`         | Invite expiry timestamp           |
| `accepted_at`        | Nullable until the invite is used |

---

### `manager_invites`

Invite rows used to add secondary managers to an existing manager account.

| Column                | Notes                             |
| --------------------- | --------------------------------- |
| `id`                  | UUID primary key                  |
| `account_id`          | FK → `accounts`                   |
| `invited_by_user_id`  | FK → `users`                      |
| `accepted_by_user_id` | Nullable FK → `users`             |
| `email`               | Invite target email               |
| `token_hash`          | Unique hashed invite token        |
| `created_at`          |                                   |
| `expires_at`          | Invite expiry timestamp           |
| `accepted_at`         | Nullable until the invite is used |

---

### `projects`

Projects belong to one account.

| Column                        | Notes            |
| ----------------------------- | ---------------- |
| `id`                          | UUID primary key |
| `account_id`                  | FK → `accounts`  |
| `name` / `overview`           |                  |
| `place_types`                 |                  |
| `start_date` / `end_date`     |                  |
| `est_places` / `est_auditors` |                  |
| `auditor_description`         |                  |
| `created_at`                  |                  |

---

### `places`

Places are shared place records that can be linked to multiple projects.

| Column                                   | Notes            |
| ---------------------------------------- | ---------------- |
| `id`                                     | UUID primary key |
| `name` / `city` / `province` / `country` |                  |
| `place_type`                             |                  |
| `lat` / `lng`                            |                  |
| `start_date` / `end_date`                |                  |
| `est_auditors` / `auditor_description`   |                  |
| `created_at`                             |                  |

---

### `project_places`

Join table linking places to projects.

| Column       | Notes                                   |
| ------------ | --------------------------------------- |
| `project_id` | FK → `projects`                         |
| `place_id`   | FK → `places`                           |
| `linked_at`  | Timestamp recorded when the link is set |

**Primary key:** `(project_id, place_id)`

---

### `auditor_assignments`

Assignments grant project-level or project-place-level access to an auditor.

| Column               | Notes                    |
| -------------------- | ------------------------ |
| `id`                 | UUID primary key         |
| `auditor_profile_id` | FK → `auditor_profiles`  |
| `project_id`         | Required FK → `projects` |
| `place_id`           | Nullable FK → `places`   |
| `assigned_at`        |                          |

> **Invariant:** `project_id` is always set. When `place_id` is also set, the row is scoped to one specific `(project_id, place_id)` pair.

---

### `audits`

Shared audit shell record used by YEE and retained for compatibility.

| Column                                  | Notes                                                 |
| --------------------------------------- | ----------------------------------------------------- |
| `id`                                    | UUID primary key                                      |
| `project_id`                            | FK → `projects`                                       |
| `place_id`                              | FK → `places`                                         |
| `auditor_profile_id`                    | FK → `auditor_profiles`                               |
| `audit_code`                            | Unique generated audit identifier                     |
| `instrument_key` / `instrument_version` |                                                       |
| `status`                                | `IN_PROGRESS`, `PAUSED`, or `SUBMITTED`               |
| `started_at`                            |                                                       |
| `submitted_at`                          | Nullable until submit                                 |
| `total_minutes`                         | Nullable until computed                               |
| `summary_score`                         | Nullable compact summary used by list/dashboard views |
| `responses_json`                        | JSONB compatibility cache                             |
| `scores_json`                           | JSONB compatibility cache                             |
| `created_at` / `updated_at`             |                                                       |

> **YEE / legacy rule:** `summary_score = play_value_total + usability_total`

---

### `playspace_submissions`

Playspace-only submission root. Scope is selected with **`execution_mode`**: `audit`, `survey`, or `both` (instrument enum); `both` counts toward both audit- and survey-axis place rollups.

| Column                                          | Notes                                             |
| ----------------------------------------------- | ------------------------------------------------- |
| `id`                                            | UUID primary key                                  |
| `project_id` / `place_id`                       | FK pair to `project_places`                       |
| `auditor_profile_id`                            | FK → `auditor_profiles`                           |
| `audit_code`                                    | Stable public-facing submission identifier        |
| `instrument_key` / `instrument_version`         | Instrument key/version active when the submission was created |
| `execution_mode`                                | `audit`, `survey`, or `both` (nullable until set) |
| `draft_progress_percent`                        | Draft progress projection for list surfaces       |
| `status`                                        | `IN_PROGRESS`, `PAUSED`, or `SUBMITTED`           |
| `summary_score`                                 | Legacy compact summary retained for compatibility |
| `audit_play_value_score`                        | Submission-level audit partition PV total         |
| `audit_usability_score`                         | Submission-level audit partition usability total  |
| `survey_play_value_score`                       | Submission-level survey partition PV total        |
| `survey_usability_score`                        | Submission-level survey partition usability total |
| `responses_json`                                | Canonical aggregate payload                       |
| `scores_json`                                   | Compatibility cache plus scored partitions        |
| `started_at` / `submitted_at` / `total_minutes` | Submission lifecycle metadata                     |
| `created_at` / `updated_at`                     |                                                   |

**Current uniqueness rule:** one Playspace submission per `(project_id, place_id, auditor_profile_id)`.

**Instrument version rule:** new submissions are stamped with the active `instruments` row for `pvua_v5_2` at creation time. Audit-session responses resolve the stored `(instrument_key, instrument_version)` first so historical submissions render/export against the version they used, not whichever version is active later. The database row metadata is authoritative; response builders override stale `content.en.instrument_version` values when a row was uploaded with mismatched embedded metadata. For legacy rows incorrectly stamped as `5.2`, the response builder compares stored response question keys against the stored-version instrument and active instrument, then uses the active instrument only when it covers more of the actual stored responses.

---

## 2. Playspace Normalized Draft Tables

These tables are the **live write path** during an active audit session. They are cleared automatically when the parent `PlayspaceSubmission` is deleted (CASCADE). At submission, the service reads from these rows to compute scores and writes the JSONB snapshot; they remain as a durable draft record until the next session opens.

### `playspace_submission_contexts`

One-to-one session metadata per submission.

| Column                      | Notes                                              |
| --------------------------- | -------------------------------------------------- |
| `submission_id`             | UUID primary key and FK → `playspace_submissions`  |
| `execution_mode`            | Auditor self-selected `audit`, `survey`, or `both` |
| `draft_progress_percent`    |                                                    |
| `created_at` / `updated_at` |                                                    |

---

### `playspace_pre_submission_answers`

One row per pre-audit selection.

| Column           | Notes                                                                                     |
| ---------------- | ----------------------------------------------------------------------------------------- |
| `id`             | UUID primary key                                                                          |
| `submission_id`  | FK → `playspace_submissions`                                                              |
| `field_key`      | `season`, `weather_conditions`, `users_present`, `user_count`, `age_groups`, `place_size` |
| `selected_value` |                                                                                           |
| `sort_order`     |                                                                                           |
| `created_at`     |                                                                                           |

**Unique constraint:** `(submission_id, field_key, selected_value)`

---

### `playspace_submission_sections`

One row per audit section with section-level note state.

| Column                      | Notes                        |
| --------------------------- | ---------------------------- |
| `id`                        | UUID primary key             |
| `submission_id`             | FK → `playspace_submissions` |
| `section_key`               |                              |
| `note`                      |                              |
| `created_at` / `updated_at` |                              |

**Unique constraint:** `(submission_id, section_key)`

---

### `playspace_question_responses`

One row per question within a section.

| Column                      | Notes                                |
| --------------------------- | ------------------------------------ |
| `id`                        | UUID primary key                     |
| `section_id`                | FK → `playspace_submission_sections` |
| `question_key`              |                                      |
| `note`                      | Nullable question-level auditor comment |
| `created_at` / `updated_at` |                                      |

**Unique constraint:** `(section_id, question_key)`

---

### `playspace_scale_answers`

One row per answered scale inside a question response.

| Column                      | Notes                               |
| --------------------------- | ----------------------------------- |
| `id`                        | UUID primary key                    |
| `question_response_id`      | FK → `playspace_question_responses` |
| `scale_key`                 |                                     |
| `option_key`                |                                     |
| `created_at` / `updated_at` |                                     |

**Unique constraint:** `(question_response_id, scale_key)`

---

### `playspace_checklist_answers`

One row per checklist-style question response. This table stores the array/object payload that cannot safely fit in `playspace_scale_answers`.

| Column                 | Notes                                                    |
| ---------------------- | -------------------------------------------------------- |
| `id`                   | UUID primary key                                         |
| `question_response_id` | FK → `playspace_question_responses`, unique one-to-one   |
| `selected_option_keys` | JSONB array of selected checklist option keys            |
| `other_details`        | JSONB object for optional checklist free text, e.g. text |
| `created_at` / `updated_at` |                                                   |

**Unique constraint:** `(question_response_id)`

**Runtime payload shape:** API responses still expose checklist answers as `selected_option_keys: string[]` plus optional `other_details: { text: string }` inside the question response payload. Mobile and frontend clients should continue using that shape rather than depending on this table directly.

---

## 3. Current Playspace Score Model

Scoring is computed from the audit's JSONB response payload, then serialized into typed partition scores and stored on `playspace_submissions`.

| Bucket              | Type            |
| ------------------- | --------------- |
| `provision_total`   | Column total    |
| `diversity_total`   | Column total    |
| `challenge_total`   | Column total    |
| `sociability_total` | Construct total |
| `play_value_total`  | Construct total |
| `usability_total`   | Construct total |

These totals are returned in: audit session responses · assigned-place summaries · dashboard/stat payloads where applicable.

---

## 4. Dual-Storage Boundary

| Phase              | Storage                          | Rationale                                             |
| ------------------ | -------------------------------- | ----------------------------------------------------- |
| Draft / in-session | Normalized tables above          | Fast per-question upserts; no race conditions         |
| Post-submission    | JSONB on `playspace_submissions` | Immutable snapshot; single-row reads; no JOINs needed |

`audit_state.py` is the source-of-truth bridge for this boundary. Draft saves write normalized rows; submission builds the immutable `playspace_submissions.responses_json` snapshot from those rows exactly once. Submitted audit reads use the JSONB snapshot, while draft reads rebuild the same API shape from normalized relations.

Checklist compatibility note: versions before `20260514_0010` could store checklist payload keys such as `selected_option_keys` and `other_details` as malformed `playspace_scale_answers` strings. The read path normalizes those recoverable stringified values back into the client-facing checklist shape.

---

## Historical Compatibility Caches

`audits.responses_json` and `audits.scores_json` exist for the YEE audit shell and are written alongside `Audit` rows in the YEE seed and submission flow.

`playspace_submissions.responses_json` and `playspace_submissions.scores_json` are the Playspace canonical records: `responses_json` holds the complete audit payload; `scores_json` holds the computed score partitions.

---

## 5. Legacy Checklist Data Migration

Migration `20260514_0010_add_checklist_answers.py` adds the normalized checklist table for new draft saves. It does **not** rewrite historical rows automatically, so it is safe to deploy without blocking reads/writes.

Recommended no-impact cleanup path:

1. **Deploy code + schema first.** Run `alembic -x product=playspace upgrade head`. Leave the legacy read-normalization code enabled.
2. **Measure recoverable legacy data.** In a read-only query, count `playspace_scale_answers` rows where `scale_key IN ('selected_option_keys', 'other_details')` and inspect whether `option_key` contains parseable JSON/Python-list strings. Also sample submitted `playspace_submissions.responses_json` for checklist keys stored as strings.
3. **Backfill in small batches.** For each recoverable normalized question response, insert or update one `playspace_checklist_answers` row from the parsed `selected_option_keys` / `other_details`, then delete only the legacy malformed `playspace_scale_answers` rows for those two keys. Keep this script idempotent and log skipped/unparseable rows.
4. **Repair mis-stamped instrument versions.** Before removing compatibility, identify submitted rows with `instrument_version='5.2'` whose stored response question keys match the active/versioned 5.13 instrument better than the 5.2 instrument. Update those rows to the correct `instrument_version` in small batches after spot-checking rendered audit details and reports.
5. **Rebuild submitted snapshots only when needed.** Historical submitted audits display through response normalization even without rewriting `responses_json`. If permanent cleanup is desired, update only checklist fields inside `responses_json` from stringified values to arrays/objects in batched transactions, preserving all other snapshot data unchanged.
6. **Verify before removing compatibility.** Confirm zero remaining malformed rows, zero stringified checklist keys in submitted snapshots, and no mis-stamped instrument versions, run audit detail/report export smoke tests, then remove `_normalize_legacy_checklist_payload()`, the checklist-specific string parsing, and the response-key instrument fallback in a later PR.

This path avoids downtime because new writes use the new table immediately, existing reads remain compatible during the backfill, and cleanup can be paused or rolled back before compatibility code is removed.

---

## 6. Not In The Current Schema

The following are **not** current backend tables:

- Generic Playspace `Audit_Responses` table
- Standalone `audit_scores` table
- Weighted Playspace score columns such as `base_total_score` or `weighted_total_score`
- Playspace manager-survey tables for combined scoring
- Reliability / kappa comparison tables
