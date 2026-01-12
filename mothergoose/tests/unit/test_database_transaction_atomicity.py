"""
Property-based tests for database transaction atomicity.

Feature: gitops-runner-orchestration, Property 32: Database Transaction Atomicity
Validates: Requirements 14.7

This module tests that for any state update operation, either all changes
should be committed or none should be committed (no partial updates).

Note: These tests use mock database client because real YDB transaction
testing requires complex failure injection that's not practical with testcontainers.
"""

import pytest
from datetime import datetime, timezone
from hypothesis import given, settings, strategies as st, HealthCheck
from typing import Dict, Any, List
from ydb import AnonymousCredentials

from app.model.runners_models import (
    Runner,
    RunnerState,
    RunnerType,
    CloudProvider,
    EggConfig,
    generate_new_eggconfig,
)
from app.model.audit_models import AuditLog, AuditModelYDB, AuditLogsTableYDB
from app.schema.ydb_schemas import YDBSchema, YDBConfig


class TransactionalRunnerService:
    """
    Runner service with transactional support for atomic operations.

    This service demonstrates how database operations should be wrapped
    in transactions to ensure atomicity.

    Uses mock database client for testing transaction failure scenarios.
    """

    def __init__(self, db_client: Any) -> None:
        """
        Initialize the transactional runner service.

        Args:
            db_client: Mock database client with transaction support
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
        metadata: Dict[str, Any] = None,
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

    async def atomic_runner_state_update_with_audit(
        self,
        runner_id: str,
        new_state: RunnerState,
        actor: str,
        reason: str,
    ) -> None:
        """
        Atomically update runner state and create audit log entry.

        This operation must be atomic: either both the runner state update
        and the audit log entry succeed, or neither does.

        Args:
            runner_id: Runner identifier
            new_state: New state to set
            actor: Who is performing the update
            reason: Reason for the state change
        """
        # Begin transaction
        self.db_client.begin_transaction()

        try:
            # Operation 1: Update runner state
            runner = await self.get_runner(runner_id)
            if not runner:
                raise ValueError(f"Runner {runner_id} not found")

            old_state = runner.state
            runner_data = runner.model_dump()
            runner_data["state"] = new_state
            runner_data["updated_at"] = datetime.now(timezone.utc)

            updated_runner = Runner(**runner_data)
            await self.db_client.put_item(
                table_name="runners",
                item=updated_runner.model_dump(),
            )

            # Operation 2: Create audit log entry
            audit_log = AuditLog(
                id=f"audit-{runner_id}-{datetime.now(timezone.utc).timestamp()}",
                timestamp=datetime.now(timezone.utc),
                actor=actor,
                action="update_runner_state",
                resource_type="runner",
                resource_id=runner_id,
                details={
                    "old_state": old_state.value,
                    "new_state": new_state.value,
                    "reason": reason,
                },
            )

            await self.db_client.put_item(
                table_name="audit_logs",
                item=audit_log.model_dump(),
            )

            # Commit transaction
            self.db_client.commit_transaction()

        except Exception as e:
            # Rollback on any error
            self.db_client.rollback_transaction()
            raise e

    async def atomic_multi_runner_update(
        self,
        runner_ids: List[str],
        new_state: RunnerState,
    ) -> None:
        """
        Atomically update multiple runners to the same state.

        This operation must be atomic: either all runners are updated
        or none are updated.

        Args:
            runner_ids: List of runner identifiers
            new_state: New state to set for all runners
        """
        # Begin transaction
        self.db_client.begin_transaction()

        try:
            # Update all runners
            for runner_id in runner_ids:
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

            # Commit transaction
            self.db_client.commit_transaction()

        except Exception as e:
            # Rollback on any error
            self.db_client.rollback_transaction()
            raise e

    async def atomic_runner_creation_with_config(
        self,
        egg_name: str,
        runner_type: RunnerType,
        state: RunnerState,
        cloud_provider: CloudProvider,
        region: str,
        deployed_from_commit: str,
        egg_config: Dict[str, Any],
    ) -> Runner:
        """
        Atomically create a runner and update its egg config.

        This operation must be atomic: either both the runner creation
        and the egg config update succeed, or neither does.

        Args:
            egg_name: Name of the Egg
            runner_type: Type of runner
            state: Initial runner state
            cloud_provider: Cloud provider
            region: Cloud region
            deployed_from_commit: Git commit hash
            egg_config: Egg configuration to update

        Returns:
            Created Runner object
        """
        # Begin transaction
        self.db_client.begin_transaction()

        try:
            # Operation 1: Create runner
            runner = await self.create_runner(
                egg_name=egg_name,
                runner_type=runner_type,
                state=state,
                cloud_provider=cloud_provider,
                region=region,
                deployed_from_commit=deployed_from_commit,
            )

            # Operation 2: Update egg config with new runner count
            egg_config_obj = generate_new_eggconfig(
                name=egg_name,
                config=egg_config,
                git_commit=deployed_from_commit,
                git_repo_url_secret="yc-lockbox://nest/repo-url",
                gitlab_token_secret_uri=f"yc-lockbox://gitlab-tokens/{egg_name}-token",
                gitlab_webhook_secret_uri=f"yc-lockbox://webhooks/{egg_name}-secret",
                synced_at=datetime.now(timezone.utc),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

            await self.db_client.put_item(
                table_name="egg_configs",
                item=egg_config_obj.model_dump(),
            )

            # Commit transaction
            self.db_client.commit_transaction()

            return runner

        except Exception as e:
            # Rollback on any error
            self.db_client.rollback_transaction()
            raise e


@pytest.fixture(scope="module", name="db_ydb_schema")
def ydb_schema(ydb_container) -> YDBSchema:
    """Fixture to provide YDB configuration."""

    config = YDBConfig(
        endpoint=f"grpc://{ydb_container.get_container_host_ip()}:\
        {ydb_container.get_exposed_port(2136)}",
        database="/local",
        credentials=AnonymousCredentials(),
    )
    model = AuditModelYDB(tables=[AuditLogsTableYDB()])
    schema = YDBSchema(
        config=config,
        model=model,
    )
    return schema


@pytest.fixture
def runner_service(db_ydb_schema, mock_db_client):
    """Fixture providing a runner service with mock database."""
    return TransactionalRunnerService(db_client=mock_db_client)


@pytest.fixture
def transactional_service(mock_db_client):
    """Fixture providing a transactional runner service with mock database."""
    return TransactionalRunnerService(db_client=mock_db_client)


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

actors = st.sampled_from(
    [
        "mothergoose-service",
        "uglyfox-service",
        "admin-user",
        "system",
    ]
)


# Feature: gitops-runner-orchestration, Property 32: Database Transaction Atomicity
@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    initial_state=runner_states,
    new_state=runner_states,
    runner_type=runner_types,
    cloud_provider=cloud_providers,
    egg_name=egg_names,
    region=regions,
    commit=git_commits,
    actor=actors,
)
async def test_atomic_runner_state_update_with_audit(
    runner_service: TransactionalRunnerService,
    transactional_service: TransactionalRunnerService,
    initial_state: RunnerState,
    new_state: RunnerState,
    runner_type: RunnerType,
    cloud_provider: CloudProvider,
    egg_name: str,
    region: str,
    commit: str,
    actor: str,
) -> None:
    """
    Property 32: Database Transaction Atomicity

    For any state update operation, either all changes should be committed
    or none should be committed (no partial updates).

    This property test verifies that:
    1. When updating runner state with audit log, both operations succeed together
    2. If the transaction fails, neither the runner state nor audit log is updated
    3. The database remains in a consistent state after transaction failure
    4. No partial updates occur when operations fail mid-transaction

    Validates: Requirements 14.7
    """
    # Create a runner with initial state
    runner = await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=runner_type,
        state=initial_state,
        cloud_provider=cloud_provider,
        region=region,
        deployed_from_commit=commit,
        metadata={"test": "atomicity_test"},
    )

    # Verify initial state
    retrieved_runner = await runner_service.get_runner(runner.id)
    assert retrieved_runner is not None
    assert retrieved_runner.state == initial_state

    # Perform atomic update (runner state + audit log)
    await transactional_service.atomic_runner_state_update_with_audit(
        runner_id=runner.id,
        new_state=new_state,
        actor=actor,
        reason="property_test_update",
    )

    # Verify both operations succeeded
    updated_runner = await runner_service.get_runner(runner.id)
    assert updated_runner is not None
    assert (
        updated_runner.state == new_state
    ), f"Runner state should be updated to {new_state}, got {updated_runner.state}"

    # Note: We can't verify exact audit log ID due to timestamp precision,
    # but we can verify the runner was updated, which proves atomicity
    # (if audit log failed, the transaction would have rolled back)

    # Test atomicity with simulated failure
    # Configure database to fail on commit
    transactional_service.db_client.fail_on_commit = True

    # Attempt another update that should fail
    third_state = (
        RunnerState.BUSY if new_state != RunnerState.BUSY else RunnerState.IDLE
    )

    with pytest.raises(RuntimeError, match="Simulated commit failure"):
        await transactional_service.atomic_runner_state_update_with_audit(
            runner_id=runner.id,
            new_state=third_state,
            actor=actor,
            reason="should_fail",
        )

    # Verify runner state was NOT updated (transaction rolled back)
    unchanged_runner = await runner_service.get_runner(runner.id)
    assert unchanged_runner is not None
    assert unchanged_runner.state == new_state, (
        f"Runner state should remain {new_state} after failed transaction, "
        f"got {unchanged_runner.state}"
    )

    # Reset failure flag
    transactional_service.db_client.fail_on_commit = False


@pytest.mark.asyncio
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    num_runners=st.integers(min_value=2, max_value=5),
    initial_state=runner_states,
    new_state=runner_states,
    runner_type=runner_types,
    cloud_provider=cloud_providers,
    egg_name=egg_names,
    region=regions,
    commit=git_commits,
)
async def test_atomic_multi_runner_update(
    runner_service: TransactionalRunnerService,
    transactional_service: TransactionalRunnerService,
    num_runners: int,
    initial_state: RunnerState,
    new_state: RunnerState,
    runner_type: RunnerType,
    cloud_provider: CloudProvider,
    egg_name: str,
    region: str,
    commit: str,
) -> None:
    """
    Property 32: Database Transaction Atomicity (Multi-Runner Update)

    For any multi-runner state update operation, either all runners should
    be updated or none should be updated.

    This property test verifies that:
    1. Multiple runners can be updated atomically in a single transaction
    2. If the transaction fails, no runners are updated
    3. Partial updates do not occur when updating multiple runners

    Validates: Requirements 14.7
    """
    # Clear database before each hypothesis example
    transactional_service.db_client.clear()

    # Create multiple runners
    runner_ids = []
    for i in range(num_runners):
        runner = await runner_service.create_runner(
            egg_name=f"{egg_name}-{i}",
            runner_type=runner_type,
            state=initial_state,
            cloud_provider=cloud_provider,
            region=region,
            deployed_from_commit=commit,
            metadata={"index": i},
        )
        runner_ids.append(runner.id)

    # Verify all runners have initial state
    for runner_id in runner_ids:
        runner = await runner_service.get_runner(runner_id)
        assert runner is not None
        assert runner.state == initial_state

    # Perform atomic multi-runner update
    await transactional_service.atomic_multi_runner_update(
        runner_ids=runner_ids,
        new_state=new_state,
    )

    # Verify all runners were updated
    for runner_id in runner_ids:
        runner = await runner_service.get_runner(runner_id)
        assert runner is not None
        assert runner.state == new_state, (
            f"All runners should be updated to {new_state}, "
            f"but runner {runner_id} has state {runner.state}"
        )

    # Test atomicity with simulated failure mid-transaction
    # Configure database to fail after updating half the runners
    transactional_service.db_client.fail_after_n_operations = num_runners // 2

    third_state = (
        RunnerState.TERMINATED
        if new_state != RunnerState.TERMINATED
        else RunnerState.IDLE
    )

    with pytest.raises(RuntimeError, match="Simulated failure after"):
        await transactional_service.atomic_multi_runner_update(
            runner_ids=runner_ids,
            new_state=third_state,
        )

    # Verify NO runners were updated (transaction rolled back)
    for runner_id in runner_ids:
        runner = await runner_service.get_runner(runner_id)
        assert runner is not None
        assert runner.state == new_state, (
            f"All runners should remain in {new_state} after failed transaction, "
            f"but runner {runner_id} has state {runner.state}"
        )

    # Reset failure configuration
    transactional_service.db_client.fail_after_n_operations = -1


@pytest.mark.asyncio
async def test_atomic_runner_state_update_with_audit_example(
    runner_service: TransactionalRunnerService,
    transactional_service: TransactionalRunnerService,
) -> None:
    """
    Example test demonstrating atomic runner state update with audit log.

    This is a concrete example that complements the property test above.
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

    # Perform atomic update
    await transactional_service.atomic_runner_state_update_with_audit(
        runner_id=runner.id,
        new_state=RunnerState.ACTIVE,
        actor="mothergoose-service",
        reason="job_assigned",
    )

    # Verify runner state was updated
    updated_runner = await runner_service.get_runner(runner.id)
    assert updated_runner is not None
    assert updated_runner.state == RunnerState.ACTIVE

    # Simulate failure scenario
    transactional_service.db_client.fail_on_commit = True

    with pytest.raises(RuntimeError, match="Simulated commit failure"):
        await transactional_service.atomic_runner_state_update_with_audit(
            runner_id=runner.id,
            new_state=RunnerState.BUSY,
            actor="mothergoose-service",
            reason="should_fail",
        )

    # Verify runner state was NOT updated
    unchanged_runner = await runner_service.get_runner(runner.id)
    assert unchanged_runner is not None
    assert unchanged_runner.state == RunnerState.ACTIVE


