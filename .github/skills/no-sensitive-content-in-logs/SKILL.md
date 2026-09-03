---
name: no-sensitive-content-in-logs
description: Document text, prompts and completions never reach logs, error messages or database columns. Review logging, exception handling and error persistence against this rule.
---

# No document content in logs

Customers upload engineering documents, contracts and drawings. Anything this system logs or stores as a diagnostic message is readable by anyone with log access or database access - a much wider group than the tenant who owns the document. Document text must never end up there.

## What to check

A log call that interpolates a variable holding document text, an extracted field, a chunk, a prompt or a model completion. Log identifiers and counts instead: a document id, a tenant id, a chunk count, a byte length.

An exception handler that writes `str(exc)` into a persisted column or a log line, where the exception may quote the input. The established pattern in this codebase is to record the type only - `f"unexpected {type(exc).__name__}"` - and `app/worker.py` shows it. A handler that widens this is a finding.

`IngestionJob.last_error` and any other column read by an operator. `app/services/jobs.py::fail()` carries the rule in a comment: this column is a diagnostic message and never document content, a prompt or a completion.

Tracing and observability calls. A span attribute, a Langfuse payload or a metric label is a log line with a different name.

An f-string inside a log call is where this usually hides. Read what each interpolated name actually holds.

## Not a finding

Logging a document id, a tenant id, a filename, a mime type, a status, a duration, a count or a byte size.

A test that deliberately asserts sensitive text is absent - for example the worker test that checks a customer string does not appear in `last_error`. That test is the rule being enforced, not broken.

Log statements in scripts under `scripts/` that operate on fixtures the repository owns.

## How to report

Name the file and line, and say which variable carries the content. State who can read it once written - operator logs, the database, a tracing backend. Suggest the identifier that should be logged instead.

