"""Lifecycle management tasks for UglyFox.

Handle runner state transitions between Apex and Nadir pools based on
demand and idle timeouts.

Requirements: 7.4, 7.6
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.celery_app import celery_app
from app.db.database_client import get_database_client
from app.model.policy_models import UFConfig
from app.services.lifecycle_service import LifecycleService, run_async
from app.services.uf_config_builder import build_uf_config

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.lifecycle.manage_apex_nadir_pools", bind=True)
def manage_apex_nadir_pools(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self,
    uf_config_dict: Optional[Dict[str, Any]] = None,
    job_queue_depth: int = 0,
) -> dict:
    """Manage Apex and Nadir runner pools.

    Evaluates runner demand and transitions runners between Apex (active)
    and Nadir (dormant) states based on:
    - Job demand (promote Nadir to Apex when demand increases)
    - Idle timeout (demote Apex to Nadir when idle)
    - Pool size limits (max_count, min_count)

    Args:
        uf_config_dict: Optional pre-parsed UF config dict from DB cache.
        job_queue_depth: Current job queue depth for scale-up decisions.

    Returns:
        dict: Pool management results with transitions.
    """
    logger.info("Managing Apex/Nadir pools (queue_depth=%d)", job_queue_depth)

    db = get_database_client()
    service = LifecycleService(db=db, uf_config=build_uf_config(uf_config_dict))

    # Task 22: Delegate to LifecycleService pool management
    pool_result = run_async(service.manage_pools(job_queue_depth=job_queue_depth))

    results: Dict[str, Any] = {
        "timestamp": pool_result.timestamp,
        "apex_count": pool_result.apex_count,
        "nadir_count": pool_result.nadir_count,
        "promotions": pool_result.promotions,
        "demotions": pool_result.demotions,
        "errors": pool_result.errors,
    }

    logger.info("Apex/Nadir pool management completed: %s", results)
    return results


@celery_app.task(name="app.tasks.lifecycle.promote_nadir_to_apex", bind=True)
def promote_nadir_to_apex(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self, runner_ids: List[str]
) -> dict:
    """Promote Nadir runners to Apex state.

    Args:
        runner_ids: List of runner IDs to promote.

    Returns:
        dict: Promotion results.
    """
    logger.info("Promoting %d runners from Nadir to Apex", len(runner_ids))

    db = get_database_client()
    service = LifecycleService(db=db, uf_config=UFConfig())

    promoted: List[str] = []
    errors: List[str] = []

    for rid in runner_ids:
        # Task 22: Transition each runner to APEX type
        success = run_async(
            service._transition_runner(  # pylint: disable=protected-access
                rid, "apex", "promote_nadir_to_apex"
            )
        )
        if success:
            promoted.append(rid)
        else:
            errors.append(f"promote({rid}) failed")

    results: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "runners_promoted": promoted,
        "errors": errors,
    }

    logger.info("Nadir to Apex promotion completed: %s", results)
    return results


@celery_app.task(name="app.tasks.lifecycle.demote_apex_to_nadir", bind=True)
def demote_apex_to_nadir(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self, runner_ids: List[str]
) -> dict:
    """Demote Apex runners to Nadir state.

    Args:
        runner_ids: List of runner IDs to demote.

    Returns:
        dict: Demotion results.
    """
    logger.info("Demoting %d runners from Apex to Nadir", len(runner_ids))

    db = get_database_client()
    service = LifecycleService(db=db, uf_config=UFConfig())

    demoted: List[str] = []
    errors: List[str] = []

    for rid in runner_ids:
        # Task 22: Transition each runner to NADIR type
        success = run_async(
            service._transition_runner(  # pylint: disable=protected-access
                rid, "nadir", "demote_apex_to_nadir"
            )
        )
        if success:
            demoted.append(rid)
        else:
            errors.append(f"demote({rid}) failed")

    results: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "runners_demoted": demoted,
        "errors": errors,
    }

    logger.info("Apex to Nadir demotion completed: %s", results)
    return results


@celery_app.task(name="app.tasks.lifecycle.transition_runner_state", bind=True)
def transition_runner_state(  # type: ignore[no-untyped-def]  # pylint: disable=unused-argument
    self, runner_id: str, from_state: str, to_state: str
) -> dict:
    """Transition a runner from one state to another.

    Args:
        runner_id: Runner identifier.
        from_state: Current state (for audit logging).
        to_state: Target state.

    Returns:
        dict: Transition result.
    """
    logger.info("Transitioning runner %s: %s -> %s", runner_id, from_state, to_state)

    db = get_database_client()

    # Task 22: Direct state update with audit log
    success = run_async(
        db.update_runner_state(
            runner_id,
            to_state,
            {"previous_state": from_state},
        )
    )

    if success:
        run_async(
            db.create_audit_log(
                action="transition_state",
                resource_type="runner",
                resource_id=runner_id,
                actor="uglyfox",
                details={"from_state": from_state, "to_state": to_state},
            )
        )

    results: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat(),
        "runner_id": runner_id,
        "from_state": from_state,
        "to_state": to_state,
        "success": success,
        "error": "" if success else "state transition failed",
    }

    logger.info("Runner state transition completed: %s", results)
    return results
