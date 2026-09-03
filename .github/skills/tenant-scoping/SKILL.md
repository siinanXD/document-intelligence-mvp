---
name: tenant-scoping
description: Every read and write of tenant data is scoped to one tenant. Review database queries, service functions and API handlers against this rule.
---

# Tenant scoping

This is a multi-tenant system. A tenant must never be able to read, modify or count another tenant's data. A missing tenant filter is not a style issue - it is a data breach that tests rarely catch, because tests usually run with a single tenant.

## What to check

Any SQLAlchemy `select`, `update` or `delete` that touches a table carrying `tenant_id` must constrain `tenant_id` in the same statement. A filter applied afterwards in Python is not a constraint - the rows already left the database.

A service function that receives an id from the caller must also receive the tenant and scope by both. Fetching by id alone and then comparing the tenant in Python is a check-then-use race and reads another tenant's row before rejecting it.

An API handler must take the tenant from the shared `TenantDep` dependency and never from a path parameter, query string or request body. `TenantDep` is the single place tenant identity is resolved, which is what makes it replaceable later.

A count, an aggregate or an existence check leaks just as much as a full row. `SELECT COUNT(*)` without a tenant filter tells a caller how much data other tenants have.

Vector store, object storage and cache keys carry the tenant too. A storage key or a collection name that a second tenant can construct is the same defect in a different layer.

## Two documented exceptions

`app/services/jobs.py::claim()` reads across tenants on purpose: one worker serves every tenant, and each claimed row carries its own `tenant_id` for everything that follows.

`app/api/dependencies.py::get_tenant()` resolves the tenant from the client-controlled `X-Tenant-Id` header. Its docstring states the reason: this is identification, not authentication, because nothing yet issues credentials, and it is deliberately the single place that has to change when real authentication arrives. Do not report it on every pull request, and do not report an endpoint for using `TenantDep`.

Both exceptions are written down here so they are stated once instead of re-litigated on every review. Neither is precedent: a new cross-tenant read, or a second place that resolves tenant identity, needs its own written justification in the code.

## Not a finding

A query on a table that has no `tenant_id` column at all.

A migration, a fixture or a test helper that deliberately sets up several tenants.

A function that takes `tenant_id` as its first parameter and passes it straight through - trace it to where the query is built before reporting.

## How to report

Name the file, the function and the statement. Say which tenant's data becomes reachable and through which caller. If you cannot name a caller that reaches it, say so - an unreachable path is worth a note, not an alarm.
---
name: tenant-scoping
description: Every read and write of tenant data is scoped to one tenant. Review database queries, service functions and API handlers against this rule.
---

# Tenant scoping

This is a multi-tenant system. A tenant must never be able to read, modify or count another tenant's data. A missing tenant filter is not a style issue - it is a data breach that tests rarely catch, because tests usually run with a single tenant.

## What to check

Any SQLAlchemy `select`, `update` or `delete` that touches a table carrying `tenant_id` must constrain `tenant_id` in the same statement. A filter applied afterwards in Python is not a constraint - the rows already left the database.

A service function that receives an id from the caller must also receive the tenant and scope by both. Fetching by id alone and then comparing the tenant in Python is a check-then-use race and reads another tenant's row before rejecting it.

An API handler must take the tenant from the authenticated request, never from a path parameter, query string, request body or header the client controls.

A count, an aggregate or an existence check leaks just as much as a full row. `SELECT COUNT(*)` without a tenant filter tells a caller how much data other tenants have.

Vector store, object storage and cache keys carry the tenant too. A storage key or a collection name that a second tenant can construct is the same defect in a different layer.

## The documented exception

`app/services/jobs.py::claim()` reads across tenants on purpose: one worker serves every tenant, and each claimed row carries its own `tenant_id` for everything that follows. This is the only place. Do not report it, and do not accept a new cross-tenant read that points at it as precedent - a new one needs its own written justification in the code.

## Not a finding

A query on a table that has no `tenant_id` column at all.

A migration, a fixture or a test helper that deliberately sets up several tenants.

A function that takes `tenant_id` as its first parameter and passes it straight through - trace it to where the query is built before reporting.

## How to report

Name the file, the function and the statement. Say which tenant's data becomes reachable and through which caller. If you cannot name a caller that reaches it, say so - an unreachable path is worth a note, not an alarm.

