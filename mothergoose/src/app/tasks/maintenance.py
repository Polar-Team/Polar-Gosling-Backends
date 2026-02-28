"""
Maintenance Tasks

Celery tasks for background maintenance operations.
These tasks run periodically to clean up old data and update metrics.
"""

import asyncio
import os
from typing import Any

from app.core.celery_app import celery_app
from app.core.celery_base import BaseTask
from app.core.config import (
    get_ydb_schema,
)
from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.services.binary_service import UpdateGithub
from app.services.binary_version_service import (
    BinaryVersionService,
)
from app.services.s3fs_mount_manager import (
    S3FSMountManager,
)
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

        # Initialize services
        schema = get_ydb_schema()
        s3fs_manager = S3FSMountManager(
            s3_bucket=os.getenv("MOTHERGOOSE_S3_BUCKET", "binaries"),
            mount_point=os.getenv("MOTHERGOOSE_BINARY_CACHE_DIR", "/tmp/mnt"),
            s3_endpoint_url=os.getenv("MOTHERGOOSE_S3_ENDPOINT_URL"),
            aws_access_key_id=os.getenv("MOTHERGOOSE_AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("MOTHERGOOSE_AWS_SECRET_ACCESS_KEY"),
        )
        binary_version_service = BinaryVersionService(
            schema=schema, s3fs_manager=s3fs_manager
        )
        downloader_gosling = UpdateGithub(
            schema=schema,
            github_repo="polar-team/polar-gosling",
            binary_name="gosling",
            table_name="gosling_versions",
            install_dir=os.getenv(
                "MOTHERGOOSE_GOSLING_CACHE_DIR",
                "/tmp/gosling",
            ),
        )

        downloader_opentofu = UpdateGithub(
            schema=schema,
            github_repo="opentofu/opentofu",
            binary_name="opentofu",
            table_name="opentofu_versions",
            install_dir=os.getenv(
                "MOTHERGOOSE_OPENTOFU_CACHE_DIR",
                "/tmp/opentofu",
            ),
        )

        # Check and download new versions (async operation)

        asyncio.run(downloader_gosling.start_update())
        asyncio.run(downloader_opentofu.start_update())

        operation = AsyncYDBOperations(
            schema,
            AsyncYDBFunctionsCollections.select_parameterized_query,
        )
        asyncio.run(
            operation.process(
                selected_columns=["version_id", "version", "sha256_hash"],
                searching_columns=["active", "source"],
                searching_values=[True, "github"],
            )
        )

        asyncio.run(
            binary_version_service.upload_version(
                version=operation.result[0]["version"],
                file_path="/tmp/gosling/gosling",
                checksum=operation.result[0]["sha256_hash"],
                binary_name="gosling",
            )
        )

        asyncio.run(
            binary_version_service.upload_version(
                version=operation.result[0]["version"],
                file_path="/tmp/opentofu/tofu",
                checksum=operation.result[0]["sha256_hash"],
                binary_name="tofu",
            )
        )

        result = {
            "status": "success",
            "task_id": task_id,
            "new_versions": operation.result,
            "message": "Binary version check completed",
        }

        return result

    except Exception as exc:
        logger.error("Binary version check failed: %s", exc)
        raise