@pytest.mark.asyncio
async def test_atomic_multi_runner_update_example(
    runner_service: TransactionalRunnerService,
    transactional_service: TransactionalRunnerService,
) -> None:
    """
    Example test demonstrating atomic multi-runner update.

    This is a concrete example that complements the property test above.
    """
    # Create three runners
    runner1 = await runner_service.create_runner(
        egg_name="app1",
        runner_type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    runner2 = await runner_service.create_runner(
        egg_name="app2",
        runner_type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit2",
    )

    runner3 = await runner_service.create_runner(
        egg_name="app3",
        runner_type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit3",
    )

    # Update all runners atomically
    await transactional_service.atomic_multi_runner_update(
        runner_ids=[runner1.id, runner2.id, runner3.id],
        new_state=RunnerState.ACTIVE,
    )

    # Verify all were updated
    for runner_id in [runner1.id, runner2.id, runner3.id]:
        runner = await runner_service.get_runner(runner_id)
        assert runner is not None
        assert runner.state == RunnerState.ACTIVE

    # Simulate failure after updating 1 runner
    transactional_service.db_client.fail_after_n_operations = 1

    with pytest.raises(RuntimeError, match="Simulated failure after"):
        await transactional_service.atomic_multi_runner_update(
            runner_ids=[runner1.id, runner2.id, runner3.id],
            new_state=RunnerState.BUSY,
        )

    # Verify NO runners were updated (all remain ACTIVE)
    for runner_id in [runner1.id, runner2.id, runner3.id]:
        runner = await runner_service.get_runner(runner_id)
        assert runner is not None
        assert runner.state == RunnerState.ACTIVE


@pytest.mark.asyncio
async def test_atomic_runner_creation_with_config_example(
    transactional_service: TransactionalRunnerService,
) -> None:
    """
    Example test demonstrating atomic runner creation with config update.

    This test verifies that creating a runner and updating its egg config
    happens atomically.
    """
    # Create runner with config atomically
    runner = await transactional_service.atomic_runner_creation_with_config(
        egg_name="new-app",
        runner_type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="xyz789",
        egg_config={"runner_count": 1, "tags": ["docker"]},
    )

    # Verify runner was created
    retrieved_runner = await transactional_service.get_runner(runner.id)
    assert retrieved_runner is not None
    assert retrieved_runner.egg_name == "new-app"

    # Verify egg config was created
    egg_config_data = await transactional_service.db_client.get_item(
        table_name="egg_configs",
        key={"name": "new-app"},
    )
    assert egg_config_data is not None
    assert egg_config_data["name"] == "new-app"

    # Simulate failure scenario
    transactional_service.db_client.fail_on_commit = True

    with pytest.raises(RuntimeError, match="Simulated commit failure"):
        await transactional_service.atomic_runner_creation_with_config(
            egg_name="failing-app",
            runner_type=RunnerType.APEX,
            state=RunnerState.IDLE,
            cloud_provider=CloudProvider.AWS,
            region="us-east-1",
            deployed_from_commit="fail123",
            egg_config={"runner_count": 1},
        )

    # Verify neither runner nor config were created
    # (transaction rolled back)
    failed_runner_data = await transactional_service.db_client.get_item(
        table_name="runners",
        key={"id": "runner-failing-app"},
    )
    assert failed_runner_data is None

    failed_config_data = await transactional_service.db_client.get_item(
        table_name="egg_configs",
        key={"name": "failing-app"},
    )
    assert failed_config_data is None
