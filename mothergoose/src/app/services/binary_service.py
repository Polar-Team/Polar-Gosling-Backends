"""
Universal binary download and update service for MotherGoose.

Provides generic base classes used by both OpenTofu and Gosling CLI
binary management. Concrete wrappers live in opentofu_binary.py and
gosling_binary.py respectively.
"""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal, Optional

from accessify import private, protected
from requests import Session

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.schema.binary_schemas import BinFileInfo
from app.schema.dynamodb_schemas import DynamoDBSchema
from app.schema.url_schemas import URLAuthSchema
from app.schema.ydb_schemas import YDBSchema
from app.util.base_logging import logged
from app.util.generator import generate_version_id_decorator
from app.util.requests_session import with_requests_session

__all__ = [
    "BinFileInfo",
    "Binary",
    "DownloadGithub",
    "DownloadFromOtherSource",
    "Update",
    "UpdateGithub",
    "UpdateOtherSource",
]


# ---------------------------------------------------------------------------
# Download base
# ---------------------------------------------------------------------------


class Binary(ABC):
    """Abstract base class for binary download management."""

    # pylint: disable=no-member

    @classmethod
    def get_bin_files_info(cls) -> list[BinFileInfo]:
        """Return the list of downloaded binary file records."""
        return cls._bin_files_info

    @classmethod
    def add_bin_info(cls, info: BinFileInfo) -> None:
        """Append a binary file record to the class-level registry."""
        cls._bin_files_info.append(info)

    @classmethod
    def clear_bin_files_info(cls) -> None:
        """Clear the class-level binary file registry."""
        cls._bin_files_info.clear()

    def _get_current_version(self) -> str:
        """Return the version reported by the installed binary, or '0.0.0'."""
        binary_path = os.path.join(self.install_dir, self._binary_name)
        if not os.path.exists(binary_path):
            self.warning(f"{self._binary_name} binary not found. OK on first run.")
            return "0.0.0"
        process = subprocess.run(
            [binary_path, "--version"], capture_output=True, text=True, check=True
        )
        return process.stdout.strip().split()[-1].replace("v", "")

    def _pre_download_validate(self) -> None:
        """Optional hook for pre-download checks. Default: no-op."""

    def store_downloaded_bin(self) -> tuple[str, str]:
        """Download, extract, copy binary to *install_dir*, return *(version, 'SUCCESS')*."""
        self._pre_download_validate()
        with tempfile.TemporaryDirectory() as tmpdir:
            self._download_and_extract(tmpdir)
            system = platform.system().lower()
            bin_filename = (
                f"{self._binary_name}.exe" if system == "windows" else self._binary_name
            )
            src = os.path.join(tmpdir, bin_filename)
            dst = os.path.join(self.install_dir, bin_filename)
            shutil.copy2(src, dst)
            os.chmod(dst, 0o755)
            self.info(f"Binary stored at {dst}")
            info = self._build_bin_info()
            self.add_bin_info(info)
        version = code = ""
        for entry in self.get_bin_files_info():
            if entry.bin_version == self.version:
                version = entry.bin_version
                code = "SUCCESS"
                break
        return version, code

    @abstractmethod
    def _download_and_extract(self, extract_to: str) -> None:
        """Download and extract the binary archive into *extract_to*."""

    @abstractmethod
    def _build_bin_info(self) -> BinFileInfo:
        """Construct the BinFileInfo record for the current download."""


# ---------------------------------------------------------------------------
# GitHub download
# ---------------------------------------------------------------------------


