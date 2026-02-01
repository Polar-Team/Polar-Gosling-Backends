"""OpenTofuConfiguration class for managing OpenTofu configurations."""

import os
import platform
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Union

from accessify import private
from jinja2 import Environment, FileSystemLoader
from tofupy import Tofu

from app.schema.tofu_schemas import TofuBackendS3Options, TofuProvidersVer
from app.schema.url_schemas import URLAuthSchema
from app.util.base_logging import logged

from .opentofu_binary import OpenTofuUpdateGithub, OpenTofuUpdateOtherSource
from .s3_artifact_cache import S3ArtifactCache


@dataclass
class TofuSetting:  # pylint: disable=too-many-instance-attributes
    """
    Tofu settings dataclass for OpenTofu configuration.
    Attributes:
        providers (List[OpenTofuProvidersConstraints]): List of provider constraints. # noqa
        backend_s3_options (OpenTofuBackendS3Options): S3 backend options.
        artifact_cache_bucket (Optional[str]): S3 bucket for artifact caching.
        health_checks (Optional[List[Dict]]): Health check configurations.
        worker_module_source (Dict[str, str]): Worker module source (git or registry).
        worker_instances (Dict[str, Dict]): Map of worker instance configurations.
        rift_module_source (Optional[Dict[str, str]]): Rift module source (optional).
        rift_instances (Optional[Dict[str, Dict]]): Map of rift instance configurations.
        vm_key_algorithm (str): SSH key algorithm for VMs.
        vm_key_rsa_bits (int): RSA key bits for SSH keys.
        rift_required (bool): Whether Rift server is required.
        worker_module_extra_variables (Optional[Dict[str, str]]): Extra variables for worker module.
    """

    providers: List[TofuProvidersVer]
    backend_s3_options: TofuBackendS3Options
    worker_module_source: Dict[str, str]
    worker_instances: Dict[str, Dict]
    vm_key_algorithm: str = "RSA"
    vm_key_rsa_bits: int = 4096
    artifact_cache_bucket: Optional[str] = None
    health_checks: Optional[List[Dict]] = None
    rift_module_source: Optional[Dict[str, str]] = None
    rift_instances: Optional[Dict[str, Dict]] = None
    rift_required: bool = False
    worker_module_extra_variables: Optional[Dict[str, str]] = None


@dataclass
class TofuRuntimeConfig:
    """Runtime configuration for OpenTofu execution."""

    binary_path: str = "/usr/local/bin/tofu"
    log_level: Literal["TRACE", "INFO", "ERROR", "DEBUG", "WARN"] = "ERROR"
    env: Optional[Dict[str, str]] = None
    workspace: Optional[str] = None

    def __post_init__(self) -> None:
        """Initialize default values."""
        if self.env is None:
            self.env = {}
        if self.workspace is None:
            self.workspace = tempfile.mkdtemp(prefix="mothergoose_opentofu_workspace_")


@dataclass
class TofuUpdaterConfig:
    """Configuration for OpenTofu binary updater."""

    rollback: bool = False
    auth_url: Optional[URLAuthSchema] = None
    rollback_factor: int = 3


