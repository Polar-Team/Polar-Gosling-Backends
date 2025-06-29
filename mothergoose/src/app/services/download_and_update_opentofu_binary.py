"""OpenTofuBinary download and update module."""

import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
from abc import ABC

from accessify import private, protected

from ..util.class_access import only_called_by
from ..util.logging import logged
from ..util.requests_session import with_requests_session


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

    def __init__(self, install_dir=None, version=None):
        """Initialize the OpenTofuDownloaded class."""

        version = version or self._get_latest_version()
        self.install_dir = install_dir or f"/mnt/tofu_binary/{version}"

    @private
    def __get_download_url(self):
        """Function to get tofu download url from github"""

        if (system := platform.system().lower()) == "linux":
            if (arch := platform.machine().lower()) in ("x86_64", "amd64"):
                arch = "amd64"
            elif (arch := platform.machine().lower()) in ("aarch64", "arm64"):
                arch = "arm64"
            else:
                self.error(f"Unsupported architecture: {arch}")
                raise RuntimeError(f"Unsupported architecture: {arch}")
            dpath = f"download/v{self._get_latest_version()}/tofu_{
                self._get_latest_version()
            }_{system}_{arch}.tar.gz"
            return f"https://github.com/opentofu/opentofu/releases/{dpath}"
        elif ((system := platform.system().lower()) == "windows") and (
            os.environ["PY_TEST"] == "True"
        ):
            arch = "amd64"
            dpath = f"download/v{self._get_latest_version()}/tofu_{
                self._get_latest_version()
            }_{system}_{arch}.zip"
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

    @private
    def __download_and_extract(self, url, extract_to):
        """Function to download and extract tofu binary from github."""

        if ((system := platform.system().lower()) == "windows") and (
            os.environ["PY_TEST"] == "True"
        ):
            import zipfile

            self.info(f"Using test environment for {system}...")
            zip_path = os.path.join(extract_to, "tofu.zip")
            self.info(f"Downloading OpenTofu from {url}...")
            response = self.session.get(url)
            with open(zip_path, "wb") as file:
                file.write(response.content)
            self.info("Extracting...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_to)
            os.remove(zip_path)
        elif ((system := platform.system().lower()) == "linux") and (
            os.environ["PY_TEST"] == "True"
        ):
            tar_path = os.path.join(extract_to, "tofu.tar.gz")
            self.info(f"Downloading OpenTofu from {url}...")
            response = self.session.get(url)
            with open(tar_path, "wb") as file:
                response = self.session.get(url)
                file.write(response.content)
            self.info("Extracting...")
            with tarfile.open(tar_path, "r:gz") as tar_ref:
                tar_ref.extractall(extract_to)
            os.remove(tar_path)

    @protected
    def _store_downloaded_bin(self):
        """Function for storing tofu bin in install dir."""

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

    def tests_get_download_url(self):
        """Test the download URL generation."""

        if os.environ["PY_TEST"] == "True":
            return self.__get_download_url()
        else:
            return None

    def tests_download_and_extract(self):
        """Test the download and extraction process."""

        if os.environ["PY_TEST"] == "True":
            with tempfile.TemporaryDirectory() as tmpdir:
                url = self.__get_download_url()
                self.__download_and_extract(url, tmpdir)
                return os.listdir(tmpdir)
        else:
            return None

    def tests_store_downloaded_bin(self):
        """Test the download and extraction process."""

        if os.environ["PY_TEST"] == "True":
            return self._store_downloaded_bin()
        else:
            return None


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
        raise NotImplementedError(
            "This method should be implemented in subclasses.")
