"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import __version__
from app.api.health import router as health_router
from app.core.db import dispose_engine
from app.core.qdrant import close_qdrant_client
from app.core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()
    await close_qdrant_client()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    app = FastAPI(title=settings.app_name, version=__version__, lifespan=lifespan)
    app.include_router(health_router)
    return app


app = create_app()
