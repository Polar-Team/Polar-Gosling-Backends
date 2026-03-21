"""
Integration tests for runner state transitions.

Tests the cross-component interaction between RunnerService and the database,
covering the full runner lifecycle: create → active → busy → failed → terminated.

Uses real YDB database via testcontainer with minimal mocks.
"""

import asyncio

import pytest
from ydb import AnonymousCredentials

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
    SyncHistoryTableYDB,
    generate_new_eggconfig,
)
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.egg_service import EggService
from app.services.runner_service import RunnerService


@pytest.fixture(scope="module", name="state_ydb_schema")
def ydb_schema_fixture(ydb_container) -> YDBSchema:
    """YDB schema with all tables needed for runner state transition tests."""
    config = YDBConfig(
        endpoint=(
            f"grpc://{ydb_container.get_container_host_ip()}:"
            f"{ydb_container.get_exposed_port(2136)}"
        ),
        database="/local",
        credentials=AnonymousCredentials(),
    )
    model = RunnerModelYDB(
        tables=[
            RunnersTableYDB(),
            EggConfigsTableYDB(),
            SyncHistoryTableYDB(),
            DeploymentPlansTableYDB(),
        ]
    )
    schema = YDBSchema(config=config, model=model)
    yield schema

    delete_op = AsyncYDBOperations(schema, AsyncYDBFunctionsCollections.drop_tables)

    async def _drop():
        await delete_op.process()

    asyncio.run(_drop())


@pytest.fixture(name="state_runner_service")
def runner_service_fixture(state_ydb_schema: YDBSchema) -> RunnerService:
    """RunnerService backed by real YDB."""
    return RunnerService(schema=state_ydb_schema)


@pytest.fixture(name="state_egg_service")
def egg_service_fixture(state_ydb_schema: YDBSchema) -> EggService:
    """EggService backed by real YDB."""
    return EggService(schema=state_ydb_schema)


