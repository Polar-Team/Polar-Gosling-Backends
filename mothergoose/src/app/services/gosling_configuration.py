"""GoslingConfiguration class for managing the Gosling CLI binary."""

import os
from typing import Optional, Union

from accessify import private

from app.schema.url_schemas import URLAuthSchema
from app.util.base_logging import logged

from .binary_service import UpdateGithub, UpdateOtherSource

# Default GitHub coordinates for the Gosling CLI binary.
_GOSLING_GITHUB_REPO = "Polar-Gosling/gosling"
_GOSLING_BINARY_NAME = "gosling"
_GOSLING_TABLE_NAME = "gosling_version"


@logged
class GoslingConfiguration:
    """Manages the Gosling CLI binary lifecycle (download, update, active path).

    The caller is responsible for wiring binary-specific values into the
    ``UpdateGithub`` / ``UpdateOtherSource`` instance before passing it here.
    Typical usage::

        updater = UpdateGithub(
            schema=ydb_schema,
            github_repo="Polar-Gosling/gosling",
            binary_name="gosling",
            table_name="gosling_version",
        )
        cfg = GoslingConfiguration(updater)
        await cfg.setup_gosling_configuration()
        path = cfg.binary_path   # e.g. "/mnt/gosling_binary/1.2.3/gosling"
    """

    # pylint: disable=no-member

    __updater_rollback: bool = False
    __updater_auth_url: Optional[URLAuthSchema] = None
    __updater_rollback_factor: int = 3
    __binary_path: str = "/usr/local/bin/gosling"

    def __init__(
        self,
        updater: Union[UpdateGithub, UpdateOtherSource],
    ) -> None:
        self.updater = updater

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def binary_path(self) -> str:
        """Path to the active Gosling CLI binary."""
        return self.__binary_path

    @binary_path.setter
    def binary_path(self, path: str) -> None:
        self.__binary_path = path

    @property
    def updater_rollback(self) -> bool:
        """Whether rollback versions should be downloaded during update."""
        return self.__updater_rollback

    @updater_rollback.setter
    def updater_rollback(self, rollback: bool) -> None:
        self.__updater_rollback = rollback

    @property
    def updater_auth_url(self) -> Optional[URLAuthSchema]:
        """Authentication URL schema used when downloading from a private source."""
        return self.__updater_auth_url

    @updater_auth_url.setter
    def updater_auth_url(self, auth_url: URLAuthSchema) -> None:
        self.__updater_auth_url = auth_url

    @property
    def updater_rollback_factor(self) -> int:
        """Number of rollback versions to keep (1–3)."""
        return self.__updater_rollback_factor

    @updater_rollback_factor.setter
    def updater_rollback_factor(self, factor: int) -> None:
        if not 1 <= factor <= 3:
            raise ValueError("Rollback factor must be between 1 and 3")
        self.__updater_rollback_factor = factor

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @private
    async def __update_gosling_binary(self) -> str:
        """Ensure the Gosling binary is up-to-date and return the active version."""
        if self.updater.c_version[0] == "dummy_id":
            self.info("Updating Gosling binary...")
            if isinstance(self.updater, UpdateOtherSource):
                self.info("Using other source to update Gosling binary...")
                self.updater.rollback = self.__updater_rollback
                await self.updater.start_update(auth_url=self.__updater_auth_url)
            else:
                self.info("Using GitHub to update Gosling binary...")
                await self.updater.start_update(rb=self.__updater_rollback_factor)
        return self.updater.c_version[1]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def setup_gosling_configuration(self) -> None:
        """Download / update the Gosling binary and set :attr:`binary_path`.

        After this call, :attr:`binary_path` points to the active binary that
        can be passed to ``FlyParserService`` (or any subprocess call).
        """
        version = await self.__update_gosling_binary()

        # Derive the install directory from the updater when available.
        install_dir: Optional[str] = getattr(self.updater, "install_dir", None)
        if install_dir:
            system_suffix = ".exe" if os.name == "nt" else ""
            candidate = os.path.join(
                install_dir, f"{_GOSLING_BINARY_NAME}{system_suffix}"
            )
            if os.path.exists(candidate):
                self.__binary_path = candidate
                self.info(f"Gosling binary active at {self.__binary_path} (v{version})")
                return

        self.info(
            f"Gosling binary v{version} ready; "
            f"binary_path remains {self.__binary_path}"
        )