@logged
@with_requests_session(retries=3, timeout=3)
class DownloadGithub(Binary):  # type: ignore[attr-defined]
    """Generic GitHub-release binary downloader.

    Parameters
    ----------
    version:
        Release version to download (e.g. ``"1.10.3"``).  Defaults to the
        latest release from *github_repo*.
    github_repo:
        ``"owner/repo"`` slug on GitHub (e.g. ``"opentofu/opentofu"``).
    binary_name:
        Name of the extracted binary file (e.g. ``"tofu"`` or ``"gosling"``).
    install_dir:
        Directory where the binary will be stored.  Defaults to
        ``/mnt/<binary_name>_binary/<version>/``.
    """

    # pylint: disable=no-member,too-many-positional-arguments,too-many-arguments

    _github_sha256_hash_of_bundle: dict[str, str] = {}
    _bin_files_info: list[BinFileInfo] = []

    @classmethod
    def clear_sha256_registry(cls) -> None:
        """Clear the class-level SHA256 hash registry."""
        cls._github_sha256_hash_of_bundle.clear()

    def __init__(
        self,
        github_repo: str,
        binary_name: str,
        version: str | None = None,
        install_dir: str | None = None,
    ) -> None:
        self._github_repo = github_repo
        self._binary_name = binary_name
        self.version = version or self._get_latest_version()
        self.install_dir = install_dir or f"/mnt/{binary_name}_binary/{self.version}"
        self.url = self._get_download_url()

    # ------------------------------------------------------------------
    # Class-level helpers
    # ------------------------------------------------------------------

    def _get_latest_version(self) -> str:
        """Fetch the latest release tag from GitHub."""
        url = f"https://api.github.com/repos/{self._github_repo}/releases/latest"
        with self.session.get(url) as response:
            return json.loads(response.content)["tag_name"].lstrip("v")

    @classmethod
    def get_sha256_hash_of_bundle_from_github(
        cls,
        session: Session,
        ver: str,
        system: str,
        arch: str,
        github_repo: str,
        binary_name: str,
    ) -> None:
        """Populate *_github_sha256_hash_of_bundle[ver]* from the GitHub releases API."""
        response = session.get(f"https://api.github.com/repos/{github_repo}/releases")
        data = response.json()
        ext = "zip" if system == "windows" else "tar.gz"
        for release in data:
            if release["tag_name"] == f"v{ver}":
                for asset in release["assets"]:
                    if asset["name"] == f"{binary_name}_{ver}_{system}_{arch}.{ext}":
                        cls._github_sha256_hash_of_bundle[ver] = asset[
                            "digest"
                        ].replace("sha256:", "")

    @property
    def get_packages_sha256_hash(self) -> dict:
        """Return the class-level SHA256 hash registry."""
        return self._github_sha256_hash_of_bundle

    # ------------------------------------------------------------------
    # URL construction
    # ------------------------------------------------------------------

    def _get_download_url(self) -> str:
        """Build the GitHub release asset download URL."""
        ver = self.version
        name = self._binary_name
        repo = self._github_repo
        system = platform.system().lower()
        if system == "linux":
            arch = (
                "amd64"
                if platform.machine().lower() in ("x86_64", "amd64")
                else "arm64"
            )
            return (
                f"https://github.com/{repo}/releases/"
                f"download/v{ver}/{name}_{ver}_{system}_{arch}.tar.gz"
            )
        arch = "amd64"
        return (
            f"https://github.com/{repo}/releases/"
            f"download/v{ver}/{name}_{ver}_{system}_{arch}.zip"
        )

    # ------------------------------------------------------------------
    # SHA256 check
    # ------------------------------------------------------------------

    @private
    def __check_shasum(self, downloaded_sum: str) -> None:
        expected = self.get_packages_sha256_hash.get(self.version)
        if downloaded_sum != expected:
            self.error(
                f"Downloaded file hash {downloaded_sum} does not match "
                f"expected hash {expected}."
            )
            raise RuntimeError("Downloaded file hash does not match.")

    # ------------------------------------------------------------------
    # Download + extract
    # ------------------------------------------------------------------

    @protected
    def _download_and_extract(self, extract_to: str) -> None:
        """Download the release archive and extract it into *extract_to*."""
        ver = self.version
        system = platform.system().lower()
        arch = "amd64"
        if system == "windows":
            self.get_sha256_hash_of_bundle_from_github(
                self.session,
                ver,
                system,
                arch,
                self._github_repo,
                self._binary_name,
            )
            archive_path = os.path.join(extract_to, f"{self._binary_name}.zip")
            response = self.session.get(self.url)
            with open(archive_path, "wb") as fh:
                fh.write(response.content)
            sha256 = hashlib.sha256()
            with open(archive_path, "rb") as fh:
                sha256.update(fh.read())
            self.__check_shasum(sha256.hexdigest())
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(extract_to)
            os.remove(archive_path)
        elif system == "linux":
            self.get_sha256_hash_of_bundle_from_github(
                self.session,
                ver,
                system,
                arch,
                self._github_repo,
                self._binary_name,
            )
            archive_path = os.path.join(extract_to, f"{self._binary_name}.tar.gz")
            response = self.session.get(self.url)
            with open(archive_path, "wb") as fh:
                fh.write(response.content)
            sha256 = hashlib.sha256()
            with open(archive_path, "rb") as fh:
                sha256.update(fh.read())
            self.__check_shasum(sha256.hexdigest())
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(extract_to)
            os.remove(archive_path)
        else:
            raise RuntimeError(f"Unsupported system: {system}")

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------
    @protected
    def _build_bin_info(self) -> BinFileInfo:
        return BinFileInfo(
            bin_version=self.version,
            bin_sha256=self.get_packages_sha256_hash[self.version],
            bin_url=self.url,
        )