@logged
class OpenTofuConfiguration:
    """Class for OpenTofu configuration management."""

    # pylint: disable=no-member

    def __init__(
        self,
        updater: Union[
            OpenTofuUpdateGithub,
            OpenTofuUpdateOtherSource,
        ],
        tofu_settings: TofuSetting,
        artifact_cache: Optional[S3ArtifactCache] = None,
    ) -> None:
        self.updater = updater
        self.tofu = Tofu()
        self.tofu_settings = tofu_settings
        self.artifact_cache = artifact_cache
        self.runtime_config = TofuRuntimeConfig()
        self.updater_config = TofuUpdaterConfig()

        # Initialize artifact cache if bucket is provided
        if artifact_cache is None and tofu_settings.artifact_cache_bucket:
            self.artifact_cache = S3ArtifactCache(
                bucket_name=tofu_settings.artifact_cache_bucket,
                region=tofu_settings.backend_s3_options.region,
                endpoint_url=tofu_settings.backend_s3_options.endpoint,
            )

    @property
    def binary_path(self) -> str:
        """Get the path to the OpenTofu binary.

        Returns:
            str: Path to the OpenTofu binary.
        """
        return self.runtime_config.binary_path

    @binary_path.setter
    def binary_path(self, path: str) -> None:
        """Set the path to the OpenTofu binary.

        Args:
            path (str): Path to set for the OpenTofu binary.
        """
        self.runtime_config.binary_path = path

    @property
    def log_level(self) -> str:
        """Get the OpenTofu log level.

        Returns:
            str: OpenTofu log level.
        """
        return self.runtime_config.log_level

    @log_level.setter
    def log_level(
        self,
        level: Literal[
            "TRACE",
            "INFO",
            "ERROR",
            "DEBUG",
            "WARN",
        ],
    ) -> None:
        """Set the OpenTofu log level.

        Args:
            level (str): Log level to set for OpenTofu.
        """
        self.runtime_config.log_level = level

    @property
    def env(self) -> Dict[str, str]:
        """Get the OpenTofu environment variables.

        Returns:
            Dict[str, str]: OpenTofu environment variables.
        """
        return self.runtime_config.env or {}

    @env.setter
    def env(self, environment: Dict[str, str]) -> None:
        """Set the OpenTofu environment variables.

        Args:
            environment (Dict[str, str]): Environment variables to pass.
        """
        self.runtime_config.env = environment

    @property
    def updater_rollback(self) -> bool:
        """Get the updater rollback status.

        Returns:
            bool: Updater rollback status.
        """
        return self.updater_config.rollback

    @updater_rollback.setter
    def updater_rollback(self, rollback: bool) -> None:
        """Set the updater rollback status.

        Args:
            rollback (bool): Updater rollback status to set.
        """
        self.updater_config.rollback = rollback

    @property
    def updater_auth_url(self) -> Optional[URLAuthSchema]:
        """Get the updater authentication URL.

        Returns:
            Optional[URLAuthSchema]: Updater authentication URL.
        """
        return self.updater_config.auth_url

    @updater_auth_url.setter
    def updater_auth_url(self, auth_url: URLAuthSchema) -> None:
        """Set the updater authentication URL.

        Args:
            auth_url (URLAuthSchema): Updater authentication URL to set.
        """
        self.updater_config.auth_url = auth_url

    @property
    def updater_rollback_factor(self) -> int:
        """Get the updater rollback factor.

        Returns:
            int: Updater rollback factor.
        """
        return self.updater_config.rollback_factor

    @updater_rollback_factor.setter
    def updater_rollback_factor(self, factor: int) -> None:
        """Set the updater rollback factor.

        Args:
            factor (int): Updater rollback factor to set.
        """
        if 1 <= factor <= 3:
            raise ValueError("Rollback factor must be between 1 and 3")
        self.updater_config.rollback_factor = factor

    @private
    def __update_opentofu_binaries(self) -> str:
        """Create OpenTofu configuration from templates."""

        if self.updater.c_version[0] == "dummy_id":
            self.info("Updating OpenTofu binary...")
            if isinstance(self.updater, OpenTofuUpdateOtherSource):
                self.info("Using other source to update OpenTofu binary...")
                self.updater.rollback = self.updater_config.rollback
                self.updater.start_update(auth_url=self.updater_config.auth_url)
            else:
                self.info("Using GitHub to update OpenTofu binary...")
                self.updater.start_update(
                    rb=self.updater_config.rollback_factor,
                )
        return self.updater.c_version[1]

    @private
    def __render_versions_tf(self, template_env: Environment) -> None:
        """Render versions.tf configuration file."""
        template = template_env.get_template("tofu_versions_tf.j2")
        version_tf_output = template.render(
            tofu_version=self.updater.c_version[1],
            tofu_s3_bucket=self.tofu_settings.backend_s3_options.bucket,
            tofu_s3_key=self.tofu_settings.backend_s3_options.key,
            tofu_s3_region=self.tofu_settings.backend_s3_options.region,
            tofu_s3_profile=self.tofu_settings.backend_s3_options.profile,
            tofu_s3_endpoint=self.tofu_settings.backend_s3_options.endpoint,
            tofu_s3_role_arn=self.tofu_settings.backend_s3_options.role_arn,
            tofu_s3_dynamodb_table=self.tofu_settings.backend_s3_options.dynamodb_table,
            tofu_providers=self.tofu_settings.providers,
        )
        version_tf_path = f"{self.runtime_config.workspace}/versions.tf"
        with open(version_tf_path, "w", encoding="utf-8") as version_tf_file:
            version_tf_file.write(version_tf_output)
        self.info(f"OpenTofu configuration created at {version_tf_path}")

    @private
    def __render_providers_tf(self, template_env: Environment) -> None:
        """Render providers.tf configuration file."""
        self.info("Creating providers configuration...")
        providers_template = template_env.get_template("tofu_providers_tf.j2")
        providers_tf_output = providers_template.render(
            tofu_providers=self.tofu_settings.providers,
        )
        providers_tf_path = f"{self.runtime_config.workspace}/providers.tf"
        with open(providers_tf_path, "w", encoding="utf-8") as providers_tf_file:
            providers_tf_file.write(providers_tf_output)
        self.info(f"Providers configuration created at {providers_tf_path}")

    @private
    def __render_resources_tf(self, template_env: Environment) -> None:
        """Render resources.tf configuration file."""
        self.info("Creating resources configuration...")
        resources_template = template_env.get_template("tofu_resources_tf.j2")
        resources_tf_output = resources_template.render(
            tofu_vm_key_algorithm=self.tofu_settings.vm_key_algorithm,
            tofu_vm_key_rsa_bits=self.tofu_settings.vm_key_rsa_bits,
            tofu_worker_module_source=self.tofu_settings.worker_module_source,
            tofu_worker_module_extra_variables=(
                self.tofu_settings.worker_module_extra_variables or {}
            ),
            tofu_rift_required=self.tofu_settings.rift_required,
            tofu_rift_module_source=self.tofu_settings.rift_module_source,
        )
        resources_tf_path = f"{self.runtime_config.workspace}/resources.tf"
        with open(resources_tf_path, "w", encoding="utf-8") as resources_tf_file:
            resources_tf_file.write(resources_tf_output)
        self.info(f"Resources configuration created at {resources_tf_path}")

    @private
    def __render_variables_tf(self, template_env: Environment) -> None:
        """Render variables.tf configuration file."""
        self.info("Creating variables configuration...")
        variables_template = template_env.get_template("tofu_variables_tf.j2")
        variables_tf_output = variables_template.render(
            tofu_rift_required=self.tofu_settings.rift_required,
        )
        variables_tf_path = f"{self.runtime_config.workspace}/variables.tf"
        with open(variables_tf_path, "w", encoding="utf-8") as variables_tf_file:
            variables_tf_file.write(variables_tf_output)
        self.info(f"Variables configuration created at {variables_tf_path}")

    @private
    def __render_data_tf(self, template_env: Environment) -> None:
        """Render data.tf configuration file."""
        self.info("Creating data configuration...")
        data_template = template_env.get_template("tofu_data_tf.j2")
        data_tf_output = data_template.render()
        data_tf_path = f"{self.runtime_config.workspace}/data.tf"
        with open(data_tf_path, "w", encoding="utf-8") as data_tf_file:
            data_tf_file.write(data_tf_output)
        self.info(f"Data configuration created at {data_tf_path}")

    @private
    def __render_tofurc(self, template_env: Environment) -> None:
        """Render .tofurc configuration file."""
        self.info("Creating .tofurc configuration...")
        tofurc_template = template_env.get_template("tofu_rc.j2")
        tofurc_output = tofurc_template.render(
            mirrors=[],
            direct_exclude=False,
        )
        tofurc_path = f"{self.runtime_config.workspace}/.tofurc"
        with open(tofurc_path, "w", encoding="utf-8") as tofurc_file:
            tofurc_file.write(tofurc_output)
        self.info(f".tofurc configuration created at {tofurc_path}")

    @private
    def __render_health_checks_tf(self, template_env: Environment) -> None:
        """Render health checks configuration file if configured."""
        if not self.tofu_settings.health_checks:
            return

        self.info("Creating health checks configuration...")
        checks_template = template_env.get_template("tofu_checks_tf.j2")
        health_check_url = (
            self.tofu_settings.health_checks[0].get("url", "")
            if self.tofu_settings.health_checks
            else ""
        )
        checks_tf_output = checks_template.render(
            health_checks=self.tofu_settings.health_checks,
            health_check_url=health_check_url,
        )
        checks_tf_path = f"{self.runtime_config.workspace}/checks.tf"
        with open(checks_tf_path, "w", encoding="utf-8") as checks_tf_file:
            checks_tf_file.write(checks_tf_output)
        self.info(f"Health checks configuration created at {checks_tf_path}")

    @private
    def __create_tofu_configuration_from_templates(self) -> None:
        """Create OpenTofu configuration from templates."""
        self.info("Creating OpenTofu configuration from templates...")
        template_loader = FileSystemLoader(searchpath="./templates")
        template_env = Environment(loader=template_loader)

        # Render all configuration files
        self.__render_versions_tf(template_env)
        self.__render_providers_tf(template_env)
        self.__render_resources_tf(template_env)
        self.__render_variables_tf(template_env)
        self.__render_data_tf(template_env)
        self.__render_tofurc(template_env)
        self.__render_health_checks_tf(template_env)

    def setup_topfu_configuration(self) -> None:
        """Set up OpenTofu configuration.

        Args:
            config_path (str): Path to the OpenTofu configuration file.
        """

        self.__update_opentofu_binaries()
        self.__create_tofu_configuration_from_templates()

        self.tofu.binary_path = self.runtime_config.binary_path
        self.tofu.log_level = self.runtime_config.log_level
        self.tofu.env = self.runtime_config.env or {}
        self.tofu.cwd = self.runtime_config.workspace or tempfile.mkdtemp(
            prefix="mothergoose_opentofu_workspace_"
        )

    # pylint: disable=too-many-arguments,too-many-positional-arguments
    def generate_cloud_init_script(
        self,
        runner_id: str,
        egg_name: str,
        mothergoose_api_url: str,
        admin_ssh_key: str,
        gosling_binary_url: str,
        s3_cache_bucket: Optional[str] = None,
        custom_commands: Optional[List[str]] = None,
        environment_vars: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Generate cloud-init script for VM runner deployment.

        NOTE: OpenTofu runs on MotherGoose backend (in Celery tasks), NOT on runners.
        Runners only need Gosling CLI to manage GitLab Runner Agent lifecycle.

        Args:
            runner_id: Unique runner identifier
            egg_name: Egg name
            mothergoose_api_url: MotherGoose API URL
            admin_ssh_key: SSH public key for admin user
            gosling_binary_url: URL to download Gosling CLI binary
            s3_cache_bucket: S3 bucket for GitLab Runner cache (optional)
            custom_commands: Additional commands to run (optional)
            environment_vars: Additional environment variables (optional)

        Returns:
            Cloud-init script as YAML string
        """
        self.info(f"Generating cloud-init script for runner {runner_id}")

        template_loader = FileSystemLoader(searchpath="./templates")
        template_env = Environment(loader=template_loader)

        template = template_env.get_template("cloud-init-runner.tpl.j2")

        cloud_init_output = template.render(
            runner_id=runner_id,
            egg_name=egg_name,
            mothergoose_api_url=mothergoose_api_url,
            admin_ssh_key=admin_ssh_key,
            gosling_binary_url=gosling_binary_url,
            s3_cache_bucket=s3_cache_bucket,
            custom_commands=custom_commands or [],
            environment_vars=environment_vars or {},
        )

        self.info(f"Cloud-init script generated for runner {runner_id}")
        return cloud_init_output

    async def cache_provider_plugins(
        self,
        plugins_dir: str,
    ) -> None:
        """
        Cache provider plugins to S3.

        Args:
            plugins_dir: Directory containing provider plugins
        """
        if not self.artifact_cache:
            self.warning("Artifact cache not configured, skipping plugin caching")
            return

        self.info("Caching provider plugins to S3...")

        for provider in self.tofu_settings.providers:
            # Find plugin file in directory
            plugin_pattern = f"terraform-provider-{provider.name}_"
            for root, _, files in os.walk(plugins_dir):
                for file in files:
                    if file.startswith(plugin_pattern):
                        plugin_path = os.path.join(root, file)
                        await self.artifact_cache.cache_provider_plugin(
                            provider_name=provider.name,
                            provider_version=provider.version,
                            provider_source=provider.source,
                            plugin_path=plugin_path,
                        )

        self.info("Provider plugins cached successfully")

    async def restore_cached_plugins(
        self,
        plugins_dir: str,
    ) -> bool:
        """
        Restore provider plugins from S3 cache.

        Args:
            plugins_dir: Directory to restore plugins to

        Returns:
            True if all plugins were restored from cache, False otherwise
        """
        if not self.artifact_cache or not self.tofu_settings.artifact_cache_bucket:
            self.warning("Artifact cache not configured, skipping plugin restoration")
            return False

        self.info("Restoring provider plugins from S3 cache...")

        all_restored = True

        for provider in self.tofu_settings.providers:
            # Determine plugin filename based on platform
            system = platform.system().lower()
            arch = (
                "amd64"
                if platform.machine().lower() in ("x86_64", "amd64")
                else "arm64"
            )
            plugin_filename = (
                f"terraform-provider-{provider.name}_{provider.version}_{system}_{arch}"
            )

            plugin_path = os.path.join(
                plugins_dir, provider.source, provider.version, plugin_filename
            )

            restored = await self.artifact_cache.get_cached_provider_plugin(
                provider_source=provider.source,
                provider_version=provider.version,
                plugin_filename=plugin_filename,
                download_path=plugin_path,
            )

            if not restored:
                self.warning(
                    f"Plugin {provider.name} v{provider.version} not found in cache"
                )
                all_restored = False

        if all_restored:
            self.info("All provider plugins restored from cache")
        else:
            self.info("Some provider plugins not found in cache, will need to download")

        return all_restored

    async def cache_terraform_directory(
        self,
        egg_name: str,
        terraform_dir: str,
    ) -> None:
        """
        Cache .terraform directory for an Egg to S3.

        Args:
            egg_name: Egg name
            terraform_dir: Path to .terraform directory
        """
        if not self.artifact_cache:
            self.warning("Artifact cache not configured, skipping .terraform caching")
            return

        self.info(f"Caching .terraform directory for {egg_name} to S3...")

        await self.artifact_cache.cache_terraform_dir(
            egg_name=egg_name,
            terraform_dir=terraform_dir,
        )

        self.info(f".terraform directory cached successfully for {egg_name}")

    async def restore_cached_terraform_directory(
        self,
        egg_name: str,
        terraform_dir: str,
    ) -> bool:
        """
        Restore .terraform directory from S3 cache.

        Args:
            egg_name: Egg name
            terraform_dir: Path to restore .terraform directory to

        Returns:
            True if .terraform directory was restored from cache, False otherwise
        """
        if not self.artifact_cache or not self.tofu_settings.artifact_cache_bucket:
            self.warning(
                "Artifact cache not configured, skipping .terraform restoration"
            )
            return False

        self.info(f"Restoring .terraform directory for {egg_name} from S3 cache...")

        restored = await self.artifact_cache.get_cached_terraform_dir(
            egg_name=egg_name,
            download_path=terraform_dir,
        )

        if restored:
            self.info(f".terraform directory restored from cache for {egg_name}")
        else:
            self.info(f".terraform directory not found in cache for {egg_name}")

        return restored

    async def generate_deployment_plan(
        self,
        egg_name: str,
        config_hash: str,
        git_commit: str,
    ) -> tuple[bytes, bool]:
        """
        Generate OpenTofu deployment plan.

        This method:
        1. Runs 'tofu plan' to generate a deployment plan
        2. Returns the plan binary and validation status
        3. Does NOT apply the plan (that's done separately)

        Args:
            egg_name: Egg name
            config_hash: Hash of the Egg configuration
            git_commit: Git commit hash for this deployment

        Returns:
            Tuple of (plan_binary, is_valid)
        """
        self.info(f"Generating deployment plan for {egg_name}")

        # Run tofu plan with subprocess for full control
        # Note: tofupy doesn't support -out and -detailed-exitcode directly
        # We'll need to use subprocess or extend tofupy

        plan_path = f"{self.runtime_config.workspace}/plan.tfplan"

        # Run tofu plan with subprocess for full control
        result = subprocess.run(
            [
                self.runtime_config.binary_path,
                "plan",
                f"-out={plan_path}",
                "-detailed-exitcode",
            ],
            cwd=self.runtime_config.workspace,
            capture_output=True,
            text=True,
            env=self.runtime_config.env,
            check=False,
        )

        # Check if plan is valid
        # Exit code 0 = no changes, 1 = error, 2 = changes present
        is_valid = result.returncode in (0, 2)

        if not is_valid:
            self.error(f"OpenTofu plan failed for {egg_name}: {result.stderr}")
            return b"", False

        # Read plan binary
        with open(plan_path, "rb") as f:
            plan_binary = f.read()

        self.info(
            f"Deployment plan generated for {egg_name} "
            f"(config_hash={config_hash}, git_commit={git_commit})"
        )

        return plan_binary, is_valid

    async def apply_deployment_plan(
        self,
        egg_name: str,
        plan_binary: bytes,
    ) -> bool:
        """
        Apply a validated OpenTofu deployment plan.

        This method:
        1. Writes the plan binary to disk
        2. Runs 'tofu apply' with the plan
        3. Updates state in S3

        Args:
            egg_name: Egg name
            plan_binary: Binary deployment plan from database

        Returns:
            True if apply succeeded, False otherwise
        """
        self.info(f"Applying deployment plan for {egg_name}")

        # Write plan binary to disk
        plan_path = f"{self.runtime_config.workspace}/plan.tfplan"
        with open(plan_path, "wb") as f:
            f.write(plan_binary)

        # Apply the plan using subprocess for full control
        result = subprocess.run(
            [self.runtime_config.binary_path, "apply", plan_path],
            cwd=self.runtime_config.workspace,
            capture_output=True,
            text=True,
            env=self.runtime_config.env,
            check=False,
        )

        if result.returncode != 0:
            self.error(f"OpenTofu apply failed for {egg_name}: {result.stderr}")
            return False

        self.info(f"Deployment plan applied successfully for {egg_name}")
        return True

    async def rollback_deployment(
        self,
        egg_name: str,
        rollback_plan_binary: bytes,
    ) -> bool:
        """
        Rollback to a previous deployment using a stored plan.

        Args:
            egg_name: Egg name
            rollback_plan_binary: Binary rollback plan from database

        Returns:
            True if rollback succeeded, False otherwise
        """
        self.info(f"Rolling back deployment for {egg_name}")

        # Apply the rollback plan
        return await self.apply_deployment_plan(egg_name, rollback_plan_binary)
