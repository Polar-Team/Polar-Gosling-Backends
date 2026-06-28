"""
Git Synchronization Tasks

Celery tasks for synchronizing Nest repository configuration to database cache.
These tasks run periodically (every 5 minutes) and on-demand (webhook triggers).
"""

import asyncio
from typing import Any

from app.core.celery_app import celery_app
from app.core.celery_base import BaseTask
from app.services.git_sync_service import git_sync_service
from app.util.base_logging import logger


@celery_app.task(
    base=BaseTask,
    name="app.tasks.git_sync.sync_nest_config",
    bind=True,
    priority=7,
    ignore_result=True,
)
def sync_nest_config(self: BaseTask, sync_type: str = "periodic") -> dict[str, Any]:
    """
    Synchronize Nest repository configuration to database cache.

    This task is scheduled to run every 5 minutes by cloud triggers.
    It can also be triggered manually via webhook when Nest repo is updated.

    The task performs the following steps:
    1. Retrieve SSH deploy key from secret storage
    2. Clone/pull Nest repository
    3. Parse all .fly files (Eggs/, Jobs/, UF/)
    4. Update database cache with parsed configurations
    5. Log sync history with Git commit hash

    Args:
        self: Task instance (bound)
        sync_type: Type of sync (periodic/webhook/manual)

    Returns:
        dict: Sync result with status, commit hash, and changes detected

    Raises:
        Exception: If Git sync fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.info("Starting Nest config sync in task %s (type: %s)", task_id, sync_type)

    try:
        # Execute Git sync (async service, run in event loop)
        result = asyncio.run(git_sync_service.sync_nest_repository(sync_type=sync_type))
        result["task_id"] = task_id

        logger.info("Nest config sync completed: %s", result)
        return result

    except Exception as exc:
        logger.error("Nest config sync failed: %s", exc)
        raise
