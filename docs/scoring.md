# YEE Scoring

## Goal

The YEE scoring pipeline has two layers:

1. question-level scoring from the source instrument
2. aggregate domain and total scoring used in reports and exports

The system should not invent missing score mappings. Question scoring must follow the source survey definition.

## Source Of Truth

Each audit is stamped with an instrument key and version. Runtime scoring
resolves the scoring contract for that stamp and stores a canonical score
snapshot with the submission. The snapshot, not the currently active
instrument, is authoritative for historical reports.

The implementation lives in:

- `app/products/yee/services/runtime_scoring.py` - resolves the stamped contract
- `app/products/yee/services/scoring_engine.py` - computes canonical snapshots
- `app/products/yee/services/scoring_types.py` - defines snapshot types
- `app/products/yee/schemas/audits.py` - validates and flattens snapshots for API responses

`app/products/yee/services/scoring_spec.py` supplies the schema-v1 baseline.
Stored instrument versions may provide a compatible contract through
`scoring_contract_from_instrument`.

## Layer 1: Question-Level Scoring

### What happens

The backend:

- resolves the audit's stamped scoring contract
- matches submitted response IDs to contract scoring entries
- applies item-specific and reverse-coded mappings
- accumulates item, domain, section, category, and total scores
- stores the scoring algorithm identifier with the canonical snapshot

### Why this matters

This approach preserves the scoring behavior defined in the instrument itself, including:

- non-trivial mappings
- reverse-coded items
- item-specific choice/answer grading

If a mapping is not defined in the instrument, the backend should not fabricate one.

## Layer 2: Aggregate YEE Scoring

Aggregate scoring is part of the canonical snapshot. It is built from the raw
item scores, the stamped contract's item counts and maxima, and
`participant_info.domain_weights`. Reporting and export paths flatten that
stored snapshot; clients do not recompute final scores.

## Domains

The current domain order is:

1. `ACCESS`
2. `ACTIVITY SPACES`
3. `AMENITIES`
4. `EXPERIENCE OF THE SPACE`
5. `AESTHETICS & CARE`
6. `USE & USABILITY`

In code, the normalized keys are:

- `access`
- `activitySpaces`
- `amenities`
- `experienceOfSpace`
- `aestheticsAndCare`
- `useAndUsability`

## Weighting Values

The survey asks the auditor to rate the importance of each domain.

Supported weight values:

- `Very important to me = 3`
- `Somewhat important to me = 2`
- `Not really important to me = 1`

These values are stored in `participant_info_json.domain_weights`.

## Required Aggregate Outputs

For each submitted audit:

### 1. Raw Domain Score

For each domain:

- sum all scored question values in that domain
- store the domain maximum from the same stamped scoring contract

### 2. Youth Weighted Domain Score

For each domain:

- divide the raw domain score by that domain's item count
- normalize the selected domain weights so they sum to one
- multiply the domain average by its normalized weight
- store the corresponding weighted maximum from the same contract

### 3. Total Enabling Environment Raw Score

- sum all six raw domain scores
- sum all six contract-derived raw domain maxima

### 4. Total Enabling Environment Youth-Weighted Score

- sum all six weighted domain scores
- sum the exact weighted domain maxima, then round the total to two decimal places

## Canonical Maximums

Every new canonical snapshot stores:

- `raw.domain_maximums` and `raw.total_maximum`
- `weighted.domain_maximums` and `weighted.total_maximum`

These values are tied to the submission's scoring contract. They must not be
replaced by a global constant or recomputed from the currently active
instrument.

Older snapshots that predate these fields are backfilled only from metadata
frozen inside that snapshot. If a legacy list row cannot be resolved safely,
list APIs return nullable maxima instead of inventing a denominator or failing
the entire list.

## Where The Logic Lives

### Backend

- `app/products/yee/services/scoring_engine.py`
  - question-level scoring and canonical raw/weighted snapshots
- `app/products/yee/services/score_snapshots.py`
  - stored snapshot resolution for submitted audits
- `app/products/yee/services/dashboard.py`
  - dashboard and reporting response construction
- `app/products/yee/schemas/audits.py`
  - legacy snapshot backfill and flattened API fields

### Clients

Web and mobile clients display the backend score and its canonical maximum.
They may derive a presentation percentage, but they do not own score or maximum
calculation.

## Reporting Expectations

Managers and admins should be able to compare audits for the same place and see:

- raw and youth-weighted percentages as the primary human-readable values
- raw and youth-weighted score fractions as secondary context
- domain-level and total scores with contract-derived maxima
- averages across selected audits

For mixed instrument versions, percentage averages are means of each audit's
own percentage. A raw fraction may be labelled with a shared maximum only when
all included audits have that same maximum. A missing, non-finite, or
non-positive maximum is unavailable; it is not `0%`.

The backend supplies place comparison groups and export rows from stored
canonical snapshots.

## Raw Data Export Expectations

CSV-ready raw export should include:

- audit ID
- generated auditor ID
- project and place identifiers
- date and timing fields
- high-level survey answers
- all question responses
- raw domain scores
- weighted domain scores
- total raw score
- total weighted score

The current backend raw-data endpoint provides row-level data structured for frontend CSV export.

## Known Limitation

Cap score logic is not implemented.

This is intentional. The code should remain extensible for cap scoring, but no guessed cap behavior should be added until the scoring rules are finalized.

## Guidance For Future Engineers

- keep the backend's stamped scoring contract and canonical snapshot authoritative
- never add client-side fallback maxima or recompute historical maxima from an active instrument
- if scoring or response contracts change, update backend, shared contracts, web, and mobile together
- preserve nullable maxima for unresolved legacy list rows so clients can show an honest unavailable state
- if cap scoring is added later, isolate it as a separate layer instead of mixing it into raw-domain calculations
