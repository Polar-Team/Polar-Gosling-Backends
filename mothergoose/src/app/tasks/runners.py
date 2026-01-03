"""
Runner Management Tasks

Celery tasks for deploying, managing, and terminating runners.
These tasks handle the lifecycle of both serverless and VM-based runners.
"""

from typing import Any

from app.core.celery_app import celery_app
from app.core.celery_base import BaseTask
from app.model.runners_models import CloudProvider
from app.schema.ydb_schemas import YDBSchema
from app.services.egg_service import egg_service
from app.services.runner_orchestration import RunnerOrchestrationService
from app.services.runner_service import RunnerService
from app.util.base_logging import logger


def _get_orchestration_service() -> RunnerOrchestrationService:
    """
    Get runner orchestration service instance.

    Creates a new service instance with required dependencies.
    In production, this would use dependency injection.

    Returns:
        RunnerOrchestrationService: Configured orchestration service
    """
    # TODO: Get schema from configuration/environment
    # For now, create a minimal schema for testing
    from ydb import AnonymousCredentials

    from app.model.runners_models import (
        EggConfigsTableYDB,
        RunnerModelYDB,
        RunnersTableYDB,
        SyncHistoryTableYDB,
    )
    from app.schema.ydb_schemas import YDBConfig

    # Create minimal YDB config for testing
    # In production, this would come from environment variables
    config = YDBConfig(
        endpoint="grpc://localhost:2136",
        database="/local",
        credentials=AnonymousCredentials(),
    )

    schema = YDBSchema(
        config=config,
        model=RunnerModelYDB(
            tables=[
                RunnersTableYDB(),
                EggConfigsTableYDB(),
                SyncHistoryTableYDB(),
            ]
        ),
        version="1.0.0",
        default_table="runners",
    )

    runner_service = RunnerService(schema=schema)
    return RunnerOrchestrationService(
        runner_service=runner_service,
        egg_service=egg_service,
    )


@celery_app.task(
    base=BaseTask,
    name="app.tasks.runners.deploy_runner",
    bind=True,
    priority=10,
)
def deploy_runner(
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
        deployed_from_commit = runner_config.get("deployed_from_commit", "unknown")

        # Convert cloud provider string to enum
        cloud_provider = CloudProvider(cloud_provider_str)

        # Determine runner type based on job requirements
        # Note: This is a synchronous wrapper around async code
        # In production, use celery with async support or run_in_executor
        import asyncio

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
    self: BaseTask, runner_id: str, reason: str = "manual", actor: str = "system"
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
        import asyncio

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
