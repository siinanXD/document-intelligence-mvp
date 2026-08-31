"""Opaque request/job correlation for generation traces.

HTTP requests reuse the privacy-safe `X-Request-Id` from request logging.
Background jobs have no HTTP request, so they bind the job and document ids
instead. Identifiers are UUIDs; they never carry document content.
"""

from contextvars import ContextVar, Token
from uuid import uuid4

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)
_document_id: ContextVar[str | None] = ContextVar("document_id", default=None)


def current_request_id() -> str | None:
    return _request_id.get()


def current_job_id() -> str | None:
    return _job_id.get()


def current_document_id() -> str | None:
    return _document_id.get()


def bind_request_id(request_id: str) -> Token:
    return _request_id.set(request_id)


def reset_request_id(token: Token) -> None:
    _request_id.reset(token)


def bind_job_context(*, job_id: str, document_id: str | None = None) -> tuple[Token, Token]:
    job_token = _job_id.set(job_id)
    document_token = _document_id.set(document_id)
    return job_token, document_token


def reset_job_context(tokens: tuple[Token, Token]) -> None:
    job_token, document_token = tokens
    _job_id.reset(job_token)
    _document_id.reset(document_token)


def current_trace_id() -> str:
    """Correlation id for a generation: request, else job, else a fresh uuid."""
    return current_request_id() or current_job_id() or str(uuid4())


def correlation_extra() -> dict[str, str]:
    """Safe identifier extras for traces. Omits unset values."""
    extra: dict[str, str] = {}
    request_id = current_request_id()
    job_id = current_job_id()
    document_id = current_document_id()
    if request_id:
        extra["request_id"] = request_id
    if job_id:
        extra["job_id"] = job_id
    if document_id:
        extra["document_id"] = document_id
    return extra
