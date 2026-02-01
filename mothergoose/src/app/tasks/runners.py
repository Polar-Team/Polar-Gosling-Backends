"""
Runner Management Tasks

Celery tasks for deploying, managing, and terminating runners.
These tasks handle the lifecycle of both serverless and VM-based runners.
"""

# pylint: disable=duplicate-code

from typing import Any

from app.core.celery_app import celery_app
from app.core.celery_base import BaseTask
from app.services.runner_orchestration import RunnerOrchestrationService
from app.services.serverless_runner_deployment import ServerlessRunnerDeploymentService
from app.util.base_logging import logger
from app.util.runner_helpers import (
    build_deployment_kwargs,
    build_runner_result,
    extract_runner_config,
)


def _get_orchestration_service() -> RunnerOrchestrationService:
    """
    Get runner orchestration service instance.

    Creates a new service instance with required dependencies.
    In production, this would use dependency injection.

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
        "For testing, use the fixture from conftest.py"
    )


def _get_serverless_deployment_service() -> ServerlessRunnerDeploymentService:
    """
    Get serverless runner deployment service instance.

    Creates a new service instance with required dependencies.
    In production, this would use dependency injection.

    Returns:
        ServerlessRunnerDeploymentService: Configured deployment service

    Note:
        In production, this should be replaced with proper dependency
        injection using environment variables for database configuration.
    """
    # Task 17: Implement proper DI with env config
    raise NotImplementedError(
        "Production database configuration not implemented. "
        "Use environment variables to configure YDB connection. "
        "For testing, use the fixture from conftest.py"
    )


@celery_app.task(
    base=BaseTask,
    name="app.tasks.runners.deploy_runner",
    bind=True,
    priority=10,
)
def deploy_runner(  # pylint: disable=too-many-locals
    self: BaseTask,
    egg_name: str,
    runner_config: dict[str, Any],
) -> dict[str, Any]:
    """
    Deploy a new runner (serverless or VM) for an Egg.

    This task handles the deployment of runners based on Egg configuration.
    High priority task to ensure responsive runner provisioning.

    Args:
        self: Task instance (bound)
        egg_name: Name of the Egg requesting the runner
        runner_config: Runner configuration parameters including:
            - job_requirements: Job requirements from GitLab webhook
            - cloud_provider: Cloud provider (yandex/aws)
            - region: Cloud region
            - deployed_from_commit: Git commit hash

    Returns:
        dict: Deployment result with runner ID and status

    Raises:
        Exception: If runner deployment fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.info("Deploying runner for Egg '%s' in task %s", egg_name, task_id)
    logger.debug("Runner config: %s", runner_config)

    try:
        # Get orchestration service
        orchestration = _get_orchestration_service()

        # Extract configuration
        (
            job_requirements,
            cloud_provider,
            region,
            deployed_from_commit,
        ) = extract_runner_config(runner_config)

        # Determine runner type based on job requirements
        # Note: This is a synchronous wrapper around async code
        # In production, use celery with async support or run_in_executor
        import asyncio  # pylint: disable=import-outside-toplevel

        loop = asyncio.get_event_loop()

        # Get Egg config to help determine runner type
        egg_config = loop.run_until_complete(
            orchestration.egg_service.get_egg_by_name(egg_name)
        )

        # Determine runner type
        runner_type = orchestration.determine_runner_type(
            job_requirements=job_requirements,
            egg_config=egg_config,
        )

        # Provision runner
        runner = loop.run_until_complete(
            orchestration.provision_runner(
                egg_name=egg_name,
                runner_type=runner_type,
                cloud_provider=cloud_provider,
                region=region,
                deployed_from_commit=deployed_from_commit,
                job_requirements=job_requirements,
            )
        )

        if not runner:
            raise RuntimeError("Runner provisioning returned None")

        result = build_runner_result(task_id, egg_name, runner)
        logger.info("Runner deployment completed: %s", result)
        return result

    except Exception as exc:
        logger.error("Runner deployment failed: %s", exc, exc_info=True)
        raise


