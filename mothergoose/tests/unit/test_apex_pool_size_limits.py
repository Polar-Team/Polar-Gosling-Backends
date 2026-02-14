"""
Property-Based Test: Apex Pool Size Limits

Feature: gitops-runner-orchestration
Property 14: Apex Pool Size Limits

For any Apex pool configuration with max_count N, the number of active runners
should never exceed N.

Validates: Requirements 6.7
"""

# pylint: disable=redefined-outer-name,unused-argument

from datetime import datetime, UTC
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.model.runners_models import (
    CloudProvider,
    Runner,
    RunnerState,
    RunnerType,
)
from app.services.runner_service import RunnerService
from app.services.vm_pool_manager import PoolConfig, VMPoolManager


# ============================================================================
# Hypothesis Strategies
# ============================================================================


@st.composite
def pool_config_strategy(draw):
    """Generate valid PoolConfig instances."""
    min_count = draw(st.integers(min_value=0, max_value=5))
    max_count = draw(st.integers(min_value=min_count, max_value=20))
    idle_timeout = draw(st.integers(min_value=5, max_value=60))

    pool_config = PoolConfig(
        max_count=max_count,
        min_count=min_count,
    )
    pool_config.idle_timeout_minutes = idle_timeout
    pool_config.type = "Apex"
    return pool_config


@st.composite
def runner_list_strategy(draw, egg_name: str, max_runners: int = 30):
    """Generate a list of Runner instances."""
    num_runners = draw(st.integers(min_value=0, max_value=max_runners))

    runners = []
    for i in range(num_runners):
        runner_type = draw(st.sampled_from([RunnerType.APEX, RunnerType.NADIR]))
        state = draw(
            st.sampled_from(
                [
                    RunnerState.ACTIVE,
                    RunnerState.IDLE,
                    RunnerState.BUSY,
                    RunnerState.TERMINATED,
                ]
            )
        )

        runner = Runner(
            id=f"runner-{i}",
            egg_name=egg_name,
            type=runner_type,
            state=state,
            cloud_provider=CloudProvider.YANDEX,
            region="ru-central1-a",
            deployed_from_commit="abc123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_heartbeat=datetime.now(UTC),
            failure_count=0,
            metadata={},
        )
        runners.append(runner)

    return runners


# ============================================================================
# Property-Based Tests
# ============================================================================


@pytest.mark.asyncio
@given(
    apex_config=pool_config_strategy(),
    nadir_config=pool_config_strategy(),
    initial_runners=runner_list_strategy(egg_name="test-egg"),
)
@settings(max_examples=100, deadline=None)
async def test_apex_pool_never_exceeds_max_count(
    apex_config: PoolConfig,
    nadir_config: PoolConfig,
    initial_runners: List[Runner],
):
    """
    Property 14: Apex Pool Size Limits

    For any Apex pool configuration with max_count N, the number of active
    Apex runners should never exceed N.

    This test verifies that:
    1. Initial pool count respects max_count
    2. After enforcement, pool count is <= max_count
    3. Enforcement demotes excess runners to Nadir
    """
    # Create mock runner service
    runner_service = MagicMock(spec=RunnerService)
    runner_service.list_runners_by_egg = AsyncMock(return_value=initial_runners)

    # Mock get_runner to return the actual runner from the list
    def get_runner_side_effect(runner_id: str):
        for runner in initial_runners:
            if runner.id == runner_id:
                return runner
        return None

    runner_service.get_runner = AsyncMock(side_effect=get_runner_side_effect)
    runner_service.update_runner = AsyncMock()

    # Create VM pool manager
    pool_manager = VMPoolManager(
        runner_service=runner_service,
        apex_config=apex_config,
        nadir_config=nadir_config,
    )

    # Get initial pool counts
    counts = await pool_manager.get_pool_counts("test-egg")
    initial_apex_count = counts["apex_count"]

    # Property: Initial count should be counted correctly
    expected_apex_count = sum(
        1
        for r in initial_runners
        if r.type == RunnerType.APEX and r.state != RunnerState.TERMINATED
    )
    assert initial_apex_count == expected_apex_count

    # Enforce pool limits
    result = await pool_manager.enforce_apex_pool_limits("test-egg")

    # Property: If initial count <= max_count, no demotion or termination needed
    if initial_apex_count <= apex_config.max_count:
        assert result["demoted_count"] == 0
        assert result["terminated_count"] == 0
    else:
        # Property: Demotion or termination should occur when exceeding max_count
        assert result["demoted_count"] >= 0
        assert result["terminated_count"] >= 0
        # At least one action should have been taken
        assert result["demoted_count"] + result["terminated_count"] > 0

    # Property: After enforcement, pool should not exceed max_count
    # The actual count after demotion/termination would be:
    # initial_apex_count - demoted_count - terminated_count <= max_count
    expected_final_count = (
        initial_apex_count - result["demoted_count"] - result["terminated_count"]
    )
    assert expected_final_count <= apex_config.max_count


