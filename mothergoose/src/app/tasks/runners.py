"""
Runner Management Tasks

Celery tasks for deploying, managing, and terminating runners.
These tasks handle the lifecycle of both serverless and VM-based runners.
"""

# pylint: disable=duplicate-code

import os
from typing import Any

from app.core.celery_app import celery_app
from app.core.celery_base import BaseTask
from app.core.config import (
    get_ydb_schema,
)
from app.schema.tofu_schemas import (
    TofuBackendS3Options,
    TofuProvidersVer,
)
from app.services.binary_service import (
    UpdateGithub,
)
from app.services.deployment_plan_service import (
    DeploymentPlanService,
)
from app.services.egg_service import (
    EggService,
)
from app.services.opentofu_configuration import (
    OpenTofuConfiguration,
    TofuSetting,
)
from app.services.runner_orchestration import RunnerOrchestrationService
from app.services.runner_service import (
    RunnerService,
)
from app.services.s3fs_mount_manager import (
    S3FSMountManager,
)
from app.services.serverless_runner_deployment import ServerlessRunnerDeploymentService
from app.util.base_logging import logger
from app.util.runner_helpers import (
    build_deployment_kwargs,
    build_runner_result,
    extract_runner_config,
)

# Retry configuration for transient failures (network errors, API rate limits)
_RETRY_COUNTDOWN = 30  # seconds between retries
_RETRY_MAX_RETRIES = 3  # maximum number of retries


def _get_orchestration_service() -> RunnerOrchestrationService:
    """
    Get runner orchestration service instance.

    Creates a new service instance with required dependencies.
    Uses environment variables for database configuration.

    Returns:
        RunnerOrchestrationService: Configured orchestration service

    Note:
        Database configuration is read from environment variables:
        - MOTHERGOOSE_YDB_ENDPOINT
        - MOTHERGOOSE_YDB_DATABASE
        - MOTHERGOOSE_YDB_POOL_SIZE
        - MOTHERGOOSE_YDB_USE_ANONYMOUS_CREDENTIALS
    """

    schema = get_ydb_schema()
    runner_service = RunnerService(schema=schema)
    egg_service = EggService(schema=schema)
    s3fs_manager = S3FSMountManager(
        s3_bucket=os.getenv("MOTHERGOOSE_S3_BUCKET", "binaries"),
        mount_point=os.getenv("MOTHERGOOSE_GOSLING_CACHE_DIR", "/tmp/gosling"),
        s3_endpoint_url=os.getenv("MOTHERGOOSE_S3_ENDPOINT_URL"),
        aws_access_key_id=os.getenv("MOTHERGOOSE_AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("MOTHERGOOSE_AWS_SECRET_ACCESS_KEY"),
    )

    return RunnerOrchestrationService(
        runner_service=runner_service,
        egg_service=egg_service,
        schema=schema,
        s3fs_manager=s3fs_manager,
    )


def _get_serverless_deployment_service() -> ServerlessRunnerDeploymentService:
    """
    Get serverless runner deployment service instance.

    Creates a new service instance with required dependencies.
    Uses environment variables for database and OpenTofu configuration.

    Returns:
        ServerlessRunnerDeploymentService: Configured deployment service

    Note:
        Configuration is read from environment variables:
        - Database: MOTHERGOOSE_YDB_* variables
        - OpenTofu: MOTHERGOOSE_TOFU_* variables
    """

    schema = get_ydb_schema()

    # Configure OpenTofu settings
    tofu_settings = TofuSetting(
        providers=[
            TofuProvidersVer(
                name="yandex",
                version=os.getenv("MOTHERGOOSE_TOFU_YANDEX_VERSION", ">= 0.100.0"),
                source="yandex-cloud/yandex",
            ),
            TofuProvidersVer(
                name="aws",
                version=os.getenv("MOTHERGOOSE_TOFU_AWS_VERSION", ">= 5.0.0"),
                source="hashicorp/aws",
            ),
        ],
        backend_s3_options=TofuBackendS3Options(
            bucket=os.getenv("MOTHERGOOSE_TOFU_STATE_BUCKET", "tofu-states"),
            key=os.getenv("MOTHERGOOSE_TOFU_STATE_KEY", "runners/state.tfstate"),
            region=os.getenv("MOTHERGOOSE_TOFU_STATE_REGION", "us-east-1"),
            endpoint=os.getenv("MOTHERGOOSE_TOFU_STATE_ENDPOINT"),
            profile=os.getenv("MOTHERGOOSE_TOFU_STATE_PROFILE"),
            role_arn=os.getenv("MOTHERGOOSE_TOFU_STATE_ROLE_ARN"),
            dynamodb_table=os.getenv("MOTHERGOOSE_TOFU_STATE_DYNAMODB_TABLE"),
        ),
        artifact_cache_bucket=os.getenv("MOTHERGOOSE_TOFU_ARTIFACT_CACHE_BUCKET"),
        health_checks=None,
    )

    opentofu_config = OpenTofuConfiguration(
        updater=UpdateGithub(
            schema=schema,
            binary_name="tofu",
            github_repo="opentofu/opentofu",
            table_name="opentofu_versions",
            install_dir=os.getenv("MOTHERGOOSE_TOFU_INSTALL_DIR"),
        ),
        tofu_settings=tofu_settings,
    )

    deployment_plan_service = DeploymentPlanService(schema=schema)

    return ServerlessRunnerDeploymentService(
        runner_service=RunnerService(schema=schema),
        egg_service=EggService(schema=schema),
        opentofu_config=opentofu_config,
        deployment_plan_service=deployment_plan_service,
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
    - Template rendering via setup_tofu_configuration()
    - Plan generation and storage in database
    - Apply execution via OpenTofu
    - 60-minute timeout enforcement
    - Automatic resource cleanup
    - Retry logic for transient failures

    Runner state transitions: queued → provisioning (ACTIVE after deploy)

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
        # Get serverless deployment service (includes DeploymentPlanService)
        serverless_service = _get_serverless_deployment_service()

        # Step 1: Render OpenTofu templates and update binary
        # This must happen before plan generation and apply
        import asyncio  # pylint: disable=import-outside-toplevel

        loop = asyncio.get_event_loop()
        loop.run_until_complete(
            serverless_service.opentofu_config.setup_tofu_configuration()
        )
        logger.info("OpenTofu configuration rendered for Egg '%s'", egg_name)

        # Extract configuration
        (
            job_requirements,
            cloud_provider,
            region,
            deployed_from_commit,
        ) = extract_runner_config(runner_config)

        # Step 2: Deploy serverless runner (plan generation + storage + apply)
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
        # Retry on transient failures (network errors, API rate limits)
        # ValueError (bad config) and RuntimeError (plan/apply failure) are not retried
        if not isinstance(exc, (ValueError, RuntimeError)):
            raise self.retry(
                exc=exc, countdown=_RETRY_COUNTDOWN, max_retries=_RETRY_MAX_RETRIES
            )
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