@pytest.mark.asyncio
@pytest.mark.dependency(name="test_state_setup_tables")
async def test_setup_tables(state_ydb_schema: YDBSchema) -> None:
    """Create all required tables before state transition tests."""
    op = AsyncYDBOperations(
        state_ydb_schema, AsyncYDBFunctionsCollections.create_tables
    )
    await op.process()
    await op.check_tables_exist()
    table_names = [t.name for t in op.result]
    assert "runners" in table_names
    assert "egg_configs" in table_names


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_state_setup_tables"])
async def test_runner_full_lifecycle(
    state_runner_service: RunnerService,
    state_egg_service: EggService,
) -> None:
    """
    Test the complete runner lifecycle: create → active → busy → terminated.

    Verifies that state transitions are persisted correctly in YDB.
    """
    # Seed Egg
    egg = generate_new_eggconfig(
        name="lifecycle-egg",
        project_id=20001,
        config={"type": "vm"},
        git_commit="lifecycle-commit",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/lifecycle-egg/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/lifecycle-egg/webhook-secret",
    )
    await state_egg_service.upsert_egg(egg)

    # Create runner in ACTIVE state
    runner = await state_runner_service.create_runner(
        egg_name="lifecycle-egg",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="lifecycle-commit",
        gitlab_runner_id=3001,
    )
    assert runner.state == RunnerState.ACTIVE

    # Transition to BUSY
    await state_runner_service.update_runner_state(runner.id, RunnerState.BUSY)
    updated = await state_runner_service.get_runner(runner.id)
    assert updated is not None
    assert updated.state == RunnerState.BUSY

    # Transition to TERMINATED
    await state_runner_service.update_runner_state(runner.id, RunnerState.TERMINATED)
    terminated = await state_runner_service.get_runner(runner.id)
    assert terminated is not None
    assert terminated.state == RunnerState.TERMINATED


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_state_setup_tables"])
async def test_runner_failure_state(
    state_runner_service: RunnerService,
) -> None:
    """Test that a runner can be transitioned to FAILED state."""
    runner = await state_runner_service.create_runner(
        egg_name="lifecycle-egg",
        runner_type=RunnerType.APEX,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="lifecycle-commit",
    )

    await state_runner_service.update_runner_state(runner.id, RunnerState.FAILED)
    failed = await state_runner_service.get_runner(runner.id)
    assert failed is not None
    assert failed.state == RunnerState.FAILED


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_state_setup_tables"])
async def test_runner_state_with_audit(
    state_runner_service: RunnerService,
) -> None:
    """
    Test that runner state is persisted after an audit-style update.

    update_runner_state_with_audit requires audit_logs in the schema, which
    cannot be included in a RunnerModelYDB schema. We use update_runner_state
    here to verify the state transition is persisted — audit log creation is
    covered separately in unit tests.
    """
    runner = await state_runner_service.create_runner(
        egg_name="lifecycle-egg",
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="lifecycle-commit",
        gitlab_runner_id=3002,
    )

    # Task 31: Use update_runner_state — audit_logs not in RunnerModelYDB schema
    await state_runner_service.update_runner_state(runner.id, RunnerState.TERMINATED)

    updated = await state_runner_service.get_runner(runner.id)
    assert updated is not None
    assert updated.state == RunnerState.TERMINATED


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_state_setup_tables"])
async def test_list_all_runners_across_eggs(
    state_runner_service: RunnerService,
    state_egg_service: EggService,
) -> None:
    """Test that list_all_runners returns runners from all Eggs."""
    egg2 = generate_new_eggconfig(
        name="lifecycle-egg-2",
        project_id=20002,
        config={"type": "serverless"},
        git_commit="commit-2",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri="yc-lockbox://gitlab/gitlab.com/lifecycle-egg-2/runner-token",
        gitlab_webhook_secret_uri="yc-lockbox://gitlab/gitlab.com/lifecycle-egg-2/webhook-secret",
    )
    await state_egg_service.upsert_egg(egg2)

    await state_runner_service.create_runner(
        egg_name="lifecycle-egg",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-b",
        deployed_from_commit="lifecycle-commit",
    )
    await state_runner_service.create_runner(
        egg_name="lifecycle-egg-2",
        runner_type=RunnerType.SERVERLESS,
        state=RunnerState.ACTIVE,
        cloud_provider=CloudProvider.AWS,
        region="us-east-1",
        deployed_from_commit="commit-2",
    )

    all_runners = await state_runner_service.list_all_runners()
    assert len(all_runners) >= 2, "Should have runners from both Eggs"

    egg_names = {r.egg_name for r in all_runners}
    assert "lifecycle-egg" in egg_names
    assert "lifecycle-egg-2" in egg_names


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_state_setup_tables"])
async def test_nadir_to_apex_state_transition(
    state_runner_service: RunnerService,
) -> None:
    """
    Test Nadir → Apex promotion: runner type stays the same but state changes.

    Simulates the UglyFox pool promotion logic at the database level.
    """
    runner = await state_runner_service.create_runner(
        egg_name="lifecycle-egg",
        runner_type=RunnerType.NADIR,
        state=RunnerState.IDLE,
        cloud_provider=CloudProvider.YANDEX,
        region="ru-central1-a",
        deployed_from_commit="lifecycle-commit",
    )
    assert runner.state == RunnerState.IDLE

    # Promote to ACTIVE (simulating Nadir → Apex demand increase)
    await state_runner_service.update_runner_state(runner.id, RunnerState.ACTIVE)
    promoted = await state_runner_service.get_runner(runner.id)
    assert promoted is not None
    assert promoted.state == RunnerState.ACTIVE
    # Runner type is unchanged — only state changes
    assert promoted.type == RunnerType.NADIR


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_state_setup_tables"])
async def test_get_nonexistent_runner_returns_none(
    state_runner_service: RunnerService,
) -> None:
    """Test that querying a non-existent runner ID returns None."""
    result = await state_runner_service.get_runner("runner-does-not-exist-xyz")
    assert result is None
