"""
Integration tests for deployment plan lifecycle.

Tests the complete flow of deployment plan management including:
- Creating deployment plans linked to Egg configurations
- Querying plans by ID and by Egg name
- Updating plan status through the lifecycle (pending → applied → rolled back)
- Rollback plan chaining

Uses real YDB database via testcontainer with minimal mocks.

Architecture note: select_parameterized_query runs against ALL tables in the
schema and returns one result per table at the same index. DeploymentPlanService
uses result[0][0], so DeploymentPlansTableYDB must be the first (and only) table
in the schema used by that service.
"""

import asyncio
from datetime import datetime, timezone

import pytest
from ydb import AnonymousCredentials

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.runners_models import (
    DeploymentPlansTableYDB,
    RunnerModelYDB,
)
from app.schema.api_schemas import DeploymentPlanStatus
from app.schema.ydb_schemas import YDBConfig, YDBSchema
from app.services.deployment_plan_service import DeploymentPlanService


@pytest.fixture(scope="module", name="plan_ydb_schema")
def ydb_schema_fixture(ydb_container) -> YDBSchema:
    """
    YDB schema with only DeploymentPlansTableYDB.

    DeploymentPlanService.get_plan_by_id / list_plans_by_egg use result[0][0],
    which maps to the first table in the schema. Using a single-table schema
    ensures the result index is always correct.
    """
    config = YDBConfig(
        endpoint=(
            f"grpc://{ydb_container.get_container_host_ip()}:"
            f"{ydb_container.get_exposed_port(2136)}"
        ),
        database="/local",
        credentials=AnonymousCredentials(),
    )
    model = RunnerModelYDB(tables=[DeploymentPlansTableYDB()])
    schema = YDBSchema(config=config, model=model)
    yield schema

    delete_op = AsyncYDBOperations(schema, AsyncYDBFunctionsCollections.drop_tables)

    async def _drop():
        await delete_op.process()

    asyncio.run(_drop())


@pytest.fixture(name="plan_service")
def plan_service_fixture(plan_ydb_schema: YDBSchema) -> DeploymentPlanService:
    """DeploymentPlanService backed by real YDB (deployment_plans table only)."""
    return DeploymentPlanService(schema=plan_ydb_schema)


@pytest.mark.asyncio
@pytest.mark.dependency(name="test_plan_setup_tables")
async def test_setup_tables(plan_ydb_schema: YDBSchema) -> None:
    """Create the deployment_plans table before plan lifecycle tests."""
    op = AsyncYDBOperations(
        plan_ydb_schema, AsyncYDBFunctionsCollections.create_tables
    )
    await op.process()
    await op.check_tables_exist()
    table_names = [t.name for t in op.result]
    assert "deployment_plans" in table_names


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_plan_setup_tables"])
async def test_create_and_retrieve_plan(
    plan_service: DeploymentPlanService,
) -> None:
    """Test creating a deployment plan and retrieving it by ID."""
    plan_id = await plan_service.create_deployment_plan(
        egg_name="plan-test-egg",
        plan_type="deploy",
        config_hash="sha256:aabbcc",
        plan_binary=b"\x00\x01\x02\x03",
        metadata={"git_commit": "abc111", "cloud": "yandex"},
    )

    assert plan_id.startswith("plan-"), f"Unexpected plan ID format: {plan_id}"

    await plan_service.get_plan_by_id(plan_id)
    plan = plan_service.plan_query_result

    assert plan is not None, "Plan should be found by ID"
    assert plan.id == plan_id
    assert plan.egg_name == "plan-test-egg"
    assert plan.plan_type == "deploy"
    assert plan.config_hash == "sha256:aabbcc"
    assert plan.plan_binary == b"\x00\x01\x02\x03"
    assert plan.status.value == DeploymentPlanStatus.PENDING.value
    assert plan.metadata["git_commit"] == "abc111"
    assert plan.metadata["cloud"] == "yandex"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_plan_setup_tables"])
