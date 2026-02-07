"""
Property-Based Test: Nadir to Apex Promotion

Feature: gitops-runner-orchestration
Property 15: Nadir to Apex Promotion

For any Nadir runner, when job demand increases and Apex pool is below max_count,
the runner should be promoted to Apex state.

Validates: Requirements 6.5
"""

# pylint: disable=redefined-outer-name,unused-argument

from datetime import datetime
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
def nadir_runner_strategy(draw, egg_name: str = "test-egg"):
    """Generate a Nadir runner."""
    runner_id = draw(
        st.text(
            min_size=5,
            max_size=20,
            alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
        )
    )

    return Runner(
        id=runner_id,
        egg_name=egg_name,
        type=RunnerType.NADIR,
        state=draw(st.sampled_from([RunnerState.IDLE, RunnerState.ACTIVE])),
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="abc123",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        last_heartbeat=datetime.now(),
        failure_count=0,
        metadata={},
    )


# ============================================================================
# Property-Based Tests
# ============================================================================


@pytest.mark.asyncio
@given(
    apex_max_count=st.integers(min_value=1, max_value=10),
    current_apex_count=st.integers(min_value=0, max_value=5),
    nadir_runner=nadir_runner_strategy(),
)
@settings(max_examples=100, deadline=None)
async def test_nadir_promoted_when_apex_has_capacity(
    apex_max_count: int,
    current_apex_count: int,
    nadir_runner: Runner,
):
    """
    Property 15: Nadir to Apex Promotion

    For any Nadir runner, when Apex pool is below max_count,
    the runner should be successfully promoted to Apex state.
    """
    # Create existing Apex runners
    apex_runners = [
        Runner(
            id=f"apex-{i}",
            egg_name="test-egg",
            type=RunnerType.APEX,
            state=RunnerState.ACTIVE,
            cloud_provider=CloudProvider.YANDEX,
            region="ru-central1-a",
            deployed_from_commit="abc123",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_heartbeat=datetime.now(),
            failure_count=0,
            metadata={},
        )
        for i in range(current_apex_count)
    ]

    all_runners = apex_runners + [nadir_runner]

    # Mock runner service
    runner_service = MagicMock(spec=RunnerService)
    runner_service.list_runners_by_egg = AsyncMock(return_value=all_runners)

    # Mock get_runner to return the actual runner
    def get_runner_side_effect(runner_id: str):
        for runner in all_runners:
            if runner.id == runner_id:
                return runner
        return None

    runner_service.get_runner = AsyncMock(side_effect=get_runner_side_effect)

    # Mock update_runner to return new runner instance (Runner is frozen/immutable)
    async def update_runner_side_effect(runner_id: str, updates: dict):
        runner = get_runner_side_effect(runner_id)
        if runner:
            # Create new Runner instance with updated values (frozen model)
            runner_dict = runner.model_dump()
            runner_dict.update(updates)
            return Runner(**runner_dict)
        return None

    runner_service.update_runner = AsyncMock(side_effect=update_runner_side_effect)

    # Create pool manager
    apex_config = PoolConfig(max_count=apex_max_count, min_count=0)
    apex_config.type = "Apex"

    nadir_config = PoolConfig(max_count=10, min_count=0)
    nadir_config.type = "Nadir"

    pool_manager = VMPoolManager(
        runner_service=runner_service,
        apex_config=apex_config,
        nadir_config=nadir_config,
    )

    # Property: If Apex pool has capacity, promotion should succeed
    has_capacity = current_apex_count < apex_max_count

    if has_capacity:
        # Promotion should succeed
        updated_runner = await pool_manager.promote_nadir_to_apex(
            nadir_runner.id,
            reason="demand_increase",
        )

        assert updated_runner is not None
        assert updated_runner.type == RunnerType.APEX
        assert updated_runner.state == RunnerState.ACTIVE
        assert "promoted_at" in updated_runner.metadata
        assert updated_runner.metadata["promotion_reason"] == "demand_increase"
    else:
        # Promotion should fail with RuntimeError
        with pytest.raises(RuntimeError, match="Apex pool at max capacity"):
            await pool_manager.promote_nadir_to_apex(
                nadir_runner.id,
                reason="demand_increase",
            )


@pytest.mark.asyncio
async def test_auto_promote_on_demand():
    """
    Unit test: Verify auto-promotion when demand increases.
    """
    # Create 3 Nadir runners
    nadir_runners = [
        Runner(
            id=f"nadir-{i}",
            egg_name="test-egg",
            type=RunnerType.NADIR,
            state=RunnerState.IDLE,
            cloud_provider=CloudProvider.YANDEX,
            region="ru-central1-a",
            deployed_from_commit="abc123",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            last_heartbeat=datetime.now(),
            failure_count=0,
            metadata={},
        )
        for i in range(3)
    ]

    # Mock runner service
    runner_service = MagicMock(spec=RunnerService)
    runner_service.list_runners_by_egg = AsyncMock(return_value=nadir_runners)

    # Mock get_runner
    def get_runner_side_effect(runner_id: str):
        for runner in nadir_runners:
            if runner.id == runner_id:
                return runner
        return None

    runner_service.get_runner = AsyncMock(side_effect=get_runner_side_effect)

    # Mock update_runner to return new runner instance (Runner is frozen/immutable)
    async def update_runner_side_effect(runner_id: str, updates: dict):
        runner = get_runner_side_effect(runner_id)
        if runner:
            # Create new Runner instance with updated values (frozen model)
            runner_dict = runner.model_dump()
            runner_dict.update(updates)
            return Runner(**runner_dict)
        return None

    runner_service.update_runner = AsyncMock(side_effect=update_runner_side_effect)

    # Create pool manager with Apex max_count=5
    apex_config = PoolConfig(max_count=5, min_count=0)
    nadir_config = PoolConfig(max_count=10, min_count=0)

    pool_manager = VMPoolManager(
        runner_service=runner_service,
        apex_config=apex_config,
        nadir_config=nadir_config,
    )

    # Auto-promote 2 runners
    result = await pool_manager.auto_promote_on_demand("test-egg", required_count=2)

    # Should promote 2 runners
    assert result["promoted_count"] == 2

    # Verify update_runner was called twice
    assert runner_service.update_runner.call_count == 2