@celery_app.task(
    base=BaseTask,
    name="app.tasks.runners.terminate_runner",
    bind=True,
    priority=9,
)
def terminate_runner(
    self: BaseTask,
    runner_id: str,
    reason: str = "manual",
    actor: str = "system",
) -> dict[str, Any]:
    """
    Terminate an existing runner.

    This task handles the graceful termination of runners.
    High priority task to ensure responsive resource cleanup.

    Args:
        self: Task instance (bound)
        runner_id: ID of the runner to terminate
        reason: Reason for termination (manual, failed, expired, etc.)
        actor: Who initiated the termination

    Returns:
        dict: Termination result with status

    Raises:
        Exception: If runner termination fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.info(
        "Terminating runner '%s' in task %s, reason: %s, actor: %s",
        runner_id,
        task_id,
        reason,
        actor,
    )

    try:
        # Get orchestration service
        orchestration = _get_orchestration_service()

        # Terminate runner
        # Note: This is a synchronous wrapper around async code
        import asyncio  # pylint: disable=import-outside-toplevel

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            orchestration.terminate_runner(
                runner_id=runner_id,
                reason=reason,
                actor=actor,
            )
        )

        result = {
            "status": "success",
            "task_id": task_id,
            "runner_id": runner_id,
            "reason": reason,
            "actor": actor,
            "message": "Runner terminated successfully",
        }

        logger.info("Runner termination completed: %s", result)
        return result

    except Exception as exc:
        logger.error("Runner termination failed: %s", exc, exc_info=True)
        raise


@celery_app.task(
    base=BaseTask,
    name="app.tasks.runners.deploy_serverless_runner",
    bind=True,
    priority=10,
)
def deploy_serverless_runner(  # pylint: disable=too-many-locals
    self: BaseTask,
    egg_name: str,
    runner_config: dict[str, Any],
) -> dict[str, Any]:
    """
    Deploy a serverless container runner for an Egg.

    This task handles the deployment of serverless runners with:
    - 60-minute timeout enforcement
    - Automatic resource cleanup
    - Pre-built container images with Gosling CLI

    Args:
        self: Task instance (bound)
        egg_name: Name of the Egg requesting the runner
        runner_config: Runner configuration parameters including:
            - cloud_provider: Cloud provider (yandex/aws)
            - region: Cloud region
            - deployed_from_commit: Git commit hash
            - job_requirements: Optional job requirements

    Returns:
        dict: Deployment result with runner ID and status

    Raises:
        Exception: If serverless runner deployment fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.info(
        "Deploying serverless runner for Egg '%s' in task %s", egg_name, task_id
    )
    logger.debug("Runner config: %s", runner_config)

    try:
        # Get serverless deployment service
        serverless_service = _get_serverless_deployment_service()

        # Extract configuration
        (
            job_requirements,
            cloud_provider,
            region,
            deployed_from_commit,
        ) = extract_runner_config(runner_config)

        # Deploy serverless runner using helper
        import asyncio  # pylint: disable=import-outside-toplevel

        loop = asyncio.get_event_loop()
        deployment_kwargs = build_deployment_kwargs(
            egg_name=egg_name,
            cloud_provider=cloud_provider,
            region=region,
            deployed_from_commit=deployed_from_commit,
            job_requirements=job_requirements,
        )
        runner = loop.run_until_complete(
            serverless_service.deploy_serverless_runner(**deployment_kwargs)
        )

        if not runner:
            raise RuntimeError("Serverless runner deployment returned None")

        result = build_runner_result(task_id, egg_name, runner)
        result["timeout_minutes"] = serverless_service.serverless_limit_timeout

        logger.info("Serverless runner deployment completed: %s", result)
        return result

    except Exception as exc:
        logger.error("Serverless runner deployment failed: %s", exc, exc_info=True)
        raise


@celery_app.task(
    base=BaseTask,
    name="app.tasks.runners.cleanup_serverless_runner",
    bind=True,
    priority=8,
)
def cleanup_serverless_runner(
    self: BaseTask,
    runner_id: str,
    reason: str = "timeout",
) -> dict[str, Any]:
    """
    Clean up a serverless runner and its resources.

    This task handles:
    - Updating runner state to TERMINATED
    - Executing OpenTofu destroy
    - Creating audit log entry

    Args:
        self: Task instance (bound)
        runner_id: ID of the runner to clean up
        reason: Reason for cleanup (timeout, manual, error)

    Returns:
        dict: Cleanup result with status

    Raises:
        Exception: If cleanup fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.info(
        "Cleaning up serverless runner '%s' in task %s, reason: %s",
        runner_id,
        task_id,
        reason,
    )

    try:
        # Get serverless deployment service
        serverless_service = _get_serverless_deployment_service()

        # Clean up serverless runner
        # Note: This is a synchronous wrapper around async code
        import asyncio  # pylint: disable=import-outside-toplevel

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            serverless_service.cleanup_serverless_runner(
                runner_id=runner_id,
                reason=reason,
            )
        )

        result = {
            "status": "success",
            "task_id": task_id,
            "runner_id": runner_id,
            "reason": reason,
            "message": "Serverless runner cleaned up successfully",
        }

        logger.info("Serverless runner cleanup completed: %s", result)
        return result

    except Exception as exc:
        logger.error("Serverless runner cleanup failed: %s", exc, exc_info=True)
        raise


@celery_app.task(
    base=BaseTask,
    name="app.tasks.runners.enforce_serverless_timeout",
    bind=True,
    priority=9,
)
def enforce_serverless_timeout(
    self: BaseTask,
    runner_id: str,
) -> dict[str, Any]:
    """
    Enforce timeout for a serverless runner.

    This task is called when a serverless runner exceeds the 60-minute limit.
    It forcefully terminates the runner and cleans up resources.

    Args:
        self: Task instance (bound)
        runner_id: ID of the runner to terminate

    Returns:
        dict: Enforcement result with status

    Raises:
        Exception: If timeout enforcement fails after retries
    """
    task_id = self.request.id or "unknown"
    logger.warning(
        "Enforcing timeout for serverless runner '%s' in task %s",
        runner_id,
        task_id,
    )

    try:
        # Get serverless deployment service
        serverless_service = _get_serverless_deployment_service()

        # Enforce timeout
        # Note: This is a synchronous wrapper around async code
        import asyncio  # pylint: disable=import-outside-toplevel

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            serverless_service.enforce_timeout(
                runner_id=runner_id,
            )
        )

        result = {
            "status": "success",
            "task_id": task_id,
            "runner_id": runner_id,
            "message": "Serverless runner timeout enforced",
        }

        logger.info("Serverless runner timeout enforcement completed: %s", result)
        return result

    except Exception as exc:
        logger.error(
            "Serverless runner timeout enforcement failed: %s", exc, exc_info=True
        )
        raise
