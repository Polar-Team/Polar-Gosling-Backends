"""
Multi-Cloud Consistency Service

Provides cloud-agnostic runner behaviour validation and deployment configuration
normalisation across Yandex Cloud and AWS.

Requirement 9.8: The system SHALL maintain consistent runner behaviour across
cloud providers.  Any Egg configuration must produce structurally equivalent
deployment parameters regardless of the target cloud.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.model.runners_models import CloudProvider, EggConfig, RunnerType
from app.util.base_logging import logger

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Serverless runner timeout is identical on both clouds (Requirement 5.2)
SERVERLESS_TIMEOUT_MINUTES: int = 60

# Job runner timeout is identical on both clouds (Requirement 13.5)
JOB_RUNNER_TIMEOUT_MINUTES: int = 10

# Supported cloud providers
SUPPORTED_PROVIDERS: List[CloudProvider] = [CloudProvider.YANDEX, CloudProvider.AWS]

# Provider names used in OpenTofu configurations (informational only)
_PROVIDER_NAMES: Dict[CloudProvider, str] = {
    CloudProvider.YANDEX: "yandex",
    CloudProvider.AWS: "aws",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
# pylint: disable=too-many-instance-attributes
class RunnerDeploymentConfig:
    """
    Cloud-agnostic deployment configuration derived from an Egg config.

    This is the normalised view that must be structurally equivalent for
    both Yandex Cloud and AWS deployments of the same Egg.
    """

    egg_name: str
    runner_type: RunnerType
    timeout_minutes: int
    tags: List[str]
    concurrent: int
    cloud_provider: CloudProvider
    region: str
    provider_name: str
    backend_bucket: str
    backend_key: str
    backend_region: str


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class MultiCloudConsistencyService:
    """
    Service that enforces cloud-agnostic runner behaviour.

    Responsibilities:
    - Build normalised RunnerDeploymentConfig for any (Egg, cloud) pair.
    - Validate that two configs for the same Egg on different clouds are
      structurally equivalent (same runner type, timeout, tags, concurrency).
    - Build TofuSetting objects for both clouds from a single Egg config.
    """

    # pylint: disable=too-few-public-methods

    # Default regions per cloud
    _DEFAULT_REGIONS: Dict[CloudProvider, str] = {
        CloudProvider.YANDEX: "ru-central1-a",
        CloudProvider.AWS: "us-east-1",
    }

    def build_deployment_config(
        self,
        egg_config: EggConfig,
        cloud_provider: CloudProvider,
        region: Optional[str] = None,
        state_bucket: str = "mothergoose-tofu-state",
    ) -> RunnerDeploymentConfig:
        """
        Build a normalised deployment config for the given Egg and cloud.

        Args:
            egg_config: Parsed Egg configuration from the Nest repository.
            cloud_provider: Target cloud provider.
            region: Override region; defaults to the cloud's default region.
            state_bucket: S3/YOS bucket name for OpenTofu state.

        Returns:
            RunnerDeploymentConfig with cloud-agnostic fields populated.

        Raises:
            ValueError: If cloud_provider is not supported.
        """
        if cloud_provider not in SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported cloud provider: {cloud_provider}. "
                f"Supported: {[p.value for p in SUPPORTED_PROVIDERS]}"
            )

        cfg: Dict[str, Any] = egg_config.config or {}
        runner_cfg: Dict[str, Any] = cfg.get("runner", {})

        # Determine runner type from explicit config or default to serverless
        explicit_type = cfg.get("type")
        if explicit_type == "vm":
            runner_type = RunnerType.APEX
        elif explicit_type == "serverless":
            runner_type = RunnerType.SERVERLESS
        else:
            runner_type = RunnerType.SERVERLESS

        # Timeout is always cloud-agnostic
        timeout = (
            JOB_RUNNER_TIMEOUT_MINUTES
            if cfg.get("is_job_runner", False)
            else SERVERLESS_TIMEOUT_MINUTES
        )

        effective_region = region or self._DEFAULT_REGIONS[cloud_provider]
        provider_name = _PROVIDER_NAMES[cloud_provider]

        logger.debug(
            "Built deployment config for Egg '%s' on %s: type=%s, timeout=%d",
            egg_config.name,
            cloud_provider.value,
            runner_type.value,
            timeout,
        )

        return RunnerDeploymentConfig(
            egg_name=egg_config.name,
            runner_type=runner_type,
            timeout_minutes=timeout,
            tags=list(runner_cfg.get("tags", [])),
            concurrent=int(runner_cfg.get("concurrent", 1)),
            cloud_provider=cloud_provider,
            region=effective_region,
            provider_name=provider_name,
            backend_bucket=state_bucket,
            backend_key=f"{egg_config.name}/terraform.tfstate",
            backend_region=effective_region,
        )

    def assert_equivalent(
        self,
        yandex_config: RunnerDeploymentConfig,
        aws_config: RunnerDeploymentConfig,
    ) -> None:
        """
        Assert that two deployment configs for the same Egg are equivalent.

        Checks all cloud-agnostic fields: runner_type, timeout_minutes, tags,
        concurrent, egg_name, backend_key.  Cloud-specific fields (region,
        provider_name, cloud_provider) are intentionally excluded.

        Args:
            yandex_config: Config built for Yandex Cloud.
            aws_config: Config built for AWS.

        Raises:
            AssertionError: If any cloud-agnostic field differs.
        """
        assert (
            yandex_config.egg_name == aws_config.egg_name
        ), f"egg_name mismatch: {yandex_config.egg_name!r} vs {aws_config.egg_name!r}"
        assert yandex_config.runner_type == aws_config.runner_type, (
            f"runner_type mismatch for Egg '{yandex_config.egg_name}': "
            f"{yandex_config.runner_type.value} vs {aws_config.runner_type.value}"
        )
        assert yandex_config.timeout_minutes == aws_config.timeout_minutes, (
            f"timeout_minutes mismatch for Egg '{yandex_config.egg_name}': "
            f"{yandex_config.timeout_minutes} vs {aws_config.timeout_minutes}"
        )
        assert sorted(yandex_config.tags) == sorted(aws_config.tags), (
            f"tags mismatch for Egg '{yandex_config.egg_name}': "
            f"{yandex_config.tags} vs {aws_config.tags}"
        )
        assert yandex_config.concurrent == aws_config.concurrent, (
            f"concurrent mismatch for Egg '{yandex_config.egg_name}': "
            f"{yandex_config.concurrent} vs {aws_config.concurrent}"
        )
        assert yandex_config.backend_key == aws_config.backend_key, (
            f"backend_key mismatch for Egg '{yandex_config.egg_name}': "
            f"{yandex_config.backend_key!r} vs {aws_config.backend_key!r}"
        )
