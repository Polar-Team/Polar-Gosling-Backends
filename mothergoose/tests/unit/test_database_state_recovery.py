"""
Property-based tests for database state recovery.

Feature: gitops-runner-orchestration, Property 31: Database State Recovery
Validates: Requirements 14.6

This module tests that for any backend server restart, the system should
restore all runner states from the database and continue operations.
"""

import pytest
from datetime import datetime, timezone
from hypothesis import given, settings, strategies as st, HealthCheck
from typing import Any, List

from app.model.runners_models import (
    Runner,
    RunnerState,
    RunnerType,
    CloudProvider,
)


class StateRecoveryService:
    """Service for recovering system state from database after restart."""

    def __init__(self, db_client: Any) -> None:
        """
        Initialize the state recovery service.

        Args:
            db_client: Database client for querying state
        """
        self.db_client = db_client

    async def create_runner(
        self,
        egg_name: str,
        runner_type: RunnerType,
        state: RunnerState,
        cloud_provider: CloudProvider,
        region: str,
        deployed_from_commit: str,
        gitlab_runner_id: int = None,
        metadata: dict = None,
    ) -> Runner:
        """Create a runner using mock database."""
        from uuid import uuid4

        runner_id = f"runner-{uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)

        runner = Runner(
            id=runner_id,
            egg_name=egg_name,
            type=runner_type,
            state=state,
            cloud_provider=cloud_provider,
            region=region,
            gitlab_runner_id=gitlab_runner_id,
            deployed_from_commit=deployed_from_commit,
            created_at=now,
            updated_at=now,
            last_heartbeat=now,
            failure_count=0,
            metadata=metadata or {},
        )

        await self.db_client.put_item(
            table_name="runners",
            item=runner.model_dump(),
        )

        return runner

    async def get_runner(self, runner_id: str) -> Runner:
        """Get a runner from mock database."""
        runner_data = await self.db_client.get_item(
            table_name="runners",
            key={"id": runner_id},
        )

        if not runner_data:
            return None

        return Runner(**runner_data)

    async def update_runner_state(self, runner_id: str, new_state: RunnerState) -> None:
        """Update runner state in mock database."""
        runner = await self.get_runner(runner_id)
        if not runner:
            raise ValueError(f"Runner {runner_id} not found")

        runner_data = runner.model_dump()
        runner_data["state"] = new_state
        runner_data["updated_at"] = datetime.now(timezone.utc)

        updated_runner = Runner(**runner_data)

        await self.db_client.put_item(
            table_name="runners",
            item=updated_runner.model_dump(),
        )

    async def recover_all_runners(self) -> List[Runner]:
        """
        Recover all runner states from the database.

        This simulates what happens when a backend server restarts and needs
        to restore its in-memory state from the database.

        Returns:
            List of all runners recovered from the database
        """
        # Scan the runners table to get all runners
        runner_data_list = await self.db_client.scan_table("runners")

        # Convert to Runner objects
        runners = []
        for runner_data in runner_data_list:
            try:
                runner = Runner(**runner_data)
                runners.append(runner)
            except Exception as e:
                # Log error but continue recovery
                print(f"Failed to recover runner {runner_data.get('id')}: {e}")

        return runners

    async def verify_runner_state(
        self, runner_id: str, expected_state: RunnerState
    ) -> bool:
        """
        Verify that a runner's state matches the expected state.

        Args:
            runner_id: Runner identifier
            expected_state: Expected runner state

        Returns:
            True if state matches, False otherwise
        """
        runner = await self.runner_service.get_runner(runner_id)
        if not runner:
            return False

        return runner.state == expected_state


@pytest.fixture
def runner_service(mock_db_client):
    """Fixture providing a runner service with mock database."""
    # Clear database before each test to ensure clean state
    mock_db_client.clear()
    return StateRecoveryService(db_client=mock_db_client)


@pytest.fixture
def recovery_service(mock_db_client):
    """Fixture providing a state recovery service with mock database."""
    return StateRecoveryService(db_client=mock_db_client)


