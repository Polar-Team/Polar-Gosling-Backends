"""
MotherGoose FastAPI Application

Main entry point for the MotherGoose backend server.
Handles webhook processing, runner orchestration, and Git sync operations.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core import config
from app.core.celery_app import celery_app
from app.routers import eggs, health, internal, runners, webhooks
from app.util.base_logging import logger


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager.

    Handles startup and shutdown events for the FastAPI application.
    Initializes Celery workers and background tasks.
    """
    logger.info("MotherGoose starting up...")

    # Initialize database schema from environment variables
    try:
        config.initialize_ydb_schema()
        logger.info("Database schema initialized successfully")
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.error("Failed to initialize database schema: %s", e)
        logger.warning("Application will start but database operations will fail")

    logger.info("Celery broker: %s", celery_app.conf.broker_url)
    logger.info("Celery result backend: %s", celery_app.conf.result_backend)
    logger.info("Celery tasks registered: %d", len(celery_app.tasks))

    # Log registered tasks
    for task_name in sorted(celery_app.tasks.keys()):
        if not task_name.startswith("celery."):
            logger.info("  - %s", task_name)

    yield

    logger.info("MotherGoose shutting down...")


def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Factory pattern allows for easier testing and multiple app instances.

    Returns:
        FastAPI: Configured FastAPI application instance
    """
    application = FastAPI(
        title=config.APP_NAME,
        description=config.APP_DESCRIPTION,
        version=config.APP_VERSION,
        docs_url=config.DOCS_URL,
        redoc_url=config.REDOC_URL,
        openapi_url=config.OPENAPI_URL,
        lifespan=lifespan,
    )

    # Configure CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ALLOW_ORIGINS,
        allow_credentials=config.CORS_ALLOW_CREDENTIALS,
        allow_methods=config.CORS_ALLOW_METHODS,
        allow_headers=config.CORS_ALLOW_HEADERS,
    )

    # Add exception handler for RuntimeError (schema not initialized)
    @application.exception_handler(RuntimeError)
    async def runtime_error_handler(
        request: Request, exc: RuntimeError
    ) -> JSONResponse:
        """
        Handle RuntimeError exceptions (e.g., schema not initialized).

        Returns a 500 Internal Server Error response with error details.
        """

        # pylint: disable=unused-argument

        logger.error("RuntimeError: %s", str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": str(exc)},
        )

    # Include routers
    application.include_router(health.router)
    application.include_router(eggs.router)
    application.include_router(runners.router)
    application.include_router(internal.router)
    application.include_router(webhooks.router)

    return application


# Create the application instance
app = create_app()
