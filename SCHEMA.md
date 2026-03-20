# Audit System Database Schema

This document records the current schema used by `audit-tools-backend`.

The backend has:

- shared core tables used by both products
- Playspace-specific normalized audit tables
- transitional JSONB caches on `audits` for compatibility with shared layers and older clients

## 1. Shared Core Tables

### `accounts`
Top-level login/account record.

- `id` - UUID primary key
- `name` - account display name
- `email` - unique login email
- `password_hash` - nullable password hash
- `account_type` - `MANAGER` or `AUDITOR`
- `created_at`
- `updated_at`

### `manager_profiles`
Manager profile rows owned by a manager account.

- `id` - UUID primary key
- `account_id` - FK to `accounts`
- `full_name`
- `email`
- `phone`
- `position`
- `organization`
- `is_primary`
- `created_at`
- `updated_at`

### `auditor_profiles`
Auditor identity/profile rows owned by auditor accounts.

- `id` - UUID primary key
- `account_id` - unique FK to `accounts`
- `auditor_code` - unique visible identifier used in reports
- `email` - nullable, unique when present
- `full_name`
- `age_range`
- `gender`
- `country`
- `role`
- `created_at`
- `updated_at`

### `projects`
Projects belong to one account.

- `id` - UUID primary key
- `account_id` - FK to `accounts`
- `name`
- `overview`
- `place_types`
- `start_date`
- `end_date`
- `est_places`
- `est_auditors`
- `auditor_description`
- `created_at`
- `updated_at`

### `places`
Places currently belong to one project.

- `id` - UUID primary key
- `project_id` - FK to `projects`
- `name`
- `city`
- `province`
- `country`
- `place_type`
- `lat`
- `lng`
- `start_date`
- `end_date`
- `est_auditors`
- `auditor_description`
- `created_at`
- `updated_at`

> Note: a place-to-project many-to-many relationship was discussed but is not implemented.

### `auditor_assignments`
Assignments grant project-level or place-level access to an auditor.

- `id` - UUID primary key
- `auditor_profile_id` - FK to `auditor_profiles`
- `project_id` - nullable FK to `projects`
- `place_id` - nullable FK to `places`
- `audit_roles` - array of assignment role strings
- `assigned_at`
- `created_at`
- `updated_at`

Exactly one of `project_id` or `place_id` is present for each assignment row.

### `audits`
Shared audit shell record used by both products.

- `id` - UUID primary key
- `place_id` - FK to `places`
- `auditor_profile_id` - FK to `auditor_profiles`
- `audit_code` - unique generated audit identifier
- `instrument_key`
- `instrument_version`
- `status` - `IN_PROGRESS`, `PAUSED`, or `SUBMITTED`
- `started_at`
- `submitted_at` - nullable until submit
- `total_minutes` - nullable until computed
- `summary_score` - nullable compact summary used by list/dashboard views
- `responses_json` - JSONB compatibility cache
- `scores_json` - JSONB compatibility cache
- `created_at`
- `updated_at`

For Playspace, `summary_score` is currently `play_value_total + usability_total`.

## 2. Playspace Normalized Audit Tables

Playspace no longer relies on a generic `Audit_Responses` table. Audit state is
stored in product-specific normalized tables instead.

### `playspace_audit_contexts`
One-to-one audit metadata row.

- `audit_id` - UUID primary key and FK to `audits`
- `execution_mode`
- `draft_progress_percent`
- `created_at`
- `updated_at`

### `playspace_pre_audit_answers`
One row per pre-audit selection.

- `id` - UUID primary key
- `audit_id` - FK to `audits`
- `field_key` - e.g. `season`, `weather_conditions`, `users_present`, `user_count`, `age_groups`, `place_size`
- `selected_value`
- `sort_order`
- `created_at`
- `updated_at`

### `playspace_audit_sections`
One row per audit section with section-level note state.

- `id` - UUID primary key
- `audit_id` - FK to `audits`
- `section_key`
- `note`
- `created_at`
- `updated_at`

### `playspace_question_responses`
One row per question within a section.

- `id` - UUID primary key
- `section_id` - FK to `playspace_audit_sections`
- `question_key`
- `created_at`
- `updated_at`

### `playspace_scale_answers`
One row per answered scale inside a question response.

- `id` - UUID primary key
- `question_response_id` - FK to `playspace_question_responses`
- `scale_key`
- `option_key`
- `created_at`
- `updated_at`

## 3. Playspace Scoring Model

Playspace scoring is computed directly from normalized audit rows.

The current raw score buckets are:

- `quantity_total`
- `diversity_total`
- `challenge_total`
- `sociability_total`
- `play_value_total`
- `usability_total`

These totals are stored in `audits.scores_json` as a compatibility cache and
returned to clients in typed score payloads.

## 4. Compatibility Notes

- `responses_json` and `scores_json` are still kept on `audits`, but Playspace writes normalized rows first.
- Older clients or shared dashboard layers may still read from the JSONB caches.
- Submitted audits with older cached score shapes can be recomputed from normalized rows.

## 5. Not Implemented In Current Schema

The following concepts have been discussed or documented historically, but are
not current database tables in the backend:

- generic `Audit_Responses` table for Playspace
- standalone `audit_scores` table
- weighted Playspace score columns such as `base_total_score` or `weighted_total_score`
- Playspace `Manager_Surveys` table for combined scoring
- reliability/kappa comparison tables