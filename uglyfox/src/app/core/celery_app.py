"""Celery application configuration for UglyFox.

This module sets up the Celery application for UglyFox worker tasks.
UglyFox runs as a serverless Celery worker triggered by cloud triggers
(Yandex Cloud Timer Trigger / AWS EventBridge Scheduler).
"""

from celery import Celery

from app.core.config import settings

# Create Celery application
celery_app = Celery(
    "uglyfox",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.tasks.health",
        "app.tasks.pruning",
        "app.tasks.lifecycle",
    ],
)

# Configure Celery
celery_app.conf.update(settings.get_celery_config())

# Task autodiscovery
celery_app.autodiscover_tasks(["app.tasks"])


@celery_app.task(bind=True, ignore_result=True)
def debug_task(self) -> str:  # type: ignore[no-untyped-def]
    """Debug task for testing Celery configuration."""
    return f"Request: {self.request!r}"