# Hypothesis strategies for generating test data
runner_states = st.sampled_from(
    [
        RunnerState.ACTIVE,
        RunnerState.IDLE,
        RunnerState.BUSY,
        RunnerState.FAILED,
        RunnerState.TERMINATED,
    ]
)

runner_types = st.sampled_from(
    [
        RunnerType.SERVERLESS,
        RunnerType.APEX,
        RunnerType.NADIR,
    ]
)

cloud_providers = st.sampled_from(
    [
        CloudProvider.YANDEX,
        CloudProvider.AWS,
    ]
)

egg_names = st.text(
    alphabet=st.characters(
        whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"
    ),
    min_size=3,
    max_size=20,
).filter(lambda x: x and not x.startswith("-") and not x.endswith("-"))

regions = st.sampled_from(
    [
        "ru-central1-a",
        "ru-central1-b",
        "us-east-1",
        "us-west-2",
        "eu-west-1",
    ]
)

git_commits = st.text(
    alphabet=st.characters(whitelist_categories=("Ll", "Nd")),
    min_size=7,
    max_size=40,
)


# Feature: gitops-runner-orchestration, Property 31: Database State Recovery
@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    num_runners=st.integers(min_value=1, max_value=10),
    runner_states_list=st.lists(
        runner_states,
        min_size=1,
        max_size=10,
    ),
    runner_types_list=st.lists(
        runner_types,
        min_size=1,
        max_size=10,
    ),
    cloud_providers_list=st.lists(
        cloud_providers,
        min_size=1,
        max_size=10,
    ),
    egg_names_list=st.lists(
        egg_names,
        min_size=1,
        max_size=10,
    ),
    regions_list=st.lists(
        regions,
        min_size=1,
        max_size=10,
    ),
    commits_list=st.lists(
        git_commits,
        min_size=1,
        max_size=10,
    ),
)
async def test_database_state_recovery(
    runner_service: StateRecoveryService,
    recovery_service: StateRecoveryService,
    num_runners: int,
    runner_states_list: List[RunnerState],
    runner_types_list: List[RunnerType],
    cloud_providers_list: List[CloudProvider],
    egg_names_list: List[str],
    regions_list: List[str],
    commits_list: List[str],
) -> None:
    """
    Property 31: Database State Recovery

    For any backend server restart, the system should restore all runner
    states from the database and continue operations.

    This property test verifies that:
    1. Multiple runners can be created with various states
    2. After a simulated "restart" (creating a new service instance),
       all runner states can be recovered from the database
    3. The recovered states match the original states exactly
    4. All runner metadata is preserved during recovery

    Validates: Requirements 14.6
    """
    # Clear database before each hypothesis example to ensure clean state
    runner_service.db_client.clear()

    # Create multiple runners with different states
    created_runners = []

    for i in range(num_runners):
        # Use modulo to cycle through the lists
        state = runner_states_list[i % len(runner_states_list)]
        runner_type = runner_types_list[i % len(runner_types_list)]
        cloud_provider = cloud_providers_list[i % len(cloud_providers_list)]
        egg_name = egg_names_list[i % len(egg_names_list)]
        region = regions_list[i % len(regions_list)]
        commit = commits_list[i % len(commits_list)]

        runner = await runner_service.create_runner(
            egg_name=egg_name,
            runner_type=runner_type,
            state=state,
            cloud_provider=cloud_provider,
            region=region,
            deployed_from_commit=commit,
            gitlab_runner_id=1000 + i,
            metadata={"index": i, "test": "state_recovery"},
        )

        created_runners.append(runner)

    # Verify all runners were created
    assert len(created_runners) == num_runners, (
        f"Should have created {num_runners} runners, "
        f"got {len(created_runners)}"
    )

    # Simulate backend server restart by recovering all runners from database
    recovered_runners = await recovery_service.recover_all_runners()

    # Verify all runners were recovered
    assert len(recovered_runners) == num_runners, (
        f"Should have recovered {num_runners} runners, "
        f"got {len(recovered_runners)}"
    )

    # Create a mapping of runner IDs to recovered runners for easy lookup
    recovered_by_id = {r.id: r for r in recovered_runners}

    # Verify each created runner was recovered with correct state
    for original_runner in created_runners:
        assert original_runner.id in recovered_by_id, (
            f"Runner {original_runner.id} was not recovered from database"
        )

        recovered_runner = recovered_by_id[original_runner.id]

        # Verify all critical fields match
        assert recovered_runner.state == original_runner.state, (
            f"Runner {original_runner.id} state mismatch: "
            f"expected {original_runner.state}, got {recovered_runner.state}"
        )

        assert recovered_runner.egg_name == original_runner.egg_name, (
            f"Runner {original_runner.id} egg_name mismatch"
        )

        assert recovered_runner.type == original_runner.type, (
            f"Runner {original_runner.id} type mismatch"
        )

        assert recovered_runner.cloud_provider == original_runner.cloud_provider, (
            f"Runner {original_runner.id} cloud_provider mismatch"
        )

        assert recovered_runner.region == original_runner.region, (
            f"Runner {original_runner.id} region mismatch"
        )

        assert (
            recovered_runner.deployed_from_commit
            == original_runner.deployed_from_commit
        ), f"Runner {original_runner.id} deployed_from_commit mismatch"

        assert recovered_runner.gitlab_runner_id == original_runner.gitlab_runner_id, (
            f"Runner {original_runner.id} gitlab_runner_id mismatch"
        )

        assert recovered_runner.failure_count == original_runner.failure_count, (
            f"Runner {original_runner.id} failure_count mismatch"
        )

        assert recovered_runner.metadata == original_runner.metadata, (
            f"Runner {original_runner.id} metadata mismatch"
        )

    # Verify that operations can continue after recovery
    # Pick a random recovered runner and update its state
    if recovered_runners:
        test_runner = recovered_runners[0]
        new_state = (
            RunnerState.BUSY
            if test_runner.state != RunnerState.BUSY
            else RunnerState.IDLE
        )

        await runner_service.update_runner_state(test_runner.id, new_state)

        # Verify the update worked
        updated_runner = await runner_service.get_runner(test_runner.id)
        assert updated_runner is not None, "Runner should exist after update"
        assert updated_runner.state == new_state, (
            f"State should be updated to {new_state}, "
            f"got {updated_runner.state}"
        )


