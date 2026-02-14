"""
VM Runner Pool Management Service

Manages Apex and Nadir runner pools with automatic promotion/demotion logic.
Implements pool size limits and idle timeout enforcement.

Task 18: VM Runner Deployment with Apex/Nadir pool management
"""

from datetime import UTC, datetime, timedelta
from typing import Dict, List, Literal, Optional

from app.model.runners_models import (
    Runner,
    RunnerState,
    RunnerType,
)
from app.services.runner_service import RunnerService
from app.util.base_logging import logger


class PoolConfig:
    """
    Configuration for Apex or Nadir pool
    Setters and Properties:
            idle_timeout_minutes: Idle time before demotion in minutes (Apex only)
            (default: 30)
            type: "Apex" or "Nadir" (default: "Apex")
    """

    __idle_timeout_minutes_default: int = 30
    __type: Literal["Apex", "Nadir"] = "Apex"

    def __init__(
        self,
        max_count: int,
        min_count: int,
    ):
        """
        Initialize pool configuration.

        Args:
            max_count: Maximum number of runners in this pool
            min_count: Minimum number of runners to maintain
        """
        if max_count < min_count:
            raise ValueError("max_count must be >= min_count")
        if min_count < 0:
            raise ValueError("min_count must be non-negative")

        self.max_count = max_count
        self.min_count = min_count

    @property
    def type(self) -> Literal["Apex", "Nadir"]:
        """Type of pool (Apex or Nadir)"""
        return self.__type

    @type.setter
    def type(self, value: Literal["Apex", "Nadir"]) -> None:
        """Set type of pool (Apex or Nadir)"""
        if value not in ("Apex", "Nadir"):
            raise ValueError("type must be 'Apex' or 'Nadir'")
        self.__type = value

    @property
    def idle_timeout_minutes(self) -> int:
        """Idle timeout in minutes for Apex runners (default: 30)"""
        return self.__idle_timeout_minutes_default

    @idle_timeout_minutes.setter
    def idle_timeout_minutes(self, value: int) -> None:
        """Set idle timeout in minutes for Apex runners"""
        if value < 1:
            raise ValueError("idle_timeout_minutes must be at least 1")
        self.__idle_timeout_minutes_default = value


