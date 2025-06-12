import os
import shutil
import tempfile
import urllib.request
import tarfile
import platform
from accessify import private
from abc import ABC, abstractmethod

from ..util.class_access import only_called_by
from ..util.logging import log


class OpenTofuBinary(ABC):
    """Abstract base class for OpenTofu binary management."""

    @abstractmethod
    def get_download_url(self):
        """Get the download URL for the OpenTofu binary."""
        pass

    @abstractmethod
    def download_and_extract(self, url, extract_to):
        """Download and extract the OpenTofu binary."""
        pass

    @abstractmethod
    def get_latest_version(self):
        """Get the latest version of OpenTofu."""
        pass


@log
class OpenTofuDownload(OpenTofuBinary):
    """Class to handle the OpenTofu binary download process."""

    def __init__(self, install_dir=None):
        """Initialize the OpenTofuDownloaded class."""

        version = self.get_latest_version()
        self.install_dir = install_dir or f"/mnt/tofu_binary/{version}"

    @private
    def get_latest_version(self):
        """Get the latest version of OpenTofu from GitHub."""

        url = "https://api.github.com/repos/opentofu/opentofu/releases/latest"
        with urllib.request.urlopen(url) as response:
            data = response.read().decode("utf-8")
            import json

            release_info = json.loads(data)
            return release_info["tag_name"].lstrip("v")

    @private
    def get_download_url(self):
        system = platform.system().lower()
        arch = platform.machine().lower()
        if system == "linux":
            if arch in ("x86_64", "amd64"):
                arch = "amd64"
            elif arch in ("aarch64", "arm64"):
                arch = "arm64"
            else:
                log.error(f"Unsupported architecture: {arch}")
                raise RuntimeError(f"Unsupported architecture: {arch}")
            dpath = f"download/v{self.get_latest_version()}/tofu_{
                self.get_latest_version()
            }_linux_{arch}.tar.gz"
            return f"https://github.com/opentofu/opentofu/releases/{dpath}"
        else:
            log.error("""
                Only Linux is supported in serverless Docker containers.
                """)
            raise RuntimeError(
                "Only Linux is supported in serverless Docker containers."
            )

    @private
    def download_and_extract(self, url, extract_to):
        tar_path = os.path.join(extract_to, "tofu.tar.gz")
        print(f"Downloading OpenTofu from {url}...")
        urllib.request.urlretrieve(url, tar_path)
        print("Extracting...")
        with tarfile.open(tar_path, "r:gz") as tar_ref:
            tar_ref.extractall(extract_to)
        os.remove(tar_path)

    def store_downloaded_bin(self):
        url = self.get_download_url()
        with tempfile.TemporaryDirectory() as tmpdir:
            self.download_and_extract(url, tmpdir)
            tofu_path = os.path.join(tmpdir, "tofu")
            dest_path = os.path.join(self.install_dir, "tofu")
            shutil.copy2(tofu_path, dest_path)
            os.chmod(dest_path, 0o755)
            print(f"OpenTofu updated at {dest_path}")


@log
class OpenTofuUpdate(OpenTofuBinary):
    """Class for Updating OpenTofu binary."""

    def __init__(self, version="1.9.1", install_dir=None):
        self.version = version
        self.install_dir = install_dir or "/usr/local/bin"
