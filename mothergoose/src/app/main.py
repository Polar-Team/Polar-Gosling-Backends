"""
MotherGoose FastAPI Application

Main entry point for the MotherGoose backend server.
Handles webhook processing, runner orchestration, and Git sync operations.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import config
from app.routers import health
from app.util.logging import logger


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.

    Handles startup and shutdown events for the FastAPI application.

    TODO: Initialize database connections, Celery workers, and background tasks
    """
    logger.info("MotherGoose starting up...")

    yield

    logger.info("MotherGoose shutting down...")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Factory pattern allows for easier testing and multiple app instances.

    Returns:
        FastAPI: Configured FastAPI application instance
    """
    app = FastAPI(
        title=config.APP_NAME,
        description=config.APP_DESCRIPTION,
        version=config.APP_VERSION,
        docs_url=config.DOCS_URL,
        redoc_url=config.REDOC_URL,
        openapi_url=config.OPENAPI_URL,
        lifespan=lifespan,
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ALLOW_ORIGINS,
        allow_credentials=config.CORS_ALLOW_CREDENTIALS,
        allow_methods=config.CORS_ALLOW_METHODS,
        allow_headers=config.CORS_ALLOW_HEADERS,
    )

    # Include routers
    app.include_router(health.router)

    return app


# Create the application instance
app = create_app()