class VMPoolManager:
    """
    Manages VM runner pools (Apex and Nadir).

    Responsibilities:
    - Enforce pool size limits (max_count, min_count)
    - Promote Nadir runners to Apex when demand increases
    - Demote Apex runners to Nadir when idle beyond timeout
    - Track pool state and runner transitions
    """

    def __init__(
        self,
        runner_service: RunnerService,
        apex_config: PoolConfig,
        nadir_config: PoolConfig,
    ):
        """
        Initialize VM pool manager.

        Args:
            runner_service: Service for runner state management
            apex_config: Configuration for Apex pool
            nadir_config: Configuration for Nadir pool
        """
        self.runner_service = runner_service
        self.apex_config = apex_config
        self.nadir_config = nadir_config

    async def get_pool_counts(
        self,
        egg_name: str,
    ) -> Dict[str, int]:
        """
        Get current pool counts for an Egg.

        Args:
            egg_name: Name of the Egg

        Returns:
            Dictionary with apex_count and nadir_count
        """
        runners = await self.runner_service.list_runners_by_egg(egg_name)

        apex_count = sum(
            1
            for r in runners
            if r.type == RunnerType.APEX and r.state != RunnerState.TERMINATED
        )
        nadir_count = sum(
            1
            for r in runners
            if r.type == RunnerType.NADIR and r.state != RunnerState.TERMINATED
        )

        return {
            "apex_count": apex_count,
            "nadir_count": nadir_count,
        }

    async def can_add_apex_runner(self, egg_name: str) -> bool:
        """
        Check if a new Apex runner can be added without exceeding max_count.

        Args:
            egg_name: Name of the Egg

        Returns:
            True if Apex pool has capacity, False otherwise
        """
        counts = await self.get_pool_counts(egg_name)
        return counts["apex_count"] < self.apex_config.max_count

    async def can_add_nadir_runner(self, egg_name: str) -> bool:
        """
        Check if a new Nadir runner can be added without exceeding max_count.

        Args:
            egg_name: Name of the Egg

        Returns:
            True if Nadir pool has capacity, False otherwise
        """
        counts = await self.get_pool_counts(egg_name)
        return counts["nadir_count"] < self.nadir_config.max_count

    async def promote_nadir_to_apex(
        self,
        runner_id: str,
        reason: str = "demand_increase",
    ) -> Optional[Runner]:
        """
        Promote a Nadir runner to Apex state.

        This transitions a dormant runner to active state when job demand increases.

        Args:
            runner_id: ID of the Nadir runner to promote
            reason: Reason for promotion (for audit trail)

        Returns:
            Updated runner object if successful, None otherwise

        Raises:
            ValueError: If runner is not in Nadir state
            RuntimeError: If Apex pool is at max capacity
        """
        # Get runner
        runner = await self.runner_service.get_runner(runner_id)
        if not runner:
            raise ValueError(f"Runner not found: {runner_id}")

        # Validate runner is Nadir
        if runner.type != RunnerType.NADIR:
            raise ValueError(
                f"Runner {runner_id} is not Nadir (current type: {runner.type})"
            )

        # Check Apex pool capacity
        if not await self.can_add_apex_runner(runner.egg_name):
            raise RuntimeError(
                f"Apex pool at max capacity ({self.apex_config.max_count})"
            )

        logger.info(
            "Promoting Nadir runner %s to Apex (reason: %s)",
            runner_id,
            reason,
        )

        # Update runner type to APEX
        updated_runner = await self.runner_service.update_runner(
            runner_id=runner_id,
            updates={
                "type": RunnerType.APEX.value,
                "state": RunnerState.ACTIVE.value,
                "metadata": {
                    **runner.metadata,
                    "promoted_at": datetime.now(UTC).isoformat(),
                    "promotion_reason": reason,
                },
            },
        )

        logger.info("Runner %s promoted to Apex successfully", runner_id)
        return updated_runner

    async def demote_apex_to_nadir(
        self,
        runner_id: str,
        reason: str = "idle_timeout",
    ) -> Optional[Runner]:
        """
        Demote an Apex runner to Nadir state.

        This transitions an active runner to dormant state when idle beyond timeout.

        Args:
            runner_id: ID of the Apex runner to demote
            reason: Reason for demotion (for audit trail)

        Returns:
            Updated runner object if successful, None otherwise

        Raises:
            ValueError: If runner is not in Apex state
            RuntimeError: If Nadir pool is at max capacity
        """
        # Get runner
        runner = await self.runner_service.get_runner(runner_id)
        if not runner:
            raise ValueError(f"Runner not found: {runner_id}")

        # Validate runner is Apex
        if runner.type != RunnerType.APEX:
            raise ValueError(
                f"Runner {runner_id} is not Apex (current type: {runner.type})"
            )

        # Check Nadir pool capacity
        if not await self.can_add_nadir_runner(runner.egg_name):
            raise RuntimeError(
                f"Nadir pool at max capacity ({self.nadir_config.max_count})"
            )

        logger.info(
            "Demoting Apex runner %s to Nadir (reason: %s)",
            runner_id,
            reason,
        )

        # Update runner type to NADIR
        updated_runner = await self.runner_service.update_runner(
            runner_id=runner_id,
            updates={
                "type": RunnerType.NADIR.value,
                "state": RunnerState.IDLE.value,
                "metadata": {
                    **runner.metadata,
                    "demoted_at": datetime.now(UTC).isoformat(),
                    "demotion_reason": reason,
                },
            },
        )

        logger.info("Runner %s demoted to Nadir successfully", runner_id)
        return updated_runner

    async def find_idle_apex_runners(
        self,
        egg_name: str,
    ) -> List[Runner]:
        """
        Find Apex runners that have been idle beyond the configured timeout.

        Args:
            egg_name: Name of the Egg

        Returns:
            List of idle Apex runners eligible for demotion
        """
        runners = await self.runner_service.list_runners_by_egg(egg_name)

        idle_threshold = datetime.now(UTC) - timedelta(
            minutes=self.apex_config.idle_timeout_minutes
        )

        idle_runners = []
        for runner in runners:
            if runner.type != RunnerType.APEX:
                continue
            if runner.state != RunnerState.IDLE:
                continue
            if not runner.last_heartbeat:
                continue

            # Check if idle beyond timeout
            if runner.last_heartbeat < idle_threshold:
                idle_runners.append(runner)

        return idle_runners

    async def find_promotable_nadir_runners(
        self,
        egg_name: str,
        count: int = 1,
    ) -> List[Runner]:
        """
        Find Nadir runners that can be promoted to Apex.

        Selects runners based on:
        - Healthy state (not failed)
        - Recent heartbeat (active within last 5 minutes)
        - Oldest first (FIFO promotion)

        Args:
            egg_name: Name of the Egg
            count: Number of runners to find

        Returns:
            List of Nadir runners eligible for promotion
        """
        runners = await self.runner_service.list_runners_by_egg(egg_name)

        # Filter for healthy Nadir runners
        nadir_runners = [
            r
            for r in runners
            if r.type == RunnerType.NADIR
            and r.state != RunnerState.FAILED
            and r.state != RunnerState.TERMINATED
            and r.last_heartbeat
            and r.last_heartbeat
            > datetime.now(UTC) - timedelta(minutes=5)  # Active within 5 min
        ]

        # Sort by creation time (oldest first)
        nadir_runners.sort(key=lambda r: r.created_at)

        return nadir_runners[:count]

    async def enforce_apex_pool_limits(
        self,
        egg_name: str,
    ) -> Dict[str, int]:
        """
        Enforce Apex pool size limits by demoting or terminating excess runners.

        If Apex pool exceeds max_count, demote idle runners to Nadir.
        If Nadir pool is at capacity, terminate runners instead.
        Prioritizes runners that have been idle longest.

        Args:
            egg_name: Name of the Egg

        Returns:
            Dictionary with demoted_count and terminated_count
        """
        counts = await self.get_pool_counts(egg_name)
        apex_count = counts["apex_count"]

        if apex_count <= self.apex_config.max_count:
            return {"demoted_count": 0, "terminated_count": 0}

        # Get all Apex runners
        runners = await self.runner_service.list_runners_by_egg(egg_name)
        apex_runners = [
            r
            for r in runners
            if r.type == RunnerType.APEX and r.state != RunnerState.TERMINATED
        ]

        # Sort by state (IDLE first) and then by last_heartbeat (oldest first)
        # This ensures we demote idle runners first, then least recently active
        def sort_key(runner: Runner) -> tuple[int, datetime]:
            # IDLE runners first (0), then others (1)
            state_priority = 0 if runner.state == RunnerState.IDLE else 1
            # Older heartbeat first (None treated as very old)
            heartbeat_time = runner.last_heartbeat or datetime.min
            return (state_priority, heartbeat_time)

        apex_runners.sort(key=sort_key)

        # Calculate how many to demote
        excess_count = apex_count - self.apex_config.max_count
        to_demote = min(excess_count, len(apex_runners))

        demoted_count = 0
        terminated_count = 0
        for runner in apex_runners[:to_demote]:
            try:
                await self.demote_apex_to_nadir(
                    runner.id,
                    reason="pool_limit_enforcement",
                )
                demoted_count += 1
            except RuntimeError as e:
                # If Nadir pool is at capacity, terminate the runner instead
                if "Nadir pool at max capacity" in str(e):
                    logger.info(
                        "Nadir pool at capacity, terminating runner %s instead",
                        runner.id,
                    )
                    await self.runner_service.update_runner(
                        runner_id=runner.id,
                        updates={
                            "state": RunnerState.TERMINATED.value,
                            "metadata": {
                                **runner.metadata,
                                "terminated_at": datetime.now(UTC).isoformat(),
                                "termination_reason": "apex_limit_enforcement_nadir_full",
                            },
                        },
                    )
                    terminated_count += 1
                else:
                    logger.warning(
                        "Failed to demote runner %s: %s",
                        runner.id,
                        str(e),
                    )
            except ValueError as e:
                logger.warning(
                    "Failed to demote runner %s: %s",
                    runner.id,
                    str(e),
                )

        logger.info(
            "Enforced Apex pool limits for %s: demoted %d runners, terminated %d runners",
            egg_name,
            demoted_count,
            terminated_count,
        )

        return {"demoted_count": demoted_count, "terminated_count": terminated_count}

    async def auto_promote_on_demand(
        self,
        egg_name: str,
        required_count: int = 1,
    ) -> Dict[str, int]:
        """
        Automatically promote Nadir runners to Apex when demand increases.

        Args:
            egg_name: Name of the Egg
            required_count: Number of additional Apex runners needed

        Returns:
            Dictionary with promoted_count
        """
        # Check if we can add more Apex runners
        counts = await self.get_pool_counts(egg_name)
        apex_count = counts["apex_count"]

        available_capacity = self.apex_config.max_count - apex_count
        if available_capacity <= 0:
            logger.warning(
                "Apex pool at max capacity for %s, cannot promote",
                egg_name,
            )
            return {"promoted_count": 0}

        # Find promotable Nadir runners
        to_promote_count = min(required_count, available_capacity)
        promotable_runners = await self.find_promotable_nadir_runners(
            egg_name,
            count=to_promote_count,
        )

        promoted_count = 0
        for runner in promotable_runners:
            try:
                await self.promote_nadir_to_apex(
                    runner.id,
                    reason="demand_increase",
                )
                promoted_count += 1
            except (ValueError, RuntimeError) as e:
                logger.warning(
                    "Failed to promote runner %s: %s",
                    runner.id,
                    str(e),
                )

        logger.info(
            "Auto-promoted %d Nadir runners to Apex for %s",
            promoted_count,
            egg_name,
        )

        return {"promoted_count": promoted_count}

    async def auto_demote_idle_runners(
        self,
        egg_name: str,
    ) -> Dict[str, int]:
        """
        Automatically demote Apex runners that are idle beyond timeout.

        Args:
            egg_name: Name of the Egg

        Returns:
            Dictionary with demoted_count
        """
        idle_runners = await self.find_idle_apex_runners(egg_name)

        demoted_count = 0
        for runner in idle_runners:
            try:
                await self.demote_apex_to_nadir(
                    runner.id,
                    reason="idle_timeout",
                )
                demoted_count += 1
            except (ValueError, RuntimeError) as e:
                logger.warning(
                    "Failed to demote runner %s: %s",
                    runner.id,
                    str(e),
                )

        if demoted_count > 0:
            logger.info(
                "Auto-demoted %d idle Apex runners to Nadir for %s",
                demoted_count,
                egg_name,
            )

        return {"demoted_count": demoted_count}
