# Copilot code review instructions

Review pull requests for concrete correctness, security, privacy, tenant-isolation and regression risks. Prefer actionable findings over style opinions.

## Project invariants

- Preserve the layering rule: `api -> services -> providers`.
- Every tenant-owned query and vector lookup must remain explicitly tenant-scoped.
- PostgreSQL is the source of truth for document text and provenance; Qdrant payloads contain identifiers only.
- Cross-tenant access, citation leakage and foreign source IDs are release-blocking defects.
- Default logs and traces must never contain document text, retrieved passages, prompts, user questions, model answers, credentials, cookies or request bodies.
- External provider failures and optional observability failures must follow the documented fail-open/fail-closed semantics instead of being silently swallowed.
- CI must never make paid external API calls.

## What to inspect carefully

- Retry, timeout and partial-failure behavior around external providers.
- Database/session lifecycle when external side effects and commits are combined.
- Delete/reindex/indexing races and stale-vector behavior.
- Provider/model/version identity and configuration wiring from settings to the actual call site.
- Grounding, source validation, no-evidence behavior and conflict handling.
- Migrations, downgrade/upgrade behavior and model/schema drift.
- Tests that only prove mocks agree with the implementation instead of exercising production wiring.

## Test integrity

- Flag any test that is skipped, marked xfail, deleted, or whose assertions were loosened so a previously failing behavior now passes. This is a hard rule in `docs/AUTOMATIONS.md`. Removing a test is only legitimate when the behavior it covered was itself removed, and the pull request must say so.

## Secrets in the diff

- Flag any credential, API key, token, private key or filled `.env` file appearing in the diff, including in fixtures, test data and example files (`docs/AUTOMATIONS.md`).

## Mandatory human-gate categories

- Flag it when a pull request touches a category that `docs/AUTOMATIONS.md` lists as requiring owner approval: authentication/authorization, tenant isolation, secrets, deployment/infrastructure, destructive migrations, deletion/retention semantics, GitHub Actions or permissions, major dependency upgrades, paid live-provider execution, Safety PLC behavior, machine control, protected PLC blocks.
- State which category applies and that the `owner-approval-required` label belongs on the pull request. Do not apply the label.

## Review style

- Flag a finding only when it can cause incorrect behavior, security/privacy exposure, data inconsistency, broken compatibility or a meaningful untested regression.
- Verify the surrounding implementation before claiming a bug.
- Give the smallest safe fix and the regression test that should accompany it.
- Do not recommend unrelated framework changes, broad refactors or speculative architecture work.
- Treat existing repo architecture and issue scope as constraints unless they are the source of the defect.
