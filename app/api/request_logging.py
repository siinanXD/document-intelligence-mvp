"""Privacy-safe request logging.

Every request gets a request_id and a single completion line. The line carries
identifiers, timing and status. It never carries a body, a query string, an
authorization header, a prompt or a model response.

Implemented as raw ASGI rather than BaseHTTPMiddleware so the completion line
is written on the same task that handled the request - pytest's log capture
(and any request-scoped context) can see it.
"""

import logging
import re
import time
import uuid

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger("app.audit")

# Tests replace this to observe extras without depending on logging config.
_audit_observers: list = []


def emit_request_audit(extra: dict[str, object]) -> None:
    """Write one request completion line. Extra must be identifiers only."""
    for observer in list(_audit_observers):
        observer(extra)
    logger.info("request completed", extra=extra)


_SKIP_PATHS = frozenset({"/health", "/health/ready"})
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DOCUMENT_ID = re.compile(
    r"/documents/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _safe_uuid(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    return candidate if _UUID.fullmatch(candidate) else None


def _header(scope: Scope, name: str) -> str | None:
    target = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or []:
        if key == target:
            return value.decode("latin-1")
    return None


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        method = scope.get("method") or ""
        request_id = _safe_uuid(_header(scope, "x-request-id")) or str(uuid.uuid4())
        tenant_id = _safe_uuid(_header(scope, "x-tenant-id"))
        started = time.perf_counter()
        status_code = 500
        error_type: str | None = None

        async def send_wrapper(message: dict) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = MutableHeaders(scope=message)
                headers["X-Request-Id"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            error_type = type(exc).__name__
            raise
        finally:
            if path not in _SKIP_PATHS:
                extra: dict[str, object] = {
                    "request_id": request_id,
                    "method": method,
                    "path": path,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                }
                if tenant_id:
                    extra["tenant_id"] = tenant_id
                match = _DOCUMENT_ID.search(path)
                if match:
                    extra["document_id"] = match.group(1)
                if error_type:
                    extra["error_type"] = error_type
                elif status_code >= 400:
                    extra["error_type"] = f"http_{status_code}"
                emit_request_audit(extra)
