"""
Eggs API router for Gosling CLI integration.

Provides endpoints for:
- Querying Egg status
- Listing deployment plans
- Creating/updating Egg configurations
- Listing all Eggs
"""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import get_ydb_schema
from app.model.runners_models import generate_new_eggconfig
from app.schema.api_schemas import (
    DeploymentPlanListResponse,
    DeploymentPlanResponse,
    DeploymentPlanStatus,
    EggConfigRequest,
    EggConfigResponse,
    EggListResponse,
    EggStatusResponse,
    RunnerResponse,
)
from app.schema.ydb_schemas import YDBSchema
from app.services.deployment_plan_service import DeploymentPlanService
from app.services.egg_service import EggService
from app.services.runner_service import RunnerService
from app.util.base_logging import logger
from app.util.model_converters import runner_to_response

router = APIRouter(prefix="/eggs", tags=["eggs"])


@router.get("/{name}/status", response_model=EggStatusResponse)
async def get_egg_status(
    name: str, schema: YDBSchema = Depends(get_ydb_schema)
) -> EggStatusResponse:
    """
    Get deployment status for an Egg.

    Used by: Gosling CLI `status` command

    Args:
        name: Egg name
        schema: YDB schema (injected dependency)

    Returns:
        EggStatusResponse: Comprehensive deployment status including:
            - Latest deployment plan
            - Current runner state
            - Deployment history
            - Configuration hash

    Raises:
        HTTPException: 404 if Egg not found
    """
    logger.info("Getting status for Egg: %s", name)

    # Query egg configuration
    egg_service = EggService(schema)
    await egg_service.get_egg_by_name(name)
    egg_config = egg_service.egg_query_result

    if egg_config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Egg '{name}' not found",
        )

    # Query deployment plans
    plan_service = DeploymentPlanService(schema)
    await plan_service.list_plans_by_egg(name)
    plans = plan_service.plans_list or []

    # Convert DeploymentPlan models to DeploymentPlanResponse
    plan_responses = [
        DeploymentPlanResponse(
            id=plan.id,
            egg_name=plan.egg_name,
            plan_type=plan.plan_type,
            config_hash=plan.config_hash,
            created_at=plan.created_at,
            applied_at=plan.applied_at,
            status=DeploymentPlanStatus(plan.status.value),  # pylint: disable=no-member
            rollback_plan_id=plan.rollback_plan_id,
            metadata=plan.metadata,
        )
        for plan in plans
    ]

    # Query active runners
    runner_service = RunnerService(schema)
    runners = await runner_service.list_runners_by_egg(name)

    # Filter for active runners (not terminated or failed)
    active_runner_states = ["active", "idle", "busy"]
    active_runners_list = [r for r in runners if r.state.value in active_runner_states]

    # Convert Runner models to RunnerResponse
    active_runners: list[RunnerResponse] = [
        runner_to_response(runner) for runner in active_runners_list
    ]

    # Calculate config hash
    config_hash = hashlib.sha256(
        json.dumps(egg_config.config, sort_keys=True).encode()
    ).hexdigest()[:16]

    return EggStatusResponse(
        egg_name=name,
        latest_plan=plan_responses[0] if plan_responses else None,
        deployment_history=plan_responses,
        active_runners=active_runners,
        config_hash=config_hash,
    )


@router.get("/{name}/plans", response_model=DeploymentPlanListResponse)
async def list_deployment_plans(
    name: str, schema: YDBSchema = Depends(get_ydb_schema)
) -> DeploymentPlanListResponse:
    """
    List all deployment plans for an Egg.

    Used by: Gosling CLI for deployment history

    Args:
        name: Egg name
        schema: YDB schema (injected dependency)

    Returns:
        DeploymentPlanListResponse: List of all deployment plans

    Raises:
        HTTPException: 404 if Egg not found
    """
    logger.info("Listing deployment plans for Egg: %s", name)

    # Verify egg exists
    egg_service = EggService(schema)
    await egg_service.get_egg_by_name(name)
    if egg_service.egg_query_result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Egg '{name}' not found",
        )

    # Query deployment plans
    plan_service = DeploymentPlanService(schema)
    await plan_service.list_plans_by_egg(name)
    plans = plan_service.plans_list or []

    # Convert DeploymentPlan models to DeploymentPlanResponse
    plan_responses = [
        DeploymentPlanResponse(
            id=plan.id,
            egg_name=plan.egg_name,
            plan_type=plan.plan_type,
            config_hash=plan.config_hash,
            created_at=plan.created_at,
            applied_at=plan.applied_at,
            status=DeploymentPlanStatus(plan.status.value),  # pylint: disable=no-member
            rollback_plan_id=plan.rollback_plan_id,
            metadata=plan.metadata,
        )
        for plan in plans
    ]

    return DeploymentPlanListResponse(
        plans=plan_responses,
        total=len(plan_responses),
    )


