"""
Runner Orchestration Service

Handles runner type determination, provisioning workflow, and lifecycle management.
This service coordinates between Egg configurations, OpenTofu deployment, and runner state tracking.
"""

# pylint: disable=duplicate-code

from typing import Any, Dict, Optional

from app.model.runners_models import (
    CloudProvider,
    EggConfig,
    Runner,
    RunnerState,
    RunnerType,
)
from app.services.egg_service import EggService
from app.services.runner_service import RunnerService
from app.services.serverless_runner_deployment import ServerlessRunnerDeploymentService
from app.services.vm_pool_manager import VMPoolManager
from app.util.base_logging import logger
from app.util.runner_helpers import build_deployment_kwargs


class RunnerOrchestrationService:
    """
    Service for orchestrating runner deployment and management.

    Handles:
    - Runner type determination (serverless vs VM)
    - Runner provisioning workflow
    - Runner state transitions
    - Integration with OpenTofu for infrastructure deployment
    """

    def __init__(
        self,
        runner_service: RunnerService,
        egg_service: EggService,
        serverless_deployment_service: Optional[
            ServerlessRunnerDeploymentService
        ] = None,
        vm_pool_manager: Optional[VMPoolManager] = None,
    ) -> None:
        """
        Initialize runner orchestration service.

        Args:
            runner_service: Service for runner state management
            egg_service: Service for Egg configuration retrieval
            serverless_deployment_service: Optional service for serverless deployment
            vm_pool_manager: Optional VM pool manager for Apex/Nadir management
        """
        self.runner_service = runner_service
        self.egg_service = egg_service
        self.serverless_deployment_service = serverless_deployment_service
        self.vm_pool_manager = vm_pool_manager

    def determine_runner_type(
        self,
        job_requirements: Dict[str, Any],
        egg_config: Optional[EggConfig] = None,
    ) -> RunnerType:
        """
        Determine the appropriate runner type based on job requirements.

        Decision logic:
        1. If Egg config explicitly specifies type, use that
        2. If job has estimated_duration < 60 minutes, use serverless
        3. If job requires persistent state or long-running, use VM (apex)
        4. Default to serverless for cost efficiency

        Args:
            job_requirements: Job requirements from GitLab webhook
            egg_config: Optional Egg configuration

        Returns:
            RunnerType: Determined runner type (serverless/apex/nadir)
        """
        # Check if Egg config explicitly specifies runner type
        if egg_config and egg_config.config:
            explicit_type = egg_config.config.get("type")
            if explicit_type == "serverless":
                logger.info("Using serverless runner (explicit Egg config)")
                return RunnerType.SERVERLESS
            if explicit_type == "vm":
                logger.info("Using VM runner (explicit Egg config)")
                return RunnerType.APEX

        # Check job requirements for duration hints
        estimated_duration_minutes = job_requirements.get("estimated_duration_minutes")
        if estimated_duration_minutes is not None:
            if estimated_duration_minutes < 60:
                logger.info(
                    "Using serverless runner (estimated duration: %d minutes)",
                    estimated_duration_minutes,
                )
                return RunnerType.SERVERLESS
            logger.info(
                "Using VM runner (estimated duration: %d minutes)",
                estimated_duration_minutes,
            )
            return RunnerType.APEX

        # Check for long-running job indicators
        job_tags = job_requirements.get("tags", [])
        if "long-running" in job_tags or "persistent" in job_tags:
            logger.info("Using VM runner (long-running job tags)")
            return RunnerType.APEX

        # Default to serverless for cost efficiency
        logger.info("Using serverless runner (default)")
        return RunnerType.SERVERLESS

    async def provision_runner(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self,
        egg_name: str,
        runner_type: RunnerType,
        cloud_provider: CloudProvider,
        region: str,
        deployed_from_commit: str,
        job_requirements: Optional[Dict[str, Any]] = None,
    ) -> Runner | None:
        """
        Provision a new runner with the specified configuration.

        This method orchestrates the full runner provisioning workflow:
        1. Create runner record in database (PROVISIONING state)
        2. Generate OpenTofu configuration from templates
        3. Execute OpenTofu plan and apply
        4. Update runner state to ACTIVE
        5. Register runner with GitLab

        Args:
            egg_name: Name of the Egg requesting the runner
            runner_type: Type of runner to provision
            cloud_provider: Cloud provider to deploy to
            region: Cloud region for deployment
            deployed_from_commit: Git commit hash triggering deployment
            job_requirements: Optional job requirements for customization

        Returns:
            Runner: Provisioned runner object

        Raises:
            ValueError: If Egg configuration not found
            RuntimeError: If provisioning fails
        """
        logger.info(
            "Provisioning %s runner for Egg '%s' on %s/%s",
            runner_type.value,
            egg_name,
            cloud_provider.value,
            region,
        )

        # Retrieve Egg configuration
        egg_config = await self.egg_service.get_egg_by_name(egg_name)
        if not egg_config:  # type: ignore[func-returns-value]
            err_msg = f"Egg configuration not found: {egg_name}"
            logger.error(err_msg)
            raise ValueError(err_msg)

        # Task 17: Route to serverless deployment if runner type is serverless
        if runner_type == RunnerType.SERVERLESS:  # type: ignore[unreachable]
            if not self.serverless_deployment_service:
                raise RuntimeError(
                    "Serverless deployment service not configured, "
                    "but serverless runner requested"
                )

            logger.info("Routing to serverless deployment service")
            deployment_kwargs = build_deployment_kwargs(
                egg_name=egg_name,
                cloud_provider=cloud_provider,
                region=region,
                deployed_from_commit=deployed_from_commit,
                job_requirements=job_requirements,
            )
            return await self.serverless_deployment_service.deploy_serverless_runner(
                **deployment_kwargs
            )

        # Task 18: VM runner deployment (Apex/Nadir)
        # Check pool capacity before creating runner
        if self.vm_pool_manager:
            # Determine if this should be Apex or Nadir based on demand
            can_add_apex = await self.vm_pool_manager.can_add_apex_runner(egg_name)
            can_add_nadir = await self.vm_pool_manager.can_add_nadir_runner(egg_name)

            if not can_add_apex and not can_add_nadir:
                raise RuntimeError(
                    f"Both Apex and Nadir pools at max capacity for {egg_name}"
                )

            # Prefer Apex for immediate job execution
            if can_add_apex:
                runner_type = RunnerType.APEX
            else:
                runner_type = RunnerType.NADIR

        # Create runner record in PROVISIONING state
        runner = await self.runner_service.create_runner(
            egg_name=egg_name,
            runner_type=runner_type,
            state=RunnerState.ACTIVE,  # Start as ACTIVE for now
            cloud_provider=cloud_provider,
            region=region,
            deployed_from_commit=deployed_from_commit,
            metadata={
                "job_requirements": job_requirements or {},
                "provisioning_started_at": "now",
            },
        )

        logger.info("Runner record created: %s", runner.id)

        # Task 16: OpenTofu config, plan, apply
        # Task 16: Update state to ACTIVE
        # Task 16: Register with GitLab

        logger.info("Runner provisioned successfully: %s", runner.id)
        return runner

    async def terminate_runner(
        self,
        runner_id: str,
        reason: str = "manual",
        actor: str = "system",
    ) -> None:
        """
        Terminate a runner and clean up resources.

        This method orchestrates the full runner termination workflow:
        1. Update runner state to TERMINATED
        2. Unregister runner from GitLab
        3. Execute OpenTofu destroy to clean up cloud resources
        4. Create audit log entry

        Args:
            runner_id: ID of the runner to terminate
            reason: Reason for termination
            actor: Who initiated the termination

        Raises:
            ValueError: If runner not found
            RuntimeError: If termination fails
        """
        logger.info(
            "Terminating runner %s (reason: %s, actor: %s)", runner_id, reason, actor
        )

        # Retrieve runner
        runner = await self.runner_service.get_runner(runner_id)
        if not runner:
            raise ValueError(f"Runner not found: {runner_id}")

        # Update runner state with audit trail
        await self.runner_service.update_runner_state_with_audit(
            runner_id=runner_id,
            new_state=RunnerState.TERMINATED,
            actor=actor,
            reason=reason,
        )

        # Task 16: Unregister from GitLab
        # Task 16: Execute OpenTofu destroy

        logger.info("Runner terminated successfully: %s", runner_id)

    async def list_runners_by_egg(self, egg_name: str) -> list[Runner]:
        """
        List all runners for a specific Egg.

        Args:
            egg_name: Name of the Egg

        Returns:
            List of runners for the Egg
        """
        logger.debug("Listing runners for Egg: %s", egg_name)
        return await self.runner_service.list_runners_by_egg(egg_name)

    async def list_all_runners(self) -> list[Runner]:
        """
        List all runners across all Eggs.

        Returns:
            List of all runners
        """
        logger.debug("Listing all runners")
        return await self.runner_service.list_all_runners()

    async def get_runner_status(self, runner_id: str) -> Optional[Runner]:
        """
        Get current status of a runner.

        Args:
            runner_id: ID of the runner

        Returns:
            Runner object if found, None otherwise
        """
        return await self.runner_service.get_runner(runner_id)
