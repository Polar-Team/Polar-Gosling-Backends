"""
Runner Management API Router

REST API endpoints for managing runners.
Provides endpoints for listing, creating, and terminating runners.
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException, status

from app.model.runners_models import Runner
from app.schema.api_schemas import (
    CreateRunnerRequest,
    CreateRunnerResponse,
    RunnerDetailResponse,
    TerminateRunnerRequest,
    TerminateRunnerResponse,
)
from app.services.runner_orchestration import RunnerOrchestrationService
from app.tasks.runners import deploy_runner as deploy_runner_task
from app.tasks.runners import terminate_runner as terminate_runner_task
from app.util.base_logging import logger

router = APIRouter(
    prefix="/runners",
    tags=["runners"],
)


# ============================================================================
# Helper Functions
# ============================================================================


def _get_orchestration_service() -> RunnerOrchestrationService:
    """
    Get runner orchestration service instance.

    Returns:
        RunnerOrchestrationService: Configured orchestration service

    Note:
        In production, this should be replaced with proper dependency
        injection using environment variables for database configuration.
        See conftest.py for test fixtures: test_ydb_config,
        test_ydb_schema, test_orchestration_service
    """
    # Task 16: Implement proper DI with env config
    raise NotImplementedError(
        "Production database configuration not implemented. "
        "Use environment variables to configure YDB connection. "
        "For testing, use the test_orchestration_service fixture from conftest.py"
    )


def _runner_to_response(runner: Runner) -> RunnerDetailResponse:
    """
    Convert Runner model to RunnerDetailResponse.

    Args:
        runner: Runner model instance

    Returns:
        RunnerDetailResponse: API response model
    """
    return RunnerDetailResponse(
        id=runner.id,
        egg_name=runner.egg_name,
        type=runner.type.value,
        state=runner.state.value,
        cloud_provider=runner.cloud_provider.value,
        region=runner.region,
        gitlab_runner_id=runner.gitlab_runner_id,
        deployed_from_commit=runner.deployed_from_commit,
        created_at=runner.created_at.isoformat(),
        updated_at=runner.updated_at.isoformat(),
        last_heartbeat=(
            runner.last_heartbeat.isoformat() if runner.last_heartbeat else None
        ),
        failure_count=runner.failure_count,
        metadata=runner.metadata,
    )


# ============================================================================
# API Endpoints
# ============================================================================


@router.get(
    "",
    response_model=List[RunnerDetailResponse],
    summary="List all runners",
    description="Retrieve a list of all runners across all Eggs",
)
async def list_runners() -> List[RunnerDetailResponse]:
    """
    List all runners.

    Returns:
        List of all runners
    """
    logger.info("Listing all runners")

    # Task 9: DB query
    return []


@router.get(
    "/{runner_id}",
    response_model=RunnerDetailResponse,
    summary="Get runner details",
    description="Retrieve detailed information about a specific runner",
)
async def get_runner(runner_id: str) -> RunnerDetailResponse:
    """
    Get runner details by ID.

    Args:
        runner_id: Unique runner identifier

    Returns:
        Runner details

    Raises:
        HTTPException: If runner not found
    """
    logger.info("Getting runner details: %s", runner_id)

    orchestration = _get_orchestration_service()
    runner = await orchestration.get_runner_status(runner_id)

    if not runner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Runner not found: {runner_id}",
        )

    return _runner_to_response(runner)


@router.post(
    "",
    response_model=CreateRunnerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a new runner",
    description="Trigger deployment of a new runner for an Egg",
)
async def create_runner(request: CreateRunnerRequest) -> CreateRunnerResponse:
    """
    Create a new runner.

    This endpoint triggers an asynchronous Celery task to deploy the runner.
    The task ID can be used to track deployment progress.

    Args:
        request: Runner creation request

    Returns:
        Task ID for tracking deployment

    Raises:
        HTTPException: If Egg not found or deployment fails
    """
    logger.info("Creating runner for Egg: %s", request.egg_name)

    # Validate Egg exists
    orchestration = _get_orchestration_service()
    egg_config = await orchestration.egg_service.get_egg_by_name(request.egg_name)
    if not egg_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Egg not found: {request.egg_name}",
        )

    # Trigger Celery task for runner deployment
    runner_config = {
        "job_requirements": request.job_requirements or {},
        "cloud_provider": request.cloud_provider,
        "region": request.region,
        "deployed_from_commit": request.deployed_from_commit,
    }

    task = deploy_runner_task.apply_async(
        args=(request.egg_name, runner_config),
        priority=10,
    )

    logger.info("Runner deployment task queued: %s", task.id)

    return CreateRunnerResponse(
        task_id=task.id,
        message=f"Runner deployment initiated for Egg '{request.egg_name}'",
    )


@router.delete(
    "/{runner_id}",
    response_model=TerminateRunnerResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Terminate a runner",
    description="Trigger termination of an existing runner",
)
async def delete_runner(
    runner_id: str, request: Optional[TerminateRunnerRequest] = None
) -> TerminateRunnerResponse:
    """
    Terminate a runner.

    This endpoint triggers an asynchronous Celery task to terminate the runner.
    The task ID can be used to track termination progress.

    Args:
        runner_id: Unique runner identifier
        request: Optional termination request with reason and actor

    Returns:
        Task ID for tracking termination

    Raises:
        HTTPException: If runner not found or termination fails
    """
    logger.info("Terminating runner: %s", runner_id)

    # Validate runner exists
    orchestration = _get_orchestration_service()
    runner = await orchestration.get_runner_status(runner_id)

    if not runner:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Runner not found: {runner_id}",
        )

    # Extract termination parameters
    reason = request.reason if request else "manual"
    actor = request.actor if request else "api"

    # Trigger Celery task for runner termination
    task = terminate_runner_task.apply_async(
        args=(runner_id, reason, actor),
        priority=9,
    )

    logger.info("Runner termination task queued: %s", task.id)

    return TerminateRunnerResponse(
        task_id=task.id,
        message=f"Runner termination initiated for '{runner_id}'",
    )


@router.get(
    "/egg/{egg_name}",
    response_model=List[RunnerDetailResponse],
    summary="List runners for an Egg",
    description="Retrieve all runners associated with a specific Egg",
)
async def list_runners_by_egg(egg_name: str) -> List[RunnerDetailResponse]:
    """
    List all runners for a specific Egg.

    Args:
        egg_name: Name of the Egg

    Returns:
        List of runners for the Egg

    Raises:
        HTTPException: If Egg not found
    """
    logger.info("Listing runners for Egg: %s", egg_name)

    # Validate Egg exists
    orchestration = _get_orchestration_service()
    egg_config = await orchestration.egg_service.get_egg_by_name(egg_name)
    if not egg_config:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Egg not found: {egg_name}",
        )

    # Get runners for Egg
    orchestration = _get_orchestration_service()
    runners = await orchestration.list_runners_by_egg(egg_name)

    return [_runner_to_response(runner) for runner in runners]
