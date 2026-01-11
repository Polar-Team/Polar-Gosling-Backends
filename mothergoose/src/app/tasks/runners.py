"""
Runner Management Tasks

Celery tasks for deploying, managing, and terminating runners.
These tasks handle the lifecycle of both serverless and VM-based runners.
"""

from typing import Any

from app.core.celery_app import celery_app
from app.core.celery_base import BaseTask
from app.model.runners_models import CloudProvider
from app.services.runner_orchestration import RunnerOrchestrationService
from app.util.base_logging import logger


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
        job_requirements = runner_config.get("job_requirements", {})
        cloud_provider_str = runner_config.get("cloud_provider", "yandex")
        region = runner_config.get("region", "ru-central1-a")
        deployed_from_commit = runner_config.get(
            "deployed_from_commit",
            "unknown",
        )

        # Convert cloud provider string to enum
        cloud_provider = CloudProvider(cloud_provider_str)

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

        result = {
            "status": "success",
            "task_id": task_id,
            "egg_name": egg_name,
            "runner_id": runner.id,
            "runner_type": runner.type.value,
            "cloud_provider": runner.cloud_provider.value,
            "region": runner.region,
            "message": "Runner deployed successfully",
        }

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
