# YEE Scoring Backend Implementation Plan

This document is the backend execution handoff for centralizing YEE scoring on
the FastAPI backend.

It is intentionally backend-first. Web and mobile client migration should
consume the backend contract that lands from this plan rather than re-deriving
score math locally.

## Goal

Replace the current split YEE scoring flow with one canonical backend scoring
engine that:

- implements the intended algorithm from the YEE instruction files
- persists a versioned score snapshot with each submission
- serves the same score object to preview, submit, report, dashboard, and export
  flows
- allows web and mobile to stop computing raw or weighted scores locally

## Acceptance

- Raw scoring no longer depends on the current QSF-row additive behavior in
  `app/yee_scoring.py` for the final YEE algorithm.
- Weighted scoring uses the newer normalized domain-average model from
  `yee/instructions/Weighted Scoring.xlsx`.
- `YeeAuditSubmission` stores a canonical backend-owned score snapshot plus a
  scoring version.
- YEE preview, submit response, submission detail, manager edit/re-submit,
  reporting, and raw export read from the same backend scoring model.
- Legacy fields remain available during rollout so web and mobile can migrate
  without a flag day.
- Tests cover both the scoring math and the API surfaces that expose it.

## Blast Radius

Cross-contract + migration + scoring/instrument.

Required coordination:

- backend migration safety
- backend/client contract rollout
- web and mobile follow-up once the backend payload is stable

## Source Documents For The Intended Algorithm

Read these before coding:

- `/Users/praty/Desktop/StudentJob.nosync/yee/instructions/Questions and Scoring.docx`
- `/Users/praty/Desktop/StudentJob.nosync/yee/instructions/Tool Structure and Scoring.xlsx`
- `/Users/praty/Desktop/StudentJob.nosync/yee/instructions/Weighted Scoring.xlsx`

## Current Backend State

Current touchpoints:

- raw scorer: `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/yee_scoring.py`
- thin YEE scoring wrapper:
  `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/services/scoring.py`
- auditor routes:
  `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/routes/audits.py`
- manager/reporting service:
  `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/services/dashboard.py`
- YEE submission ORM:
  `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/models.py`
- YEE schemas:
  `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/schemas/audits.py`
  `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/schemas/dashboard.py`

Current persistence in `YeeAuditSubmission`:

- `participant_info_json`
- `responses_json`
- `section_scores_json`
- `total_score`
- `submit_idempotency_key`

Current behavior problems:

- the backend raw scorer reflects the current QSF-derived implementation, not
  the intended item-level scoring model in the instruction files
- weighted scoring is derived separately in reporting paths rather than being
  part of one canonical score snapshot
- clients still reconstruct weighted scores locally
- mobile and web do not currently share the same weighting behavior

## Intended Scoring Model To Implement

### Raw scoring

Implement item-based scoring from the instruction files:

- paired presence + condition questions score as `presence x condition`
- presence-only three-level items score using the mapped ordinal values defined
  in the sheets
- reverse-coded items use the reversed numeric mapping specified in the sheets
- domain raw scores are the sum of all scored items in that domain

Do not keep the current QSF-row matching logic as the authoritative final YEE
algorithm once the new scorer is in place.

### Weighted scoring

Use the newer workbook model from `Weighted Scoring.xlsx`:

- unweighted domain score = sum of item scores in the domain
- unweighted domain average = domain raw score divided by the number of scored
  items in that domain
- normalized weight = raw youth-assigned domain weight divided by the total
  weight sum
- weighted domain score = unweighted domain average multiplied by normalized
  weight
- total weighted score = sum of weighted domain scores
- priority gap = `(max domain average - unweighted domain average) x normalized domain weight`

### Priority-gap decision for implementation

Use a domain-specific maximum average, not a fixed `3.0`.

Canonical rule:

- `max_domain_average = sum(max_item_score for each configured item in domain) / configured_domain_item_count`
- `priority_gap = (max_domain_average - unweighted_domain_average) x normalized_domain_weight`

Reasoning:

- `Weighting Instructions` says "Maximum Average Value for each Domain", which
  indicates the ceiling varies by domain.