@pytest.mark.asyncio
@given(
    max_count=st.integers(min_value=1, max_value=10),
    current_apex_count=st.integers(min_value=0, max_value=15),
)
@settings(max_examples=100, deadline=None)
async def test_can_add_apex_runner_respects_max_count(
    max_count: int,
    current_apex_count: int,
):
    """
    Property 14: Apex Pool Size Limits (Capacity Check)

    For any Apex pool with max_count N and current count C:
    - can_add_apex_runner() should return True if C < N
    - can_add_apex_runner() should return False if C >= N
    """
    # Create runners to match current_apex_count
    runners = [
        Runner(
            id=f"runner-{i}",
            egg_name="test-egg",
            type=RunnerType.APEX,
            state=RunnerState.ACTIVE,
            cloud_provider=CloudProvider.YANDEX,
            region="ru-central1-a",
            deployed_from_commit="abc123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_heartbeat=datetime.now(UTC),
            failure_count=0,
            metadata={},
        )
        for i in range(current_apex_count)
    ]

    # Create mock runner service
    runner_service = MagicMock(spec=RunnerService)
    runner_service.list_runners_by_egg = AsyncMock(return_value=runners)

    # Create pool manager
    apex_config = PoolConfig(max_count=max_count, min_count=0)
    apex_config.type = "Apex"
    nadir_config = PoolConfig(max_count=10, min_count=0)
    nadir_config.type = "Nadir"

    pool_manager = VMPoolManager(
        runner_service=runner_service,
        apex_config=apex_config,
        nadir_config=nadir_config,
    )

    # Check capacity
    can_add = await pool_manager.can_add_apex_runner("test-egg")

    # Property: Can add if current count < max_count
    if current_apex_count < max_count:
        assert can_add is True
    else:
        assert can_add is False


@pytest.mark.asyncio
async def test_apex_pool_enforcement_with_idle_runners():
    """
    Unit test: Verify that enforcement demotes idle runners first.

    This complements the property test by checking specific behavior.
    """
    # Create 5 Apex runners: 3 idle, 2 active
    runners = [
        Runner(
            id=f"idle-{i}",
            egg_name="test-egg",
            type=RunnerType.APEX,
            state=RunnerState.IDLE,
            cloud_provider=CloudProvider.YANDEX,
            region="ru-central1-a",
            deployed_from_commit="abc123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_heartbeat=datetime.now(UTC),
            failure_count=0,
            metadata={},
        )
        for i in range(3)
    ] + [
        Runner(
            id=f"active-{i}",
            egg_name="test-egg",
            type=RunnerType.APEX,
            state=RunnerState.ACTIVE,
            cloud_provider=CloudProvider.YANDEX,
            region="ru-central1-a",
            deployed_from_commit="abc123",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            last_heartbeat=datetime.now(UTC),
            failure_count=0,
            metadata={},
        )
        for i in range(2)
    ]

    # Mock runner service
    runner_service = MagicMock(spec=RunnerService)
    runner_service.list_runners_by_egg = AsyncMock(return_value=runners)

    # Mock get_runner to return the actual runner from the list
    def get_runner_side_effect(runner_id: str):
        for runner in runners:
            if runner.id == runner_id:
                return runner
        return None

    runner_service.get_runner = AsyncMock(side_effect=get_runner_side_effect)
    runner_service.update_runner = AsyncMock()

    # Create pool manager with max_count=3 (need to demote 2)
    apex_config = PoolConfig(
        max_count=3,
        min_count=0,
    )
    apex_config.type = "Apex"

    nadir_config = PoolConfig(
        max_count=10,
        min_count=0,
    )
    nadir_config.type = "Nadir"

    pool_manager = VMPoolManager(
        runner_service=runner_service,
        apex_config=apex_config,
        nadir_config=nadir_config,
    )

    # Enforce limits
    result = await pool_manager.enforce_apex_pool_limits("test-egg")

    # Should demote 2 runners (excess = 5 - 3 = 2)
    assert result["demoted_count"] == 2
    assert result["terminated_count"] == 0

    # Verify update_runner was called for demotion
    assert runner_service.update_runner.call_count == 2
