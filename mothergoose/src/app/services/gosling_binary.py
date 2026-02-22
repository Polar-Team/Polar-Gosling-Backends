"""GoslingBinary download and update module."""

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
from app.schema.dynamodb_schemas import DynamoDBSchema
from app.schema.tofu_schemas import OpenTofuBinFileInfo
from app.schema.url_schemas import URLAuthSchema
from app.schema.ydb_schemas import YDBSchema
from app.util.base_logging import logged
from app.util.generator import generate_version_id_decorator
from app.util.requests_session import with_requests_session

__all__ = [
    "GoslingDownloadGithub",
    "GoslingDownloadFromOtherSource",
    "GoslingUpdateGithub",
    "GoslingUpdateOtherSource",
]


class GoslingBinary(ABC):
    """Abstract base class for Gosling binary management."""

    # pylint: disable=no-member

    @classmethod
    def get_gosling_bin_files_info(cls) -> list[OpenTofuBinFileInfo]:
        """Get the Gosling binary files information."""
        return cls._bin_files_info

    @classmethod
    def add_gosling_bin_info(cls, info: OpenTofuBinFileInfo) -> None:
        """Add a Gosling binary file info entry to the registry."""
        cls._bin_files_info.append(info)

    def _get_latest_version(self) -> str:
        """Get the latest version of Gosling from GitHub."""

        url = "https://api.github.com/repos/Polar-Gosling/gosling/releases/latest"
        with self.session.get(url) as response:  # type: ignore[attr-defined]
            release_info = json.loads(response.content)
            return release_info["tag_name"].lstrip("v")

    def _get_current_version(self) -> str:
        """Get the current version of Gosling from the installed binary."""

        if platform.system().lower() == "windows":
            bin_name = "gosling.exe"
        else:
            bin_name = "gosling"
        tp = os.path.join(self.install_dir, bin_name)  # type: ignore[attr-defined]
        if not os.path.exists(tp):
            result = "0.0.0"
            self.warning(  # type: ignore[attr-defined]
                "Gosling binary not found. It's ok if first run."
            )
        else:
            process = subprocess.run(
                [tp, "--version"], capture_output=True, text=True, check=True
            )
            result = process.stdout.strip().split()[-1].replace("v", "")
        return result

    @abstractmethod
    def _download_and_extract(self, extract_to: str) -> None:
        """Download and extract the Gosling binary from the given URL."""

    @abstractmethod
    def store_downloaded_bin(self) -> tuple[str, str]:
        """Store the downloaded Gosling binary in the specified directory."""


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class GoslingDownloadGithub(GoslingBinary):  # type: ignore[attr-defined]
    """Class to handle the Gosling binary download process from GitHub."""

    # pylint: disable=no-member

    # pylint: disable=too-many-positional-arguments

    _github_sha256_hash_of_bundle: dict[str, str] = {}
    _bin_files_info: list[OpenTofuBinFileInfo] = []

    def __init__(
        self,
        version: str | None = None,
        github_repo: str = "Polar-Gosling/gosling",
        install_dir: str | None = None,
    ) -> None:
        """Initialize the GoslingDownloadGithub class."""

        self.github_repo = github_repo
        self.version = version or self._get_latest_version()
        self.install_dir = install_dir or f"/mnt/gosling_binary/{self.version}"
        self.url = self._get_download_url()

    @classmethod
    def get_sha256_hash_of_bundle_from_github(
        cls,
        session: Session,
        ver: str,
        system: str,
        arch: str,
    ) -> None:
        """Get the SHA256 hash of the Gosling bundle from GitHub."""

        response = session.get(
            "https://api.github.com/repos/Polar-Gosling/gosling/releases"
        )
        data = response.json()
        if system == "windows":
            ext = "zip"
        else:
            ext = "tar.gz"
        for release in data:
            if release["tag_name"] == f"v{ver}":
                for asset in release["assets"]:
                    if asset["name"] == f"gosling_{ver}_{system}_{arch}.{ext}":
                        hash_bin = asset["digest"].replace("sha256:", "")
                        cls._github_sha256_hash_of_bundle[ver] = hash_bin

    @property
    def get_packages_sha256_hash(self) -> dict:
        """Get the SHA256 hash of the Gosling bundle."""
        return self._github_sha256_hash_of_bundle

    def _get_download_url(self) -> str:
        """Construct the Gosling download URL from GitHub releases."""

        ver = self.version
        repo = self.github_repo
        if (system := platform.system().lower()) == "linux":
            if (arch := platform.machine().lower()) in ("x86_64", "amd64"):
                arch = "amd64"
            else:
                arch = "arm64"
            dpath = f"download/v{ver}/gosling_{ver}_{system}_{arch}.tar.gz"
            url = f"https://github.com/{repo}/releases/{dpath}"
        else:
            arch = "amd64"
            dpath = f"download/v{ver}/gosling_{ver}_{system}_{arch}.zip"
            url = f"https://github.com/{repo}/releases/{dpath}"
        return url

    @private
    def __check_shasum(self, downloaded_sum: str) -> None:
        """Check the SHA256 hash of the downloaded file."""

        expected_sum = self.get_packages_sha256_hash.get(self.version)
        if downloaded_sum != expected_sum:
            self.error(
                f"Downloaded file hash {downloaded_sum} does "
                f"not match expected hash {expected_sum}."
            )
            raise RuntimeError("Downloaded file hash does not match.")

    @protected
    def _download_and_extract(self, extract_to: str) -> None:
        """Download and extract the Gosling binary from GitHub."""

        ver = self.version
        if (system := platform.system().lower()) == "windows":
            self.info("Getting hash of the bundle and save it in class var...")
            self.get_sha256_hash_of_bundle_from_github(
                self.session,
                ver,
                system,
                "amd64",
            )
            self.info(f"Using environment for {system}...")
            zip_path = os.path.join(extract_to, "gosling.zip")
            self.info(f"Downloading Gosling from {self.url}...")
            response = self.session.get(self.url)
            with open(zip_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(zip_path, "rb") as file:
                    sha256.update(file.read())
                downloaded_sum = sha256.hexdigest()
            else:
                self.error("SHA256 hash calculation failed.")
                raise RuntimeError("SHA256 hash calculation failed.")
            self.info(f"Downloaded file hash: {downloaded_sum}")
            self.__check_shasum(downloaded_sum)
            self.info("Extracting...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_to)
            os.remove(zip_path)
        elif (system := platform.system().lower()) == "linux":
            self.info("Getting hash of the bundle and save it in class var...")
            self.get_sha256_hash_of_bundle_from_github(
                self.session, ver, system, "amd64"
            )
            tar_path = os.path.join(extract_to, "gosling.tar.gz")
            self.info(f"Downloading Gosling from {self.url}...")
            response = self.session.get(self.url)
            with open(tar_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(tar_path, "rb") as file:
                    sha256.update(file.read())
                    downloaded_sum = sha256.hexdigest()
            self.info(f"Downloaded file hash: {downloaded_sum}")
            self.__check_shasum(downloaded_sum)
            self.info("Extracting...")
            with tarfile.open(tar_path, "r:gz") as tar_ref:
                tar_ref.extractall(extract_to)
            os.remove(tar_path)
        else:
            self.error(f"Unsupported system: {system}")
            raise RuntimeError(f"Unsupported system: {system}")

    def store_downloaded_bin(self) -> tuple[str, str]:
        """Store the downloaded Gosling binary in the install directory."""

        with tempfile.TemporaryDirectory() as tmpdir:
            self._download_and_extract(tmpdir)
            if (system := platform.system().lower()) == "windows":
                self.info(f"Using environment for {system}...")
                gosling_path = os.path.join(tmpdir, "gosling.exe")
                dest_path = os.path.join(self.install_dir, "gosling.exe")
            else:
                self.info(f"Using environment for {system}...")
                gosling_path = os.path.join(tmpdir, "gosling")
                dest_path = os.path.join(self.install_dir, "gosling")
            shutil.copy2(gosling_path, dest_path)
            os.chmod(dest_path, 0o755)
            self.info(f"Gosling updated at {dest_path}")
            info = OpenTofuBinFileInfo(
                bin_version=self.version,
                bin_sha256=self.get_packages_sha256_hash[self.version],
                bin_url=self.url,
            )
            GoslingDownloadGithub.add_gosling_bin_info(info)
        version: str = ""
        code = "FAILED"
        for bin_t in GoslingDownloadGithub.get_gosling_bin_files_info():
            if bin_t.bin_version == self.version:
                version = bin_t.bin_version
                code = "SUCCESS"
                break
        return version, code


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class GoslingDownloadFromOtherSource(GoslingBinary):
    """
    Class to handle the Gosling binary
    download process from other sources.
    """

    # pylint: disable=no-member,too-many-instance-attributes

    __token: Optional[str] = None
    __bearer_token: bool = False
    __auth_header_name: str = "PRIVATE-TOKEN"
    _bin_files_info: list[OpenTofuBinFileInfo] = []

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        version: str,
        download_url: str,
        hash_sha256: str,
        install_dir: str | None = None,
        token: Optional[str] = None,
        bearer_token: bool = False,
        auth_header_name: str = "PRIVATE-TOKEN",
    ) -> None:
        """Initialize the GoslingDownloadFromOtherSource class."""

        self.url = download_url
        self.install_dir = install_dir or f"/mnt/gosling_binary/{version}"
        self.version = version
        if token is not None:
            self.__token = token
        self.__bearer_token = bearer_token
        self.__auth_header_name = auth_header_name
        GoslingDownloadFromOtherSource._bin_files_info.append(
            OpenTofuBinFileInfo(
                bin_version=version, bin_sha256=hash_sha256, bin_url=download_url
            )
        )

    @property
    def token(self) -> Optional[str]:
        """Get the token for authentication."""
        return self.__token

    @token.setter
    def token(self, value: str) -> None:
        """Set the token for authentication."""
        self.__token = value

    @property
    def bearer_token(self) -> bool:
        """Get the bearer token flag."""
        return self.__bearer_token

    @bearer_token.setter
    def bearer_token(self, value: bool) -> None:
        """Set the bearer token flag."""
        self.__bearer_token = value

    @property
    def auth_header_name(self) -> str:
        """Get the authentication header name."""
        return self.__auth_header_name

    @auth_header_name.setter
    def auth_header_name(self, value: str) -> None:
        """Set the authentication header name."""
        self.__auth_header_name = value

    def __check_shasum(self, downloaded_sum: str) -> None:
        """Check the SHA256 hash of the downloaded file."""

        if (
            expected_sum := next(
                (
                    bin.bin_sha256
                    for bin in self._bin_files_info
                    if bin.bin_version == self.version
                ),
                None,
            )
        ) is None:
            self.error("Expected SHA256 hash not found.")
            raise RuntimeError("Expected SHA256 hash not found.")
        if downloaded_sum != expected_sum:
            self.error(
                f"Downloaded file hash {downloaded_sum} does "
                f"not match expected hash {expected_sum}."
            )
            raise RuntimeError("Downloaded file hash does not match.")

    def __authorization_url(self) -> object:
        """Function to add token to url if needed."""

        header_auth = False
        token = ""
        if self.__token is not None:
            header_auth = True
        if self.__bearer_token and self.__token is not None:
            token = f"Bearer {self.__token}"
        elif self.__token is not None:
            token = self.__token
        if header_auth:
            headers = {
                f"{self.__auth_header_name}": f"{token}",
            }
            result = self.session.get(self.url, headers=headers)
        else:
            result = self.session.get(self.url)

        return result

    @protected
    def _download_and_extract(self, extract_to: str) -> None:
        """Function to download and extract gosling binary from other sources."""

        if (system := platform.system().lower()) == "windows":
            self.info(f"Using environment for {system}...")
            zip_path = os.path.join(extract_to, "gosling.zip")
            self.info(f"Downloading Gosling from {self.url}...")
            response = self.__authorization_url()
            with open(zip_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(zip_path, "rb") as file:
                    sha256.update(file.read())
                downloaded_sum = sha256.hexdigest()
            else:
                self.error("SHA256 hash calculation failed.")
                raise RuntimeError("SHA256 hash calculation failed.")
            self.info(f"Downloaded file hash: {downloaded_sum}")
            self.__check_shasum(downloaded_sum)
            self.info("Extracting...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_to)
            os.remove(zip_path)
        elif (system := platform.system().lower()) == "linux":
            tar_path = os.path.join(extract_to, "gosling.tar.gz")
            self.info(f"Downloading Gosling from {self.url}...")
            response = self.__authorization_url()
            with open(tar_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(tar_path, "rb") as file:
                    sha256.update(file.read())
                    downloaded_sum = sha256.hexdigest()
            self.info(f"Downloaded file hash: {downloaded_sum}")
            self.__check_shasum(downloaded_sum)
            self.info("Extracting...")
            with tarfile.open(tar_path, "r:gz") as tar_ref:
                tar_ref.extractall(extract_to)
            os.remove(tar_path)
        else:
            self.error(f"Unsupported system: {system}")
            raise RuntimeError(f"Unsupported system: {system}")

    def store_downloaded_bin(self) -> tuple[str, str]:
        """Store the downloaded Gosling binary in the install directory."""

        url = next(
            (
                bin.bin_url
                for bin in self._bin_files_info
                if bin.bin_version == self.version
            ),
            None,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            self._download_and_extract(tmpdir)
            if (system := platform.system().lower()) == "windows":
                self.info(f"Using environment for {system}...")
                gosling_path = os.path.join(tmpdir, "gosling.exe")
                dest_path = os.path.join(self.install_dir, "gosling.exe")
            else:
                self.info(f"Using environment for {system}...")
                gosling_path = os.path.join(tmpdir, "gosling")
                dest_path = os.path.join(self.install_dir, "gosling")
            shutil.copy2(gosling_path, dest_path)
            os.chmod(dest_path, 0o755)
            self.info(f"Gosling updated at {dest_path}")
            if self.version is None:
                raise RuntimeError("Version is not set.")
            if url is None:
                raise RuntimeError("URL is not set.")
            info = OpenTofuBinFileInfo(
                bin_version=self.version,
                bin_sha256=next(
                    (
                        bin.bin_sha256
                        for bin in self._bin_files_info
                        if bin.bin_version == self.version
                    ),
                    "",
                ),
                bin_url=url,
            )
            GoslingDownloadFromOtherSource.add_gosling_bin_info(info)
        code = "FAILED"
        version: str = ""
        for bin_t in GoslingDownloadFromOtherSource.get_gosling_bin_files_info():
            if bin_t.bin_version == self.version:
                version = bin_t.bin_version
                code = "SUCCESS"
                break
        return version, code


class GoslingUpdate(ABC):
    """Abstract base class for Gosling binary update management."""

    # pylint: disable=no-member

    @protected
    async def _select_version(self, source: Literal["github", "other"]) -> Any:
        """Select the version of Gosling from the database."""
        operation = AsyncYDBOperations(
            self.schema,  # type: ignore[arg-type]
            AsyncYDBFunctionsCollections.select_parameterized_query,
        )
        await operation.process(
            selected_columns=[
                "version_id",
                "version",
                "source",
                "downloaded_at",
                "sha256_hash",
                "active",
            ],
            searching_columns=["active", "source"],
            searching_values=[True, source],
        )
        return operation.result

    @protected
    async def _upsert_data_ydb(self) -> None:
        """Upsert data into YDB database."""
        operation = AsyncYDBOperations(
            self.schema,  # type: ignore[arg-type]
            AsyncYDBFunctionsCollections.upsert_query,
        )
        await operation.process(
            table_name="gosling_version",
        )

    @protected
    async def _deactivate_previous_versions(
        self, source: Literal["github", "other"]
    ) -> None:
        """Deactivate all previous active versions for the given source."""
        result = await self._select_version(source=source)
        if result and result[0][0].rows:
            for row in result[0][0].rows:
                for table in self.schema.model.tables:  # type: ignore[attr-defined]
                    if table.table_name == "gosling_version":
                        table.values_for_operate = (
                            row.version_id,
                            row.version,
                            source,
                            datetime.now().isoformat(),
                            row.sha256_hash,
                            False,
                        )
                if isinstance(self.schema, YDBSchema):  # type: ignore[attr-defined]
                    await self._upsert_data_ydb()
                elif isinstance(self.schema, DynamoDBSchema):  # type: ignore[attr-defined]
                    self.error("DynamoDB is not supported yet.")  # type: ignore[attr-defined]
                    raise NotImplementedError("DynamoDB is not supported yet.")

    @protected
    async def _latest_info_update(
        self, latest: OpenTofuBinFileInfo, source: Literal["github", "other"]
    ) -> None:
        """Update the database with the latest version info."""
        await self._deactivate_previous_versions(source)

        for table in self.schema.model.tables:  # type: ignore[attr-defined]
            if table.table_name == "gosling_version":
                table.values_for_operate = (
                    self.get_version_info(
                        latest.bin_sha256,
                        latest.bin_version,
                    ),
                    latest.bin_version,
                    source,
                    datetime.now().isoformat(),
                    latest.bin_sha256,
                    True,
                )
        if isinstance(self.schema, YDBSchema):  # type: ignore[attr-defined]
            self.info("Upserting data into YDB...")  # type: ignore[attr-defined]
            await self._upsert_data_ydb()
        elif isinstance(self.schema, DynamoDBSchema):  # type: ignore[attr-defined]
            self.error("DynamoDB is not supported yet.")  # type: ignore[attr-defined]
            raise NotImplementedError("DynamoDB is not supported yet.")

    @protected
    async def _rollback_info_update(
        self,
        rollback: list[OpenTofuBinFileInfo],
        source: Literal["github", "other"],
    ) -> None:
        """Update the database with rollback version info."""
        for rba in rollback:
            for table in self.schema.model.tables:  # type: ignore[attr-defined]
                if table.table_name == "gosling_version":
                    table.values_for_operate = (
                        self.get_version_info(
                            rba.bin_sha256,
                            rba.bin_version,
                        ),
                        rba.bin_version,
                        source,
                        datetime.now().isoformat(),
                        rba.bin_sha256,
                        False,
                    )
            if isinstance(self.schema, YDBSchema):  # type: ignore[attr-defined]
                self.info("Upserting data into YDB...")  # type: ignore[attr-defined]
                await self._upsert_data_ydb()
            elif isinstance(self.schema, DynamoDBSchema):  # type: ignore[attr-defined]
                self.error("DynamoDB is not supported yet.")  # type: ignore[attr-defined]
                raise NotImplementedError("DynamoDB is not supported yet.")

    @property
    async def get_current_version(
        self,
    ) -> Any:
        """Get the current version of Gosling from the database."""

        if isinstance(self.schema, YDBSchema):  # type: ignore[attr-defined]
            operation = AsyncYDBOperations(
                self.schema,  # type: ignore[arg-type]
                AsyncYDBFunctionsCollections.tables_not_empty,
            )
            await operation.check_tables_exist()
            if operation.result[0].name != "gosling_version":
                self.info(  # type: ignore[attr-defined]
                    "Gosling version table does not exist in the DB."
                )
            else:
                await operation.process()
                if operation.result[0] is True:
                    result = await self._select_version(  # type: ignore[attr-defined]
                        source=self._source  # type: ignore[attr-defined]
                    )
                    if result and result[0][0].rows:
                        row = result[0][0].rows[0]
                        version_id = row.version_id
                        version = row.version
                        source = row.source
                        self.info(  # type: ignore[attr-defined]
                            f"Gosling Selected version: {version}"
                        )
                        return (version_id, version, source)
                else:
                    self.warning("Gosling version table is empty.")  # type: ignore[attr-defined]
        elif isinstance(self.schema, DynamoDBSchema):  # type: ignore[attr-defined]
            self.error("DynamoDB is not supported yet.")  # type: ignore[attr-defined]
            raise NotImplementedError("DynamoDB is not supported yet.")

    @generate_version_id_decorator()
    def get_version_info(
        self,
        sha256_version: str,
        version_name: str,
    ) -> tuple[str, str, str]:
        """Generate a version ID by hashing the concatenation."""

        return sha256_version, version_name, self._source  # type: ignore[attr-defined]

    @abstractmethod
    def download_available_versions(self) -> list[str]:
        """Download available Gosling versions from GitHub."""

    @abstractmethod
    async def check_required_actions(self) -> bool:
        """Check if Gosling binary needs to be updated."""

    @abstractmethod
    async def start_update(
        self,
        auth_url: Optional[URLAuthSchema] = None,
        rb: Optional[int] = None,
    ) -> None:
        """Start the update process."""


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class GoslingUpdateGithub(GoslingUpdate):
    """Class for Updating Gosling binary."""

    # pylint: disable=no-member

    _source: Literal["github"] = "github"

    def __init__(
        self,
        schema: YDBSchema | DynamoDBSchema,
        github_repo: str = "Polar-Gosling/gosling",
    ) -> None:
        self.schema = schema
        self.github_repo = github_repo
        self.__c_version: tuple[str, str, str] = (
            "dummy_id",
            "0.0.0",
            "dummy_source",
        )

    @property
    def c_version(self) -> tuple[str, str, str]:
        """Get the current version of Gosling."""
        return self.__c_version

    async def sync_version(self) -> None:
        """Sync the current version of Gosling from the database."""

        if (version := await self.get_current_version) is not None:
            self.__c_version = version
            self.info(f"Current version of Gosling: {self.__c_version[1]}")

    def _get_latest_version(self) -> str:
        """Get the latest version of Gosling from GitHub."""

        url = f"https://api.github.com/repos/{self.github_repo}/releases/latest"
        with self.session.get(url) as response:
            release_info = json.loads(response.content)
            return release_info["tag_name"].lstrip("v")

    @private
    def __update_to_latest_version(self) -> OpenTofuBinFileInfo | None:
        """Update Gosling binary if a new version is available."""

        last_version = self._get_latest_version()
        if self.__c_version[1] == last_version:
            self.info(f"Gosling is already at the last version: {last_version}")
            return None
        self.info(f"Update Gosling from {self.__c_version} to {last_version}")
        downloader = GoslingDownloadGithub(
            version=last_version,
            github_repo=self.github_repo,
        )
        downloader.store_downloaded_bin()
        self.__c_version = (
            "latest_id",
            downloader.get_gosling_bin_files_info()[-1].bin_version,
            self._source,
        )
        return downloader.get_gosling_bin_files_info()[-1]

    @private
    def __download_rollback_releases(
        self, rb_factor: int = 3
    ) -> list[OpenTofuBinFileInfo]:
        """Download up to 3 previous versions from current version."""

        if rb_factor < 1 or rb_factor > 3:
            self.error("Rollback factor must be between 1 and 3.")
            raise ValueError("Rollback factor must be between 1 and 3.")
        available_versions = self.download_available_versions()
        if self.__c_version[1] in available_versions:
            c_index = available_versions.index(self.__c_version[1])
            left = c_index + 1
            right = c_index + (rb_factor + 1)
            rollback_versions = available_versions[left:right]
            for task in rollback_versions:
                self.info(f"Downloading rollback version: {task}")
                instance = GoslingDownloadGithub(
                    version=task,
                    github_repo=self.github_repo,
                )
                instance.store_downloaded_bin()

            all_versions = GoslingDownloadGithub.get_gosling_bin_files_info()
            result = all_versions[-rb_factor:]
        else:
            # fmt: off
            self.error(f"""
                Current version {self.__c_version} not found
                in available versions.
                """)
            raise RuntimeError(f"""
                Current version {self.c_version} not found
                in available versions.
                """)
            # fmt: on
        return result

    def download_available_versions(self) -> list[str]:
        """Download available Gosling versions from GitHub."""

        url = f"https://api.github.com/repos/{self.github_repo}/releases"
        with self.session.get(url) as response:
            releases = json.loads(response.content)
            versions = [
                release["tag_name"].lstrip("v")
                for release in releases
                if "tag_name" in release
            ]
            return sorted(versions, reverse=True)

    async def check_required_actions(self) -> bool:
        """Check if Gosling binary needs to be updated."""

        if (cversion := self.__c_version[1]) == self._get_latest_version():
            self.info(f"Gosling already at the last version: {cversion}")
            return False
        return True

    async def start_update(
        self,
        auth_url: Optional[URLAuthSchema] = None,
        rb: Optional[int] = 3,
    ) -> None:
        """Start the update process."""
        self.debug(
            f"Auth URL is ommited here you should see here empty: {auth_url}",
        )
        if req := await self.check_required_actions():
            self.info(f"Update required is {req}, starting the process...")

            await self._latest_info_update(
                self.__update_to_latest_version(),
                self._source,
            )

            if files := self.__download_rollback_releases(rb):
                await self._rollback_info_update(files, self._source)

        else:
            self.info("No update required, exiting.")


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class GoslingUpdateOtherSource(GoslingUpdate):
    """Class for Updating Gosling binary from other sources."""

    # pylint: disable=no-member

    _source: Literal["other"] = "other"
    _rollback: bool = False

    def __init__(
        self,
        schema: YDBSchema | DynamoDBSchema,
        files: list[OpenTofuBinFileInfo],
        install_dir: str | None = None,
    ) -> None:
        self.schema = schema
        self.files = files
        self.install_dir = install_dir
        self.__c_version: tuple[str, str, str] = (
            "dummy_id",
            "0.0.0",
            "dummy_hash",
        )

    @property
    def c_version(self) -> tuple[str, str, str]:
        """Get the current version of Gosling."""
        return self.__c_version

    @property
    def rollaback(self) -> bool:
        """Get the rollback flag."""
        return self._rollback

    @rollaback.setter
    def rollback(self, value: bool) -> None:
        """Set the rollback flag."""
        self._rollback = value

    async def sync_version(self) -> None:
        """Sync the current version of Gosling from the installed binary."""

        if (version := await self.get_current_version) is not None:
            self.__c_version = version
            self.info(f"Current version of Gosling: {self.__c_version[1]}")

    @private
    def __download_rollback_releases(self) -> list[OpenTofuBinFileInfo] | list:
        """Download all previous versions from current version."""

        rb = len(self.files) - 1

        result = []
        if rb != 0 and rb < 3 and self._rollback:
            available_versions = self.download_available_versions()
            if self.__c_version[1] in available_versions:
                c_index = available_versions.index(self.__c_version[1])
                left = c_index + 1
                right = c_index + (rb + 1)
                rollback_versions = available_versions[left:right]
                for task in rollback_versions:
                    self.info(f"Downloading rollback version: {task}")
                    file_info = next(
                        (
                            file
                            for file in self.files
                            if file.bin_version == task  # noqa: E501
                        ),
                        None,
                    )
                    if file_info is None:
                        self.error(f"File info for version {task} not found.")
                        raise RuntimeError(
                            f"File info for version {task} not found.",
                        )
                    instance = GoslingDownloadFromOtherSource(
                        install_dir=self.install_dir,
                        version=task,
                        download_url=file_info.bin_url,
                        hash_sha256=file_info.bin_sha256,
                    )
                    instance.store_downloaded_bin()

                all_versions = (
                    GoslingDownloadFromOtherSource.get_gosling_bin_files_info()
                )
                result = all_versions[-rb:]
            else:
                # fmt: off
                self.error(f"""
                    Current version {self.__c_version} not found
                    in available versions.
                    """)
                raise RuntimeError(f"""
                    Current version {self.__c_version} not found
                    in available versions.
                    """)
                # fmt: on
        return result

    def download_available_versions(self) -> list[str]:
        """Download available Gosling versions from other source."""

        versions = [file.bin_version for file in self.files]
        return sorted(versions, reverse=True)

    async def check_required_actions(self) -> bool:
        """Check if Gosling binary needs to be updated."""

        if (cversion := self.__c_version[1]) == max(
            file.bin_version for file in self.files
        ):
            self.info(f"Gosling already at the last version: {cversion}")
            return False
        return True

    async def start_update(
        self,
        auth_url: Optional[URLAuthSchema] = None,
        rb: Optional[int] = None,
    ) -> None:
        """Start the update process."""

        self.debug(
            f"Rollback factor is ommited here you should see here empty: {rb}",
        )
        if req := await self.check_required_actions():
            self.info(f"Update required is {req}, starting the process...")
            latest_file = max(self.files, key=lambda x: x.bin_version)
            downloader = GoslingDownloadFromOtherSource(
                version=latest_file.bin_version,
                download_url=latest_file.bin_url,
                hash_sha256=latest_file.bin_sha256,
                install_dir=self.install_dir,
            )
            if auth_url is not None:
                downloader.token = auth_url.token
                downloader.bearer_token = auth_url.bearer
                downloader.auth_header_name = auth_url.auth_header

            downloader.store_downloaded_bin()
            self.__c_version = (
                "latest_id",
                downloader.get_gosling_bin_files_info()[-1].bin_version,
                downloader.get_gosling_bin_files_info()[-1].bin_sha256,
            )

            await self._latest_info_update(
                latest_file,
                self._source,
            )

            if files := self.__download_rollback_releases():
                await self._rollback_info_update(files, self._source)
        else:
            self.info("No update required, exiting.")
