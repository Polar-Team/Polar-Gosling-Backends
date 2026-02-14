"""
Unit tests for runner queries by egg_name.

Feature: gitops-runner-orchestration, Task 9.5
Validates: Requirements 4.6, 14.1

This module tests the list_runners_by_egg method in RunnerService
and the integration with the GET /eggs/{name}/status endpoint.
"""

import pytest
import asyncio

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.runners_models import (
    CloudProvider,
    EggConfigsTableYDB,
    RunnerModelYDB,
    RunnersTableYDB,
    RunnerState,
    RunnerType,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.runner_service import RunnerService
from ydb import AnonymousCredentials


@pytest.fixture(scope="module", name="ydb_schema")
def ydb_schema(ydb_container):
    """Fixture to provide YDB configuration with runner tables."""
    config = YDBConfig(
        endpoint=f"grpc://{ydb_container.get_container_host_ip()}:"
        f"{ydb_container.get_exposed_port(2136)}",
        database="/local",
        credentials=AnonymousCredentials(),
    )

    # Create model with runner tables
    model = RunnerModelYDB(tables=[RunnersTableYDB(), EggConfigsTableYDB()])

    schema = YDBSchema(
        config=config,
        model=model,
    )

    yield schema

    delete_operation = AsyncYDBOperations(
        schema, AsyncYDBFunctionsCollections.drop_tables
    )

    async def process():
        await delete_operation.process()

    asyncio.run(process())


@pytest.fixture(scope="module", name="runner_service")
def runner_service_fixture(ydb_schema):
    """Fixture providing a runner service with real YDB schema."""
    return RunnerService(schema=ydb_schema)


@pytest.mark.dependency()
@pytest.mark.asyncio
async def test_ydb_create_runner_tables(ydb_schema):
    """Create tables for testing runner queries."""
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


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_runners_by_egg_empty(runner_service: RunnerService) -> None:
    """
    Test that querying runners for a non-existent egg returns an empty list.
    """
    runners = await runner_service.list_runners_by_egg("nonexistent-egg")
    assert runners == [], "Should return empty list for non-existent egg"


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_runners_by_egg_single_runner(
    runner_service: RunnerService,
) -> None:
    """
    Test that querying runners for an egg with one runner returns that runner.
    """
    # Create a runner for test-app-1
    runner = await runner_service.create_runner(
        egg_name="test-app-1",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="abc123",
        gitlab_runner_id=12345,
        metadata={"test": "single_runner"},
    )

    # Query runners for test-app-1
    runners = await runner_service.list_runners_by_egg("test-app-1")

    assert len(runners) == 1, "Should return exactly one runner"
    assert runners[0].id == runner.id, "Should return the correct runner"
    assert runners[0].egg_name == "test-app-1", "Runner should belong to test-app-1"
    assert runners[0].state == RunnerState.ACTIVE, "Runner should be active"


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_runners_by_egg_multiple_runners(
    runner_service: RunnerService,
) -> None:
    """
    Test that querying runners for an egg with multiple runners returns all of them.
    """
    # Create multiple runners for test-app-2
    runner1 = await runner_service.create_runner(
        egg_name="test-app-2",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
        metadata={"instance": "1"},
    )

    runner2 = await runner_service.create_runner(
        egg_name="test-app-2",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-b",
        deployed_from_commit="commit1",
        metadata={"instance": "2"},
    )

    runner3 = await runner_service.create_runner(
        egg_name="test-app-2",
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.BUSY,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="commit1",
        metadata={"instance": "3"},
    )

    # Query runners for test-app-2
    runners = await runner_service.list_runners_by_egg("test-app-2")

    assert len(runners) == 3, "Should return all three runners"

    # Verify all runners belong to test-app-2
    for runner in runners:
        assert runner.egg_name == "test-app-2", (
            "All runners should belong to test-app-2"
        )

    # Verify we got all the runners we created
    runner_ids = {r.id for r in runners}
    assert runner1.id in runner_ids, "Should include runner1"
    assert runner2.id in runner_ids, "Should include runner2"
    assert runner3.id in runner_ids, "Should include runner3"


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_runners_by_egg_isolation(runner_service: RunnerService) -> None:
    """
    Test that runners are properly isolated by egg_name.
    """
    # Create runners for different eggs
    await runner_service.create_runner(
        egg_name="app-a",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit-a",
    )

    await runner_service.create_runner(
        egg_name="app-b",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit-b",
    )

    await runner_service.create_runner(
        egg_name="app-a",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit-a",
    )

    # Query runners for app-a
    runners_a = await runner_service.list_runners_by_egg("app-a")
    assert len(runners_a) == 2, "app-a should have 2 runners"
    for runner in runners_a:
        assert runner.egg_name == "app-a", "All runners should belong to app-a"

    # Query runners for app-b
    runners_b = await runner_service.list_runners_by_egg("app-b")
    assert len(runners_b) == 1, "app-b should have 1 runner"
    assert runners_b[0].egg_name == "app-b", "Runner should belong to app-b"


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_runners_by_egg_all_states(runner_service: RunnerService) -> None:
    """
    Test that list_runners_by_egg returns runners in all states.
    """
    egg_name = "test-app-states"

    # Create runners in different states
    await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=RunnerType.APEX,
        state=RunnerState.BUSY,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=RunnerType.APEX,
        state=RunnerState.FAILED,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=RunnerType.APEX,
        state=RunnerState.TERMINATED,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    # Query all runners
    runners = await runner_service.list_runners_by_egg(egg_name)

    assert len(runners) == 5, "Should return all 5 runners regardless of state"

    # Verify we have all states
    states = {r.state for r in runners}
    assert RunnerState.ACTIVE in states, "Should include ACTIVE runner"
    assert RunnerState.IDLE in states, "Should include IDLE runner"
    assert RunnerState.BUSY in states, "Should include BUSY runner"
    assert RunnerState.FAILED in states, "Should include FAILED runner"
    assert RunnerState.TERMINATED in states, "Should include TERMINATED runner"


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_runners_by_egg_different_types(
    runner_service: RunnerService,
) -> None:
    """
    Test that list_runners_by_egg returns runners of all types.
    """
    egg_name = "test-app-types"

    # Create runners of different types
    await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name=egg_name,
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    # Query all runners
    runners = await runner_service.list_runners_by_egg(egg_name)

    assert len(runners) == 3, "Should return all 3 runners"

    # Verify we have all types
    types = {r.type for r in runners}
    assert RunnerType.SERVERLESS in types, "Should include SERVERLESS runner"
    assert RunnerType.APEX in types, "Should include APEX runner"
    assert RunnerType.NADIR in types, "Should include NADIR runner"
