"""
Property-Based Test: Apex to Nadir Demotion

Feature: gitops-runner-orchestration
Property 16: Apex to Nadir Demotion

For any Apex runner that is idle beyond the configured idle_timeout,
the runner should be demoted to Nadir state.

Validates: Requirements 6.6
"""

# pylint: disable=redefined-outer-name,unused-argument

from datetime import datetime, timedelta, timezone
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
def idle_apex_runner_strategy(draw, egg_name: str = "test-egg", idle_minutes: int = 35):
    """Generate an idle Apex runner."""
    runner_id = draw(
        st.text(
            min_size=5,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
        )
    )

    # Create runner with old heartbeat (idle beyond timeout)
    last_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)

    return Runner(
        id=runner_id,
        egg_name=egg_name,
        type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="abc123",
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=idle_minutes),
        last_heartbeat=last_heartbeat,
        failure_count=0,
        metadata={},
    )


# ============================================================================
# Property-Based Tests
# ============================================================================


@pytest.mark.asyncio
@given(
    idle_timeout_minutes=st.integers(min_value=5, max_value=60),
    idle_minutes=st.integers(min_value=1, max_value=120),
    nadir_max_count=st.integers(min_value=1, max_value=10),
    current_nadir_count=st.integers(min_value=0, max_value=5),
)
@settings(max_examples=100, deadline=None)
async def test_apex_demoted_when_idle_beyond_timeout(
    idle_timeout_minutes: int,
    idle_minutes: int,
    nadir_max_count: int,
    current_nadir_count: int,
):
    """
    Property 16: Apex to Nadir Demotion

    For any Apex runner that is idle beyond the configured idle_timeout,
    the runner should be demoted to Nadir state (if Nadir has capacity).
    """
    # Create idle Apex runner
    apex_runner = Runner(
        id="apex-idle",
        egg_name="test-egg",
        type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="abc123",
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        updated_at=datetime.now(timezone.utc) - timedelta(minutes=idle_minutes),
        last_heartbeat=datetime.now(timezone.utc) - timedelta(minutes=idle_minutes),
        failure_count=0,
        metadata={},
    )

    # Create existing Nadir runners
    nadir_runners = [
        Runner(
            id=f"nadir-{i}",
            egg_name="test-egg",
            type=RunnerType.NADIR,
            state=RunnerState.IDLE,
            cloud_provider=CloudProvider.YANDEX,
            region="ru-central1-a",
            deployed_from_commit="abc123",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            last_heartbeat=datetime.now(timezone.utc),
            failure_count=0,
            metadata={},
        )
        for i in range(current_nadir_count)
    ]

    all_runners = [apex_runner] + nadir_runners

    # Mock runner service
    runner_service = MagicMock(spec=RunnerService)
    runner_service.list_runners_by_egg = AsyncMock(return_value=all_runners)

    # Mock get_runner
    def get_runner_side_effect(runner_id: str):
        for runner in all_runners:
            if runner.id == runner_id:
                return runner
        return None

    runner_service.get_runner = AsyncMock(side_effect=get_runner_side_effect)

    # Mock update_runner to return a new Runner with updated values
    async def update_runner_side_effect(runner_id: str, updates: dict):
        runner = get_runner_side_effect(runner_id)
        if runner:
            # Create new Runner with updated values
            runner_dict = runner.model_dump()
            runner_dict.update(updates)
            return Runner(**runner_dict)
        return None

    runner_service.update_runner = AsyncMock(side_effect=update_runner_side_effect)

    # Create pool manager
    apex_config = PoolConfig(
        max_count=10,
        min_count=0,
    )
    apex_config.idle_timeout_minutes = idle_timeout_minutes  # Set idle timeout for Apex
    apex_config.type = "Apex"

    nadir_config = PoolConfig(max_count=nadir_max_count, min_count=0)
    nadir_config.type = "Nadir"

    pool_manager = VMPoolManager(
        runner_service=runner_service,
        apex_config=apex_config,
        nadir_config=nadir_config,
    )

    # Property: If runner is idle beyond timeout AND Nadir has capacity, demotion should succeed
    is_idle_beyond_timeout = idle_minutes > idle_timeout_minutes
    nadir_has_capacity = current_nadir_count < nadir_max_count

    if is_idle_beyond_timeout and nadir_has_capacity:
        # Demotion should succeed
        updated_runner = await pool_manager.demote_apex_to_nadir(
            apex_runner.id,
            reason="idle_timeout",
        )

        assert updated_runner is not None
        assert updated_runner.type == RunnerType.NADIR
        assert updated_runner.state == RunnerState.IDLE
        assert "demoted_at" in updated_runner.metadata
        assert updated_runner.metadata["demotion_reason"] == "idle_timeout"
    elif not nadir_has_capacity:
        # Demotion should fail with RuntimeError
        with pytest.raises(RuntimeError, match="Nadir pool at max capacity"):
            await pool_manager.demote_apex_to_nadir(
                apex_runner.id,
                reason="idle_timeout",
            )
    # If not idle beyond timeout, we don't test demotion (not part of this property)


@pytest.mark.asyncio
async def test_auto_demote_idle_runners():
    """
    Unit test: Verify auto-demotion of idle runners.
    """
    # Create 2 idle Apex runners (idle for 35 minutes)
    idle_runners = [
        Runner(
            id=f"apex-idle-{i}",
            egg_name="test-egg",
            type=RunnerType.APEX,
            state=RunnerState.IDLE,
            cloud_provider=CloudProvider.YANDEX,
            region="ru-central1-a",
            deployed_from_commit="abc123",
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
            updated_at=datetime.now(timezone.utc) - timedelta(minutes=35),
            last_heartbeat=datetime.now(timezone.utc) - timedelta(minutes=35),
            failure_count=0,
            metadata={},
        )
        for i in range(2)
    ]

    # Create 1 active Apex runner (should not be demoted)
    active_runner = Runner(
        id="apex-active",
        egg_name="test-egg",
        type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="abc123",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        last_heartbeat=datetime.now(timezone.utc),
        failure_count=0,
        metadata={},
    )

    all_runners = idle_runners + [active_runner]

    # Mock runner service
    runner_service = MagicMock(spec=RunnerService)
    runner_service.list_runners_by_egg = AsyncMock(return_value=all_runners)

    # Mock get_runner
    def get_runner_side_effect(runner_id: str):
        for runner in all_runners:
            if runner.id == runner_id:
                return runner
        return None

    runner_service.get_runner = AsyncMock(side_effect=get_runner_side_effect)

    # Mock update_runner
    async def update_runner_side_effect(runner_id: str, updates: dict):
        runner = get_runner_side_effect(runner_id)
        if runner:
            runner_dict = runner.model_dump()
            runner_dict.update(updates)
            return Runner(**runner_dict)
        return None

    runner_service.update_runner = AsyncMock(side_effect=update_runner_side_effect)

    # Create pool manager with idle_timeout=30 minutes
    apex_config = PoolConfig(
        max_count=10,
        min_count=0,
    )
    apex_config.type = "Apex"
    apex_config.idle_timeout_minutes = 30

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

    # Auto-demote idle runners
    result = await pool_manager.auto_demote_idle_runners("test-egg")

    # Should demote 2 idle runners (not the active one)
    assert result["demoted_count"] == 2

    # Verify update_runner was called twice
    assert runner_service.update_runner.call_count == 2
