---
name: mobile-release-policy-test-sync
description: Keep backend tests in lockstep with COPA/Playspace and YEE mobile release-policy versions. Use when changing minimum_supported_version, latest_version, minimum_supported_build, latest_build, PLAYSPACE_RELEASE_POLICY, YEE_RELEASE_POLICY, current_version, or any force-update floor in audit-tools-backend app/products/mobile_release_*.py.
---

# Mobile Release Policy Test Sync

## When to use

Same change as a version-gate edit. Trigger whenever `PLAYSPACE_RELEASE_POLICY` or `YEE_RELEASE_POLICY` (or any `latest_*` / `minimum_supported_*` field they contain) changes for COPA (`copa-mobile` / playspace) or YEE (`yee-mobile`). Also trigger after `mobile-version-bump` applies a confirmed floor or fallback `latest_version` change.

Do not declare the policy edit done until the matching tests move in the same change.

## Hard rule

A version-gate constant and its pytest assertions are one unit of work. Never ship one without the other. Do not import `PLAYSPACE_RELEASE_POLICY` / `YEE_RELEASE_POLICY` into those tests to dodge the edit — the contract tests are a second, conscious copy of the shipped fallback numbers.

## What to update

| Product | Policy object | Contract test |
| --- | --- | --- |
| playspace / COPA | `PLAYSPACE_RELEASE_POLICY` in `app/products/mobile_release_policy.py` | `tests/products/playspace/test_api_endpoints.py` → `test_playspace_mobile_release_policy_is_public` |
| yee | `YEE_RELEASE_POLICY` in the same file | `tests/products/yee/test_mobile_release_policy.py` → `test_yee_mobile_release_policy_is_public` |

Update every platform block you changed (`android` and `ios`) and every field you changed:

- `latest_version`
- `minimum_supported_version`
- `latest_build` / `minimum_supported_build` if set

If the Playspace contract test still only asserts Android, add the iOS assertions when you touch iOS values.

## What not to retarget

These files use synthetic fixture versions to prove resolver/parser/evaluator behavior. Leave them unless you changed that behavior:

- `tests/products/test_mobile_release_policy_sources.py` (Google / EAS / GitHub resolver unit tests; the EAS webhook test proves a cache override, not the live floor)
- `playspace/copa-mobile/tests/release-policy.spec.ts`
- `yee/yee-mobile/tests/unit/release-policy.test.ts`
- `ASSIGNED_FIRST_AUTHORING_V2_MOBILE_DISPLAY_VERSION` in `app/products/yee/services/migration_manifest.py` — a decoder-version marker, not the force-update floor

## Procedure

1. Diff `app/products/mobile_release_policy.py` and list product × platform × field → new value.
2. Edit the matching contract test assertions to those exact strings.
3. If you introduced `minimum_supported_build` (or iOS coverage that was missing), add assertions rather than leaving them implicit.
4. Run:

```bash
./.venv/bin/pytest tests/products/yee/test_mobile_release_policy.py tests/products/playspace/test_api_endpoints.py::test_playspace_mobile_release_policy_is_public tests/products/test_mobile_release_policy_sources.py --tb=short
```

5. Stop if pytest fails. Do not "fix" a mismatch by reverting the test to the old numbers or by reading the policy object from production code.

## Policy owner

Floor vs auto-resolved `latest_version`, and when to raise the floor: `docs/deployment.md` → "Mobile Release Policy Sources". Propose floor changes; apply only after the user confirms (`mobile-version-bump`).

This skill owns only the test pairing after that confirmation.
