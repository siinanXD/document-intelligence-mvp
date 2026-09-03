---
name: layering
description: Enforce the api to services to providers dependency direction and keep business rules out of transport and provider layers.
---

# Layering

Dependencies point one way: `app/api` calls `app/services`, and `app/services` calls `app/providers`. Nothing points back.

## What to check

- An import in `app/services` that reaches into `app/api`.
- An import in `app/providers` that reaches into `app/services` or `app/api`.
- A provider that knows about requests, response models or HTTP status codes.
- Business decisions implemented in API handlers instead of services.
- Providers that access the database or orchestrate other providers.

Shared types, settings and models may be imported from any layer. A service may use several providers. API input validation is not a violation.

## How to report

Name both files and the reversed dependency. For misplaced business logic, identify the rule and the service boundary where it belongs.
