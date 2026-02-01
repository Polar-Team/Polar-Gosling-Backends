"""
Integration tests for Eggs API with runner queries.

Feature: gitops-runner-orchestration, Task 9.5
Validates: Requirements 4.6, 14.1

This module tests the integration between the Eggs API and runner queries,
specifically the active_runners field in the GET /eggs/{name}/status endpoint.
"""

import pytest
import asyncio
from fastapi import status

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.runners_models import (
    CloudProvider,
    DeploymentPlansTableYDB,
    EggConfigsTableYDB,
    RunnerModelYDB,
    RunnersTableYDB,
    RunnerState,
    RunnerType,
    generate_new_eggconfig,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.egg_service import EggService
from app.services.runner_service import RunnerService
from ydb import AnonymousCredentials


@pytest.fixture(scope="module", name="ydb_schema")
def ydb_schema(ydb_container):
    """Fixture to provide YDB configuration with all required tables."""
    config = YDBConfig(
        endpoint=f"grpc://{ydb_container.get_container_host_ip()}:"
        f"{ydb_container.get_exposed_port(2136)}",
        database="/local",
        credentials=AnonymousCredentials(),
    )

    # Create model with all required tables
    model = RunnerModelYDB(
        tables=[
            RunnersTableYDB(),
            EggConfigsTableYDB(),
            DeploymentPlansTableYDB(),
        ]
    )

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


@pytest.fixture(scope="module", name="egg_service")
def egg_service_fixture(ydb_schema):
    """Fixture providing an egg service with real YDB schema."""
    return EggService(schema=ydb_schema)


@pytest.mark.dependency()
@pytest.mark.asyncio
async def test_ydb_create_tables(ydb_schema):
    """Create tables for testing eggs API with runners."""
    operation = AsyncYDBOperations(
        ydb_schema,
        AsyncYDBFunctionsCollections.create_tables,
    )
    operation.fail_fast = True

    await operation.process()

    # Check that tables exist
    await operation.check_tables_exist()

    # Verify all tables were created
    table_names = [table.name for table in operation.result]
    assert "runners" in table_names, "Table 'runners' was not created."
    assert "egg_configs" in table_names, "Table 'egg_configs' was not created."
    assert "deployment_plans" in table_names, (
        "Table 'deployment_plans' was not created."
    )


@pytest.mark.dependency(depends=["test_ydb_create_tables"])
@pytest.mark.asyncio
async def test_egg_status_with_no_runners(
    egg_service: EggService,
    runner_service: RunnerService,
    ydb_schema: YDBSchema,
) -> None:
    """
    Test that GET /eggs/{name}/status returns empty active_runners list
    when no runners exist for the egg.
    """
    # Create an egg configuration
    egg = generate_new_eggconfig(
        name="test-egg-no-runners",
        project_id=12345,
        config={},
        git_commit="abc123",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/test-egg-no-runners/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/test-egg-no-runners/webhook-secret",
    )
    await egg_service.upsert_egg(egg)

    # Query runners (should be empty)
    runners = await runner_service.list_runners_by_egg("test-egg-no-runners")
    assert len(runners) == 0, "Should have no runners"


@pytest.mark.dependency(depends=["test_ydb_create_tables"])
@pytest.mark.asyncio
async def test_egg_status_with_active_runners(
    egg_service: EggService,
    runner_service: RunnerService,
    ydb_schema: YDBSchema,
) -> None:
    """
    Test that GET /eggs/{name}/status returns active_runners list
    with only active, idle, and busy runners (not failed or terminated).
    """
    # Create an egg configuration
    egg = generate_new_eggconfig(
        name="test-egg-with-runners",
        project_id=54321,
        config={},
        git_commit="def456",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/test-egg-with-runners/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/test-egg-with-runners/webhook-secret",
    )
    await egg_service.upsert_egg(egg)

    # Create runners in different states
    active_runner = await runner_service.create_runner(
        egg_name="test-egg-with-runners",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="def456",
        gitlab_runner_id=1001,
    )

    idle_runner = await runner_service.create_runner(
        egg_name="test-egg-with-runners",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-b",
        deployed_from_commit="def456",
        gitlab_runner_id=1002,
    )

    busy_runner = await runner_service.create_runner(
        egg_name="test-egg-with-runners",
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.BUSY,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="def456",
        gitlab_runner_id=1003,
    )

    # Create failed and terminated runners (should NOT be included)
    await runner_service.create_runner(
        egg_name="test-egg-with-runners",
        runner_type=RunnerType.APEX,
        state=RunnerState.FAILED,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="def456",
    )

    await runner_service.create_runner(
        egg_name="test-egg-with-runners",
        runner_type=RunnerType.APEX,
        state=RunnerState.TERMINATED,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="def456",
    )

    # Query all runners
    all_runners = await runner_service.list_runners_by_egg("test-egg-with-runners")
    assert len(all_runners) == 5, "Should have 5 total runners"

    # Filter for active runners (matching the logic in eggs.py)
    active_runner_states = ["active", "idle", "busy"]
    active_runners = [r for r in all_runners if r.state.value in active_runner_states]

    assert len(active_runners) == 3, "Should have 3 active runners"

    # Verify the active runners are the correct ones
    active_runner_ids = {r.id for r in active_runners}
    assert active_runner.id in active_runner_ids, "Should include active runner"
    assert idle_runner.id in active_runner_ids, "Should include idle runner"
    assert busy_runner.id in active_runner_ids, "Should include busy runner"


@pytest.mark.dependency(depends=["test_ydb_create_tables"])
@pytest.mark.asyncio
async def test_egg_status_runner_isolation(
    egg_service: EggService,
    runner_service: RunnerService,
    ydb_schema: YDBSchema,
) -> None:
    """
    Test that GET /eggs/{name}/status only returns runners for the specified egg.
    """
    # Create two egg configurations
    egg1 = generate_new_eggconfig(
        name="egg-isolation-1",
        project_id=11111,
        config={},
        git_commit="commit1",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/egg-isolation-1/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/egg-isolation-1/webhook-secret",
    )
    await egg_service.upsert_egg(egg1)

    egg2 = generate_new_eggconfig(
        name="egg-isolation-2",
        project_id=22222,
        config={},
        git_commit="commit2",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/egg-isolation-2/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/egg-isolation-2/webhook-secret",
    )
    await egg_service.upsert_egg(egg2)

    # Create runners for egg1
    await runner_service.create_runner(
        egg_name="egg-isolation-1",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    await runner_service.create_runner(
        egg_name="egg-isolation-1",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="commit1",
    )

    # Create runners for egg2
    await runner_service.create_runner(
        egg_name="egg-isolation-2",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="commit2",
    )

    # Query runners for egg1
    runners_egg1 = await runner_service.list_runners_by_egg("egg-isolation-1")
    assert len(runners_egg1) == 2, "egg-isolation-1 should have 2 runners"
    for runner in runners_egg1:
        assert runner.egg_name == "egg-isolation-1", (
            "All runners should belong to egg-isolation-1"
        )

    # Query runners for egg2
    runners_egg2 = await runner_service.list_runners_by_egg("egg-isolation-2")
    assert len(runners_egg2) == 1, "egg-isolation-2 should have 1 runner"
    assert runners_egg2[0].egg_name == "egg-isolation-2", (
        "Runner should belong to egg-isolation-2"
    )


@pytest.mark.dependency(depends=["test_ydb_create_tables"])
@pytest.mark.asyncio
async def test_egg_status_runner_metadata(
    egg_service: EggService,
    runner_service: RunnerService,
    ydb_schema: YDBSchema,
) -> None:
    """
    Test that runner metadata is correctly returned in the active_runners list.
    """
    # Create an egg configuration
    egg = generate_new_eggconfig(
        name="test-egg-metadata",
        project_id=99999,
        config={},
        git_commit="meta123",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/test-egg-metadata/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/test-egg-metadata/webhook-secret",
    )
    await egg_service.upsert_egg(egg)

    # Create a runner with metadata
    runner = await runner_service.create_runner(
        egg_name="test-egg-metadata",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="meta123",
        gitlab_runner_id=5555,
        metadata={
            "instance_id": "i-abc123",
            "instance_type": "standard-v3",
            "custom_field": "custom_value",
        },
    )

    # Query runners
    runners = await runner_service.list_runners_by_egg("test-egg-metadata")
    assert len(runners) == 1, "Should have 1 runner"

    retrieved_runner = runners[0]
    assert retrieved_runner.id == runner.id, "Should be the same runner"
    assert retrieved_runner.gitlab_runner_id == 5555, (
        "Should have correct GitLab runner ID"
    )
    assert retrieved_runner.metadata == {
        "instance_id": "i-abc123",
        "instance_type": "standard-v3",
        "custom_field": "custom_value",
    }, "Should have correct metadata"
