"""Health monitoring tasks for UglyFox.

Triggered by cloud triggers (every 10 minutes) to monitor runner health
and identify runners that need pruning.

Requirements: 7.1, 7.3, 7.5, 7.7
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.celery_app import celery_app
from app.db.database_client import get_database_client
from app.services.lifecycle_service import LifecycleService, run_async
from app.services.uf_config_builder import build_uf_config

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.health.check_runner_health", bind=True)
def check_runner_health(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self,
    uf_config_dict: Optional[Dict[str, Any]] = None,
) -> dict:
    """Check health of all runners.

    Triggered by cloud triggers (Yandex Cloud Timer Trigger / AWS EventBridge)
    every 10 minutes to monitor runner health.

    Args:
        uf_config_dict: Optional pre-parsed UF config dict from DB cache.

    Returns:
        dict: Health check results with counts of runners by state.
    """
    logger.info("Starting runner health check")

    db = get_database_client()
    service = LifecycleService(db=db, uf_config=build_uf_config(uf_config_dict))

    # Task 22: Run health check via LifecycleService
    health = run_async(service.check_all_runners())

    results: Dict[str, Any] = {
        "timestamp": health.timestamp,
        "total_runners": health.total_runners,
        "healthy_runners": health.healthy_runners,
        "unhealthy_runners": health.unhealthy_runners,
        "failed_runners": health.failed_runners,
        "idle_runners": health.idle_runners,
        "runners_to_prune": health.runners_to_prune,
        "errors": health.errors,
    }

    logger.info("Health check completed: %s", results)
    return results


@celery_app.task(name="app.tasks.health.collect_runner_metrics", bind=True)
def collect_runner_metrics(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self, runner_ids: List[str]
) -> dict:
    """Collect metrics for specific runners.

    Args:
        runner_ids: List of runner IDs to collect metrics for.

    Returns:
        dict: Metrics collection results.
    """
    logger.info("Collecting metrics for %d runners", len(runner_ids))

    # Task 24: Runner metrics table not yet implemented
    results: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "runners_processed": len(runner_ids),
        "metrics_collected": 0,
    }

    logger.info("Metrics collection completed: %s", results)
    return results


@celery_app.task(name="app.tasks.health.identify_unhealthy_runners", bind=True)
def identify_unhealthy_runners(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self,
    uf_config_dict: Optional[Dict[str, Any]] = None,
) -> dict:
    """Identify runners that are unhealthy based on policy evaluation.

    Args:
        uf_config_dict: Optional pre-parsed UF config dict from DB cache.

    Returns:
        dict: List of unhealthy runner IDs and reasons.
    """
    logger.info("Identifying unhealthy runners")

    db = get_database_client()
    service = LifecycleService(db=db, uf_config=build_uf_config(uf_config_dict))

    # Task 22: Delegate to LifecycleService health check
    health = run_async(service.check_all_runners())

    results: Dict[str, Any] = {
        "timestamp": health.timestamp,
        "unhealthy_runners": health.runners_to_prune,
        "reasons": {},
        "errors": health.errors,
    }

    logger.info("Unhealthy runner identification completed: %s", results)
    return results
