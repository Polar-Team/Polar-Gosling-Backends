"""OpenTofuConfiguration class for managing OpenTofu configurations."""

import tempfile
from dataclasses import dataclass
from typing import Dict, Literal, Union, Optional, List
from jinja2 import Environment, FileSystemLoader

from accessify import private
from tofupy import Tofu

from app.util.logging import logged
from app.schema.url_schemas import URLAuthSchema
from app.schema.tofu_schemas import (
    OpenTofuBackendS3Options,
    OpenTofuProvidersConstraints,
)

from .opentofu_binary import OpenTofuUpdateGithub, OpenTofuUpdateOtherSource


@dataclass
class TofuSetting:
    providers: List[OpenTofuProvidersConstraints]
    backend_s3_options: OpenTofuBackendS3Options


@logged
class OpenTofuConfiguration:
    """Class for OpenTofu configuration management."""

    # pylint: disable=no-member

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
    ) -> None:
        self.updater = updater
        self.tofu = Tofu()

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
        if factor < 1 and factor > 3:
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
