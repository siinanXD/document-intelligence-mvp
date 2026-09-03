# Copilot code review instructions

Review pull requests for concrete correctness, security, privacy and regression risks. Prefer actionable findings over style opinions.

The project-specific review invariants live in the five authoritative skills under `.github/skills/`. Read the applicable `SKILL.md` files before reviewing and cite the skill name when reporting an invariant violation. Do not recreate those rules here.

## What to inspect carefully

- Retry, timeout and partial-failure behavior around external providers.
- Database/session lifecycle when external side effects and commits are combined.
- Delete/reindex/indexing races and stale-vector behavior.
- Provider/model/version identity and configuration wiring from settings to the actual call site.
- Grounding, source validation, no-evidence behavior and conflict handling.
- Migrations, downgrade/upgrade behavior and model/schema drift.
- Tests that only prove mocks agree with the implementation instead of exercising production wiring.

## Secrets in the diff

Flag any credential, API key, token, private key or filled `.env` file appearing in the diff, including fixtures, test data and example files.

## Mandatory human-gate categories

Flag it when a pull request touches a category that `docs/AUTOMATIONS.md` lists as requiring owner approval. State which category applies and that the `owner-approval-required` label belongs on the pull request. Do not apply the label.

## Review style

- Flag a finding only when it can cause incorrect behavior, security/privacy exposure, data inconsistency, broken compatibility or a meaningful untested regression.
- Verify the surrounding implementation before claiming a bug.
- Give the smallest safe fix and the regression test that should accompany it.
- Do not recommend unrelated framework changes, broad refactors or speculative architecture work.
- Treat existing repository architecture and issue scope as constraints unless they are the source of the defect.
