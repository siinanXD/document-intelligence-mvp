---
name: no-paid-providers-in-ci
description: Tests and CI never call paid external providers or depend on credentials or external network availability.
---

# No paid providers in CI

CI must be repeatable at zero external API cost and must not depend on provider availability.

## What to check

- Tests or fixtures constructing real OpenAI, Anthropic, hosted Hugging Face inference or another billed client.
- CI settings that select a paid provider when a secret happens to be present.
- Workflow steps that require provider API keys.
- Evaluation or benchmark scripts wired into pull-request CI.

Use fakes, stubs, recorded fixtures or local models. Production runtime code and deliberately invoked evaluation scripts are not violations merely because they can use a paid provider.

## How to report

Name the test or workflow step, the provider it would call, and the existing fake or smallest replacement that keeps the behavior testable.
