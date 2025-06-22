"""OpenTofuBinary download and update module."""

import subprocess
import os
import shutil
import tempfile
import requests
import tarfile
import json
import platform
from accessify import private, protected
from abc import ABC, abstractmethod

from ..util.requests_session import with_requests_session
from ..util.class_access import only_called_by
from ..util.logging import logged


class OpenTofuBinary(ABC):
    """Abstract base class for OpenTofu binary management."""

    @abstractmethod
    def get_latest_version(self):
        """Get the latest version of OpenTofu."""
        pass

    @abstractmethod
    def get_current_version(self):
        """Get the current version of OpenTofu."""
        pass


@logged
@with_requests_session(
    retries=3,
    backoff_factor=0.5,
    timeout=10,
    status_forcelist=(502, 503, 504),
)
class OpenTofuDownload(OpenTofuBinary):
    """Class to handle the OpenTofu binary download process."""

    def __init__(self, install_dir=None, version=None):
        """Initialize the OpenTofuDownloaded class."""

        version = version or self.get_latest_version()
        self.install_dir = install_dir or f"/mnt/tofu_binary/{version}"

    @private
    def get_current_version(self):
        """Get the current version of OpenTofu from the installed binary."""
        tofu_path = os.path.join(self.install_dir, "tofu")
        if not os.path.exists(tofu_path):
            self.error("OpenTofu binary not found.")
            raise FileNotFoundError("OpenTofu binary not found.")
        result = subprocess.run(
            [tofu_path, "--version"], capture_output=True, text=True, check=True
        )
        return result.stdout.strip().split()[-1]

    def get_latest_version(self):
        """Get the latest version of OpenTofu from GitHub."""

        url = "https://api.github.com/repos/opentofu/opentofu/releases/latest"
        with self.session.get(url) as response:
            release_info = json.loads(response.content)
            return release_info["tag_name"].lstrip("v")

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
            dpath = f"download/v{self.get_latest_version()}/tofu_{
                self.get_latest_version()
            }_{system}_{arch}.tar.gz"
            return f"https://github.com/opentofu/opentofu/releases/{dpath}"
        elif ((system := platform.system().lower()) == "windows") and (
            os.environ["PY_TEST"] == "True"
        ):
            arch = "amd64"
            dpath = f"download/v{self.get_latest_version()}/tofu_{
                self.get_latest_version()
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

    @protected
    def _download_and_extract(self, url, extract_to):
        if ((system := platform.system().lower()) == "windows") and (
            os.environ["PY_TEST"] == "True"
        ):
            import zipfile

            self.info(f"Using test environment for {system}...")
            zip_path = os.path.join(extract_to, "tofu.zip")
            self.info(f"Downloading OpenTofu from {url}...")
            self.session.get(url, zip_path)
            self.info("Extracting...")
            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                zip_ref.extractall(extract_to)
            os.remove(zip_path)
        elif ((system := platform.system().lower()) == "linux") and (
            os.environ["PY_TEST"] == "True"
        ):
            tar_path = os.path.join(extract_to, "tofu.tar.gz")
            print(f"Downloading OpenTofu from {url}...")
            self.session.get(url, tar_path)
            print("Extracting...")
            with tarfile.open(tar_path, "r:gz") as tar_ref:
                tar_ref.extractall(extract_to)
            os.remove(tar_path)

    @protected
    def _store_downloaded_bin(self):
        url = self.__get_download_url()
        with tempfile.TemporaryDirectory() as tmpdir:
            self.__download_and_extract(url, tmpdir)
            tofu_path = os.path.join(tmpdir, "tofu")
            dest_path = os.path.join(self.install_dir, "tofu")
            shutil.copy2(tofu_path, dest_path)
            os.chmod(dest_path, 0o755)
            print(f"OpenTofu updated at {dest_path}")

    def tests_get_download_url(self):
        """Test the download URL generation."""

        if os.environ["PY_TEST"] == "True":
            return self._get_download_url()
        else:
            return None

    def tests_download_and_extract(self):
        """Test the download and extraction process."""

        if os.environ["PY_TEST"] == "True":
            with tempfile.TemporaryDirectory() as tmpdir:
                url = self._get_download_url()
                self._download_and_extract(url, tmpdir)
                return os.listdir(tmpdir)
        else:
            return None


@logged
class OpenTofuUpdate(OpenTofuBinary):
    """Class for Updating OpenTofu binary."""

    def __init__(self, version="1.9.1", install_dir=None):
        self.version = version
        self.install_dir = install_dir or "/usr/local/bin"
