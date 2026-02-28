"""Policy evaluation engine for UglyFox.

Evaluates runners against pruning policies parsed from UF/config.fly
and determines Apex/Nadir pool transitions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from app.model.policy_models import PruningPolicy, UFConfig
from app.model.runners_models import RunnerType
from app.util.base_logging import logged
from app.util.time_parser import hours_to_seconds, minutes_to_seconds


@dataclass
class PolicyEvaluationResult:
    """Result of evaluating a single runner against pruning policies."""

    runner_id: str
    should_prune: bool
    reason: str = field(default="")
    policy_applied: str = field(default="")


@logged
class PolicyEngine:
    """Evaluates runners against UF/config.fly pruning policies.

    Determines which runners should be pruned and manages Apex/Nadir
    pool transitions based on configured thresholds.
    """

    def __init__(self, uf_config: UFConfig) -> None:
        """Initialise the policy engine with a parsed UFConfig.

        Args:
            uf_config: Parsed UF/config.fly configuration.
        """
        self._uf_config = uf_config

    def get_effective_policy(self, egg_name: str) -> PruningPolicy:
        """Return the effective pruning policy for a given egg.

        Merges global policy with any per-egg runners_condition override.

        Args:
            egg_name: Name of the egg to look up.

        Returns:
            Effective PruningPolicy (global defaults + per-egg overrides).
        """
        global_policy = self._uf_config.pruning
        for condition in self._uf_config.runners_condition:
            if condition.egg_name == egg_name:
                max_age = (
                    condition.max_age_hours
                    if condition.max_age_hours is not None
                    else global_policy.max_age_hours
                )
                max_fail = (
                    condition.max_failures
                    if condition.max_failures is not None
                    else global_policy.max_failures
                )
                return PruningPolicy(
                    max_age_hours=max_age,
                    max_failures=max_fail,
                    idle_timeout_minutes=global_policy.idle_timeout_minutes,
                    check_interval_seconds=global_policy.check_interval_seconds,
                )
        return global_policy

    def evaluate_runner(
        self, runner: dict, egg_name: str  # type: ignore[type-arg]
    ) -> PolicyEvaluationResult:
        """Evaluate a single runner against the effective policy.

        Checks (in order):
        1. failure_count >= max_failures
        2. runner age >= max_age_hours
        3. idle time >= idle_timeout_minutes (APEX runners only)

        Args:
            runner: Runner dict with keys matching the Runner model.
            egg_name: Egg name used to resolve the effective policy.

        Returns:
            PolicyEvaluationResult indicating whether to prune and why.
        """
        runner_id: str = str(runner.get("id", ""))
        policy = self.get_effective_policy(egg_name)
        now = datetime.utcnow()

        # Task 21: Check failure threshold
        failure_count = int(runner.get("failure_count", 0))
        if failure_count >= policy.max_failures:
            self.debug(
                "Runner %s exceeds failure threshold (%d >= %d)",
                runner_id,
                failure_count,
                policy.max_failures,
            )
            return PolicyEvaluationResult(
                runner_id=runner_id,
                should_prune=True,
                reason="exceeded_failure_threshold",
                policy_applied="max_failures",
            )

        # Task 21: Check max age
        created_at_raw = runner.get("created_at")
        if created_at_raw is not None:
            created_at = (
                created_at_raw
                if isinstance(created_at_raw, datetime)
                else datetime.fromisoformat(str(created_at_raw))
            )
            age_seconds = (now - created_at).total_seconds()
            if age_seconds >= hours_to_seconds(policy.max_age_hours):
                self.debug(
                    "Runner %s exceeds max age (%.1fs >= %.1fs)",
                    runner_id,
                    age_seconds,
                    hours_to_seconds(policy.max_age_hours),
                )
                return PolicyEvaluationResult(
                    runner_id=runner_id,
                    should_prune=True,
                    reason="exceeded_max_age",
                    policy_applied="max_age",
                )

        # Task 21: Check idle timeout (APEX runners only)
        runner_type_raw = runner.get("type", "")
        is_apex = str(runner_type_raw).lower() == RunnerType.APEX.value
        if is_apex:
            last_heartbeat_raw = runner.get("last_heartbeat")
            if last_heartbeat_raw is not None:
                last_heartbeat = (
                    last_heartbeat_raw
                    if isinstance(last_heartbeat_raw, datetime)
                    else datetime.fromisoformat(str(last_heartbeat_raw))
                )
                idle_seconds = (now - last_heartbeat).total_seconds()
                if idle_seconds >= minutes_to_seconds(policy.idle_timeout_minutes):
                    self.debug(
                        "Runner %s idle timeout (%.1fs >= %.1fs)",
                        runner_id,
                        idle_seconds,
                        minutes_to_seconds(policy.idle_timeout_minutes),
                    )
                    return PolicyEvaluationResult(
                        runner_id=runner_id,
                        should_prune=True,
                        reason="idle_timeout",
                        policy_applied="idle_timeout",
                    )

        return PolicyEvaluationResult(
            runner_id=runner_id,
            should_prune=False,
        )

    def evaluate_runners(
        self, runners: List[dict], egg_name: str  # type: ignore[type-arg]
    ) -> List[PolicyEvaluationResult]:
        """Evaluate a list of runners against the effective policy.

        Args:
            runners: List of runner dicts.
            egg_name: Egg name used to resolve the effective policy.

        Returns:
            List of PolicyEvaluationResult, one per runner.
        """
        return [self.evaluate_runner(r, egg_name) for r in runners]

    def should_scale_up_apex(
        self, current_apex_count: int, job_queue_depth: int
    ) -> bool:
        """Determine whether the Apex pool should scale up.

        Args:
            current_apex_count: Current number of APEX runners.
            job_queue_depth: Current job queue depth.

        Returns:
            True if scale-up is warranted.
        """
        apex_cfg = self._uf_config.apex_pool
        if current_apex_count >= apex_cfg.max_size:
            return False
        return job_queue_depth >= apex_cfg.scale_up_threshold

    def should_demote_to_nadir(self, current_apex_count: int) -> bool:
        """Determine whether excess APEX runners should be demoted to NADIR.

        Args:
            current_apex_count: Current number of APEX runners.

        Returns:
            True if demotion is warranted.
        """
        return current_apex_count > self._uf_config.apex_pool.max_size

    def should_promote_to_apex(
        self, current_nadir_count: int, current_apex_count: int
    ) -> bool:
        """Determine whether a NADIR runner should be promoted to APEX.

        Args:
            current_nadir_count: Current number of NADIR runners.
            current_apex_count: Current number of APEX runners.

        Returns:
            True if promotion is warranted.
        """
        apex_cfg = self._uf_config.apex_pool
        nadir_cfg = self._uf_config.nadir_pool
        if current_apex_count >= apex_cfg.max_size:
            return False
        if current_nadir_count <= nadir_cfg.min_size:
            return False
        return current_apex_count < apex_cfg.min_size
