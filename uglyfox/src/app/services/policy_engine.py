"""Policy evaluation engine for UglyFox.

Evaluates runners against pruning policies parsed from UF/config.fly
and determines Apex/Nadir pool transitions.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from app.model.policy_models import PruningPolicy, RunnerCondition, UFConfig
from app.model.runners_models import RunnerType
from app.util.base_logging import logged
from app.util.time_parser import parse_duration


@dataclass
class PolicyEvaluationResult:
    """Result of evaluating a single runner against pruning policies."""

    runner_id: str
    should_prune: bool
    reason: str = field(default="")
    policy_applied: str = field(default="")


def _get_idle_timeout_seconds(
    egg_name: str, runners_condition: List[RunnerCondition], default: str
) -> float:
    """Resolve idle_timeout in seconds for the given egg.

    Looks up the first runners_condition whose eggs_entities contains egg_name.
    Falls back to the provided default duration string.
    """
    for condition in runners_condition:
        if egg_name in condition.eggs_entities:
            return parse_duration(condition.nadir.idle_timeout)
    return parse_duration(default)


def _check_idle_timeout(
    runner: dict,  # type: ignore[type-arg]
    runner_id: str,
    egg_name: str,
    runners_condition: List[RunnerCondition],
    now: datetime,
) -> Optional["PolicyEvaluationResult"]:
    """Check idle timeout for APEX runners. Returns a result if pruning is needed."""
    runner_type_raw = runner.get("type", "")
    if str(runner_type_raw).lower() != RunnerType.APEX.value:
        return None
    last_heartbeat_raw = runner.get("last_heartbeat")
    if last_heartbeat_raw is None:
        return None
    last_heartbeat = (
        last_heartbeat_raw
        if isinstance(last_heartbeat_raw, datetime)
        else datetime.fromisoformat(str(last_heartbeat_raw))
    )
    idle_seconds = (now - last_heartbeat).total_seconds()
    idle_timeout_seconds = _get_idle_timeout_seconds(
        egg_name, runners_condition, default="30m"
    )
    if idle_seconds >= idle_timeout_seconds:
        return PolicyEvaluationResult(
            runner_id=runner_id,
            should_prune=True,
            reason="idle_timeout",
            policy_applied="idle_timeout",
        )
    return None


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

    def get_effective_policy(self, _egg_name: str) -> PruningPolicy:
        """Return the effective pruning policy for a given egg.

        The pruning block is global  per-egg overrides live in
        runners_condition (failure threshold via policies, age via pruning).
        Returns the global PruningPolicy unchanged; callers that need
        per-condition idle_timeout should use _get_idle_timeout_seconds.

        Args:
            _egg_name: Name of the egg to look up (unused; kept for API stability).

        Returns:
            Global PruningPolicy.
        """
        return self._uf_config.pruning

    def evaluate_runner(
        self, runner: dict, egg_name: str  # type: ignore[type-arg]
    ) -> PolicyEvaluationResult:
        """Evaluate a single runner against the effective policy.

        Checks (in order):
        1. failure_count >= failed_threshold
        2. runner age >= max_age (duration string)
        3. idle time >= idle_timeout from runners_condition.nadir (APEX only)

        Args:
            runner: Runner dict with keys matching the Runner model.
            egg_name: Egg name used to resolve idle_timeout from runners_condition.

        Returns:
            PolicyEvaluationResult indicating whether to prune and why.
        """
        runner_id: str = str(runner.get("id", ""))
        policy = self.get_effective_policy(egg_name)
        now = datetime.utcnow()

        # 1. Check failure threshold
        failure_count = int(runner.get("failure_count", 0))
        if failure_count >= policy.failed_threshold:
            self.debug(  # pylint: disable=no-member
                "Runner %s exceeds failure threshold (%d >= %d)",
                runner_id,
                failure_count,
                policy.failed_threshold,
            )
            return PolicyEvaluationResult(
                runner_id=runner_id,
                should_prune=True,
                reason="exceeded_failure_threshold",
                policy_applied="max_failures",
            )

        # 2. Check max age
        created_at_raw = runner.get("created_at")
        if created_at_raw is not None:
            created_at = (
                created_at_raw
                if isinstance(created_at_raw, datetime)
                else datetime.fromisoformat(str(created_at_raw))
            )
            age_seconds = (now - created_at).total_seconds()
            max_age_seconds = parse_duration(policy.max_age)
            if age_seconds >= max_age_seconds:
                self.debug(  # pylint: disable=no-member
                    "Runner %s exceeds max age (%.1fs >= %.1fs)",
                    runner_id,
                    age_seconds,
                    max_age_seconds,
                )
                return PolicyEvaluationResult(
                    runner_id=runner_id,
                    should_prune=True,
                    reason="exceeded_max_age",
                    policy_applied="max_age",
                )

        # 3. Check idle timeout (APEX runners only)
        idle_result = _check_idle_timeout(
            runner, runner_id, egg_name, self._uf_config.runners_condition, now
        )
        if idle_result is not None:
            self.debug(  # pylint: disable=no-member
                "Runner %s idle timeout triggered", runner_id
            )
            return idle_result

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
        """Determine whether the Apex pool should scale up."""
        apex_cfg = self._uf_config.apex_pool
        if current_apex_count >= apex_cfg.max_size:
            return False
        return job_queue_depth >= apex_cfg.scale_up_threshold

    def should_demote_to_nadir(self, current_apex_count: int) -> bool:
        """Determine whether excess APEX runners should be demoted to NADIR."""
        return current_apex_count > self._uf_config.apex_pool.max_size

    def should_promote_to_apex(
        self, current_nadir_count: int, current_apex_count: int
    ) -> bool:
        """Determine whether a NADIR runner should be promoted to APEX."""
        apex_cfg = self._uf_config.apex_pool
        nadir_cfg = self._uf_config.nadir_pool
        if current_apex_count >= apex_cfg.max_size:
            return False
        if current_nadir_count <= nadir_cfg.min_size:
            return False
        return current_apex_count < apex_cfg.min_size

    def get_idle_timeout_seconds(self, egg_name: str) -> Optional[float]:
        """Return idle_timeout in seconds for the given egg, or None if not configured.

        Args:
            egg_name: Egg name to look up in runners_condition.

        Returns:
            Idle timeout in seconds, or None if no matching condition found.
        """
        for condition in self._uf_config.runners_condition:
            if egg_name in condition.eggs_entities:
                return parse_duration(condition.nadir.idle_timeout)
        return None