# ---------------------------------------------------------------------------
# Other-source download
# ---------------------------------------------------------------------------


@logged
@with_requests_session(retries=3, timeout=3)
class DownloadFromOtherSource(Binary):  # type: ignore[attr-defined]
    """Download a binary from an arbitrary URL with optional authentication.

    Parameters
    ----------
    version:
        Semantic version string.
    download_url:
        Direct download URL for the archive.
    hash_sha256:
        Expected SHA256 hex digest of the downloaded archive.
    binary_name:
        Name of the extracted binary (e.g. ``"tofu"`` or ``"gosling"``).
    install_dir:
        Destination directory.  Defaults to ``/mnt/<binary_name>_binary/<version>/``.
    token / bearer_token / auth_header_name:
        Optional authentication parameters (can also be set via properties).
    """

    # pylint: disable=no-member,too-many-instance-attributes,too-many-positional-arguments,too-many-arguments

    _bin_files_info: list[BinFileInfo] = []

    def __init__(
        self,
        version: str,
        download_url: str,
        hash_sha256: str,
        binary_name: str = "binary",
        install_dir: str | None = None,
        token: Optional[str] = None,
        bearer_token: bool = False,
        auth_header_name: str = "PRIVATE-TOKEN",
    ) -> None:
        self.version = version
        self.url = download_url
        self._binary_name = binary_name
        self.install_dir = install_dir or f"/mnt/{binary_name}_binary/{version}"
        self.__token: Optional[str] = token
        self.__bearer_token: bool = bearer_token
        self.__auth_header_name: str = auth_header_name
        self._bin_files_info.append(
            BinFileInfo(
                bin_version=version, bin_sha256=hash_sha256, bin_url=download_url
            )
        )

    # ------------------------------------------------------------------
    # Auth properties
    # ------------------------------------------------------------------

    @property
    def token(self) -> str | None:
        """Authentication token."""
        return self.__token

    @token.setter
    def token(self, value: str) -> None:
        self.__token = value

    @property
    def bearer_token(self) -> bool:
        """Whether to send the token as ``Bearer <token>``."""
        return self.__bearer_token

    @bearer_token.setter
    def bearer_token(self, value: bool) -> None:
        self.__bearer_token = value

    @property
    def auth_header_name(self) -> str:
        """HTTP header name used for authentication."""
        return self.__auth_header_name

    @auth_header_name.setter
    def auth_header_name(self, value: str) -> None:
        self.__auth_header_name = value

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def __check_shasum(self, downloaded_sum: str) -> None:
        expected = next(
            (
                b.bin_sha256
                for b in self._bin_files_info
                if b.bin_version == self.version
            ),
            None,
        )
        if expected is None:
            raise RuntimeError("Expected SHA256 hash not found.")
        if downloaded_sum != expected:
            self.error(
                f"Downloaded file hash {downloaded_sum} does not match "
                f"expected hash {expected}."
            )
            raise RuntimeError("Downloaded file hash does not match.")

    def __authorization_url(self) -> Session:
        if self.__token is not None:
            token_value = (
                f"Bearer {self.__token}" if self.__bearer_token else self.__token
            )
            return self.session.get(
                self.url, headers={self.__auth_header_name: token_value}
            )
        return self.session.get(self.url)

    # ------------------------------------------------------------------
    # Download + extract
    # ------------------------------------------------------------------

    @protected
    def _download_and_extract(self, extract_to: str) -> None:
        system = platform.system().lower()
        response = self.__authorization_url()
        if system == "windows":
            archive_path = os.path.join(extract_to, f"{self._binary_name}.zip")
            with open(archive_path, "wb") as fh:
                fh.write(response.content)
            sha256 = hashlib.sha256()
            with open(archive_path, "rb") as fh:
                sha256.update(fh.read())
            self.__check_shasum(sha256.hexdigest())
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(extract_to)
            os.remove(archive_path)
        elif system == "linux":
            archive_path = os.path.join(extract_to, f"{self._binary_name}.tar.gz")
            with open(archive_path, "wb") as fh:
                fh.write(response.content)
            sha256 = hashlib.sha256()
            with open(archive_path, "rb") as fh:
                sha256.update(fh.read())
            self.__check_shasum(sha256.hexdigest())
            with tarfile.open(archive_path, "r:gz") as tf:
                tf.extractall(extract_to)
            os.remove(archive_path)
        else:
            raise RuntimeError(f"Unsupported system: {system}")

    # ------------------------------------------------------------------
    # Store
    # ------------------------------------------------------------------

    @protected
    def _pre_download_validate(self) -> None:
        url = next(
            (b.bin_url for b in self._bin_files_info if b.bin_version == self.version),
            None,
        )
        if url is None:
            self.error("Download URL is not set for the current version.")
            raise RuntimeError("URL is not set.")

    @protected
    def _build_bin_info(self) -> BinFileInfo:
        sha = next(
            (
                b.bin_sha256
                for b in self._bin_files_info
                if b.bin_version == self.version
            ),
            "",
        )
        url = next(
            (b.bin_url for b in self._bin_files_info if b.bin_version == self.version),
            self.url,
        )
        return BinFileInfo(
            bin_version=self.version,
            bin_sha256=sha,
            bin_url=url,
        )


