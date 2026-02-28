"""
Property-based tests for runner state persistence.

Feature: gitops-runner-orchestration, Property 11: Runner State Persistence
Validates: Requirements 4.6, 14.1

This module tests that for any runner state update, querying the database
immediately after should return the updated state.
"""

import pytest
import pytest_asyncio
from hypothesis import given, settings, strategies as st, HealthCheck

from ydb import AnonymousCredentials
from ydb.issues import GenericError as AsyncGenericError

from app.model.runners_models import (
    RunnerState,
    RunnerType,
    CloudProvider,
    RunnersTableYDB,
    EggConfigsTableYDB,
    RunnerModelYDB,
)
from app.model.audit_models import AuditLogsTableYDB, AuditModelYDB
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.runner_service import RunnerService
from app.db.ydb_connection import AsyncYDBOperations
from app.db.manage_db import AsyncYDBFunctionsCollections


@pytest_asyncio.fixture(scope="module", name="ydb_schema")
async def ydb_schema(ydb_container):
    """Fixture to provide YDB configuration with runner tables."""
    config = YDBConfig(
        endpoint=f"grpc://{ydb_container.get_container_host_ip()}:"
        f"{ydb_container.get_exposed_port(2136)}",
        database="/local",
        credentials=AnonymousCredentials(),
    )

    # Create model with runner tables only
    # Audit logs will be tested separately
    model = RunnerModelYDB(tables=[RunnersTableYDB(), EggConfigsTableYDB()])

    schema = YDBSchema(
        config=config,
        model=model,
    )

    yield schema

    delete_operation = AsyncYDBOperations(
        schema, AsyncYDBFunctionsCollections.drop_tables
    )
    await delete_operation.process()


@pytest.fixture(scope="module", name="runner_service")
def runner_service_fixture(ydb_schema):
    """Fixture providing a runner service with real YDB schema."""
    return RunnerService(schema=ydb_schema)


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


@pytest.mark.dependency()
@pytest.mark.asyncio
async def test_ydb_create_runner_tables(ydb_schema):
    """Create tables for testing runner service."""
    operation = AsyncYDBOperations(
        ydb_schema,
        AsyncYDBFunctionsCollections.create_tables,
    )
    operation.fail_fast = True

    await operation.process()

    # Check that tables exist
    await operation.check_tables_exist()

    # Verify the runners table was created
    table_names = [table.name for table in operation.result]
    assert "runners" in table_names, "Table 'runners' was not created."
    assert "egg_configs" in table_names, "Table 'egg_configs' was not created."

    # Verify they are tables (type == 2)
    for table in operation.result:
        assert table.type == 2, f"Created target '{table.name}' is not a table."


# Feature: gitops-runner-orchestration, Property 11: Runner State Persistence
@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
@settings(
    max_examples=10,  # Reduced for faster testing with real YDB
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
)
async def test_runner_state_persistence(
    runner_service: RunnerService,
    initial_state: RunnerState,
    new_state: RunnerState,
    runner_type: RunnerType,
    cloud_provider: CloudProvider,
    egg_name: str,
    region: str,
    commit: str,
) -> None:
    """
    Property 11: Runner State Persistence

    For any runner state update, querying the database immediately after
    should return the updated state.

    This property test verifies that:
    1. A runner can be created with any valid initial state
    2. The runner's state can be updated to any valid new state
    3. Querying the runner immediately after update returns the new state
    4. The state transition is persisted correctly in the database

    Validates: Requirements 4.6, 14.1
    """
    # Create a runner with initial state
    runner = await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=runner_type,
        state=initial_state,
        cloud_provider=cloud_provider,
        region=region,
        deployed_from_commit=commit,
        gitlab_runner_id=None,
        metadata={"test": "property_test"},
    )

    # Verify initial state is persisted
    retrieved_runner = await runner_service.get_runner(runner.id)
    assert retrieved_runner is not None, "Runner should exist after creation"
    assert retrieved_runner.state == initial_state, f"""
     Initial state should be {initial_state}, got {retrieved_runner.state}
     """

    # Update runner state
    await runner_service.update_runner_state(runner.id, new_state)

    # Query state immediately after update
    updated_runner = await runner_service.get_runner(runner.id)

    # Verify the state was persisted correctly
    assert updated_runner is not None, "Runner should still exist after update"
    assert updated_runner.state == new_state, (
        f"State should be updated to {new_state}, got {updated_runner.state}"
    )

    # Verify other fields remain unchanged
    assert updated_runner.id == runner.id, "Runner ID should not change"
    assert updated_runner.egg_name == egg_name, "Egg name should not change"
    assert updated_runner.type == runner_type, "Runner type should not change"
    assert updated_runner.cloud_provider == cloud_provider, (
        "Cloud provider should not change"
    )
    assert updated_runner.region == region, "Region should not change"

    # Verify updated_at timestamp was updated (>= to handle fast operations)
    assert updated_runner.updated_at >= runner.created_at, (
        "updated_at should be at least as recent as created_at after state update"
    )


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_runner_state_persistence_example(runner_service: RunnerService) -> None:
    """
    Example test demonstrating runner state persistence with specific values.

    This is a concrete example that complements the property test above.
    """
    # Create a runner in IDLE state
    runner = await runner_service.create_runner(
        egg_name="test-app",
        runner_type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="abc123def456",
        gitlab_runner_id=12345,
        metadata={"instance_id": "i-xyz789"},
    )

    # Verify initial state
    retrieved = await runner_service.get_runner(runner.id)
    assert retrieved is not None
    assert retrieved.state == RunnerState.IDLE

    # Update to ACTIVE state
    await runner_service.update_runner_state(runner.id, RunnerState.ACTIVE)

    # Verify state was updated
    updated = await runner_service.get_runner(runner.id)
    assert updated is not None
    assert updated.state == RunnerState.ACTIVE

    # Update to BUSY state
    await runner_service.update_runner_state(runner.id, RunnerState.BUSY)

    # Verify state was updated again
    busy_runner = await runner_service.get_runner(runner.id)
    assert busy_runner is not None
    assert busy_runner.state == RunnerState.BUSY

    # Update to TERMINATED state
    await runner_service.update_runner_state(runner.id, RunnerState.TERMINATED)

    # Verify final state
    terminated_runner = await runner_service.get_runner(runner.id)
    assert terminated_runner is not None
    assert terminated_runner.state == RunnerState.TERMINATED


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_runner_state_persistence_nonexistent_runner(
    runner_service: RunnerService,
) -> None:
    """
    Test that updating a nonexistent runner raises an error.

    This edge case test verifies error handling for invalid runner IDs.
    """
    with pytest.raises(ValueError, match="Runner .* not found"):
        await runner_service.update_runner_state(
            "nonexistent-runner-id", RunnerState.ACTIVE
        )


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_runner_state_persistence_multiple_runners(
    runner_service: RunnerService,
) -> None:
    """
    Test that state updates are isolated between different runners.

    This test verifies that updating one runner's state doesn't affect others.
    """
    # Create multiple runners
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
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="commit2",
    )

    # Update runner1 state
    await runner_service.update_runner_state(runner1.id, RunnerState.ACTIVE)

    # Verify runner1 was updated
    updated_runner1 = await runner_service.get_runner(runner1.id)
    assert updated_runner1 is not None
    assert updated_runner1.state == RunnerState.ACTIVE

    # Verify runner2 was NOT affected
    unchanged_runner2 = await runner_service.get_runner(runner2.id)
    assert unchanged_runner2 is not None
    assert unchanged_runner2.state == RunnerState.IDLE
