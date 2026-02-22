"""
Maintenance Tasks

Celery tasks for background maintenance operations.
These tasks run periodically to clean up old data and update metrics.
"""

from typing import Any

from app.core.celery_app import celery_app
from app.core.celery_base import BaseTask
from app.util.base_logging import logger


@celery_app.task(
    base=BaseTask,
    name="app.tasks.maintenance.cleanup_old_results",
    bind=True,
    priority=3,
)
def cleanup_old_results(self: BaseTask) -> dict[str, Any]:
    """
    Clean up old task results from the result backend.

    This task runs hourly to remove expired task results and free up storage.
    Low priority task as it's not time-critical.

    Args:
        self: Task instance (bound)

    Returns:
        dict: Cleanup result with number of results removed

    Raises:
        Exception: If cleanup fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.info("Starting cleanup of old task results in task %s", task_id)

    try:
        # Task 30: Cleanup expired results

        result = {
            "status": "success",
            "task_id": task_id,
            "results_removed": 0,
            "message": "Old results cleaned up successfully (placeholder)",
        }

        logger.info("Cleanup completed: %s", result)
        return result

    except Exception as exc:
        logger.error("Cleanup failed: %s", exc)
        raise


@celery_app.task(
    base=BaseTask,
    name="app.tasks.maintenance.update_metrics",
    bind=True,
    priority=3,
)
def update_metrics(self: BaseTask) -> dict[str, Any]:
    """
    Update system metrics in the database.

    This task runs every 10 minutes to collect and update system metrics.
    Low priority task as it's not time-critical.

    Args:
        self: Task instance (bound)

    Returns:
        dict: Metrics update result

    Raises:
        Exception: If metrics update fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.info("Starting metrics update in task %s", task_id)

    try:
        # Task 30: Collect and update metrics

        result = {
            "status": "success",
            "task_id": task_id,
            "metrics_updated": 0,
            "message": "Metrics updated successfully (placeholder)",
        }

        logger.info("Metrics update completed: %s", result)
        return result

    except Exception as exc:
        logger.error("Metrics update failed: %s", exc)
        raise


@celery_app.task(
    base=BaseTask,
    name="app.tasks.maintenance.check_binary_versions",
    bind=True,
    priority=3,
)
def check_binary_versions(self: BaseTask) -> dict[str, Any]:
    """
    Check for new binary versions on GitHub.

    This task runs daily to check for new Gosling CLI and OpenTofu versions.
    Downloads new versions to S3 but does NOT activate them automatically.
    Logs warnings when new versions are available.

    Task 12.6: GitHub Binary Auto-Download

    Args:
        self: Task instance (bound)

    Returns:
        dict: Check result with new versions found

    Raises:
        Exception: If version check fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.info("Starting binary version check in task %s", task_id)

    try:
        # Task 12.6: Import here to avoid circular dependencies
        import os  # pylint: disable=import-outside-toplevel

        from app.core.config import (  # pylint: disable=import-outside-toplevel
            get_ydb_schema,
        )
        from app.services.binary_version_service import (  # pylint: disable=import-outside-toplevel
            BinaryVersionService,
        )
        from app.services.github_binary_downloader import (  # pylint: disable=import-outside-toplevel
            GitHubBinaryDownloader,
        )
        from app.services.s3fs_mount_manager import (  # pylint: disable=import-outside-toplevel
            S3FSMountManager,
        )

        # Initialize services
        schema = get_ydb_schema()
        s3fs_manager = S3FSMountManager(
            s3_bucket=os.getenv("MOTHERGOOSE_S3_BUCKET", "binaries"),
            mount_point=os.getenv("MOTHERGOOSE_GOSLING_CACHE_DIR", "/tmp/gosling"),
            s3_endpoint_url=os.getenv("MOTHERGOOSE_S3_ENDPOINT_URL"),
            aws_access_key_id=os.getenv("MOTHERGOOSE_AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("MOTHERGOOSE_AWS_SECRET_ACCESS_KEY"),
        )
        binary_version_service = BinaryVersionService(
            schema=schema, s3fs_manager=s3fs_manager
        )
        downloader = GitHubBinaryDownloader(
            binary_version_service=binary_version_service,
            schema=schema,
        )

        # Check and download new versions (async operation)
        import asyncio  # pylint: disable=import-outside-toplevel

        new_versions = asyncio.run(downloader.check_and_download_new_versions())

        result = {
            "status": "success",
            "task_id": task_id,
            "new_versions": new_versions,
            "message": "Binary version check completed",
        }

        if new_versions["gosling"] or new_versions["opentofu"]:
            logger.warning(
                "New binary versions available: %s",
                {k: v for k, v in new_versions.items() if v is not None},
            )
        else:
            logger.info("All binaries are up to date")

        return result

    except Exception as exc:
        logger.error("Binary version check failed: %s", exc)
        raise
