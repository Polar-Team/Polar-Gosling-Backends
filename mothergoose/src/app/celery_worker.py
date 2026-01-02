"""
Celery Worker Entry Point

Script to start Celery workers and Celery Beat scheduler.
This script is used to run the Celery worker processes separately from the FastAPI application.

Usage:
    # Start worker
    celery -A app.celery_worker worker --loglevel=info

    # Start beat scheduler
    celery -A app.celery_worker beat --loglevel=info

    # Start worker with beat scheduler
    celery -A app.celery_worker worker --beat --loglevel=info

    # Start worker with specific queues
    celery -A app.celery_worker worker -Q high-priority,default --loglevel=info
"""

from app.core.celery_app import celery_app

# Auto-discover tasks when worker starts
celery_app.autodiscover_tasks(["app.tasks"], force=True)

# Export celery_app for Celery CLI
__all__ = ["celery_app"]
