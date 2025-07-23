"""OpenTofuBinary download and update module."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from abc import ABC

from accessify import protected

from app.schema.tofu_schemas import OpenTofuBinFileInfo
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


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class OpenTofuDownload(OpenTofuBinary):
    """Class to handle the OpenTofu binary download process."""

    __test__ = False

    _github_sha256_hash_of_bundle: dict[str, str] = {}

    def __init__(self, install_dir=None, version=None):
        """Initialize the OpenTofuDownload class."""

        self.version = version or self._get_latest_version()
        self.install_dir = install_dir or f"/mnt/tofu_binary/{version}"

    @classmethod
    def get_sha256_hash_of_bundle_from_github(
        cls, session, ver: str, system: str, arch: str, ext: str
    ) -> str:
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

                        return hash

    @property
    def get_packages_sha256_hash(self):
        """Get the SHA256 hash of the OpenTofu bundle."""
        return self._github_sha256_hash_of_bundle

    @protected
    def _get_download_url(self):
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
        elif ((system := platform.system().lower()) == "windows") and (
            os.environ["PY_TEST"] == "True"
        ):
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
    def _download_and_extract(self, url, extract_to):
        """Function to download and extract tofu binary from github."""

        ver = self.version
        if ((system := platform.system().lower()) == "windows") and (
            os.environ["PY_TEST"] == "True"
        ):
            import zipfile

            self.info("Getting hash of the bundle and save it in class var...")
            self.get_sha256_hash_of_bundle_from_github(
                self.session, ver, system, "amd64", "zip"
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
        elif ((system := platform.system().lower()) == "linux") and (
            os.environ["PY_TEST"] == "True"
        ):
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

    @protected
    def _store_downloaded_bin(self) -> OpenTofuBinFileInfo:
        """Function for storing tofu bin in install dir."""

        if (ver := self._get_current_version()) == self._get_latest_version():
            self.info(f"OpenTofu is already at the latest version: {ver}")
            return None
        url = self._get_download_url()
        with tempfile.TemporaryDirectory() as tmpdir:
            self._download_and_extract(url, tmpdir)
            if ((system := platform.system().lower()) == "windows") and (
                os.environ["PY_TEST"] == "True"
            ):
                self.debug(f"Using test environment for {system}...")
                tofu_path = os.path.join(tmpdir, "tofu.exe")
                dest_path = os.path.join(self.install_dir, "tofu.exe")
            else:
                self.debug(f"Using test environment for {system}...")
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
        return info


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class OpenTofuUpdate(OpenTofuBinary):
    """Class for Updating OpenTofu binary."""

    def __init__(self, install_dir=None):
        self.current_version = self._get_current_version()
        self.install_dir = install_dir or "/usr/local/bin"

    def update(self):
        """Update OpenTofu binary if a new version is available."""

        latest_version = self._get_latest_version()
        if self.current_version == latest_version:
            self.info(f"Tofu is already at the latest version: {latest_version}")
            return None
        self.info(f"Updating Tofu from {self.current_version} to {latest_version}")
        downloader = OpenTofuDownload(
            install_dir=self.install_dir, version=latest_version
        )
        downloader._store_downloaded_bin()


@logged
@with_requests_session(
    retries=3,
    timeout=3,
)
class OpenTofuDownloadFromOtherSource(OpenTofuDownload):
    """
    Class to handle the OpenTofu binary
    download process from other sources.
    """

    def __init__(self, url: str, install_dir: str = None):
        """Initialize the OpenTofuDownloaded class."""

        self.install_dir = install_dir or "/mnt/tofu_binary/tofu_other_source"
        self.url = url

    @protected
    def _download_and_extract(self):
        """Funhction to download and extract tofu binary from other sources."""

    @protected
    def _store_downloaded_bin(self):
        """Function for storing tofu bin in the working bin directory."""

        with tempfile.TemporaryDirectory() as tmpdir:
            self._download_and_extract(self.url, tmpdir)
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
