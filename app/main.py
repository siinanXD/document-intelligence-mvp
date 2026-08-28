"""FastAPI application factory."""

from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router
from app.core.settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=__version__)
    app.include_router(health_router)
    return app


app = create_app()
