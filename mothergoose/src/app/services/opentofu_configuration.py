"""OpenTofuConfiguration class for managing OpenTofu configurations."""

import tempfile

# from os import symlink
from typing import Union, Literal, Dict
from accessify import private

from tofupy import Tofu

from app.util.logging import logged
from app.services.download_and_update_opentofu_binary import (
    OpenTofuUpdateGithub,
    OpenTofuUpdateOtherSource,
)


@logged
class OpenTofuConfiguration:
    """Class for OpenTofu configuration management."""

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

    @private
    def __update_opentofu_binaries(self) -> str:
        """Create OpenTofu configuration from templates."""

        self.updater.start_update()

    @private
    def __create_tofu_configuration_from_templates(self) -> str:
        """Create OpenTofu configuration from templates."""

    def setup_topfu_configuration(self) -> None:
        """Set up OpenTofu configuration.

        Args:
            config_path (str): Path to the OpenTofu configuration file.
        """

        self.__create_tofu_configuration_from_templates()

        self.tofu.binary_path = self.__binary_path
        self.tofu.log_level = self.__log_level
        self.tofu.env = self.__env
        self.tofu.cwd = self.__tofu_workspace