# ---------------------------------------------------------------------------
# Update base
# ---------------------------------------------------------------------------


class Update(ABC):
    """Abstract base class for binary version management.

    Subclasses must declare ``_table_name`` (the YDB table to read/write)
    and ``_source`` (``"github"`` or ``"other"``).
    """

    # pylint: disable=no-member

    @property
    @abstractmethod
    def _table_name(self) -> str:
        """YDB table name for version records (e.g. ``"opentofu_version"``)."""

    @protected
    async def _select_version(self, source: Literal["github", "other"]) -> Any:
        """Query the version table for the active record matching *source*."""
        operation = AsyncYDBOperations(
            self.schema,  # type: ignore[arg-type]
            AsyncYDBFunctionsCollections.select_parameterized_query,
        )
        await operation.process(
            selected_columns=["version_id", "version", "sha256_hash"],
            searching_columns=["active", "source"],
            searching_values=[True, source],
        )
        return operation.result

    @protected
    async def _upsert_data_ydb(self) -> None:
        """Write the current ``values_for_operate`` row into the version table."""
        operation = AsyncYDBOperations(
            self.schema,  # type: ignore[arg-type]
            AsyncYDBFunctionsCollections.upsert_query,
        )
        await operation.process(table_name=self._table_name)

    @protected
    async def _deactivate_previous_versions(
        self, source: Literal["github", "other"]
    ) -> None:
        """Mark all currently active rows for *source* as inactive."""
        result = await self._select_version(source=source)
        if result and result[0][0].rows:
            for row in result[0][0].rows:
                for table in self.schema.model.tables:
                    if table.table_name == self._table_name:
                        table.values_for_operate = (
                            row.version_id,
                            row.version,
                            source,
                            datetime.now().isoformat(),
                            row.sha256_hash,
                            False,
                        )
                if isinstance(self.schema, YDBSchema):
                    await self._upsert_data_ydb()
                elif isinstance(self.schema, DynamoDBSchema):
                    raise NotImplementedError("DynamoDB is not supported yet.")

    @protected
    async def _latest_info_update(
        self, latest: BinFileInfo, source: Literal["github", "other"]
    ) -> None:
        await self._deactivate_previous_versions(source)
        for table in self.schema.model.tables:
            if table.table_name == self._table_name:
                table.values_for_operate = (
                    self.get_version_info(latest.bin_sha256, latest.bin_version),
                    latest.bin_version,
                    source,
                    datetime.now().isoformat(),
                    latest.bin_sha256,
                    True,
                )
        if isinstance(self.schema, YDBSchema):
            await self._upsert_data_ydb()
        elif isinstance(self.schema, DynamoDBSchema):
            raise NotImplementedError("DynamoDB is not supported yet.")

    @protected
    async def _rollback_info_update(
        self,
        rollback: list[BinFileInfo],
        source: Literal["github", "other"],
    ) -> None:
        for rba in rollback:
            for table in self.schema.model.tables:
                if table.table_name == self._table_name:
                    table.values_for_operate = (
                        self.get_version_info(rba.bin_sha256, rba.bin_version),
                        rba.bin_version,
                        source,
                        datetime.now().isoformat(),
                        rba.bin_sha256,
                        False,
                    )
            if isinstance(self.schema, YDBSchema):
                await self._upsert_data_ydb()
            elif isinstance(self.schema, DynamoDBSchema):
                raise NotImplementedError("DynamoDB is not supported yet.")

    @property
    async def get_current_version(self) -> Any:
        """Return *(version_id, version, sha256_hash)* from the DB, or ``None``."""
        if isinstance(self.schema, YDBSchema):
            operation = AsyncYDBOperations(
                self.schema,  # type: ignore[arg-type]
                AsyncYDBFunctionsCollections.tables_not_empty,
            )
            await operation.check_tables_exist()
            if operation.result[0].name != self._table_name:
                self.info(f"{self._table_name} table does not exist in the DB.")
                return None
            await operation.process()
            if operation.result[0] is True:
                result = await self._select_version(source=self._source)
                if result and result[0][0].rows:
                    row = result[0][0].rows[0]
                    self.info(f"Selected version: {row.version}")
                    return (row.version_id, row.version, row.sha256_hash)
            else:
                self.warning(f"{self._table_name} table is empty.")
        elif isinstance(self.schema, DynamoDBSchema):
            raise NotImplementedError("DynamoDB is not supported yet.")
        return None

    @generate_version_id_decorator()
    def get_version_info(
        self,
        sha256_version: str,
        version_name: str,
    ) -> tuple[str, str, str]:
        """Generate a deterministic version ID from the SHA256 + version + source."""
        return sha256_version, version_name, self._source

    @abstractmethod
    def download_available_versions(self) -> list[str]:
        """Return a sorted list of available version strings."""

    @abstractmethod
    async def check_required_actions(self) -> bool:
        """Return ``True`` if an update is needed."""

    @abstractmethod
    async def start_update(
        self,
        auth_url: Optional[URLAuthSchema] = None,
        rb: Optional[int] = None,
    ) -> None:
        """Execute the update process."""


