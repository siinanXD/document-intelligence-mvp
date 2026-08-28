"""Health endpoint.

The check stays cheap and dependency-free at this stage; dependency probes are
added together with the database and vector store wiring.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app import __version__
from app.core.settings import Settings, get_settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings: Settings = get_settings()
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=settings.environment,
    )
