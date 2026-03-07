"""Runner lifecycle management service for UglyFox.

Implements health monitoring, failure-threshold termination, age-based
termination, Apex/Nadir pool transitions, and audit logging.

Requirements: 7.1, 7.3, 7.4, 7.5, 7.6, 7.7
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db.database_client import DatabaseClient
from app.model.policy_models import UFConfig
from app.model.runners_models import RunnerState, RunnerType
from app.services.policy_engine import PolicyEngine, PolicyEvaluationResult
from app.util.base_logging import logged

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:  # pylint: disable=too-many-instance-attributes
    """Aggregated result of a full health-check sweep."""

    timestamp: str
    total_runners: int = 0
    healthy_runners: int = 0
    unhealthy_runners: int = 0
    failed_runners: int = 0
    idle_runners: int = 0
    runners_to_prune: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class PruningResult:
    """Result of a pruning sweep."""

    timestamp: str
    runners_evaluated: int = 0
    runners_pruned: List[str] = field(default_factory=list)
    policies_applied: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@dataclass
class PoolTransitionResult:
    """Result of an Apex/Nadir pool transition sweep."""

    timestamp: str
    apex_count: int = 0
    nadir_count: int = 0
    promotions: List[str] = field(default_factory=list)
    demotions: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


@logged
class LifecycleService:  # pylint: disable=too-many-instance-attributes
    """Orchestrates runner lifecycle: health checks, pruning, pool transitions.

    All database I/O is async; callers must ``await`` the public methods.
    """

    def __init__(self, db: DatabaseClient, uf_config: UFConfig) -> None:
        """Initialise the service.

        Args:
            db: Async database client (YDB or DynamoDB).
            uf_config: Parsed UF/config.fly configuration.
        """
        self._db = db
        self._engine = PolicyEngine(uf_config)

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------

    async def check_all_runners(self) -> HealthCheckResult:
        """Sweep all non-terminated runners and classify their health.

        Returns:
            HealthCheckResult with counts and list of runners to prune.
        """
        now = datetime.utcnow().isoformat()
        result = HealthCheckResult(timestamp=now)

        # Task 22: Collect runners across all active states
        active_states = [
            RunnerState.ACTIVE.value,
            RunnerState.IDLE.value,
            RunnerState.BUSY.value,
            RunnerState.FAILED.value,
        ]

        all_runners: List[Dict[str, Any]] = []
        for state in active_states:
            try:
                runners = await self._db.list_runners_by_state(state)
                all_runners.extend(runners)
            except Exception as exc:  # pylint: disable=broad-except
                self.warning(  # pylint: disable=no-member
                    "Failed to list runners in state %s: %s", state, exc
                )
                result.errors.append(f"list_runners_by_state({state}): {exc}")

        result.total_runners = len(all_runners)

        for runner in all_runners:
            state = str(runner.get("state", ""))
            egg_name = str(runner.get("egg_name", ""))
            runner_id = str(runner.get("id", ""))

            if state == RunnerState.FAILED.value:
                result.failed_runners += 1
            elif state == RunnerState.IDLE.value:
                result.idle_runners += 1

            # Task 22: Evaluate against policy
            eval_result: PolicyEvaluationResult = self._engine.evaluate_runner(
                runner, egg_name
            )
            if eval_result.should_prune:
                result.unhealthy_runners += 1
                result.runners_to_prune.append(runner_id)
            else:
                result.healthy_runners += 1

        self.info(  # pylint: disable=no-member
            "Health check: total=%d healthy=%d unhealthy=%d to_prune=%d",
            result.total_runners,
            result.healthy_runners,
            result.unhealthy_runners,
            len(result.runners_to_prune),
        )
        return result

    # ------------------------------------------------------------------
    # Pruning
    # ------------------------------------------------------------------

    async def prune_runners(
        self, runner_ids: Optional[List[str]] = None, egg_name: str = ""
    ) -> PruningResult:
        """Terminate runners that violate pruning policies.

        Args:
            runner_ids: Explicit list of runner IDs to evaluate.
                        If None, all non-terminated runners are evaluated.
            egg_name: Egg name used for per-egg policy resolution when
                      runner_ids is provided without full runner dicts.

        Returns:
            PruningResult with counts and terminated runner IDs.
        """
        now = datetime.utcnow().isoformat()
        result = PruningResult(timestamp=now)

        if runner_ids is not None:
            # Task 22: Evaluate explicit list
            runners: List[Dict[str, Any]] = []
            for rid in runner_ids:
                try:
                    runner = await self._db.get_runner_by_id(rid)
                    if runner:
                        runners.append(runner)
                except Exception as exc:  # pylint: disable=broad-except
                    result.errors.append(f"get_runner_by_id({rid}): {exc}")
        else:
            # Task 22: Evaluate all active runners
            runners = []
            for state in [
                RunnerState.ACTIVE.value,
                RunnerState.IDLE.value,
                RunnerState.BUSY.value,
                RunnerState.FAILED.value,
            ]:
                try:
                    runners.extend(await self._db.list_runners_by_state(state))
                except Exception as exc:  # pylint: disable=broad-except
                    result.errors.append(f"list_runners_by_state({state}): {exc}")

        result.runners_evaluated = len(runners)

        for runner in runners:
            rid = str(runner.get("id", ""))
            ename = str(runner.get("egg_name", egg_name))
            eval_result = self._engine.evaluate_runner(runner, ename)

            if eval_result.should_prune:
                terminated = await self._terminate_runner(
                    rid, eval_result.reason, eval_result.policy_applied
                )
                if terminated:
                    result.runners_pruned.append(rid)
                    if eval_result.policy_applied not in result.policies_applied:
                        result.policies_applied.append(eval_result.policy_applied)
                else:
                    result.errors.append(f"terminate_runner({rid}) failed")

        self.info(  # pylint: disable=no-member
            "Pruning: evaluated=%d pruned=%d",
            result.runners_evaluated,
            len(result.runners_pruned),
        )
        return result

    async def _terminate_runner(
        self, runner_id: str, reason: str, policy_applied: str
    ) -> bool:
        """Terminate a single runner and write an audit log.

        Args:
            runner_id: Runner to terminate.
            reason: Human-readable termination reason.
            policy_applied: Policy name that triggered termination.

        Returns:
            True if state update succeeded.
        """
        self.info(  # pylint: disable=no-member
            "Terminating runner %s (reason=%s, policy=%s)",
            runner_id,
            reason,
            policy_applied,
        )
        try:
            updated = await self._db.update_runner_state(
                runner_id,
                RunnerState.TERMINATED.value,
                {"termination_reason": reason, "policy_applied": policy_applied},
            )
            if updated:
                await self._db.create_audit_log(
                    action="terminate",
                    resource_type="runner",
                    resource_id=runner_id,
                    actor="uglyfox",
                    details={"reason": reason, "policy_applied": policy_applied},
                )
            return updated
        except Exception as exc:  # pylint: disable=broad-except
            self.error(  # pylint: disable=no-member
                "Failed to terminate runner %s: %s", runner_id, exc
            )
            return False

    # ------------------------------------------------------------------
    # Apex / Nadir pool transitions
    # ------------------------------------------------------------------

    async def manage_pools(self, job_queue_depth: int = 0) -> PoolTransitionResult:
        """Evaluate and execute Apex/Nadir pool transitions.

        Args:
            job_queue_depth: Current job queue depth (used for scale-up decisions).

        Returns:
            PoolTransitionResult with counts and transition lists.
        """
        now = datetime.utcnow().isoformat()
        result = PoolTransitionResult(timestamp=now)

        try:
            apex_runners = await self._db.list_runners_by_state(RunnerType.APEX.value)
            nadir_runners = await self._db.list_runners_by_state(RunnerType.NADIR.value)
        except Exception as exc:  # pylint: disable=broad-except
            result.errors.append(f"list_runners_by_state: {exc}")
            return result

        result.apex_count = len(apex_runners)
        result.nadir_count = len(nadir_runners)

        # Task 22: Demote excess APEX → NADIR
        if self._engine.should_demote_to_nadir(result.apex_count):
            excess = (
                result.apex_count
                - self._engine._uf_config.apex_pool.max_size  # pylint: disable=protected-access
            )
            idle_apex = [
                r
                for r in apex_runners
                if str(r.get("state", "")) == RunnerState.IDLE.value
            ]
            to_demote = idle_apex[:excess]
            for runner in to_demote:
                rid = str(runner.get("id", ""))
                demoted = await self._transition_runner(
                    rid, RunnerType.NADIR.value, "demote_apex_to_nadir"
                )
                if demoted:
                    result.demotions.append(rid)
                else:
                    result.errors.append(f"demote({rid}) failed")

        # Task 22: Promote NADIR → APEX when apex below min
        if self._engine.should_promote_to_apex(result.nadir_count, result.apex_count):
            nadir_candidate: Optional[Dict[str, Any]] = (
                nadir_runners[0] if nadir_runners else None
            )
            if nadir_candidate:
                rid = str(nadir_candidate.get("id", ""))
                promoted = await self._transition_runner(
                    rid, RunnerType.APEX.value, "promote_nadir_to_apex"
                )
                if promoted:
                    result.promotions.append(rid)
                else:
                    result.errors.append(f"promote({rid}) failed")

        # Task 22: Scale up APEX when queue is deep
        if self._engine.should_scale_up_apex(result.apex_count, job_queue_depth):
            self.info(  # pylint: disable=no-member
                "Scale-up warranted: apex=%d queue_depth=%d",
                result.apex_count,
                job_queue_depth,
            )

        self.info(  # pylint: disable=no-member
            "Pool management: apex=%d nadir=%d promotions=%d demotions=%d",
            result.apex_count,
            result.nadir_count,
            len(result.promotions),
            len(result.demotions),
        )
        return result

    async def _transition_runner(
        self, runner_id: str, new_type: str, action: str
    ) -> bool:
        """Transition a runner's type and write an audit log.

        Args:
            runner_id: Runner to transition.
            new_type: Target RunnerType value.
            action: Audit action label.

        Returns:
            True if state update succeeded.
        """
        try:
            updated = await self._db.update_runner_state(
                runner_id,
                RunnerState.IDLE.value,
                {"type": new_type},
            )
            if updated:
                await self._db.create_audit_log(
                    action=action,
                    resource_type="runner",
                    resource_id=runner_id,
                    actor="uglyfox",
                    details={"new_type": new_type},
                )
            return updated
        except Exception as exc:  # pylint: disable=broad-except
            self.error(  # pylint: disable=no-member
                "Failed to transition runner %s to %s: %s", runner_id, new_type, exc
            )
            return False

    # ------------------------------------------------------------------
    # Convenience: run health + prune in one call
    # ------------------------------------------------------------------

    async def run_health_and_prune(self) -> Dict[str, Any]:
        """Run a full health-check sweep followed by pruning.

        Returns:
            Combined dict with health and pruning results.
        """
        health = await self.check_all_runners()
        pruning = PruningResult(timestamp=health.timestamp)

        if health.runners_to_prune:
            pruning = await self.prune_runners(runner_ids=health.runners_to_prune)

        return {
            "health": health.__dict__,
            "pruning": pruning.__dict__,
        }


def run_async(coro: Any) -> Any:  # type: ignore[type-arg]
    """Run an async coroutine from a sync Celery task context."""
    import concurrent.futures  # pylint: disable=import-outside-toplevel

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
