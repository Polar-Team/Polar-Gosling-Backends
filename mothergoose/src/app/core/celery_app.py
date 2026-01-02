"""
Celery Application Instance

Creates and configures the Celery application for MotherGoose.
This module provides the main Celery app instance used throughout the application.
"""

from celery import Celery

from app.core import celery_config
from app.util.base_logging import logger


def create_celery_app() -> Celery:
    """
    Create and configure the Celery application.

    Factory pattern allows for easier testing and configuration management.

    Returns:
        Celery: Configured Celery application instance
    """
    app = Celery("mothergoose")

    # Load configuration from celery_config module
    app.config_from_object(celery_config, namespace="CELERY")

    # Configure error handling
    @app.task(bind=True, max_retries=celery_config.CELERY_TASK_MAX_RETRIES)
    def error_handler(self, uuid: str):  # type: ignore[no-untyped-def]
        """
        Handle task errors and implement retry logic.

        Args:
            self: Task instance (bound)
            uuid: Task UUID
        """
        result = app.AsyncResult(uuid)
        logger.error("Task %s failed: %s", uuid, result.info)

        # Retry with exponential backoff
        try:
            raise self.retry(countdown=celery_config.CELERY_TASK_DEFAULT_RETRY_DELAY)
        except self.MaxRetriesExceededError:
            logger.error("Task %s exceeded max tetries", uuid)
            raise

    logger.info("Celery application created and configured")
    return app


# Create the Celery application instance
celery_app = create_celery_app()