- `Weighting example` includes an explicit `Max domain average` concept and a
  priority-gap example using a domain-specific ceiling such as `2.33`.
- the later `Max avg = 3` note in the example sheet conflicts with the rest of
  the workbook and should be treated as a stale annotation rather than the
  canonical rule.

Implementation note:

- compute `max_domain_average` from the configured scoring spec, not from a
  submission's answered-item count
- if every audit is complete, the configured item count and answered-item count
  will match, but the scoring engine should still use the configured ceiling
  from the instrument definition

## Recommended Persistence Model

Store the canonical score snapshot in JSONB and version it explicitly.

Recommended `YeeAuditSubmission` additions:

- `scores_json JSONB NOT NULL DEFAULT '{}'`
- `scoring_version TEXT NOT NULL DEFAULT 'yee_v2'`

Recommended transition behavior:

- keep `total_score` during rollout as a denormalized convenience field for
  list sorting/filtering and backward compatibility
- keep `section_scores_json` temporarily for compatibility only
- treat `scores_json` as the source of truth once the new scorer lands

Recommended `scores_json` shape:

```json
{
  "scoring_version": "yee_v2",
  "raw": {
    "total_score": 0,
    "domain_scores": {},
    "section_scores": {},
    "category_scores": {},
    "item_scores": {}
  },
  "weighted": {
    "raw_domain_weights": {},
    "normalized_domain_weights": {},
    "domain_average_scores": {},
    "weighted_domain_scores": {},
    "total_weighted_score": 0,
    "priority_gaps": {}
  },
  "meta": {
    "domain_order": [],
    "domain_item_counts": {}
  }
}
```

Notes:

- `item_scores` is recommended for auditability and debugging because the
  intended algorithm is item-based. If payload size becomes a concern, this can
  be made optional, but default to keeping it.
- Keep `scoring_version` both as a top-level DB column and inside `scores_json`
  so future backfills and mixed-version reads are easy to reason about.

## Smallest Viable Backend Scope

The safest minimal slice is:

1. add the new canonical score snapshot and versioning
2. implement the new scorer behind backend APIs
3. return the new score shape additively while preserving legacy fields
4. migrate web/mobile afterward
5. remove compatibility fields only after both clients are off local scoring

Do not combine this with UI redesign, export format redesign, or non-scoring
instrument edits.

## Implementation Plan

### Phase 0: Spec lock

Owner: backend

1. Re-read the three instruction files and turn the intended scoring rules into
   code-level constants and mapping tables.
2. Write down the final authoritative domain order, item counts, raw weight
   values, reverse-coded items, and paired presence/condition relationships.
3. Encode the priority-gap rule using the domain-specific maximum-average
   ceiling defined in this document.

Deliverable:

- one authoritative backend scoring config/module with no UI dependencies

### Phase 1: Build a canonical scoring engine

Owner: backend

Create a new YEE scoring module under
`app/products/yee/services/` that returns one canonical score snapshot from:

- `responses_json`
- `participant_info_json`

Recommended structure:

- `app/products/yee/services/scoring_spec.py`
- `app/products/yee/services/scoring_engine.py`

Responsibilities:

- item scoring
- domain aggregation
- normalized weight calculation
- weighted domain scores
- total weighted score
- priority gaps
- serialization into the canonical `scores_json` shape

Recommendation:

- keep `app/yee_scoring.py` only as a legacy wrapper during migration, or
  retire it entirely if the cutover is clean

### Phase 2: Add schema and migration support

Owner: backend

1. Update `app/models.py` to add:
   - `scores_json`
   - `scoring_version`
2. Add a new YEE Alembic migration, likely:
   - `alembic/versions/yee_0005_add_canonical_score_snapshot.py`
3. Ensure the migration only targets YEE tables.
4. Provide downgrade support.

Migration notes:

- this repo uses product-scoped Alembic branches
- the new migration must stay on the YEE branch
- do not edit existing applied migrations

### Phase 3: Replace score production and storage paths

Owner: backend

Update all write paths so they produce and persist the canonical score snapshot:

