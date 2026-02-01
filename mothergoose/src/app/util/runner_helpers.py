"""
Runner Helpers Utility

Provides reusable helper functions for runner provisioning and configuration.
This eliminates code duplication across services and tasks.
"""

# pylint: disable=duplicate-code

from typing import Any, Dict, Optional

from app.model.runners_models import CloudProvider, Runner, RunnerType


def extract_runner_config(
    runner_config: Dict[str, Any],
) -> tuple[Dict[str, Any], CloudProvider, str, str]:
    """
    Extract and normalize runner configuration parameters.

    Args:
        runner_config: Runner configuration dictionary containing:
            - job_requirements: Job requirements from GitLab webhook
            - cloud_provider: Cloud provider (yandex/aws)
            - region: Cloud region
            - deployed_from_commit: Git commit hash

    Returns:
        Tuple of (job_requirements, cloud_provider, region, deployed_from_commit)
    """
    job_requirements = runner_config.get("job_requirements", {})
    cloud_provider_str = runner_config.get("cloud_provider", "yandex")
    region = runner_config.get("region", "ru-central1-a")
    deployed_from_commit = runner_config.get("deployed_from_commit", "unknown")

    # Convert cloud provider string to enum
    cloud_provider = CloudProvider(cloud_provider_str)

    return job_requirements, cloud_provider, region, deployed_from_commit


def build_deployment_kwargs(
    egg_name: str,
    cloud_provider: CloudProvider,
    region: str,
    deployed_from_commit: str,
    job_requirements: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build keyword arguments for serverless runner deployment.

    This helper eliminates duplicate code when calling deploy_serverless_runner.

    Args:
        egg_name: Name of the Egg requesting the runner
        cloud_provider: Cloud provider to deploy to
        region: Cloud region for deployment
        deployed_from_commit: Git commit hash triggering deployment
        job_requirements: Optional job requirements for customization

    Returns:
        Dictionary of keyword arguments for deploy_serverless_runner
    """
    return {
        "egg_name": egg_name,
        "cloud_provider": cloud_provider,
        "region": region,
        "deployed_from_commit": deployed_from_commit,
        "job_requirements": job_requirements,
    }


def build_runner_result(
    task_id: str,
    egg_name: str,
    runner: Runner,
    message: str = "Runner deployed successfully",
) -> Dict[str, Any]:
    """
    Build standardized result dictionary for runner deployment tasks.

    Args:
        task_id: Celery task ID
        egg_name: Name of the Egg
        runner: Deployed runner instance
        message: Success message

    Returns:
        Standardized result dictionary
    """
    return {
        "status": "success",
        "task_id": task_id,
        "egg_name": egg_name,
        "runner_id": runner.id,
        "runner_type": runner.type.value,
        "cloud_provider": runner.cloud_provider.value,
        "region": runner.region,
        "message": message,
    }


class RunnerProvisioningParams:
    """
    Encapsulates runner provisioning parameters to reduce duplication.
    """

    def __init__(
        self,
        egg_name: str,
        cloud_provider: CloudProvider,
        region: str,
        deployed_from_commit: str,
        job_requirements: Optional[Dict[str, Any]] = None,
    ):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """
        Initialize runner provisioning parameters.

        Args:
            egg_name: Name of the Egg requesting the runner
            cloud_provider: Cloud provider to deploy to
            region: Cloud region for deployment
            deployed_from_commit: Git commit hash triggering deployment
            job_requirements: Optional job requirements for customization
        """
        self.egg_name = egg_name
        self.cloud_provider = cloud_provider
        self.region = region
        self.deployed_from_commit = deployed_from_commit
        self.job_requirements = job_requirements or {}

    @classmethod
    def from_config(
        cls, egg_name: str, runner_config: Dict[str, Any]
    ) -> "RunnerProvisioningParams":
        """
        Create provisioning parameters from runner config dictionary.

        Args:
            egg_name: Name of the Egg requesting the runner
            runner_config: Runner configuration dictionary

        Returns:
            RunnerProvisioningParams instance
        """
        (
            job_requirements,
            cloud_provider,
            region,
            deployed_from_commit,
        ) = extract_runner_config(runner_config)

        return cls(
            egg_name=egg_name,
            cloud_provider=cloud_provider,
            region=region,
            deployed_from_commit=deployed_from_commit,
            job_requirements=job_requirements,
        )

    def to_provision_kwargs(self, runner_type: RunnerType) -> Dict[str, Any]:
        """
        Convert to keyword arguments for provision_runner method.

        Args:
            runner_type: Type of runner to provision

        Returns:
            Dictionary of keyword arguments
        """
        return {
            "egg_name": self.egg_name,
            "runner_type": runner_type,
            "cloud_provider": self.cloud_provider,
            "region": self.region,
            "deployed_from_commit": self.deployed_from_commit,
            "job_requirements": self.job_requirements,
        }
