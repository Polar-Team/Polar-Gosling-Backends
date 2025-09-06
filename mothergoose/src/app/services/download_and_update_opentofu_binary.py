"""OpenTofuBinary download and update module."""

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

from accessify import private, protected
from requests import Session

from app.db.ydb_connection import AsyncYDBOperations
from app.db.manage_db import AsyncYDBFunctionsCollections
from app.schema.dynamodb_schemas import DynamoDBSchema
from app.schema.ydb_schemas import YDBSchema
from app.schema.tofu_schemas import OpenTofuBinFileInfo
from app.util.logging import logged
from app.util.requests_session import with_requests_session
from app.util.generator import generate_version_id_decorator


class OpenTofuBinary(ABC):
    """Abstract base class for OpenTofu binary management."""

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
    def _download_and_extract(self, url: str, extract_to: str) -> None:
        """Download and extract the OpenTofu binary from the given URL."""
        pass

    @abstractmethod
    def store_downloaded_bin(self) -> OpenTofuBinFileInfo:
        """Store the downloaded OpenTofu binary in the specified directory."""
        pass


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class OpenTofuDownloadGithub(OpenTofuBinary):  # type: ignore[attr-defined]
    """Class to handle the OpenTofu binary download process."""

    # pylint: disable=no-member,too-few-public-methods

    _github_sha256_hash_of_bundle: dict[str, str] = {}
    _opentofu_bin_files_info: list[OpenTofuBinFileInfo] = []

    def __init__(
        self, install_dir: str | None = None, version: str | None = None
    ) -> None:
        """Initialize the OpenTofuDownload class."""

        self.version = version or self._get_latest_version()
        self.install_dir = install_dir or f"/mnt/tofu_binary/{version}"

    @classmethod
    def get_sha256_hash_of_bundle_from_github(
        cls, session: Session, ver: str, system: str, arch: str, ext: str
    ) -> None:
        """Get the SHA255 hash of the OpenTofu bundle from GitHub."""

        response = session.get(
            "https://api.github.com/repos/opentofu/opentofu/releases"
        )
        data = response.json()
        for release in data:
            if release["tag_name"] == f"v{ver}":
                for asset in release["assets"]:
                    if asset["name"] == f"tofu_{ver}_{system}_{arch}.{ext}":
                        hash = asset["digest"].replace("sha256:", "")
                        cls._github_sha256_hash_of_bundle[ver] = hash

    @property
    def get_packages_sha256_hash(self) -> dict:
        """Get the SHA256 hash of the OpenTofu bundle."""
        return self._github_sha256_hash_of_bundle

    @protected
    def _get_download_url(self) -> str:
        """Function to get tofu download url from github"""

        ver = self.version
        if (system := platform.system().lower()) == "linux":
            if (arch := platform.machine().lower()) in ("x86_64", "amd64"):
                arch = "amd64"
            elif (arch := platform.machine().lower()) in ("aarch64", "arm64"):
                arch = "arm64"
            else:
                self.error(f"Unsupported architecture: {arch}")
                raise RuntimeError(f"Unsupported architecture: {arch}")
            dpath = f"download/v{ver}tofu_{ver}_{system}_{arch}.tar.gz"
            return f"https://github.com/opentofu/opentofu/releases/{dpath}"
        elif (system := platform.system().lower()) == "windows":
            arch = "amd64"
            dpath = f"download/v{ver}/tofu_{ver}_{system}_{arch}.zip"
            return f"https://github.com/opentofu/opentofu/releases/{dpath}"
        else:
            self.error(
                f"""
                Only Linux is supported in serverless Docker containers.
                You tried {system}.
                """
            )
            raise RuntimeError(
                "Only Linux is supported in serverless Docker containers."
                f"You tried {system}."
            )

    @protected
    def _download_and_extract(self, url: str, extract_to: str) -> None:
        """Function to download and extract tofu binary from github."""

        ver = self.version
        if (system := platform.system().lower()) == "windows":
            self.info("Getting hash of the bundle and save it in class var...")
            self.get_sha256_hash_of_bundle_from_github(
                self.session, ver, system, "amd64", "zip"
            )
            self.info(f"Using environment for {system}...")
            zip_path = os.path.join(extract_to, "tofu.zip")
            self.info(f"Downloading OpenTofu from {url}...")
            response = self.session.get(url)
            with open(zip_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(zip_path, "rb") as file:
                    sha256.update(file.read())
                downloaded_sum = sha256.hexdigest()
            self.info(f"Downloaded file hash: {downloaded_sum}")
            if expected_sum := self._github_sha256_hash_of_bundle.get(ver):
                if downloaded_sum != expected_sum:
                    self.error(
                        f"Downloaded file hash {downloaded_sum} does "
                        f"not match expected hash {expected_sum}."
                    )
                    raise RuntimeError(
                        "Downloaded file hash does not match expected hash."
                    )
            self.info("Extracting...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_to)
            os.remove(zip_path)
        elif (system := platform.system().lower()) == "linux":
            self.info("Getting hash of the bundle and save it in class var...")
            self.get_sha256_hash_of_bundle_from_github(
                self.session, ver, system, "amd64", "tar.gz"
            )
            tar_path = os.path.join(extract_to, "tofu.tar.gz")
            self.info(f"Downloading OpenTofu from {url}...")
            response = self.session.get(url)
            with open(tar_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(tar_path, "rb") as file:
                    sha256.update(file.read())
                    downloaded_sum = sha256.hexdigest()
            self.info(f"Downloaded file hash: {downloaded_sum}")
            if expected_sum := self._github_sha256_hash_of_bundle.get(ver):
                if downloaded_sum != expected_sum:
                    self.error(
                        f"Downloaded file hash {downloaded_sum} does "
                        f"not match expected hash {expected_sum}."
                    )
                    raise RuntimeError(
                        "Downloaded file hash does not match expected hash."
                    )
            self.info("Extracting...")
            with tarfile.open(tar_path, "r:gz") as tar_ref:
                tar_ref.extractall(extract_to)
            os.remove(tar_path)
        else:
            self.error(f"Unsupported system: {system}")
            raise RuntimeError(f"Unsupported system: {system}")

    def store_downloaded_bin(self) -> None:
        """Function for storing tofu bin in install dir."""

        url = self._get_download_url()
        with tempfile.TemporaryDirectory() as tmpdir:
            self._download_and_extract(url, tmpdir)
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
                bin_url=url,
            )
            OpenTofuDownloadGithub.add_opentofu_bin_info(info)


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

    # pylint: disable=no-member,too-few-public-methods
    __token: str
    __bearer_token: bool = False
    __auth_header_name: str = "Private-Token"
    _opentofu_bin_files_info: list[OpenTofuBinFileInfo] = []

    def __init__(
        self,
        version: str,
        url: str,
        hash_sha256: str,
        install_dir: str | None = None,
    ) -> None:
        """Initialize the OpenTofuDownloaded class."""

        self.install_dir = install_dir or f"/mnt/tofu_binary/{version}"
        self.version = version
        OpenTofuDownloadFromOtherSource._opentofu_bin_files_info.append(
            OpenTofuBinFileInfo(
                bin_version=version, bin_sha256=hash_sha256, bin_url=url
            )
        )

    @property
    def token(self) -> str:
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

    @protected
    def _download_and_extract(self, extract_to: str) -> None:
        """Function to download and extract tofu binary from other sources."""

        self.header_auth = False
        if self.__token is not None:
            self.header_auth = True
        if (system := platform.system().lower()) == "windows":
            self.info(f"Using environment for {system}...")
            zip_path = os.path.join(extract_to, "tofu.zip")
            self.info(f"Downloading OpenTofu from {self.url}...")
            if self.__bearer_token:
                token = f"Bearer {self.__token}"
            else:
                token = self.__token
            if self.header_auth:
                headers = {
                    f"{self.__auth_header_name}": f"{token}",
                }
                response = self.session.get(self.url, headers=headers)
            else:
                response = self.session.get(self.url)
            with open(zip_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(zip_path, "rb") as file:
                    sha256.update(file.read())
                downloaded_sum = sha256.hexdigest()
            self.info(f"Downloaded file hash: {downloaded_sum}")
            if expected_sum := next(
                (
                    bin.bin_sha256
                    for bin in self._opentofu_bin_files_info
                    if bin.bin_version == self.version
                ),
                None,
            ):
                if downloaded_sum != expected_sum:
                    self.error(
                        f"Downloaded file hash {downloaded_sum} does "
                        f"not match expected hash {expected_sum}."
                    )
                    raise RuntimeError(
                        "Downloaded file hash does not match expected hash."
                    )
            self.info("Extracting...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_to)
            os.remove(zip_path)
        elif (system := platform.system().lower()) == "linux":
            tar_path = os.path.join(extract_to, "tofu.tar.gz")
            self.info(f"Downloading OpenTofu from {self.url}...")
            if self.__bearer_token:
                token = f"Bearer {self.__token}"
            else:
                token = self.__token
            if self.header_auth:
                headers = {
                    f"{self.__auth_header_name}": f"{token}",
                }
                response = self.session.get(self.url, headers=headers)
            else:
                response = self.session.get(self.url)
            with open(tar_path, "wb") as file:
                file.write(response.content)
            self.info("Calculating SHA256 hash of the downloaded file...")
            if sha256 := hashlib.sha256():
                with open(tar_path, "rb") as file:
                    sha256.update(file.read())
                    downloaded_sum = sha256.hexdigest()
            self.info(f"Downloaded file hash: {downloaded_sum}")
            if expected_sum := next(
                (
                    bin.bin_sha256
                    for bin in self._opentofu_bin_files_info
                    if bin.bin_version == self.version
                ),
                None,
            ):
                if downloaded_sum != expected_sum:
                    self.error(
                        f"Downloaded file hash {downloaded_sum} does "
                        f"not match expected hash {expected_sum}."
                    )
                    raise RuntimeError(
                        "Downloaded file hash does not match expected hash."
                    )
            self.info("Extracting...")
            with tarfile.open(tar_path, "r:gz") as tar_ref:
                tar_ref.extractall(extract_to)
            os.remove(tar_path)
        else:
            self.error(f"Unsupported system: {system}")
            raise RuntimeError(f"Unsupported system: {system}")

    @protected
    def _store_downloaded_bin(self) -> None:
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
            self._download_and_extract(url, tmpdir)
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
                bin_sha256=next(
                    (
                        bin.bin_sha256
                        for bin in self._opentofu_bin_files_info
                        if bin.bin_version == self.version
                    ),
                    None,
                ),
                bin_url=url,
            )
            self._opentofu_bin_files_info.append(info)


class OpenTofuUpdate(ABC):
    """Abstract base class for OpenTofu binary update management."""

    @abstractmethod
    def download_available_versions(self) -> list[str]:
        """Download available OpenTofu versions from GitHub."""
        pass

    @abstractmethod
    def check_required_actions(self, rollback_count: int) -> bool:
        """Check if OpenTofu binary needs to be updated."""
        pass


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class OpenTofuUpdateGithub(OpenTofuUpdate):
    """Class for Updating OpenTofu binary."""

    # pylint: disable=no-member

    def __init__(self, install_dir: str | None = None) -> None:
        self.current_version = self.get_current_version()

    @property
    def get_current_version(self, schema: YDBSchema | DynamoDBSchema) -> str:
        """Get the current version of OpenTofu from the installed binary."""

        operation = AsyncYDBOperations(
            schema,
            AsyncYDBFunctionsCollections.select_with_parameters,
        )

    @private
    @generate_version_id_decorator()
    def __update_to_latest_version(self) -> OpenTofuBinFileInfo | None:
        """Update OpenTofu binary if a new version is available."""

        last_version = self._get_latest_version()
        if self.current_version == last_version:
            self.info(f"Tofu is already at the last version: {last_version}")
            return None
        self.info(f"Update Tofu from {self.current_version} to {last_version}")
        downloader = OpenTofuDownloadGithub(
            install_dir=self.install_dir, version=last_version
        )
        return downloader._store_downloaded_bin()

    @private
    def __download_rollback_releases(self):
        """Download up to 10 preveious version from current version."""

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

    def check_required_actions(self, rollback_count: int) -> bool:
        """Check if OpenTofu binary needs to be updated."""

        if (
            current_version := self._get_current_version()
        ) == self._get_latest_version():
            self.info(f"Tofu already at the last version: {current_version}")
            return False
        return True


class OpenTofuUpdateOtherSource(OpenTofuUpdate):
    """Class for Updating OpenTofu binary from other sources."""

    # pylint: disable=no-member

    def __init__(
        self,
        version: str,
        url: str,
        hash_sha256: str,
        install_dir: str | None = None,
    ) -> None:
        self.current_version = self._get_current_version()
        self.install_dir = install_dir or "/usr/local/bin"
        self.url = url
        self.version = version
        self.hash_sha256 = hash_sha256
