"""OpenTofuBinary download and update module."""

import asyncio
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
from app.util.generator import generate_version_id_decorator
from app.util.logging import logged
from app.util.requests_session import with_requests_session

__all__ = [
    "OpenTofuUpdateGithub",
    "OpenTofuUpdateOtherSource",
]


class OpenTofuBinary(ABC):
    """Abstract base class for OpenTofu binary management."""

    # pylint: disable=no-member

    @classmethod
    def get_opentofu_bin_files_info(cls) -> list[OpenTofuBinFileInfo]:
        """Get the OpenTofu binary files information."""
        return cls._opentofu_bin_files_info

    @classmethod
    def add_opentofu_bin_info(cls, info: OpenTofuBinFileInfo) -> None:
        """Set the OpenTofu binary files information."""
        cls._opentofu_bin_files_info.append(info)

    def _get_latest_version(self) -> str:
        """Get the latest version of OpenTofu from GitHub."""

        url = "https://api.github.com/repos/opentofu/opentofu/releases/latest"
        with self.session.get(url) as response:
            release_info = json.loads(response.content)
            return release_info["tag_name"].lstrip("v")

    def _get_current_version(self) -> str:
        """Get the current version of OpenTofu from the installed binary."""

        tp = os.path.join(self.install_dir, "tofu")
        if not os.path.exists(tp):
            result = "0.0.0"
            self.warning("OpenTofu binary not found.It's ok if first run.")
        else:
            process = subprocess.run(
                [tp, "--version"], capture_output=True, text=True, check=True
            )
            result = process.stdout.strip().split()[-1].replace("v", "")
        return result

    @abstractmethod
    def _download_and_extract(self, extract_to: str) -> None:
        """Download and extract the OpenTofu binary from the given URL."""

    @abstractmethod
    def store_downloaded_bin(self) -> tuple[str, str]:
        """Store the downloaded OpenTofu binary in the specified directory."""


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class OpenTofuDownloadGithub(OpenTofuBinary):  # type: ignore[attr-defined]
    """Class to handle the OpenTofu binary download process."""

    # pylint: disable=no-member

    # pylint: disable=too-many-positional-arguments

    _github_sha256_hash_of_bundle: dict[str, str] = {}
    _opentofu_bin_files_info: list[OpenTofuBinFileInfo] = []

    def __init__(
        self, install_dir: str | None = None, version: str | None = None
    ) -> None:
        """Initialize the OpenTofuDownload class."""

        self.version = version or self._get_latest_version()
        self.install_dir = install_dir or f"/mnt/tofu_binary/{version}"
        self.url = self._get_download_url()

    @classmethod
    def get_sha256_hash_of_bundle_from_github(
        cls,
        session: Session,
        ver: str,
        system: str,
        arch: str,
    ) -> None:
        """Get the SHA255 hash of the OpenTofu bundle from GitHub."""

        response = session.get(
            "https://api.github.com/repos/opentofu/opentofu/releases"
        )
        data = response.json()
        if system == "windows":
            ext = "zip"
        else:
            ext = "tar.gz"
        for release in data:
            if release["tag_name"] == f"v{ver}":
                for asset in release["assets"]:
                    if asset["name"] == f"tofu_{ver}_{system}_{arch}.{ext}":
                        hash_bin = asset["digest"].replace("sha256:", "")
                        cls._github_sha256_hash_of_bundle[ver] = hash_bin

    @property
    def get_packages_sha256_hash(self) -> dict:
        """Get the SHA256 hash of the OpenTofu bundle."""
        return self._github_sha256_hash_of_bundle

    def _get_download_url(self) -> str:
        """Function to get tofu download url from github"""

        ver = self.version
        if (system := platform.system().lower()) == "linux":
            if (arch := platform.machine().lower()) in ("x86_64", "amd64"):
                arch = "amd64"
            else:
                arch = "arm64"
            dpath = f"download/v{ver}tofu_{ver}_{system}_{arch}.tar.gz"
            url = f"https://github.com/opentofu/opentofu/releases/{dpath}"
        else:
            arch = "amd64"
            dpath = f"download/v{ver}/tofu_{ver}_{system}_{arch}.zip"
            url = f"https://github.com/opentofu/opentofu/releases/{dpath}"
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
        """Function to download and extract tofu binary from github."""

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
            zip_path = os.path.join(extract_to, "tofu.zip")
            self.info(f"Downloading OpenTofu from {self.url}...")
            response = self.session.get(self.url)
            with open(zip_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(zip_path, "rb") as file:
                    sha256.update(file.read())
                downloaded_sum = sha256.hexdigest()
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
            tar_path = os.path.join(extract_to, "tofu.tar.gz")
            self.info(f"Downloading OpenTofu from {self.url}...")
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
        """Function for storing tofu bin in install dir."""

        with tempfile.TemporaryDirectory() as tmpdir:
            self._download_and_extract(tmpdir)
            if (system := platform.system().lower()) == "windows":
                self.info(f"Using environment for {system}...")
                tofu_path = os.path.join(tmpdir, "tofu.exe")
                dest_path = os.path.join(self.install_dir, "tofu.exe")
            else:
                self.info(f"Using environment for {system}...")
                tofu_path = os.path.join(tmpdir, "tofu")
                dest_path = os.path.join(self.install_dir, "tofu")
            shutil.copy2(tofu_path, dest_path)
            os.chmod(dest_path, 0o755)
            self.info(f"OpenTofu updated at {dest_path}")
            info = OpenTofuBinFileInfo(
                bin_version=self.version,
                bin_sha256=self.get_packages_sha256_hash[self.version],
                bin_url=self.url,
            )
            OpenTofuDownloadGithub.add_opentofu_bin_info(info)
        for bin_t in OpenTofuDownloadGithub.get_opentofu_bin_files_info():  # noqa: E501
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
class OpenTofuDownloadFromOtherSource(OpenTofuBinary):
    """
    Class to handle the OpenTofu binary
    download process from other sources.
    """

    # pylint: disable=no-member,too-many-instance-attributes

    __token: Optional[str] = None
    __bearer_token: bool = False
    __auth_header_name: str = "PRIVATE-TOKEN"
    _opentofu_bin_files_info: list[OpenTofuBinFileInfo] = []

    def __init__(
        self,
        version: str,
        url: str,
        hash_sha256: str,
        install_dir: str | None = None,
    ) -> None:
        """Initialize the OpenTofuDownloaded class."""

        self.url = url
        self.install_dir = install_dir or f"/mnt/tofu_binary/{version}"
        self.version = version
        OpenTofuDownloadFromOtherSource._opentofu_bin_files_info.append(
            OpenTofuBinFileInfo(
                bin_version=version, bin_sha256=hash_sha256, bin_url=url
            )
        )

    @property
    def token(self) -> str | None:
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
                    for bin in self._opentofu_bin_files_info
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

    def __authorization_url(self) -> Session:
        """Function to add token to url if needed."""

        header_auth = False
        token = ""
        if self.__token is not None:
            header_auth = True
        if self.__bearer_token and self.__token is not None:
            token = f"Bearer {self.__token}"
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
        """Function to download and extract tofu binary from other sources."""

        if (system := platform.system().lower()) == "windows":
            self.info(f"Using environment for {system}...")
            zip_path = os.path.join(extract_to, "tofu.zip")
            self.info(f"Downloading OpenTofu from {self.url}...")
            response = self.__authorization_url()
            with open(zip_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(zip_path, "rb") as file:
                    sha256.update(file.read())
                downloaded_sum = sha256.hexdigest()
            self.info(f"Downloaded file hash: {downloaded_sum}")
            self.__check_shasum(downloaded_sum)
            self.info("Extracting...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_to)
            os.remove(zip_path)
        elif (system := platform.system().lower()) == "linux":
            tar_path = os.path.join(extract_to, "tofu.tar.gz")
            self.info(f"Downloading OpenTofu from {self.url}...")
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
        """Function for storing tofu bin in install dir."""

        url = next(
            (
                bin.bin_url
                for bin in self._opentofu_bin_files_info
                if bin.bin_version == self.version
            ),
            None,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            self._download_and_extract(tmpdir)
            if (system := platform.system().lower()) == "windows":
                self.info(f"Using environment for {system}...")
                tofu_path = os.path.join(tmpdir, "tofu.exe")
                dest_path = os.path.join(self.install_dir, "tofu.exe")
            else:
                self.info(f"Using environment for {system}...")
                tofu_path = os.path.join(tmpdir, "tofu")
                dest_path = os.path.join(self.install_dir, "tofu")
            shutil.copy2(tofu_path, dest_path)
            os.chmod(dest_path, 0o755)
            self.info(f"OpenTofu updated at {dest_path}")
            if self.version is None:
                raise RuntimeError("Version is not set.")
            if url is None:
                raise RuntimeError("URL is not set.")
            info = OpenTofuBinFileInfo(
                bin_version=self.version,
                bin_sha256=next(
                    (
                        bin.bin_sha256
                        for bin in self._opentofu_bin_files_info
                        if bin.bin_version == self.version
                    ),
                    "",
                ),
                bin_url=url,
            )
            OpenTofuDownloadFromOtherSource.add_opentofu_bin_info(info)
        code = "FAILED"
        for (
            tofu
        ) in (
            OpenTofuDownloadFromOtherSource.get_opentofu_bin_files_info()
        ):  # noqa: E501
            if tofu.bin_version == self.version:
                version = tofu.bin_version
                code = "SUCCESS"
                break
        return version, code


class OpenTofuUpdate(ABC):
    """Abstract base class for OpenTofu binary update management."""

    # pylint: disable=no-member

    @protected
    async def _select_version(self, source: Literal["github", "other"]) -> Any:
        """Select the version of OpenTofu from the database."""
        operation = AsyncYDBOperations(
            self.schema,  # type: ignore[arg-type]
            AsyncYDBFunctionsCollections.select_parameterized_query,
        )
        await operation.process(
            selected_columns=[
                "version_id",
                "version",
                "sha256_hash",
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
            table_name="opentofu_version",
        )

    @protected
    def _latest_info_update(
        self, latest: OpenTofuBinFileInfo, source: Literal["github", "other"]
    ) -> None:
        for table in self.schema.model.tables:
            if table.table_name == "opentofu_version":
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
        if isinstance(self.schema, YDBSchema):
            self.info("Upserting data into YDB...")
            asyncio.run(self._upsert_data_ydb())
        elif isinstance(self.schema, DynamoDBSchema):
            self.error("DynamoDB is not supported yet.")
            raise NotImplementedError("DynamoDB is not supported yet.")

    @protected
    def _rollback_info_update(
        self,
        rollback: list[OpenTofuBinFileInfo],
        source: Literal[
            "github",
            "other",
        ],
    ) -> None:
        for rba in rollback:
            for table in self.schema.model.tables:
                if table.table_name == "opentofu_version":
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
            if isinstance(self.schema, YDBSchema):
                self.info("Upserting data into YDB...")
                asyncio.run(self._upsert_data_ydb())
            elif isinstance(self.schema, DynamoDBSchema):
                self.error("DynamoDB is not supported yet.")
                raise NotImplementedError(
                    "DynamoDB is not supported yet.",
                )

    @property
    async def get_current_version(
        self,
    ) -> Any:
        """Get the current version of OpenTofu from the installed binary."""

        if isinstance(self.schema, YDBSchema):
            operation = AsyncYDBOperations(
                self.schema,  # type: ignore[arg-type]
                AsyncYDBFunctionsCollections.tables_not_empty,
            )
            await operation.check_tables_exist()
            if operation.result[0].name != "opentofu_version":
                self.info("OpenTofu version table does not exist in the DB.")
            else:
                await operation.process()
                if operation.result[0] is True:
                    result = await self._select_version(source=self._source)
                    if result and result[0][0].rows:
                        row = result[0][0].rows[0]
                        version_id = row.version_id
                        version = row.version
                        s_hash = row.sha256_hash
                        self.info(f"OpenTofu Selected version: {version}")
                        return (version_id, version, s_hash)
                else:
                    self.warning("OpenTofu version table is empty.")
        elif isinstance(self.schema, DynamoDBSchema):
            self.error("DynamoDB is not supported yet.")
            raise NotImplementedError("DynamoDB is not supported yet.")

    @generate_version_id_decorator()
    def get_version_info(
        self,
        sha256_version: str,
        version_name: str,
    ) -> tuple[str, str, str]:
        """Generate a version ID by hashing the concatenation"""

        return sha256_version, version_name, self._source

    @abstractmethod
    def download_available_versions(self) -> list[str]:
        """Download available OpenTofu versions from GitHub."""

    @abstractmethod
    def check_required_actions(self) -> bool:
        """Check if OpenTofu binary needs to be updated."""

    @abstractmethod
    def start_update(
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
class OpenTofuUpdateGithub(OpenTofuUpdate):
    """Class for Updating OpenTofu binary."""

    # pylint: disable=no-member

    _source: Literal["github"] = "github"

    def __init__(
        self,
        schema: YDBSchema | DynamoDBSchema,
        install_dir: str | None = None,
    ) -> None:
        self.schema = schema
        self.c_version = asyncio.run(self.get_current_version) or (
            "dummy_id",
            "0.0.0",
            "dummy_hash",
        )
        self.install_dir = install_dir or f"/mnt/tofu_binary/{self.c_version}"

    def _get_latest_version(self) -> str:
        """Get the latest version of OpenTofu from GitHub."""

        url = "https://api.github.com/repos/opentofu/opentofu/releases/latest"
        with self.session.get(url) as response:
            release_info = json.loads(response.content)
            return release_info["tag_name"].lstrip("v")

    @private
    def __update_to_latest_version(self) -> OpenTofuBinFileInfo | None:
        """Update OpenTofu binary if a new version is available."""

        last_version = self._get_latest_version()
        if self.c_version[1] == last_version:
            self.info(f"Tofu is already at the last version: {last_version}")
            return None
        self.info(f"Update Tofu from {self.c_version} to {last_version}")
        downloader = OpenTofuDownloadGithub(
            install_dir=self.install_dir, version=last_version
        )
        downloader.store_downloaded_bin()
        self.c_version = (
            "latest_id",
            downloader.get_opentofu_bin_files_info()[-1].bin_version,
            downloader.get_opentofu_bin_files_info()[-1].bin_sha256,
        )
        return downloader.get_opentofu_bin_files_info()[-1]

    @private
    def __download_rollback_releases(
        self, rb_factor: int = 3
    ) -> list[OpenTofuBinFileInfo]:
        """Download up to 3 previous versions from current version."""

        if rb_factor < 1 or rb_factor > 3:
            self.error("Rollback factor must be between 1 and 3.")
            raise ValueError("Rollback factor must be between 1 and 3.")
        available_versions = self.download_available_versions()
        if self.c_version[1] in available_versions:
            c_index = available_versions.index(self.c_version[1])
            left = c_index + 1
            right = c_index + (rb_factor + 1)
            rollback_versions = available_versions[left:right]
            for task in rollback_versions:
                self.info(f"Downloading rollback version: {task}")
                instance = OpenTofuDownloadGithub(
                    install_dir=self.install_dir,
                    version=task,
                )
                instance.store_downloaded_bin()

            all_versions = OpenTofuDownloadGithub.get_opentofu_bin_files_info()
            result = all_versions[-rb_factor:]
        else:
            self.error(
                f"""Current version {self.c_version} not found
                in available versions.
                """
            )
            raise RuntimeError(
                f"""
                Current version {self.c_version} not found
                in available versions.
                """
            )
        return result

    def download_available_versions(self) -> list[str]:
        """Download available OpenTofu versions from GitHub."""

        url = "https://api.github.com/repos/opentofu/opentofu/releases"
        with self.session.get(url) as response:
            releases = json.loads(response.content)
            versions = [
                release["tag_name"].lstrip("v")
                for release in releases
                if "tag_name" in release
            ]
            return sorted(versions, reverse=True)

    def check_required_actions(self) -> bool:
        """Check if OpenTofu binary needs to be updated."""

        if (cversion := self.c_version[1]) == self._get_latest_version():
            self.info(f"Tofu already at the last version: {cversion}")
            return False
        return True

    def start_update(
        self,
        auth_url: Optional[URLAuthSchema] = None,
        rb: Optional[int] = 3,
    ) -> None:
        """Start the update process."""
        self.debug(
            f"Auth URL is ommited here you should see here empty: {auth_url}",
        )
        if req := self.check_required_actions():
            self.info(f"Update required is {req}, starting the process...")

            self._latest_info_update(
                self.__update_to_latest_version(),
                self._source,
            )
            if files := self.__download_rollback_releases(rb):
                self._rollback_info_update(files, self._source)

        else:
            self.info("No update required, exiting.")


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class OpenTofuUpdateOtherSource(OpenTofuUpdate):
    """Class for Updating OpenTofu binary from other sources."""

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
        self.c_version = asyncio.run(self.get_current_version) or (
            "dummy_id",
            "0.0.0",
            "dummy_hash",
        )
        self.install_dir = install_dir or f"/mnt/tofu_binary/{self.c_version}"

    @property
    def rollaback(self) -> bool:
        """Get the rollback flag."""
        return self._rollback

    @rollaback.setter
    def rollback(self, value: bool) -> None:
        """Set the rollback flag."""
        self._rollback = value

    @private
    def __download_rollback_releases(self) -> list[OpenTofuBinFileInfo] | list:
        """Download all previous versions from current version."""

        rb = len(self.files) - 1

        result = []
        if rb != 0 and rb < 3 and self._rollback:
            available_versions = self.download_available_versions()
            if self.c_version[1] in available_versions:
                c_index = available_versions.index(self.c_version[1])
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
                    instance = OpenTofuDownloadFromOtherSource(
                        install_dir=self.install_dir,
                        version=task,
                        url=file_info.bin_url,
                        hash_sha256=file_info.bin_sha256,
                    )
                    instance.store_downloaded_bin()

                c = OpenTofuDownloadFromOtherSource
                all_versions = c.get_opentofu_bin_files_info()
                result = all_versions[-rb:]
            else:
                self.error(
                    f"""Current version {self.c_version} not found
                    in available versions.
                    """
                )
                raise RuntimeError(
                    f"""
                    Current version {self.c_version} not found
                    in available versions.
                    """
                )
        return result

    def download_available_versions(self) -> list[str]:
        """Download available OpenTofu versions from other source."""

        versions = [file.bin_version for file in self.files]
        return sorted(versions, reverse=True)

    def check_required_actions(self) -> bool:
        """Check if OpenTofu binary needs to be updated."""

        if (cversion := self.c_version[1]) == max(
            file.bin_version for file in self.files
        ):
            self.info(f"Tofu already at the last version: {cversion}")
            return False
        return True

    def start_update(
        self,
        auth_url: Optional[URLAuthSchema] = None,
        rb: Optional[int] = None,
    ) -> None:
        """Start the update process."""

        self.debug(
            f"Rollback factor is ommited here you should see here empty: {rb}",
        )
        if req := self.check_required_actions():
            self.info(f"Update required is {req}, starting the process...")
            latest_file = max(self.files, key=lambda x: x.bin_version)
            downloader = OpenTofuDownloadFromOtherSource(
                version=latest_file.bin_version,
                url=latest_file.bin_url,
                hash_sha256=latest_file.bin_sha256,
                install_dir=self.install_dir,
            )
            if auth_url is not None:
                downloader.token = auth_url.token
                downloader.bearer_token = auth_url.bearer
                downloader.auth_header_name = auth_url.auth_header

            downloader.store_downloaded_bin()
            self.c_version = (
                "latest_id",
                downloader.get_opentofu_bin_files_info()[-1].bin_version,
                downloader.get_opentofu_bin_files_info()[-1].bin_sha256,
            )

            self._latest_info_update(
                latest_file,
                self._source,
            )

            if files := self.__download_rollback_releases():
                self._rollback_info_update(files, self._source)

        else:
            self.info("No update required, exiting.")
