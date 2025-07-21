"""OpenTofuBinary download and update module."""

import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import hashlib
from abc import ABC

from accessify import private, protected

from app.util.class_access import only_called_by
from app.util.logging import logged
from app.util.requests_session import with_requests_session


class OpenTofuBinary(ABC):
    """Abstract base class for OpenTofu binary management."""

    def _get_latest_version(self):
        """Get the latest version of OpenTofu from GitHub."""

        url = "https://api.github.com/repos/opentofu/opentofu/releases/latest"
        with self.session.get(url) as response:
            release_info = json.loads(response.content)
            return release_info["tag_name"].lstrip("v")

    def _get_current_version(self):
        """Get the current version of OpenTofu from the installed binary."""

        tofu_path = os.path.join(self.install_dir, "tofu")
        if not os.path.exists(tofu_path):
            self.error("OpenTofu binary not found.")
            raise FileNotFoundError("OpenTofu binary not found.")
        result = subprocess.run(
            [tofu_path, "--version"], capture_output=True, text=True, check=True
        )
        return result.stdout.strip().split()[-1]


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class OpenTofuDownload(OpenTofuBinary):
    """Class to handle the OpenTofu binary download process."""

    __test__ = False

    _github_sha256_hash_of_bundle = {}

    def __init__(self, install_dir=None, version=None):
        """Initialize the OpenTofuDownload class."""

        self.version = version or self._get_latest_version()
        self.install_dir = install_dir or f"/mnt/tofu_binary/{version}"

    @classmethod
    def get_sha256_hash_of_bundle_from_github(
        cls, session, version: str, system: str, arch: str, extension: str
    ) -> str:
        """Get the SHA255 hash of the OpenTofu bundle from GitHub."""

        response = session.get(
            "https://api.github.com/repos/opentofu/opentofu/releases"
        )
        data = response.json()
        for release in data:
            if release["tag_name"] == f"v{version}":
                for asset in release["assets"]:
                    if asset["name"] == f"tofu_{version}_{system}_{arch}.{extension}":
                        hash = asset["digest"].replace("sha256:", "")
                        cls._github_sha256_hash_of_bundle[version] = hash

                        return hash

    @property
    def get_packages_sha256_hash(self):
        """Get the SHA256 hash of the OpenTofu bundle."""
        return self._github_sha256_hash_of_bundle

    @protected
    def _get_download_url(self):
        """Function to get tofu download url from github"""

        if (system := platform.system().lower()) == "linux":
            if (arch := platform.machine().lower()) in ("x86_64", "amd64"):
                arch = "amd64"
            elif (arch := platform.machine().lower()) in ("aarch64", "arm64"):
                arch = "arm64"
            else:
                self.error(f"Unsupported architecture: {arch}")
                raise RuntimeError(f"Unsupported architecture: {arch}")
            dpath = (
                f"download/v{self.version}tofu_{self.version}_{system}_{arch}.tar.gz"
            )
            return f"https://github.com/opentofu/opentofu/releases/{dpath}"
        elif ((system := platform.system().lower()) == "windows") and (
            os.environ["PY_TEST"] == "True"
        ):
            arch = "amd64"
            dpath = f"download/v{self.version}/tofu_{self.version}_{system}_{arch}.zip"
            return f"https://github.com/opentofu/opentofu/releases/{dpath}"
        else:
            self.error(f"""
                Only Linux is supported in serverless Docker containers.
                You tried {system}.
                """)
            raise RuntimeError(
                "Only Linux is supported in serverless Docker containers."
                f"You tried {system}."
            )

    @protected
    def _download_and_extract(self, url, extract_to):
        """Function to download and extract tofu binary from github."""

        if ((system := platform.system().lower()) == "windows") and (
            os.environ["PY_TEST"] == "True"
        ):
            import zipfile

            self.info("Getting has of the bundl and save it in class variavle...")
            self.get_sha256_hash_of_bundle_from_github(
                self.session, self.version, system, "amd64", "zip"
            )
            self.info(f"Using test environment for {system}...")
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
            if expected_sum := self._github_sha256_hash_of_bundle.get(self.version):
                if downloaded_sum != expected_sum:
                    self.error(
                        f"Downloaded file hash {downloaded_sum} does not match "
                        f"expected hash {expected_sum}."
                    )
                    raise RuntimeError(
                        "Downloaded file hash does not match expected hash."
                    )
            self.info("Extracting...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_to)
            os.remove(zip_path)
        elif ((system := platform.system().lower()) == "linux") and (
            os.environ["PY_TEST"] == "True"
        ):
            self.info("Getting has of the bundl and save it in class variavle...")
            self.get_sha256_hash_of_bundle_from_github(
                self.session, self.version, system, "amd64", "tar.gz"
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
            if expected_sum := self._github_sha256_hash_of_bundle.get(self.version):
                if downloaded_sum != expected_sum:
                    self.error(
                        f"Downloaded file hash {downloaded_sum} does not match "
                        f"expected hash {expected_sum}."
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
    def _store_downloaded_bin(self):
        """Function for storing tofu bin in install dir."""

        if version := self._get_current_version() == self._get_latest_version():
            self.info(f"OpenTofu is already at the latest version: {version}")
            return
        url = self.__get_download_url()
        with tempfile.TemporaryDirectory() as tmpdir:
            self.__download_and_extract(url, tmpdir)
            if ((system := platform.system().lower()) == "windows") and (
                os.environ["PY_TEST"] == "True"
            ):
                self.debug(f"Using test environment for {system}...")
                tofu_path = os.path.join(tmpdir, "tofu.exe")
                dest_path = os.path.join(self.install_dir, "tofu.exe")
            else:
                tofu_path = os.path.join(tmpdir, "tofu")
                dest_path = os.path.join(self.install_dir, "tofu")
            shutil.copy2(tofu_path, dest_path)
            os.chmod(dest_path, 0o755)
            self.info(f"OpenTofu updated at {dest_path}")


@logged
class OpenTofuUpdate(OpenTofuBinary):
    """Class for Updating OpenTofu binary."""

    def __init__(self, version="1.9.1", install_dir=None):
        self.version = version
        self.install_dir = install_dir or "/usr/local/bin"


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class OpenTofuDownloadFromOtherSource(OpenTofuDownload):
    """Class to handle the OpenTofu binary download process from other sources."""

    def __init__(self, url: str, install_dir: str = None):
        """Initialize the OpenTofuDownloaded class."""

        self.install_dir = install_dir or "/mnt/tofu_binary/tofu_other_source"
        self.url = url

    @protected
    def _store_downloaded_bin(self):
        """Function for storing tofu bin in the working bin directory."""

        with tempfile.TemporaryDirectory() as tmpdir:
            self.__download_and_extract(self.url, tmpdir)
            if ((system := platform.system().lower()) == "windows") and (
                os.environ["PY_TEST"] == "True"
            ):
                self.debug(f"Using test environment for {system}...")
                tofu_path = os.path.join(tmpdir, "tofu.exe")
                dest_path = os.path.join(self.install_dir, "tofu.exe")
            else:
                tofu_path = os.path.join(tmpdir, "tofu")
                dest_path = os.path.join(self.install_dir, "tofu")
            shutil.copy2(tofu_path, dest_path)
            os.chmod(dest_path, 0o755)
            self.info(f"OpenTofu updated at {dest_path}")
