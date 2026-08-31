"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware import Middleware

from app import __version__
from app.api.ask import router as ask_router
from app.api.documents import lifecycle_router
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.api.request_logging import RequestLoggingMiddleware
from app.api.search import router as search_router
from app.core.db import dispose_engine
from app.core.qdrant import close_qdrant_client, warm_qdrant_client
from app.core.settings import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await warm_qdrant_client()
    yield
    await dispose_engine()
    await close_qdrant_client()


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        middleware=[Middleware(RequestLoggingMiddleware)],
    )
    app.include_router(health_router)
    app.include_router(documents_router)
    app.include_router(lifecycle_router)
    app.include_router(search_router)
    app.include_router(ask_router)
    return app


app = create_app()
