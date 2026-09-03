---
name: layering-and-providers
description: The api to services to providers layering holds, and no test or CI job calls a paid external API. Review imports, dependency direction and provider use against these rules.
---

# Layering and providers

Two rules that keep this codebase testable and its CI free.

## Layering

Dependencies point one way: `app/api` calls `app/services`, and `app/services` calls `app/providers`. Nothing points back.

What to check: an import in `app/services` that reaches into `app/api`, or an import in `app/providers` that reaches into `app/services` or `app/api`. A provider that knows about a request, a response model or an HTTP status code has the arrow backwards even when no import shows it.

Business rules belong in `app/services`. An API handler that decides what a valid merge is, when a document is ready, or how confidence is computed has taken work that belongs one layer down. A handler that validates its input, calls one service function and shapes the response is correct.

A provider wraps one external system - storage, parsing, embeddings, an LLM, a vector store - behind the interface in `app/providers/base.py`. A provider that reaches into the database or orchestrates other providers is doing service work.

Not a finding: shared types, settings and models imported from any layer. A service importing several providers. A handler doing input validation.

## No paid providers in tests or CI

CI must be runnable by anyone, repeatedly, at zero external cost, with no network dependency that can fail. A test that reaches a paid API is both a bill and a flake.

What to check: a test or a CI step that constructs a real OpenAI, Anthropic, Hugging Face inference or other billed client instead of a fake or a local model. Look at fixtures and conftest, not only the test body.

A default in `app/core/settings.py` or a CI environment variable that silently selects a paid provider when a key happens to be present. The safe shape fails loudly when a paid provider is requested in CI rather than quietly using one.

A new step in `.github/workflows/ci.yml` that needs an API key to pass.

An evaluation or benchmark script wired into CI. Those may call real providers when a person runs them deliberately; they must not run on every pull request.

Not a finding: a test using a fake, a stub, a recorded fixture, a local model or the local Qdrant. Code that calls a paid provider at runtime in production. A script under `scripts/` or `app/evaluation/` that a person invokes by hand.

## How to report

For layering, name both files and state the direction of the arrow. For providers, name the test or CI step and which external service it would bill, and say whether an existing fake in the repository already covers the case.