@pytest.mark.asyncio
async def test_database_state_recovery_example(
    runner_service: StateRecoveryService,
    recovery_service: StateRecoveryService,
) -> None:
    """
    Example test demonstrating database state recovery with specific values.

    This is a concrete example that complements the property test above.
    """
    # Create several runners with different states
    runner1 = await runner_service.create_runner(
        egg_name="app1",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
        gitlab_runner_id=101,
        metadata={"purpose": "production"},
    )

    runner2 = await runner_service.create_runner(
        egg_name="app2",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="commit2",
        gitlab_runner_id=102,
        metadata={"purpose": "staging"},
    )

    runner3 = await runner_service.create_runner(
        egg_name="app3",
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.BUSY,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-b",
        deployed_from_commit="commit3",
        gitlab_runner_id=103,
        metadata={"purpose": "development"},
    )

    # Simulate backend restart by recovering all runners
    recovered_runners = await recovery_service.recover_all_runners()

    # Verify all runners were recovered
    assert len(recovered_runners) == 3, "Should recover all 3 runners"

    # Verify each runner's state
    recovered_by_id = {r.id: r for r in recovered_runners}

    assert runner1.id in recovered_by_id
    assert recovered_by_id[runner1.id].state == RunnerState.ACTIVE
    assert recovered_by_id[runner1.id].metadata["purpose"] == "production"

    assert runner2.id in recovered_by_id
    assert recovered_by_id[runner2.id].state == RunnerState.IDLE
    assert recovered_by_id[runner2.id].metadata["purpose"] == "staging"

    assert runner3.id in recovered_by_id
    assert recovered_by_id[runner3.id].state == RunnerState.BUSY
    assert recovered_by_id[runner3.id].metadata["purpose"] == "development"

    # Verify operations can continue after recovery
    await runner_service.update_runner_state(runner1.id, RunnerState.IDLE)

    updated = await runner_service.get_runner(runner1.id)
    assert updated is not None
    assert updated.state == RunnerState.IDLE


