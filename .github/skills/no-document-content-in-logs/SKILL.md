---
name: no-document-content-in-logs
description: Document text, filenames, prompts and completions never reach logs, tracebacks, error messages, traces or diagnostic database columns.
---

# No document content in logs

Customer engineering documents, questions and model output are sensitive tenant data. Operators and observability systems must not receive that content.

## What to check

- Log calls containing document text, extracted fields, chunks, prompts, completions or customer filenames.
- `logger.exception(...)`, `exc_info=True` or persisted `str(exc)` where the exception may quote input.
- Diagnostic columns such as `IngestionJob.last_error`.
- Span attributes, Langfuse payloads and metric labels containing customer content.

Log identifiers and bounded metadata instead: document ID, tenant ID, MIME type, status, duration, counts and byte sizes. Persist the exception type, not an unsafe message.

Tests proving sensitive text is absent and fixture-only scripts are not violations.

## How to report

Name the file and variable carrying the content, identify where it becomes visible, and recommend a safe identifier or count instead.