- auditor score preview route
- auditor submit route
- auditor submission detail route
- manager edit and re-submit flow
- any repair/backfill logic that recreates missing `YeeAuditSubmission` rows

Primary files:

- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/routes/audits.py`
- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/services/dashboard.py`
- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/services/scoring.py`

Rules:

- compute the canonical score once per write/update path
- persist it on `YeeAuditSubmission`
- also persist the compatible subset needed in `Audit.scores_json` while the
  clients are still migrating

### Phase 4: Expand the API contract additively

Owner: backend

Update Pydantic schemas so the same score object is exposed consistently across:

- score preview
- submit response
- audit state
- submission detail
- manager audit edit state
- place comparison reporting
- raw data export

Primary files:

- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/schemas/audits.py`
- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/schemas/dashboard.py`

Compatibility strategy:

- keep existing `total_score` and similar legacy fields in responses during the
  transition if web/mobile still expect them
- add the canonical score object without breaking current clients
- only remove legacy shapes after both clients are updated and verified

### Phase 5: Update reporting and export readers

Owner: backend

Refactor reporting/export code to read from `scores_json` instead of rebuilding
weighted scores ad hoc from `section_scores_json` and `participant_info_json`.

Primary file:

- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/app/products/yee/services/dashboard.py`

Target outcome:

- one read model for place comparison, overview/reporting rows, and raw export
- no duplicate weighted-score math outside the canonical scorer

### Phase 6: Backfill and compatibility cleanup

Owner: backend

Recommended rollout:

1. ship the additive schema and runtime persistence first
2. lazy-recompute missing `scores_json` on reads/writes for old submissions
3. run a one-off backfill only after the runtime path is proven stable
4. remove legacy reliance on `section_scores_json` after both clients migrate

Do not make the initial release depend on a full historical backfill.

## Test Obligation

Backend tests to update or add:

- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/tests/products/yee/test_yee_scoring.py`
- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/tests/products/yee/test_audit_lifecycle.py`
- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/tests/products/yee/test_audit_submit_validation.py`
- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/tests/products/yee/test_submit_durability.py`
- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/tests/products/yee/test_dashboard_reports.py`
- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/tests/products/yee/test_dashboard_raw_data.py`
- `/Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/tests/products/yee/test_dashboard_overview.py`

Test coverage goals:

- workbook example cases for raw scoring
- normalized weighting correctness
- priority-gap correctness after the baseline is confirmed
- preview, submit, detail, manager edit, reporting, and export contract
  consistency
- compatibility behavior for legacy fields during rollout
- repair/backfill logic for historical submissions without `scores_json`

Quality gates:

- `ruff`
- `mypy .`
- targeted YEE pytest slice

Important note:

- the YEE backend test harness is DB-backed and destructive, so run the YEE
  suite serially

## Risks

- the instruction documents are not fully aligned, especially around weighting
  and priority-gap interpretation
- changing the response contract too aggressively will break the clients before
  they are migrated
- historical submissions need a compatibility story during rollout
- `section_scores_json` and `total_score` may drift if they remain writable
  after `scores_json` becomes authoritative
- string-matching section names to domains should not survive as the final
  authoritative implementation

## Out Of Scope In This Pass

- frontend rendering changes
- mobile rendering changes
- offline mobile report behavior
- removal of every legacy response field in the same backend change
- non-scoring wording changes to the instrument
- cap-score logic

## Suggested Execution Prompt For A Fresh Chat

Use a fresh Codex chat and paste this:

```text
Read /Users/praty/Desktop/StudentJob.nosync/audit-tools-backend/docs/yee-scoring-backend-implementation-plan.md and execute the backend plan through the smallest safe additive slice.

Constraints:
- Backend only in this chat.
- Treat the backend as the future single source of truth for YEE scoring.
- Preserve compatibility fields during rollout unless removing them is explicitly proven safe.
- Read the three YEE instruction files before coding.
- If the priority-gap baseline is still ambiguous in the source docs, stop and surface the exact ambiguity before finalizing that part of the scorer.

Required verification:
- Run ruff, mypy ., and the relevant YEE backend tests.
- Summarize any API contract changes that web/mobile will need to consume.
```
