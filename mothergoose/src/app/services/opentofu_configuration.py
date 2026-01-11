"""OpenTofuConfiguration class for managing OpenTofu configurations."""

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
class TofuSetting:
    """
    Tofu settings dataclass for OpenTofu configuration.
    Attributes:
        providers (List[OpenTofuProvidersConstraints]): List of provider constraints. # noqa
        backend_s3_options (OpenTofuBackendS3Options): S3 backend options.
        artifact_cache_bucket (Optional[str]): S3 bucket for artifact caching.
        health_checks (Optional[List[Dict]]): Health check configurations.
    """

    providers: List[TofuProvidersVer]
    backend_s3_options: TofuBackendS3Options
    artifact_cache_bucket: Optional[str] = None
    health_checks: Optional[List[Dict]] = None


@logged
class OpenTofuConfiguration:
    """Class for OpenTofu configuration management."""

    # pylint: disable=no-member,too-many-instance-attributes

    __updater_rollback: bool = False
    __updater_auth_url: Optional[URLAuthSchema] = None
    __updater_rollback_factor: int = 3
    __binary_path: str = "/usr/local/bin/tofu"
    __log_level: Literal[
        "TRACE",
        "INFO",
        "ERROR",
        "DEBUG",
        "WARN",
    ] = "ERROR"
    __env: Dict[str, str] = {}
    __tofu_workspace: str = tempfile.mkdtemp(
        prefix="mothergoose_opentofu_workspace_",
    )

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
        return self.__binary_path

    @binary_path.setter
    def binary_path(self, path: str) -> None:
        """Set the path to the OpenTofu binary.

        Args:
            path (str): Path to set for the OpenTofu binary.
        """
        self.__binary_path = path

    @property
    def log_level(self) -> str:
        """Get the OpenTofu log level.

        Returns:
            str: OpenTofu log level.
        """
        return self.__log_level

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
        self.__log_level = level

    @property
    def env(self) -> Dict[str, str]:
        """Get the OpenTofu environment variables.

        Returns:
            Dict[str, str]: OpenTofu environment variables.
        """
        return self.__env

    @env.setter
    def env(self, environment: Dict[str, str]) -> None:
        """Set the OpenTofu environment variables.

        Args:
            environment (Dict[str, str]): Environment variables to pass.
        """
        self.__env = environment

    @property
    def updater_rollback(self) -> bool:
        """Get the updater rollback status.

        Returns:
            bool: Updater rollback status.
        """
        return self.__updater_rollback

    @updater_rollback.setter
    def updater_rollback(self, rollback: bool) -> None:
        """Set the updater rollback status.

        Args:
            rollback (bool): Updater rollback status to set.
        """
        self.__updater_rollback = rollback

    @property
    def updater_auth_url(self) -> Optional[URLAuthSchema]:
        """Get the updater authentication URL.

        Returns:
            Optional[URLAuthSchema]: Updater authentication URL.
        """
        return self.__updater_auth_url

    @updater_auth_url.setter
    def updater_auth_url(self, auth_url: URLAuthSchema) -> None:
        """Set the updater authentication URL.

        Args:
            auth_url (URLAuthSchema): Updater authentication URL to set.
        """
        self.__updater_auth_url = auth_url

    @property
    def updater_rollback_factor(self) -> int:
        """Get the updater rollback factor.

        Returns:
            int: Updater rollback factor.
        """
        return self.__updater_rollback_factor

    @updater_rollback_factor.setter
    def updater_rollback_factor(self, factor: int) -> None:
        """Set the updater rollback factor.

        Args:
            factor (int): Updater rollback factor to set.
        """
        if 1 < factor < 3:
            raise ValueError("Rollback factor must be between 1 and 3")
        self.__updater_rollback_factor = factor

    @private
    def __update_opentofu_binaries(self) -> str:
        """Create OpenTofu configuration from templates."""

        if self.updater.c_version[0] == "dummy_id":
            self.info("Updating OpenTofu binary...")
            if isinstance(self.updater, OpenTofuUpdateOtherSource):
                self.info("Using other source to update OpenTofu binary...")
                self.updater.rollback = self.__updater_rollback
                self.updater.start_update(auth_url=self.__updater_auth_url)
            else:
                self.info("Using GitHub to update OpenTofu binary...")
                self.updater.start_update(
                    rb=self.__updater_rollback_factor,
                )
        return self.updater.c_version[1]

    @private
    def __create_tofu_configuration_from_templates(self) -> None:
        """Create OpenTofu configuration from templates."""

        self.info("Creating OpenTofu configuration from templates...")
        template_loader = FileSystemLoader(
            searchpath="./templates",
        )
        template_env = Environment(loader=template_loader)

        # Render versions.tf
        template = template_env.get_template("tofu_version.j2")

        version_tf_output = template.render(
            tofu_version=self.updater.c_version[1],
            tofu_s3_bucket=self.tofu_settings.backend_s3_options.bucket,
            tofu_s3_key=self.tofu_settings.backend_s3_options.key,
            tofu_s3_region=self.tofu_settings.backend_s3_options.region,
            tofu_s3_profile=self.tofu_settings.backend_s3_options.profile,
            tofu_s3_endpoint=self.tofu_settings.backend_s3_options.endpoint,
            tofu_s3_role_arn=self.tofu_settings.backend_s3_options.role_arn,
            tofu_s3_dynamodb_table=self.tofu_settings.backend_s3_options.dynamodb_table,  # noqa
            tofu_providers=self.tofu_settings.providers,
        )

        version_tf_path = f"{self.__tofu_workspace}/versions.tf"

        with open(version_tf_path, "w", encoding="utf-8") as version_tf_file:
            version_tf_file.write(version_tf_output)

        self.info(f"OpenTofu configuration created at {version_tf_path}")
        
        # Render health checks if configured
        if self.tofu_settings.health_checks:
            self.info("Creating health checks configuration...")
            checks_template = template_env.get_template("tofu_checks_tf.j2")
            
            checks_tf_output = checks_template.render(
                health_checks=self.tofu_settings.health_checks,
                health_check_url=self.tofu_settings.health_checks[0].get("url", "") if self.tofu_settings.health_checks else "",
            )
            
            checks_tf_path = f"{self.__tofu_workspace}/checks.tf"
            
            with open(checks_tf_path, "w", encoding="utf-8") as checks_tf_file:
                checks_tf_file.write(checks_tf_output)
            
            self.info(f"Health checks configuration created at {checks_tf_path}")

    def setup_topfu_configuration(self) -> None:
        """Set up OpenTofu configuration.

        Args:
            config_path (str): Path to the OpenTofu configuration file.
        """

        self.__update_opentofu_binaries()
        self.__create_tofu_configuration_from_templates()

        self.tofu.binary_path = self.__binary_path
        self.tofu.log_level = self.__log_level
        self.tofu.env = self.__env
        self.tofu.cwd = self.__tofu_workspace

    def generate_cloud_init_script(
        self,
        runner_id: str,
        egg_name: str,
        mothergoose_api_url: str,
        admin_ssh_key: str,
        gosling_binary_url: str,
        tofu_version: Optional[str] = None,
        custom_commands: Optional[List[str]] = None,
        environment_vars: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Generate cloud-init script for VM runner deployment.
        
        Args:
            runner_id: Unique runner identifier
            egg_name: Egg name
            mothergoose_api_url: MotherGoose API URL
            admin_ssh_key: SSH public key for admin user
            gosling_binary_url: URL to download Gosling CLI binary
            tofu_version: OpenTofu version (optional)
            custom_commands: Additional commands to run (optional)
            environment_vars: Additional environment variables (optional)
            
        Returns:
            Cloud-init script as YAML string
        """
        self.info(f"Generating cloud-init script for runner {runner_id}")
        
        template_loader = FileSystemLoader(searchpath="./templates")
        template_env = Environment(loader=template_loader)
        
        template = template_env.get_template("cloud-init-runner.yml.j2")
        
        cloud_init_output = template.render(
            runner_id=runner_id,
            egg_name=egg_name,
            mothergoose_api_url=mothergoose_api_url,
            admin_ssh_key=admin_ssh_key,
            gosling_binary_url=gosling_binary_url,
            tofu_version=tofu_version or self.updater.c_version[1],
            tofu_s3_bucket=self.tofu_settings.backend_s3_options.bucket,
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
        
        import os
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
        if not self.artifact_cache:
            self.warning("Artifact cache not configured, skipping plugin restoration")
            return False
        
        self.info("Restoring provider plugins from S3 cache...")
        
        import os
        all_restored = True
        
        for provider in self.tofu_settings.providers:
            # Determine plugin filename based on platform
            import platform
            system = platform.system().lower()
            arch = "amd64" if platform.machine().lower() in ("x86_64", "amd64") else "arm64"
            plugin_filename = f"terraform-provider-{provider.name}_{provider.version}_{system}_{arch}"
            
            plugin_path = os.path.join(plugins_dir, provider.source, provider.version, plugin_filename)
            
            restored = await self.artifact_cache.get_cached_provider_plugin(
                provider_source=provider.source,
                provider_version=provider.version,
                plugin_filename=plugin_filename,
                download_path=plugin_path,
            )
            
            if not restored:
                self.warning(f"Plugin {provider.name} v{provider.version} not found in cache")
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
        if not self.artifact_cache:
            self.warning("Artifact cache not configured, skipping .terraform restoration")
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
