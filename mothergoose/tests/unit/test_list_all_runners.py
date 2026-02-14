"""
Unit tests for list_all_runners method in RunnerService.

Feature: gitops-runner-orchestration, Task 9
Validates: Requirements 4.6, 14.1

This module tests the list_all_runners method in RunnerService
which retrieves all runners across all Eggs from the database.
"""

import asyncio

import pytest
from ydb import AnonymousCredentials

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
    """Create tables for testing list_all_runners."""
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
async def test_list_all_runners_empty(runner_service: RunnerService) -> None:
    """
    Test that list_all_runners returns empty list when no runners exist.
    """
    runners = await runner_service.list_all_runners()
    assert runners == [], "Should return empty list when no runners exist"


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_all_runners_single_egg(runner_service: RunnerService) -> None:
    """
    Test that list_all_runners returns all runners from a single Egg.
    """
    # Create multiple runners for one egg
    runner1 = await runner_service.create_runner(
        egg_name="single-egg",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="abc123",
        metadata={"instance": "1"},
    )

    runner2 = await runner_service.create_runner(
        egg_name="single-egg",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-b",
        deployed_from_commit="abc123",
        metadata={"instance": "2"},
    )

    # Query all runners
    runners = await runner_service.list_all_runners()

    assert len(runners) == 2, "Should return all 2 runners"

    # Verify we got the runners we created
    runner_ids = {r.id for r in runners}
    assert runner1.id in runner_ids, "Should include runner1"
    assert runner2.id in runner_ids, "Should include runner2"

    # Verify all belong to the same egg
    for runner in runners:
        assert runner.egg_name == "single-egg", (
            "All runners should belong to single-egg"
        )


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_all_runners_multiple_eggs(runner_service: RunnerService) -> None:
    """
    Test that list_all_runners returns runners from multiple Eggs.
    """
    # Get baseline count
    baseline_runners = await runner_service.list_all_runners()
    baseline_count = len(baseline_runners)

    # Create runners for different eggs
    runner_a1 = await runner_service.create_runner(
        egg_name="egg-a",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit-a",
        metadata={"egg": "a", "instance": "1"},
    )

    runner_a2 = await runner_service.create_runner(
        egg_name="egg-a",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit-a",
        metadata={"egg": "a", "instance": "2"},
    )

    runner_b1 = await runner_service.create_runner(
        egg_name="egg-b",
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.BUSY,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="commit-b",
        metadata={"egg": "b", "instance": "1"},
    )

    runner_c1 = await runner_service.create_runner(
        egg_name="egg-c",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-b",
        deployed_from_commit="commit-c",
        metadata={"egg": "c", "instance": "1"},
    )

    # Query all runners
    runners = await runner_service.list_all_runners()

    assert len(runners) == baseline_count + 4, (
        f"Should return {baseline_count + 4} runners (baseline + 4 new)"
    )

    # Verify we got all the runners we created
    runner_ids = {r.id for r in runners}
    assert runner_a1.id in runner_ids, "Should include runner_a1"
    assert runner_a2.id in runner_ids, "Should include runner_a2"
    assert runner_b1.id in runner_ids, "Should include runner_b1"
    assert runner_c1.id in runner_ids, "Should include runner_c1"

    # Verify we have runners from all eggs
    egg_names = {r.egg_name for r in runners}
    assert "egg-a" in egg_names, "Should include runners from egg-a"
    assert "egg-b" in egg_names, "Should include runners from egg-b"
    assert "egg-c" in egg_names, "Should include runners from egg-c"


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_all_runners_all_states(runner_service: RunnerService) -> None:
    """
    Test that list_all_runners returns runners in all states.
    """
    # Create runners in different states across different eggs
    await runner_service.create_runner(
        egg_name="state-test-1",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name="state-test-2",
        runner_type=RunnerType.APEX,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name="state-test-3",
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.BUSY,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name="state-test-4",
        runner_type=RunnerType.APEX,
        state=RunnerState.FAILED,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name="state-test-5",
        runner_type=RunnerType.APEX,
        state=RunnerState.TERMINATED,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    # Query all runners
    runners = await runner_service.list_all_runners()

    assert len(runners) >= 5, "Should return at least 5 runners"

    # Verify we have all states
    states = {r.state for r in runners}
    assert RunnerState.ACTIVE in states, "Should include ACTIVE runner"
    assert RunnerState.IDLE in states, "Should include IDLE runner"
    assert RunnerState.BUSY in states, "Should include BUSY runner"
    assert RunnerState.FAILED in states, "Should include FAILED runner"
    assert RunnerState.TERMINATED in states, "Should include TERMINATED runner"


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_all_runners_all_types(runner_service: RunnerService) -> None:
    """
    Test that list_all_runners returns runners of all types.
    """
    # Create runners of different types across different eggs
    await runner_service.create_runner(
        egg_name="type-test-serverless",
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name="type-test-apex",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name="type-test-nadir",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="commit1",
    )

    # Query all runners
    runners = await runner_service.list_all_runners()

    assert len(runners) >= 3, "Should return at least 3 runners"

    # Verify we have all types
    types = {r.type for r in runners}
    assert RunnerType.SERVERLESS in types, "Should include SERVERLESS runner"
    assert RunnerType.APEX in types, "Should include APEX runner"
    assert RunnerType.NADIR in types, "Should include NADIR runner"


@pytest.mark.dependency(depends=["test_ydb_create_runner_tables"])
@pytest.mark.asyncio
async def test_list_all_runners_all_cloud_providers(
    runner_service: RunnerService,
) -> None:
    """
    Test that list_all_runners returns runners from all cloud providers.
    """
    # Create runners on different cloud providers
    await runner_service.create_runner(
        egg_name="cloud-test-yandex",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name="cloud-test-aws",
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="commit1",
    )

    # Query all runners
    runners = await runner_service.list_all_runners()

    assert len(runners) >= 2, "Should return at least 2 runners"

    # Verify we have both cloud providers
    cloud_providers = {r.cloud_provider for r in runners}
    assert CloudProvider.YANDEX in cloud_providers, (
        "Should include Yandex Cloud runner"
    )
    assert CloudProvider.AWS in cloud_providers, "Should include AWS runner"
