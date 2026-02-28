"""Pruning tasks for UglyFox.

These tasks handle the evaluation and execution of runner pruning policies
based on failure thresholds, age limits, and idle timeouts.
"""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from app.core.celery_app import celery_app
from app.core.config import settings
from app.model.policy_models import PruningPolicy, UFConfig
from app.services.policy_engine import PolicyEngine
from app.services.policy_parser import PolicyParser

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.pruning.evaluate_pruning_policies", bind=True)
def evaluate_pruning_policies(  # type: ignore[no-untyped-def]
    self, uf_config_dict: Optional[Dict[str, Any]] = None
) -> dict:
    """Evaluate pruning policies for all runners.

    This task evaluates UF/config.fly policies to determine which runners
    should be terminated based on:
    - Failure count threshold
    - Maximum age
    - Idle timeout
    - Custom policy conditions

    Args:
        uf_config_dict: Optional pre-parsed UF config dict from DB cache.
                        If None, falls back to UglyFoxSettings defaults.

    Returns:
        dict: Evaluation results with runners to prune
    """
    logger.info("Evaluating pruning policies")

    # Task 21: Resolve effective UFConfig
    if uf_config_dict:
        parser = PolicyParser()
        uf_config = parser.parse_from_dict(uf_config_dict)
    else:
        # Task 21: Fall back to settings-based defaults
        default_pruning = PruningPolicy(
            max_failures=settings.failed_threshold,
            max_age_hours=settings.max_runner_age / 3600.0,
        )
        uf_config = UFConfig(pruning=default_pruning)

    engine = PolicyEngine(uf_config)

    results: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "runners_evaluated": 0,
        "runners_to_prune": [],
        "policies_applied": [],
    }

    logger.info("Pruning policy evaluation completed: %s", results)
    return results


@celery_app.task(name="app.tasks.pruning.prune_failed_runners", bind=True)
def prune_failed_runners(self) -> dict:  # type: ignore[no-untyped-def]
    """Prune runners that exceed failure threshold.

    Terminates runners with failure_count >= configured failed_threshold.

    Returns:
        dict: Pruning results with terminated runner IDs
    """
    logger.info(
        "Pruning failed runners (threshold=%d)", settings.failed_threshold
    )

    # Task 20: Failed runner pruning implementation placeholder
    # This will be implemented in Task 22 (Runner Lifecycle Management)
    results = {
        "timestamp": datetime.utcnow().isoformat(),
        "threshold": settings.failed_threshold,
        "runners_pruned": [],
        "errors": [],
    }

    logger.info("Failed runner pruning completed: %s", results)
    return results


@celery_app.task(name="app.tasks.pruning.prune_old_runners", bind=True)
def prune_old_runners(self) -> dict:  # type: ignore[no-untyped-def]
    """Prune runners that exceed maximum age.

    Terminates runners with age > configured max_runner_age.

    Returns:
        dict: Pruning results with terminated runner IDs
    """
    logger.info(
        "Pruning old runners (max_age=%d seconds)", settings.max_runner_age
    )

    # Task 20: Old runner pruning implementation placeholder
    # This will be implemented in Task 22 (Runner Lifecycle Management)
    results = {
        "timestamp": datetime.utcnow().isoformat(),
        "max_age_seconds": settings.max_runner_age,
        "runners_pruned": [],
        "errors": [],
    }

    logger.info("Old runner pruning completed: %s", results)
    return results


@celery_app.task(name="app.tasks.pruning.terminate_runner", bind=True)
def terminate_runner(self, runner_id: str, reason: str) -> dict:  # type: ignore[no-untyped-def]
    """Terminate a specific runner.

    Args:
        runner_id: Runner identifier
        reason: Reason for termination

    Returns:
        dict: Termination result
    """
    logger.info("Terminating runner: %s (reason: %s)", runner_id, reason)

    # Task 20: Runner termination implementation placeholder
    # This will be implemented in Task 22 (Runner Lifecycle Management)
    results = {
        "timestamp": datetime.utcnow().isoformat(),
        "runner_id": runner_id,
        "reason": reason,
        "success": False,
        "error": "Not implemented",
    }

    logger.info("Runner termination completed: %s", results)
    return results