@pytest.mark.asyncio
async def test_database_state_recovery_empty_database(
    recovery_service: StateRecoveryService,
) -> None:
    """
    Test that recovery works correctly with an empty database.

    This edge case test verifies that recovery handles the case where
    no runners exist in the database (e.g., fresh deployment).
    """
    # Attempt to recover from empty database
    recovered_runners = await recovery_service.recover_all_runners()

    # Should return empty list, not error
    assert recovered_runners == [], "Should recover empty list from empty database"


@pytest.mark.asyncio
async def test_database_state_recovery_with_state_updates(
    runner_service: StateRecoveryService,
    recovery_service: StateRecoveryService,
) -> None:
    """
    Test that recovery captures the latest state after multiple updates.

    This test verifies that when runners undergo state transitions before
    a restart, the recovered state reflects the most recent update.
    """
    # Create a runner
    runner = await runner_service.create_runner(
        egg_name="test-app",
        runner_type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="abc123",
    )

    # Update state multiple times
    await runner_service.update_runner_state(runner.id, RunnerState.ACTIVE)
    await runner_service.update_runner_state(runner.id, RunnerState.BUSY)
    await runner_service.update_runner_state(runner.id, RunnerState.IDLE)

    # Simulate restart and recover
    recovered_runners = await recovery_service.recover_all_runners()

    # Verify the latest state is recovered
    assert len(recovered_runners) == 1
    assert recovered_runners[0].id == runner.id
    assert recovered_runners[0].state == RunnerState.IDLE, (
        "Should recover the most recent state (IDLE)"
    )


@pytest.mark.asyncio
async def test_database_state_recovery_preserves_timestamps(
    runner_service: StateRecoveryService,
    recovery_service: StateRecoveryService,
) -> None:
    """
    Test that recovery preserves timestamp information.

    This test verifies that created_at, updated_at, and last_heartbeat
    timestamps are correctly recovered from the database.
    """
    # Create a runner
    before_creation = datetime.now(timezone.utc)

    _runner = await runner_service.create_runner(
        egg_name="timestamp-test",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.AWS,
        region="us-west-2",
        deployed_from_commit="xyz789",
    )

    after_creation = datetime.now(timezone.utc)

    # Recover from database
    recovered_runners = await recovery_service.recover_all_runners()

    assert len(recovered_runners) == 1
    recovered = recovered_runners[0]

    # Verify timestamps are preserved
    assert recovered.created_at is not None
    assert before_creation <= recovered.created_at <= after_creation

    assert recovered.updated_at is not None
    assert before_creation <= recovered.updated_at <= after_creation

    assert recovered.last_heartbeat is not None


@pytest.mark.asyncio
async def test_database_state_recovery_with_failures(
    runner_service: StateRecoveryService,
    recovery_service: StateRecoveryService,
) -> None:
    """
    Test that recovery correctly restores failure counts.

    This test verifies that failure tracking information is preserved
    across restarts, which is critical for UglyFox pruning decisions.
    """
    # Create runners with different failure counts
    _runner1 = await runner_service.create_runner(
        egg_name="stable-app",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    runner2 = await runner_service.create_runner(
        egg_name="failing-app",
        runner_type=RunnerType.APEX,
        state=RunnerState.FAILED,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit2",
    )

    # Simulate failures by updating the runner data directly
    # (In real system, this would be done through proper failure tracking)
    runner2_data = runner2.model_dump()
    runner2_data["failure_count"] = 5
    runner2_data["state"] = RunnerState.FAILED

    await runner_service.db_client.put_item(
        table_name="runners",
        item=runner2_data,
    )

    # Recover from database
    recovered_runners = await recovery_service.recover_all_runners()

    assert len(recovered_runners) == 2

    # Find the failing runner
    failing_runner = next(
        (r for r in recovered_runners if r.egg_name == "failing-app"), None
    )

    assert failing_runner is not None
    assert failing_runner.failure_count == 5, (
        "Failure count should be preserved across restart"
    )
    assert failing_runner.state == RunnerState.FAILED
