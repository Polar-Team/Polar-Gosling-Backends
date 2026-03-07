"""Pruning tasks for UglyFox.

Handle evaluation and execution of runner pruning policies based on
failure thresholds, age limits, and idle timeouts.

Requirements: 7.3, 7.5, 7.7
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.celery_app import celery_app
from app.core.config import settings
from app.db.database_client import get_database_client
from app.model.policy_models import UFConfig
from app.model.runners_models import RunnerState
from app.services.lifecycle_service import LifecycleService, run_async
from app.services.uf_config_builder import build_uf_config

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.pruning.evaluate_pruning_policies", bind=True)
def evaluate_pruning_policies(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self, uf_config_dict: Optional[Dict[str, Any]] = None
) -> dict:
    """Evaluate pruning policies for all runners.

    Evaluates UF/config.fly policies to determine which runners should be
    terminated based on failure count, maximum age, and idle timeout.

    Args:
        uf_config_dict: Optional pre-parsed UF config dict from DB cache.
                        If None, falls back to UglyFoxSettings defaults.

    Returns:
        dict: Evaluation results with runners to prune.
    """
    logger.info("Evaluating pruning policies")

    uf_config = build_uf_config(uf_config_dict)
    logger.debug("Resolved UFConfig: %s", uf_config)

    db = get_database_client()
    service = LifecycleService(db=db, uf_config=uf_config)

    # Task 22: Dry-run evaluation (no termination)
    health = run_async(service.check_all_runners())

    results: Dict[str, Any] = {
        "timestamp": health.timestamp,
        "runners_evaluated": health.total_runners,
        "runners_to_prune": health.runners_to_prune,
        "policies_applied": [],
        "errors": health.errors,
    }

    logger.info("Pruning policy evaluation completed: %s", results)
    return results


@celery_app.task(name="app.tasks.pruning.prune_failed_runners", bind=True)
def prune_failed_runners(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self, uf_config_dict: Optional[Dict[str, Any]] = None
) -> dict:
    """Prune runners that exceed failure threshold.

    Terminates runners with failure_count >= configured failed_threshold.

    Args:
        uf_config_dict: Optional pre-parsed UF config dict from DB cache.

    Returns:
        dict: Pruning results with terminated runner IDs.
    """
    logger.info("Pruning failed runners (threshold=%d)", settings.failed_threshold)

    db = get_database_client()
    service = LifecycleService(db=db, uf_config=build_uf_config(uf_config_dict))

    # Task 22: Prune only runners in FAILED state
    failed_runners: List[Dict[str, Any]] = run_async(
        db.list_runners_by_state(RunnerState.FAILED.value)
    )
    failed_ids: List[str] = [
        str(r.get("id", "")) for r in failed_runners if r.get("id")
    ]

    pruning = run_async(service.prune_runners(runner_ids=failed_ids))

    results: Dict[str, Any] = {
        "timestamp": pruning.timestamp,
        "threshold": settings.failed_threshold,
        "runners_pruned": pruning.runners_pruned,
        "errors": pruning.errors,
    }

    logger.info("Failed runner pruning completed: %s", results)
    return results


@celery_app.task(name="app.tasks.pruning.prune_old_runners", bind=True)
def prune_old_runners(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self, uf_config_dict: Optional[Dict[str, Any]] = None
) -> dict:
    """Prune runners that exceed maximum age.

    Terminates runners with age > configured max_runner_age.

    Args:
        uf_config_dict: Optional pre-parsed UF config dict from DB cache.

    Returns:
        dict: Pruning results with terminated runner IDs.
    """
    logger.info("Pruning old runners (max_age=%s)", settings.max_age)

    db = get_database_client()
    service = LifecycleService(db=db, uf_config=build_uf_config(uf_config_dict))

    # Task 22: Full prune sweep (age check is part of policy evaluation)
    pruning = run_async(service.prune_runners())

    results: Dict[str, Any] = {
        "timestamp": pruning.timestamp,
        "max_age": settings.max_age,
        "runners_pruned": pruning.runners_pruned,
        "errors": pruning.errors,
    }

    logger.info("Old runner pruning completed: %s", results)
    return results


@celery_app.task(name="app.tasks.pruning.terminate_runner", bind=True)
def terminate_runner(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self, runner_id: str, reason: str
) -> dict:
    """Terminate a specific runner.

    Args:
        runner_id: Runner identifier.
        reason: Reason for termination.

    Returns:
        dict: Termination result.
    """
    logger.info("Terminating runner: %s (reason: %s)", runner_id, reason)

    db = get_database_client()
    service = LifecycleService(db=db, uf_config=UFConfig())

    # Task 22: Delegate to LifecycleService
    success = run_async(
        service._terminate_runner(  # pylint: disable=protected-access
            runner_id, reason, "manual"
        )
    )

    results: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "runner_id": runner_id,
        "reason": reason,
        "success": success,
        "error": "" if success else "termination failed",
    }

    logger.info("Runner termination completed: %s", results)
    return results