async def test_plan_status_transitions(
    plan_service: DeploymentPlanService,
) -> None:
    """Test updating plan status through the full lifecycle."""
    plan_id = await plan_service.create_deployment_plan(
        egg_name="plan-test-egg",
        plan_type="deploy",
        config_hash="sha256:lifecycle",
        plan_binary=b"plan-data",
    )

    # Verify initial status is PENDING
    await plan_service.get_plan_by_id(plan_id)
    assert plan_service.plan_query_result is not None
    assert (
        plan_service.plan_query_result.status.value == DeploymentPlanStatus.PENDING.value
    )

    # Transition to APPLIED
    applied_at = datetime.now(timezone.utc)
    await plan_service.update_plan_status(
        plan_id=plan_id,
        status=DeploymentPlanStatus.APPLIED,
        applied_at=applied_at,
    )

    await plan_service.get_plan_by_id(plan_id)
    plan = plan_service.plan_query_result
    assert plan is not None
    assert plan.status.value == DeploymentPlanStatus.APPLIED.value
    assert plan.applied_at is not None

    # Transition to ROLLED_BACK
    await plan_service.update_plan_status(
        plan_id=plan_id,
        status=DeploymentPlanStatus.ROLLED_BACK,
    )

    await plan_service.get_plan_by_id(plan_id)
    plan = plan_service.plan_query_result
    assert plan is not None
    assert plan.status.value == DeploymentPlanStatus.ROLLED_BACK.value


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_plan_setup_tables"])
async def test_list_plans_by_egg(
    plan_service: DeploymentPlanService,
) -> None:
    """Test listing all deployment plans for a given Egg."""
    egg_name = "multi-plan-egg"

    plan_ids = []
    for i in range(3):
        pid = await plan_service.create_deployment_plan(
            egg_name=egg_name,
            plan_type="deploy",
            config_hash=f"sha256:hash{i}",
            plan_binary=f"plan-{i}".encode(),
            metadata={"index": i},
        )
        plan_ids.append(pid)

    await plan_service.list_plans_by_egg(egg_name)
    plans = plan_service.plans_list

    assert plans is not None
    assert len(plans) == 3, f"Expected 3 plans, got {len(plans)}"

    retrieved_ids = {p.id for p in plans}
    for pid in plan_ids:
        assert pid in retrieved_ids, f"Plan {pid} missing from list"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_plan_setup_tables"])
async def test_list_plans_empty_for_unknown_egg(
    plan_service: DeploymentPlanService,
) -> None:
    """Test that listing plans for an unknown Egg returns an empty list."""
    await plan_service.list_plans_by_egg("nonexistent-egg-xyz")
    plans = plan_service.plans_list
    assert plans is not None
    assert len(plans) == 0, "Should return empty list for unknown Egg"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_plan_setup_tables"])
async def test_rollback_plan_chaining(
    plan_service: DeploymentPlanService,
) -> None:
    """Test that rollback_plan_id correctly chains plans for rollback support."""
    egg_name = "rollback-chain-egg"

    v1_id = await plan_service.create_deployment_plan(
        egg_name=egg_name,
        plan_type="deploy",
        config_hash="sha256:v1",
        plan_binary=b"v1-plan",
    )

    v2_id = await plan_service.create_deployment_plan(
        egg_name=egg_name,
        plan_type="deploy",
        config_hash="sha256:v2",
        plan_binary=b"v2-plan",
        rollback_plan_id=v1_id,
    )

    await plan_service.get_plan_by_id(v2_id)
    v2 = plan_service.plan_query_result
    assert v2 is not None
    assert v2.rollback_plan_id == v1_id, "v2 should reference v1 as rollback target"

    await plan_service.get_plan_by_id(v1_id)
    v1 = plan_service.plan_query_result
    assert v1 is not None
    assert v1.rollback_plan_id is None, "v1 should have no rollback target"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_plan_setup_tables"])
async def test_plan_isolation_between_eggs(
    plan_service: DeploymentPlanService,
) -> None:
    """Test that plans for different Eggs are isolated from each other."""
    await plan_service.create_deployment_plan(
        egg_name="isolation-egg-a",
        plan_type="deploy",
        config_hash="sha256:a1",
        plan_binary=b"a-plan",
    )
    await plan_service.create_deployment_plan(
        egg_name="isolation-egg-b",
        plan_type="deploy",
        config_hash="sha256:b1",
        plan_binary=b"b-plan",
    )

    await plan_service.list_plans_by_egg("isolation-egg-a")
    plans_a = plan_service.plans_list
    assert plans_a is not None
    for p in plans_a:
        assert p.egg_name == "isolation-egg-a", "Plan belongs to wrong Egg"

    await plan_service.list_plans_by_egg("isolation-egg-b")
    plans_b = plan_service.plans_list
    assert plans_b is not None
    for p in plans_b:
        assert p.egg_name == "isolation-egg-b", "Plan belongs to wrong Egg"


@pytest.mark.asyncio
@pytest.mark.dependency(depends=["test_plan_setup_tables"])
async def test_get_nonexistent_plan_returns_none(
    plan_service: DeploymentPlanService,
) -> None:
    """Test that querying a non-existent plan ID returns None."""
    await plan_service.get_plan_by_id("plan-doesnotexist000")
    assert plan_service.plan_query_result is None