# ---------------------------------------------------------------------------
# GitHub updater
# ---------------------------------------------------------------------------


@logged
@with_requests_session(retries=3, timeout=3)
class UpdateGithub(Update):  # type: ignore[attr-defined]
    """Manage binary versions sourced from GitHub releases.

    Parameters
    ----------
    schema:
        YDB or DynamoDB schema instance.
    github_repo:
        ``"owner/repo"`` slug (e.g. ``"opentofu/opentofu"``).
    binary_name:
        Binary filename without extension (e.g. ``"tofu"`` or ``"gosling"``).
    table_name:
        YDB table to persist version records in.
    install_dir:
        Directory where binaries are stored.
    """

    # pylint: disable=no-member,too-many-positional-arguments,too-many-arguments

    _source: Literal["github"] = "github"
    __c_version: tuple[str, str, str] = ("dummy_id", "0.0.0", "dummy_hash")

    def __init__(
        self,
        schema: YDBSchema | DynamoDBSchema,
        github_repo: str,
        binary_name: str,
        table_name: str,
        install_dir: str | None = None,
    ) -> None:
        self.schema = schema
        self._github_repo = github_repo
        self._binary_name = binary_name
        self.__table_name = table_name
        self.install_dir = install_dir

    @property
    def _table_name(self) -> str:
        return self.__table_name

    @property
    def c_version(self) -> tuple[str, str, str]:
        """Current *(version_id, version, sha256)* tuple."""
        return self.__c_version

    async def sync_version(self) -> None:
        """Refresh *c_version* from the database."""
        if (version := await self.get_current_version) is not None:
            self.__c_version = version
            self.info(f"Current version: {self.__c_version[1]}")

    def _get_latest_version(self) -> str:
        url = f"https://api.github.com/repos/{self._github_repo}/releases/latest"
        with self.session.get(url) as response:
            return json.loads(response.content)["tag_name"].lstrip("v")

    @private
    def __update_to_latest_version(self) -> BinFileInfo | None:
        last_version = self._get_latest_version()
        if self.__c_version[1] == last_version:
            self.info(f"Already at latest version: {last_version}")
            return None
        self.info(f"Updating from {self.__c_version[1]} to {last_version}")
        downloader = DownloadGithub(
            github_repo=self._github_repo,
            binary_name=self._binary_name,
            version=last_version,
            install_dir=self.install_dir,
        )
        downloader.store_downloaded_bin()
        info = DownloadGithub.get_bin_files_info()[-1]
        self.__c_version = ("latest_id", info.bin_version, info.bin_sha256)
        return info

    @private
    def __download_rollback_releases(self, rb_factor: int = 3) -> list[BinFileInfo]:
        if rb_factor < 1 or rb_factor > 3:
            raise ValueError("Rollback factor must be between 1 and 3.")
        available = self.download_available_versions()
        if self.__c_version[1] not in available:
            raise RuntimeError(
                f"Current version {self.__c_version[1]}"
                " not found in available versions."
            )
        idx = available.index(self.__c_version[1])
        for ver in available[idx + 1 : idx + rb_factor + 1]:
            DownloadGithub(
                github_repo=self._github_repo,
                binary_name=self._binary_name,
                version=ver,
                install_dir=self.install_dir,
            ).store_downloaded_bin()
        return DownloadGithub.get_bin_files_info()[-rb_factor:]

    def download_available_versions(self) -> list[str]:
        url = f"https://api.github.com/repos/{self._github_repo}/releases"
        with self.session.get(url) as response:
            releases = json.loads(response.content)
            return sorted(
                [r["tag_name"].lstrip("v") for r in releases if "tag_name" in r],
                reverse=True,
            )

    async def check_required_actions(self) -> bool:
        if self.__c_version[1] == self._get_latest_version():
            self.info(f"Already at latest version: {self.__c_version[1]}")
            return False
        return True

    async def start_update(
        self,
        auth_url: Optional[URLAuthSchema] = None,
        rb: Optional[int] = 3,
    ) -> None:
        self.debug(f"auth_url omitted here (expected empty): {auth_url}")
        if not await self.check_required_actions():
            self.info("No update required.")
            return
        latest = self.__update_to_latest_version()
        if latest:
            await self._latest_info_update(latest, self._source)
        if files := self.__download_rollback_releases(rb or 3):
            await self._rollback_info_update(files, self._source)