@router.get("/{name}/plans/{plan_id}", response_model=DeploymentPlanResponse)
async def get_deployment_plan(
    name: str, plan_id: str, schema: YDBSchema = Depends(get_ydb_schema)
) -> DeploymentPlanResponse:
    """
    Get specific deployment plan details.

    Used by: Gosling CLI for rollback operations

    Args:
        name: Egg name
        plan_id: Deployment plan ID
        schema: YDB schema (injected dependency)

    Returns:
        DeploymentPlanResponse: Deployment plan details

    Raises:
        HTTPException: 404 if Egg or plan not found
    """
    logger.info("Getting deployment plan %s for Egg: %s", plan_id, name)

    # Verify egg exists
    egg_service = EggService(schema)
    await egg_service.get_egg_by_name(name)
    if egg_service.egg_query_result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Egg '{name}' not found",
        )

    # Query specific deployment plan
    plan_service = DeploymentPlanService(schema)
    await plan_service.get_plan_by_id(plan_id)
    plan = plan_service.plan_query_result

    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Deployment plan '{plan_id}' not found for Egg '{name}'",
        )

    # Convert DeploymentPlan model to DeploymentPlanResponse
    return DeploymentPlanResponse(
        id=plan.id,
        egg_name=plan.egg_name,
        plan_type=plan.plan_type,
        config_hash=plan.config_hash,
        created_at=plan.created_at,
        applied_at=plan.applied_at,
        status=DeploymentPlanStatus(plan.status.value),  # pylint: disable=no-member
        rollback_plan_id=plan.rollback_plan_id,
        metadata=plan.metadata,
    )


@router.post("", response_model=EggConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_or_update_egg(
    egg_config: EggConfigRequest, schema: YDBSchema = Depends(get_ydb_schema)
) -> EggConfigResponse:
    """
    Create or update Egg configuration.

    Used by: Gosling CLI `deploy` command

    This endpoint is called during the initial deployment to store
    Egg configuration in the database cache. The configuration is
    synced from the Nest Git repository.

    Args:
        egg_config: Egg configuration request
        schema: YDB schema (injected dependency)

    Returns:
        EggConfigResponse: Created or updated Egg configuration

    Raises:
        HTTPException: 400 if configuration is invalid
    """
    logger.info("Creating or updating Egg: %s", egg_config.name)

    # Convert API request to database model
    now = datetime.now(timezone.utc)

    # Build config dict from request
    config_dict = {
        "type": egg_config.type.value,
        "cloud": egg_config.cloud.model_dump(),
        "resources": egg_config.resources.model_dump(),
        "runner": egg_config.runner.model_dump(),
        "gitlab": egg_config.gitlab.model_dump(),
        "environment": egg_config.environment,
    }

    # Create EggConfig model
    egg = generate_new_eggconfig(
        name=egg_config.name,
        project_id=egg_config.gitlab.project_id,
        group_id=egg_config.gitlab.group_id,
        config=config_dict,
        git_commit=egg_config.git_commit or "unknown",
        git_repo_url_secret="yc-lockbox://nest/repo-url",
        gitlab_token_secret_uri=egg_config.gitlab.token_secret,
        gitlab_webhook_secret_uri=egg_config.gitlab.webhook_secret,
        synced_at=now,
        created_at=now,
        updated_at=now,
    )

    # Upsert to database
    egg_service = EggService(schema)
    await egg_service.upsert_egg(egg)

    # Return response
    return EggConfigResponse(
        name=egg.name,
        type=egg_config.type,
        cloud=egg_config.cloud,
        resources=egg_config.resources,
        runner=egg_config.runner,
        gitlab=egg_config.gitlab,
        environment=egg_config.environment,
        created_at=egg.created_at,
        updated_at=egg.updated_at,
        git_commit=egg.git_commit,
        synced_at=egg.synced_at,
    )


@router.get("", response_model=EggListResponse)
async def list_eggs(schema: YDBSchema = Depends(get_ydb_schema)) -> EggListResponse:
    """
    List all Eggs.

    Used by: Gosling CLI for listing configured Eggs

    Args:
        schema: YDB schema (injected dependency)

    Returns:
        EggListResponse: List of all Egg names
    """
    logger.info("Listing all Eggs")

    # Query all eggs
    egg_service = EggService(schema)
    await egg_service.list_eggs()
    eggs = egg_service.eggs_list or []

    egg_names = [egg.name for egg in eggs]

    return EggListResponse(
        eggs=egg_names,
        total=len(egg_names),
    )
