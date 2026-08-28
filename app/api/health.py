"""Health endpoints.

`/health` is liveness: it answers as long as the process serves requests, so an
orchestrator does not restart the app because a database is briefly down.
`/health/ready` is readiness: it probes dependencies and fails the check when
one is unreachable, without disclosing endpoints or credentials.
"""

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from app import __version__
from app.core.settings import get_settings
from app.services.health import check_dependencies

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class ReadinessResponse(HealthResponse):
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=settings.environment,
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(response: Response) -> ReadinessResponse:
    settings = get_settings()
    statuses = await check_dependencies()
    checks = {item.name: "up" if item.healthy else "down" for item in statuses}
    ready = all(item.healthy for item in statuses)

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return ReadinessResponse(
        status="ready" if ready else "degraded",
        version=__version__,
        environment=settings.environment,
        checks=checks,
    )