# ---------------------------------------------------------------------------
# Other-source updater
# ---------------------------------------------------------------------------


@logged
@with_requests_session(retries=3, timeout=3)
class UpdateOtherSource(Update):  # type: ignore[attr-defined]
    """Manage binary versions sourced from arbitrary URLs.

    Parameters
    ----------
    schema:
        YDB or DynamoDB schema instance.
    files:
        Ordered list of :class:`BinFileInfo` records (newest first is fine;
        the class sorts internally).
    binary_name:
        Binary filename without extension.
    table_name:
        YDB table to persist version records in.
    install_dir:
        Directory where binaries are stored.
    """

    # pylint: disable=no-member,too-many-positional-arguments,too-many-arguments

    _source: Literal["other"] = "other"
    _rollback: bool = False
    __c_version: tuple[str, str, str] = ("dummy_id", "0.0.0", "dummy_hash")

    def __init__(
        self,
        schema: YDBSchema | DynamoDBSchema,
        files: list[BinFileInfo],
        binary_name: str,
        table_name: str,
        install_dir: str | None = None,
    ) -> None:
        self.schema = schema
        self.files = files
        self._binary_name = binary_name
        self.__table_name = table_name
        self.install_dir = install_dir

    @property
    def _table_name(self) -> str:
        return self.__table_name

    @property
    def c_version(self) -> tuple[str, str, str]:
        """Current *(version_id, version, sha256)* tuple."""
        return self.__c_version

    @property
    def rollaback(self) -> bool:
        """Rollback flag (read)."""
        return self._rollback

    @rollaback.setter
    def rollback(self, value: bool) -> None:
        """Set the rollback flag."""
        self._rollback = value

    async def sync_version(self) -> None:
        """Refresh *c_version* from the database."""
        if (version := await self.get_current_version) is not None:
            self.__c_version = version
            self.info(f"Current version: {self.__c_version[1]}")

    @private
    def __download_rollback_releases(self) -> list[BinFileInfo] | list:
        rb = len(self.files) - 1
        if rb == 0 or rb >= 3 or not self._rollback:
            return []
        available = self.download_available_versions()
        if self.__c_version[1] not in available:
            raise RuntimeError(
                f"Current version {self.__c_version[1]}"
                " not found in available versions."
            )
        idx = available.index(self.__c_version[1])
        result = []
        for ver in available[idx + 1 : idx + rb + 1]:
            file_info = next((f for f in self.files if f.bin_version == ver), None)
            if file_info is None:
                raise RuntimeError(f"File info for version {ver} not found.")
            DownloadFromOtherSource(
                version=ver,
                download_url=file_info.bin_url,
                hash_sha256=file_info.bin_sha256,
                binary_name=self._binary_name,
                install_dir=self.install_dir,
            ).store_downloaded_bin()
        result = DownloadFromOtherSource.get_bin_files_info()[-rb:]
        return result

    def download_available_versions(self) -> list[str]:
        return sorted([f.bin_version for f in self.files], reverse=True)

    async def check_required_actions(self) -> bool:
        latest = max(f.bin_version for f in self.files)
        if self.__c_version[1] == latest:
            self.info(f"Already at latest version: {latest}")
            return False
        return True

    async def start_update(
        self,
        auth_url: Optional[URLAuthSchema] = None,
        rb: Optional[int] = None,
    ) -> None:
        self.debug(f"rb omitted here (expected empty): {rb}")
        if not await self.check_required_actions():
            self.info("No update required.")
            return
        latest_file = max(self.files, key=lambda x: x.bin_version)
        downloader = DownloadFromOtherSource(
            version=latest_file.bin_version,
            download_url=latest_file.bin_url,
            hash_sha256=latest_file.bin_sha256,
            binary_name=self._binary_name,
            install_dir=self.install_dir,
        )
        if auth_url is not None:
            downloader.token = auth_url.token
            downloader.bearer_token = auth_url.bearer
            downloader.auth_header_name = auth_url.auth_header
        downloader.store_downloaded_bin()
        info = DownloadFromOtherSource.get_bin_files_info()[-1]
        self.__c_version = ("latest_id", info.bin_version, info.bin_sha256)
        await self._latest_info_update(latest_file, self._source)
        if files := self.__download_rollback_releases():
            await self._rollback_info_update(files, self._source)
