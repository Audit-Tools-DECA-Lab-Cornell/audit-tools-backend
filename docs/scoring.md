# YEE Scoring

## Goal

The YEE scoring pipeline has two layers:

1. question-level scoring from the source instrument
2. aggregate domain and total scoring used in reports and exports

The system should not invent missing score mappings. Question scoring must follow the source survey definition.

## Source Of Truth

Question scoring is derived from:

- `app/data/yee_instrument.qsf`

Parsing and score application are implemented in:

- `app/yee_scoring.py`

## Layer 1: Question-Level Scoring

### What happens

The backend:

- loads the QSF file
- parses scoring categories
- parses question block membership
- parses grading rows for single-choice and matrix-style items
- matches user responses to grading rows
- accumulates category totals and section totals

### Why this matters

This approach preserves the scoring behavior defined in the instrument itself, including:

- non-trivial mappings
- reverse-coded items
- item-specific choice/answer grading

If a mapping is not defined in the instrument, the backend should not fabricate one.

## Layer 2: Aggregate YEE Scoring

Aggregate scoring is built from:

- stored `section_scores_json`
- stored `participant_info_json.domain_weights`

This logic is used in reporting/export paths and is aligned with the frontend review summary.

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

### 2. Youth Weighted Domain Score

For each domain:

- `raw domain score * domain weight`

### 3. Total Enabling Environment Raw Score

- sum all six raw domain scores

### 4. Total Enabling Environment Youth-Weighted Score

- sum all six weighted domain scores

## Where The Logic Lives

### Backend

- `app/yee_scoring.py`
  - question-level matching and section score generation
- `app/dashboard_router.py`
  - aggregate reporting/export derivation from stored submission data

Important helpers there include:

- `_section_to_domain`
- `_extract_domain_weights`
- `_build_submission_scores`

### Frontend

- `src/lib/yee-scoring.ts`
- `src/components/yee/yee-score-summary.tsx`

The frontend currently uses backend raw/section data and applies the weighting layer for review/submitted displays.

## Reporting Expectations

Managers and admins should be able to compare audits for the same place and see:

- raw domain scores
- weighted domain scores
- total raw scores
- total weighted scores
- averages across selected audits

The backend already supplies place comparison groups and export rows using stored YEE submissions.

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

- do not replace QSF-derived scoring with hardcoded front-end mappings
- keep backend question scoring as the authoritative interpretation of the instrument
- if aggregate logic changes, update backend reporting/export and frontend review displays together
- if cap scoring is added later, isolate it as a separate layer instead of mixing it into raw-domain calculations
