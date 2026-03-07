"""Celery worker entry point for UglyFox.

This module provides the entry point for running UglyFox as a Celery worker.
UglyFox runs as a serverless Celery worker triggered by cloud triggers.

Usage:
    celery -A app.celery_worker worker --loglevel=info -Q uglyfox
"""

import logging

from app.core.celery_app import celery_app
from app.core.config import settings

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper()),  # pylint: disable=no-member
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

# Export celery_app for Celery worker
__all__ = ["celery_app"]

if __name__ == "__main__":
    logger.info("Starting UglyFox Celery worker")
    celery_app.start()
