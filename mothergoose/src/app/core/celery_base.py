"""
Celery Base Task Class

Provides a base task class with default retry configuration and error handling.
This module is separate to avoid circular imports.
"""

# pylint: disable=too-many-arguments,too-many-positional-arguments,abstract-method

from typing import Any

from celery import Task  # type: ignore[import-untyped]

from app.core import celery_config
from app.util.base_logging import logger


class BaseTask(Task):  # type: ignore[misc,no-any-unimported]
    """
    Base task class with default retry configuration.

    All tasks should inherit from this class to get consistent retry behavior.
    """

    autoretry_for = celery_config.CELERY_TASK_AUTORETRY_FOR
    retry_kwargs = {
        "max_retries": celery_config.CELERY_TASK_MAX_RETRIES,
        "countdown": celery_config.CELERY_TASK_DEFAULT_RETRY_DELAY,
    }
    retry_backoff = celery_config.CELERY_TASK_RETRY_BACKOFF
    retry_backoff_max = celery_config.CELERY_TASK_RETRY_BACKOFF_MAX
    retry_jitter = celery_config.CELERY_TASK_RETRY_JITTER

    def on_failure(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """
        Handle task failure.

        Args:
            exc: Exception raised by the task
            task_id: Unique task ID
            args: Task positional arguments
            kwargs: Task keyword arguments
            einfo: Exception info
        """
        logger.error("Task %s[%s] failed: %s", self.name, task_id, exc, exc_info=einfo)
        super().on_failure(exc, task_id, args, kwargs, einfo)

    def on_retry(
        self,
        exc: Exception,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        einfo: Any,
    ) -> None:
        """
        Handle task retry.

        Args:
            exc: Exception that caused the retry
            task_id: Unique task ID
            args: Task positional arguments
            kwargs: Task keyword arguments
            einfo: Exception info
        """
        logger.warning("Task %s[%s] retrying: %s", self.name, task_id, exc)
        super().on_retry(exc, task_id, args, kwargs, einfo)

    def on_success(
        self,
        retval: Any,
        task_id: str,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        """
        Handle task success.

        Args:
            retval: Task return value
            task_id: Unique task ID
            args: Task positional arguments
            kwargs: Task keyword arguments
        """
        logger.info("Task %s[%s] succeeded", self.name, task_id)
        super().on_success(retval, task_id, args, kwargs)
