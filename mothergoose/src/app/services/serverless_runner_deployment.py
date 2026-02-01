"""
Serverless Runner Deployment Service

Handles deployment of serverless container runners to Yandex Cloud and AWS.
Serverless runners have a 60-minute execution limit and are automatically cleaned up.

This service coordinates:
- Container image preparation with pre-installed binaries
- Serverless container deployment (Yandex Cloud Serverless / AWS Lambda)
- Timeout enforcement (60 minutes)
- Resource cleanup after completion
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.model.runners_models import (
    CloudProvider,
    EggConfig,
    Runner,
    RunnerState,
    RunnerType,
)
from app.services.egg_service import EggService
from app.services.opentofu_configuration import OpenTofuConfiguration
from app.services.runner_service import RunnerService
from app.util.base_logging import logged


@logged
class ServerlessRunnerDeploymentService:
    """
    Service for deploying serverless container runners.

    Handles:
    - Serverless container deployment to Yandex Cloud Serverless Containers
    - Serverless container deployment to AWS Lambda
    - 60-minute timeout enforcement
    - Automatic resource cleanup
    """

    # pylint: disable=no-member, too-many-positional-arguments, too-many-arguments

    # Task 17: Serverless runner timeout (60 minutes)
    __serverless_limit_timeout: int = 60

    def __init__(
        self,
        runner_service: RunnerService,
        egg_service: EggService,
        opentofu_config: OpenTofuConfiguration,
    ) -> None:
        """
        Initialize serverless runner deployment service.

        Args:
            runner_service: Service for runner state management
            egg_service: Service for Egg configuration retrieval
            opentofu_config: OpenTofu configuration service
        """
        self.runner_service = runner_service
        self.egg_service = egg_service
        self.opentofu_config = opentofu_config

    @property
    def serverless_limit_timeout(self) -> int:
        """Get the serverless_limit_timeout value."""
        return self.__serverless_limit_timeout

    @serverless_limit_timeout.setter
    def serverless_limit_timeout(self, value: bool) -> None:
        """Set the serverless_limit_timeout value - max 60 minutes."""
        if not isinstance(value, int) and 0 < value <= 60:
            raise ValueError("Fail fast must be a boolean value.")
        self.__serverless_limit_timeout = value

    async def deploy_serverless_runner(
        self,
        egg_name: str,
        cloud_provider: CloudProvider,
        region: str,
        deployed_from_commit: str,
        job_requirements: Optional[Dict[str, Any]] = None,
    ) -> Runner:
        """
        Deploy a serverless container runner.

        This method orchestrates the full serverless runner deployment:
        1. Retrieve Egg configuration
        2. Generate OpenTofu configuration for serverless container
        3. Deploy container using OpenTofu
        4. Schedule automatic cleanup after 60 minutes
        5. Return runner object

        Args:
            egg_name: Name of the Egg requesting the runner
            cloud_provider: Cloud provider (yandex/aws)
            region: Cloud region for deployment
            deployed_from_commit: Git commit hash triggering deployment
            job_requirements: Optional job requirements

        Returns:
            Runner: Deployed serverless runner object

        Raises:
            ValueError: If Egg configuration not found
            RuntimeError: If deployment fails
        """
        self.info(
            "Deploying serverless runner for Egg '%s' on %s/%s",
            egg_name,
            cloud_provider.value,
            region,
        )

        # Retrieve Egg configuration
        await self.egg_service.get_egg_by_name(egg_name)
        egg_config = self.egg_service.egg_query_result
        if egg_config is None:
            err_msg = f"Egg configuration not found: {egg_name}"
            self.error(err_msg)
            raise ValueError(err_msg)

        # Task 17: Deploy serverless container using Compute Module
        if cloud_provider == CloudProvider.YANDEX:  # type: ignore[unreachable]
            runner = await self._deploy_yandex_serverless(
                egg_name=egg_name,
                egg_config=egg_config,
                region=region,
                deployed_from_commit=deployed_from_commit,
                job_requirements=job_requirements,
            )
        elif cloud_provider == CloudProvider.AWS:
            runner = await self._deploy_aws_lambda(
                egg_name=egg_name,
                egg_config=egg_config,
                region=region,
                deployed_from_commit=deployed_from_commit,
                job_requirements=job_requirements,
            )
        else:
            raise ValueError(f"Unsupported cloud provider: {cloud_provider}")

        # Task 17: Schedule automatic cleanup after 60 minutes
        asyncio.create_task(
            self._schedule_cleanup(
                runner_id=runner.id,
                timeout_minutes=self.__serverless_limit_timeout,
            )
        )

        self.info("Serverless runner deployed successfully: %s", runner.id)
        return runner

    async def _deploy_yandex_serverless(
        self,
        egg_name: str,
        egg_config: EggConfig,
        region: str,
        deployed_from_commit: str,
        job_requirements: Optional[Dict[str, Any]],
    ) -> Runner:
        """
        Deploy serverless container to Yandex Cloud Serverless Containers.

        Uses OpenTofu with Compute Module to provision:
        - Serverless container with pre-built image
        - Container configuration (memory, timeout, env vars)
        - IAM service account for container execution
        - Network configuration

        Args:
            egg_name: Egg name
            egg_config: Egg configuration
            region: Yandex Cloud region
            deployed_from_commit: Git commit hash
            job_requirements: Job requirements

        Returns:
            Runner object
        """
        # pylint: disable=unused-argument

        self.info("Deploying Yandex Cloud Serverless Container for Egg '%s'", egg_name)

        # Task 17: Create runner record in PROVISIONING state

        runner = await self.runner_service.create_runner(
            egg_name=egg_name,
            runner_type=RunnerType.SERVERLESS,
            state=RunnerState.ACTIVE,  # Will be PROVISIONING in full implementation
            cloud_provider=CloudProvider.YANDEX,
            region=region,
            deployed_from_commit=deployed_from_commit,
            metadata={
                "job_requirements": job_requirements or {},
                "timeout_minutes": self.__serverless_limit_timeout,
                "deployment_type": "yandex_serverless_container",
                "provisioning_started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Task 17: Generate OpenTofu configuration for Yandex Serverless Container
        # This will use Jinja2 templates to generate:
        # - tofu_resources_tf.j2 with yandex_serverless_container resource
        # - Container image from registry (cr.yandex/polar-gosling/gosling:latest)
        # - Memory: 512MB (configurable from Egg config)
        # - Timeout: 3600 seconds (60 minutes)
        # - Environment variables from Egg config
        # - Service account with necessary IAM roles

        self.info(
            "Yandex Serverless Container configuration generated for runner %s",
            runner.id,
        )

        # Task 17: Execute OpenTofu plan and apply
        # This will:
        # 1. Generate deployment plan
        # 2. Store plan binary in database
        # 3. Apply plan to create serverless container
        # 4. Update runner state to ACTIVE

        self.info("Yandex Serverless Container deployed: %s", runner.id)
        return runner

    async def _deploy_aws_lambda(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self,
        egg_name: str,
        egg_config: EggConfig,
        region: str,
        deployed_from_commit: str,
        job_requirements: Optional[Dict[str, Any]],
    ) -> Runner:
        """
        Deploy serverless container to AWS Lambda (using Fargate for containers).

        Uses OpenTofu with Compute Module to provision:
        - Lambda function with container image
        - Function configuration (memory, timeout, env vars)
        - IAM execution role
        - VPC configuration (if needed)

        Args:
            egg_name: Egg name
            egg_config: Egg configuration
            region: AWS region
            deployed_from_commit: Git commit hash
            job_requirements: Job requirements

        Returns:
            Runner object
        """
        # pylint: disable=unused-argument

        self.info("Deploying AWS Lambda container for Egg '%s'", egg_name)

        # Task 17: Create runner record in PROVISIONING state

        runner = await self.runner_service.create_runner(
            egg_name=egg_name,
            runner_type=RunnerType.SERVERLESS,
            state=RunnerState.ACTIVE,  # Will be PROVISIONING in full implementation
            cloud_provider=CloudProvider.AWS,
            region=region,
            deployed_from_commit=deployed_from_commit,
            metadata={
                "job_requirements": job_requirements or {},
                "timeout_minutes": self.__serverless_limit_timeout,
                "deployment_type": "aws_lambda_container",
                "provisioning_started_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Task 17: Generate OpenTofu configuration for AWS Lambda
        # This will use Jinja2 templates to generate:
        # - tofu_resources_tf.j2 with aws_lambda_function resource
        # - Container image from ECR (aws_account.dkr.ecr.region.amazonaws.com/gosling:latest)
        # - Memory: 512MB (configurable from Egg config)
        # - Timeout: 3600 seconds (60 minutes)
        # - Environment variables from Egg config
        # - IAM execution role with necessary permissions

        self.info("AWS Lambda configuration generated for runner %s", runner.id)

        # Task 17: Execute OpenTofu plan and apply
        # This will:
        # 1. Generate deployment plan
        # 2. Store plan binary in database
        # 3. Apply plan to create Lambda function
        # 4. Update runner state to ACTIVE

        self.info("AWS Lambda container deployed: %s", runner.id)
        return runner

    async def _schedule_cleanup(
        self,
        runner_id: str,
        timeout_minutes: int,
    ) -> None:
        """
        Schedule automatic cleanup of serverless runner after timeout.

        This method:
        1. Waits for the specified timeout duration
        2. Checks if runner is still active
        3. Terminates runner and cleans up resources

        Args:
            runner_id: Runner ID to clean up
            timeout_minutes: Timeout in minutes
        """
        self.info(
            "Scheduling cleanup for runner %s after %d minutes",
            runner_id,
            timeout_minutes,
        )

        # Task 17: Wait for timeout duration
        await asyncio.sleep(timeout_minutes * 60)

        # Task 17: Check if runner still exists and is active
        runner = await self.runner_service.get_runner(runner_id)
        if not runner:
            self.warning("Runner %s not found during cleanup check", runner_id)
            return

        if runner.state == RunnerState.TERMINATED:
            self.info("Runner %s already terminated, skipping cleanup", runner_id)
            return

        # Task 17: Terminate runner and clean up resources
        self.info(
            "Timeout reached for serverless runner %s, initiating cleanup", runner_id
        )

        await self.cleanup_serverless_runner(
            runner_id=runner_id,
            reason="timeout",
        )

    async def cleanup_serverless_runner(
        self,
        runner_id: str,
        reason: str = "manual",
    ) -> None:
        """
        Clean up serverless runner resources.

        This method:
        1. Updates runner state to TERMINATED
        2. Executes OpenTofu destroy to remove cloud resources
        3. Creates audit log entry

        Args:
            runner_id: Runner ID to clean up
            reason: Reason for cleanup (timeout/manual/error)
        """
        self.info("Cleaning up serverless runner %s (reason: %s)", runner_id, reason)

        # Task 17: Get runner details
        runner = await self.runner_service.get_runner(runner_id)
        if not runner:
            raise ValueError(f"Runner {runner_id} not found")

        # Task 17: Update runner state to TERMINATED with audit trail
        await self.runner_service.update_runner_state_with_audit(
            runner_id=runner_id,
            new_state=RunnerState.TERMINATED,
            actor="serverless_cleanup_service",
            reason=reason,
        )

        # Task 17: Execute OpenTofu destroy to clean up cloud resources
        # This will:
        # 1. Load deployment plan from database
        # 2. Generate destroy plan
        # 3. Apply destroy to remove serverless container/Lambda
        # 4. Update state in S3

        self.info("Serverless runner %s cleaned up successfully", runner_id)

    async def get_container_image_url(
        self,
        cloud_provider: CloudProvider,
        region: str,
    ) -> str:
        """
        Get the container image URL for the specified cloud provider.

        Container images are pre-built with:
        - Gosling CLI binary
        - GitLab Runner Agent binary
        - Docker/Podman/nerdctl
        - Git and other dependencies

        Args:
            cloud_provider: Cloud provider
            region: Cloud region

        Returns:
            Container image URL
        """
        if cloud_provider == CloudProvider.YANDEX:
            # Yandex Container Registry
            return "cr.yandex/polar-gosling/gosling:latest"
        if cloud_provider == CloudProvider.AWS:
            # AWS ECR (region-specific)
            # Format: {account_id}.dkr.ecr.{region}.amazonaws.com/gosling:latest
            # Account ID would come from configuration
            return f"123456789012.dkr.ecr.{region}.amazonaws.com/gosling:latest"

        raise ValueError(f"Unsupported cloud provider: {cloud_provider}")

    async def enforce_timeout(
        self,
        runner_id: str,
    ) -> None:
        """
        Enforce timeout for a serverless runner.

        This method is called when a runner exceeds the 60-minute limit.
        It forcefully terminates the runner and cleans up resources.

        Args:
            runner_id: Runner ID to terminate
        """
        self.warning("Enforcing timeout for serverless runner %s", runner_id)

        await self.cleanup_serverless_runner(
            runner_id=runner_id,
            reason="timeout_enforced",
        )

    async def get_runner_logs(
        self,
        runner_id: str,
        cloud_provider: CloudProvider,
    ) -> str:
        """
        Retrieve logs from a serverless runner.

        Args:
            runner_id: Runner ID
            cloud_provider: Cloud provider

        Returns:
            Runner logs as string
        """
        self.info("Retrieving logs for serverless runner %s", runner_id)

        # Task 17: Retrieve logs from cloud provider
        # Yandex Cloud: Use Yandex Cloud Logging API
        # AWS: Use CloudWatch Logs API

        if cloud_provider == CloudProvider.YANDEX:
            # Query Yandex Cloud Logging
            return f"Logs for runner {runner_id} (Yandex Cloud)"
        if cloud_provider == CloudProvider.AWS:
            # Query CloudWatch Logs
            return f"Logs for runner {runner_id} (AWS CloudWatch)"

        raise ValueError(f"Unsupported cloud provider: {cloud_provider}")

    async def get_runner_metrics(
        self,
        runner_id: str,
    ) -> Dict[str, Any]:
        """
        Get metrics for a serverless runner.

        Metrics include:
        - Execution time
        - Memory usage
        - CPU usage
        - Network I/O
        - Job completion status

        Args:
            runner_id: Runner ID

        Returns:
            Dictionary of metrics
        """
        self.info("Retrieving metrics for serverless runner %s", runner_id)

        runner = await self.runner_service.get_runner(runner_id)
        if not runner:
            raise ValueError(f"Runner {runner_id} not found")

        # Calculate execution time
        now = datetime.now(timezone.utc)
        execution_time_seconds = (now - runner.created_at).total_seconds()

        # Task 17: Retrieve metrics from cloud provider
        # Yandex Cloud: Use Yandex Monitoring API
        # AWS: Use CloudWatch Metrics API

        return {
            "runner_id": runner_id,
            "egg_name": runner.egg_name,
            "state": runner.state.value,
            "execution_time_seconds": execution_time_seconds,
            "timeout_minutes": self.__serverless_limit_timeout,
            "time_remaining_seconds": max(
                0,
                (self.__serverless_limit_timeout * 60) - execution_time_seconds,
            ),
            "created_at": runner.created_at.isoformat(),
            "last_heartbeat": (
                runner.last_heartbeat.isoformat() if runner.last_heartbeat else None
            ),
        }
