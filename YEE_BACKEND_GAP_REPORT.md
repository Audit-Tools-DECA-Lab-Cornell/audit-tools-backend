YEE Backend Gap Report

> Status note: YEE backend module extraction has progressed since this report was
> written. YEE product routes now live under `app/products/yee/routes/`
> (`audits.py`, `instrument.py`, `dashboard.py`), with `app/dashboard_router.py`
> still holding `/yee/dashboard/*`; `app/yee_router.py` no longer exists. Read the
> "concentrated in top-level routers" framing below as the earlier baseline. For the
> current shape see `docs/architecture.md` and `docs/client-map.md`. This report is
> retained as planning context.

Comparison target: the current Playspace backend inside the same repository. Focus: architecture shape, route surface, schema depth, and test maturity.

Bottom line
YEE is partially complete functionally, but it is still far behind Playspace in backend structure. The biggest issue is not that YEE has zero features; it is that the implemented features are still concentrated in top-level routers instead of living in a real product module with dedicated services, schemas, state helpers, and product-owned tests.
84
Playspace product routes
46
YEE product routes outside shared auth
108
Playspace product tests
23
YEE product tests
Source: route decorator counts under product route files plus broad repo scan of product test files on Jun 24, 2026.

What YEE Already Has
Area What exists now
Auth and onboarding Shared auth endpoints support YEE login, signup, verify-email, password reset, auditor invite acceptance, and manager invite acceptance.
Manager and admin operations Dashboard routes already cover projects, places, auditors, invites, approvals, assignments, manager profiles, and raw-data/comparison reporting.
Auditor audit flow YEE exposes instrument fetch, audit-state, draft save, score preview, submit, list-my-audits, and submission detail.
Migrations and product DB split YEE has its own Alembic branch and its own submission table, separate from Playspace-only tables.
Main Gaps
Workstream Current state Gap to close Severity Effort
YEE product architecture Real YEE logic still lives in top-level routers; product folder is mostly a stub. Move YEE into product-scoped routes, services, schemas, and helpers the same way Playspace is organized.
Audit persistence model YEE stores drafts in shared audits JSON and finals in both audits and yee_audit_submissions. Decide whether YEE keeps this dual-write shell or needs a cleaner YEE-specific state model like Playspace.
API contract maturity Core manager, admin, invite, audit, and reporting flows exist, but they are centralized and less isolated. Split request handling from business logic and define explicit YEE schemas/contracts instead of inline router models.
Test coverage YEE has one broad integration file with 23 tests. Add route-surface, service, scoring, state, and regression tests closer to Playspace coverage patterns.
Schema depth YEE-only schema is only yee_audit_submissions plus shared core tables. Confirm whether YEE needs more normalized product-owned tables or whether the simpler model is intentional.
Feature parity review YEE has projects, places, invites, assignments, reports, and audits, but not Playspace's full product surface. Decide which Playspace-only capabilities are truly shared expectations versus intentionally product-specific.
Interpretation: if your target is “YEE differs only in submission model and table shape,” this is still a substantial backend catch-up project.

Evidence Highlights
Playspace is fully modular
Playspace owns dedicated route groups, services, schemas, jobs, instruments, scoring, and audit-state modules under one product namespace.

app/products/playspace/routes/**init**.py

app/products/playspace/services/

app/products/playspace/schemas/

app/products/playspace/audit_state.py

app/products/playspace/scoring.py

YEE product folder is still skeletal
The intended product module exists, but it only exposes a status endpoint while actual YEE behavior remains elsewhere.

app/products/yee/routes.py

app/yee_router.py

app/dashboard_router.py

app/main.py

Playspace owns richer product tables
Playspace has normalized draft/session tables and product migrations that support long-lived state, scoring, and recovery behavior.

app/models.py

alembic/versions/ps_0001_playspace_tables.py

SCHEMA.md

YEE schema is intentionally thinner
YEE currently owns only yee_audit_submissions on its product branch, with drafts and compatibility behavior leaning on shared audits.

app/models.py

alembic/versions/yee_0001_yee_audit_submissions.py

app/yee_router.py

Playspace test depth is much higher
Playspace has endpoint inventory, audit-state, scoring, bug-report, instrument, privacy, durability, and seed tests across many files.

tests/products/playspace/test_api_endpoints.py

tests/products/playspace/test_audit_state.py

tests/products/playspace/test_bug_reports.py

tests/products/playspace/test_scoring_runtime.py

YEE tests are broad but concentrated
YEE has meaningful end-to-end coverage for auth, manager invites, manager self-auditor creation, and audit draft/submit, but in a single file.

tests/products/yee/test_yee_routes.py

Practical Read
Functionally, YEE is not empty. The auth flow, core manager workflows, assignment flow, auditor flow, and reporting endpoints are already present.

Architecturally, though, YEE is still in a transitional state. The repo documentation points toward product-isolated modules under app/products/yee/, but the real code is still concentrated in app/yee_router.py and app/dashboard_router.py.

Schema-wise, Playspace has a durable normalized write path for drafts and submissions. YEE currently relies on a much simpler model, which may be acceptable, but only if that simplicity is intentional and sufficient for long-term reporting, edit, and recovery needs.

Testing is the clearest maturity signal: Playspace spreads coverage across many focused files, while YEE currently relies on one broad integration file.

Questions To Resolve Before Refactoring
Question Why it matters
Should YEE mirror the Playspace module layout under app/products/yee/? This decides whether the next step is mostly refactoring structure or only filling feature gaps.
Should YEE keep the current audits + yee_audit_submissions dual-write model? This decides whether new work builds on the current shell or introduces a cleaner YEE-specific state layer.
Which Playspace features are truly required for YEE parity? Some surfaces, especially bug reporting and some export/admin workflows, may be product-specific rather than missing parity work.
