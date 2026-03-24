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
- [Playspace Normalized Audit Tables](#2-playspace-normalized-audit-tables)
- [Playspace Write-Path Notes](#3-playspace-write-path-notes)
- [Current Score Model](#4-current-playspace-score-model)
- [Compatibility Caches](#5-compatibility-caches)
- [Not In The Current Schema](#6-not-in-the-current-schema)

---

## 1. Shared Core Tables

### `accounts`

Top-level login/account record.


| Column                      | Notes                  |
| --------------------------- | ---------------------- |
| `id`                        | UUID primary key       |
| `name`                      | Account display name   |
| `email`                     | Unique login email     |
| `password_hash`             | Nullable               |
| `account_type`              | `ADMIN`, `MANAGER`, or `AUDITOR` |
| `created_at` / `updated_at` |                        |


---

### `manager_profiles`

Manager profile rows owned by a manager account.


| Column                          | Notes            |
| ------------------------------- | ---------------- |
| `id`                            | UUID primary key |
| `account_id`                    | FK → `accounts`  |
| `full_name` / `email` / `phone` |                  |
| `position` / `organization`     |                  |
| `is_primary`                    |                  |
| `created_at` / `updated_at`     |                  |


---

### `auditor_profiles`

Auditor identity/profile rows owned by auditor accounts.


| Column                                                    | Notes                                                       |
| --------------------------------------------------------- | ----------------------------------------------------------- |
| `id`                                                      | UUID primary key                                            |
| `account_id`                                              | Unique FK → `accounts`                                      |
| `auditor_code`                                            | Unique public-facing identifier used in reports and exports |
| `email`                                                   | Nullable, unique when present                               |
| `full_name` / `age_range` / `gender` / `country` / `role` |                                                             |
| `created_at` / `updated_at`                               |                                                             |


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
| `created_at` / `updated_at`   |                  |


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
| `created_at` / `updated_at`              |                  |


---

### `project_places`

Join table linking places to projects.


| Column      | Notes                                   |
| ----------- | --------------------------------------- |
| `project_id` | FK → `projects`                         |
| `place_id`   | FK → `places`                           |
| `linked_at`  | Timestamp recorded when the link is set |


**Primary key:** `(project_id, place_id)`

---

### `auditor_assignments`

Assignments grant project-level or project-place-level access to an auditor.


| Column                                      | Notes                                   |
| ------------------------------------------- | --------------------------------------- |
| `id`                                        | UUID primary key                        |
| `auditor_profile_id`                        | FK → `auditor_profiles`                 |
| `project_id`                                | Required FK → `projects`                |
| `place_id`                                  | Nullable FK → `places`                  |
| `assigned_at` / `created_at` / `updated_at` |                                         |


> **Invariant:** `project_id` is always set. When `place_id` is also set, the row is scoped to one specific `(project_id, place_id)` pair.

---

### `audits`

Shared audit shell record used by both products.


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


> **Current Playspace rule:** `summary_score = play_value_total + usability_total`
>
> **Current uniqueness rule:** one audit per `(project_id, place_id, auditor_profile_id)`.

---

## 2. Playspace Normalized Audit Tables

Playspace audit state is stored in product-specific normalized tables. These are the **current authoritative write targets** for draft and submit flows.

### `playspace_audit_contexts`

One-to-one audit metadata row.


| Column                      | Notes                              |
| --------------------------- | ---------------------------------- |
| `audit_id`                  | UUID primary key and FK → `audits` |
| `execution_mode`            | Auditor self-selected `audit`, `survey`, or `both` |
| `draft_progress_percent`    |                                    |
| `created_at` / `updated_at` |                                    |


---

### `playspace_pre_audit_answers`

One row per pre-audit selection.


| Column                      | Notes                                                                                     |
| --------------------------- | ----------------------------------------------------------------------------------------- |
| `id`                        | UUID primary key                                                                          |
| `audit_id`                  | FK → `audits`                                                                             |
| `field_key`                 | `season`, `weather_conditions`, `users_present`, `user_count`, `age_groups`, `place_size` |
| `selected_value`            |                                                                                           |
| `sort_order`                |                                                                                           |
| `created_at` / `updated_at` |                                                                                           |


**Unique constraint:** `(audit_id, field_key, selected_value)`

---

### `playspace_audit_sections`

One row per audit section with section-level note state.


| Column                      | Notes            |
| --------------------------- | ---------------- |
| `id`                        | UUID primary key |
| `audit_id`                  | FK → `audits`    |
| `section_key`               |                  |
| `note`                      |                  |
| `created_at` / `updated_at` |                  |


**Unique constraint:** `(audit_id, section_key)`

---

### `playspace_question_responses`

One row per question within a section.


| Column                      | Notes                           |
| --------------------------- | ------------------------------- |
| `id`                        | UUID primary key                |
| `section_id`                | FK → `playspace_audit_sections` |
| `question_key`              |                                 |
| `created_at` / `updated_at` |                                 |


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

## 3. Playspace Write-Path Notes

Normalization helpers must respect natural unique keys. Repeated draft saves should:


| Rule                                                                                 |
| ------------------------------------------------------------------------------------ |
| Reuse existing rows when the logical key is unchanged                                |
| Update/remove rows that are no longer present                                        |
| Avoid blind delete/reinsert replacement that can collide with uniqueness constraints |


This is especially critical for: pre-audit answers · section rows · question response rows · scale answer rows.

---

## 4. Current Playspace Score Model

Scoring is computed from normalized audit rows, then serialized into typed payloads and compatibility caches.


| Bucket              | Type            |
| ------------------- | --------------- |
| `quantity_total`    | Column total    |
| `diversity_total`   | Column total    |
| `challenge_total`   | Column total    |
| `sociability_total` | Construct total |
| `play_value_total`  | Construct total |
| `usability_total`   | Construct total |


These totals are returned in: audit session responses · assigned-place summaries · dashboard/stat payloads where applicable.

---

## 5. Compatibility Caches

`audits.responses_json` and `audits.scores_json` are still present because some consumers depend on them.


| Step | Action                                                              |
| ---- | ------------------------------------------------------------------- |
| 1    | Playspace writes normalized rows first                              |
| 2    | Compatibility caches are rebuilt or maintained alongside those rows |


> Older or shared layers may still read JSON caches. Normalized data remains the source for scoring and durable draft state.

---

## 6. Not In The Current Schema

The following have been discussed historically but are **not** current backend tables:

- Generic Playspace `Audit_Responses` table
- Standalone `audit_scores` table
- Weighted Playspace score columns such as `base_total_score` or `weighted_total_score`
- Playspace manager-survey tables for combined scoring
- Reliability / kappa comparison tables

