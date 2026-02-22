"""
GitHub Binary Downloader Service

Service for automatically checking and downloading new binary versions
from GitHub releases (Gosling CLI and OpenTofu).
"""

import hashlib
import os
import tempfile
from typing import Any

import requests
from accessify import private

from app.services.binary_version_service import BinaryVersionService
from app.services.opentofu_binary import OpenTofuUpdateGithub
from app.util.base_logging import logged


@logged
class GitHubBinaryDownloader:
    """Service for checking and downloading new binary versions from GitHub."""

    # pylint: disable=no-member

    GOSLING_REPO = "opentofu/opentofu"
    OPENTOFU_REPO = "opentofu/opentofu"

    def __init__(self, binary_version_service: BinaryVersionService, schema: Any) -> None:
        """Initialize GitHubBinaryDownloader with service dependencies."""
        self.binary_version_service = binary_version_service
        self.schema = schema
        self.session = requests.Session()

    def check_latest_gosling_version(self) -> str:
        """Check the latest Gosling CLI version available on GitHub."""
        url = f"https://api.github.com/repos/{self.GOSLING_REPO}/releases/latest"
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            release_info = response.json()
            version = release_info["tag_name"].lstrip("v")
            self.info("Latest Gosling CLI version on GitHub: %s", version)
            return version
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.error("Failed to check latest Gosling version: %s", exc)
            raise RuntimeError(
                f"Failed to check latest Gosling version: {exc}"
            ) from exc

    async def download_gosling_from_github(self, version: str) -> str:
        """Download a specific Gosling CLI version from GitHub and upload to S3."""
        self.info("Downloading Gosling CLI version %s from GitHub", version)
        download_url = (
            f"https://github.com/{self.GOSLING_REPO}/releases/download"
            f"/v{version}/gosling-linux-amd64"
        )
        try:
            with tempfile.NamedTemporaryFile(
                delete=False, suffix="-gosling"
            ) as tmp_file:
                response = self.session.get(download_url, timeout=300)
                response.raise_for_status()
                tmp_file.write(response.content)
                tmp_path = tmp_file.name
            checksum = self._calculate_checksum(tmp_path)
            self.info("Downloaded Gosling CLI %s, checksum: %s", version, checksum)
            s3_path = await self.binary_version_service.upload_version(
                binary_name="gosling",
                version=version,
                file_path=tmp_path,
                checksum=checksum,
            )
            os.unlink(tmp_path)
            self.info("Successfully uploaded Gosling CLI %s to S3", version)
            return s3_path
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.error("Failed to download Gosling CLI %s: %s", version, exc)
            raise RuntimeError(
                f"Failed to download Gosling CLI {version}: {exc}"
            ) from exc

    def check_latest_opentofu_version(self) -> str:
        """Check the latest OpenTofu version available on GitHub."""
        updater = OpenTofuUpdateGithub(schema=self.schema)
        latest_version = (
            updater._get_latest_version()
        )  # pylint: disable=protected-access
        self.info("Latest OpenTofu version on GitHub: %s", latest_version)
        return latest_version

    async def check_and_download_new_versions(self) -> dict[str, str | None]:
        """Check for new binary versions and download them if available."""
        result: dict[str, str | None] = {"gosling": None, "opentofu": None}
        try:
            latest_gosling = self.check_latest_gosling_version()
            await self.binary_version_service.list_versions("gosling")
            active_gosling = self.binary_version_service.active_version
            if active_gosling is None or active_gosling.version != latest_gosling:
                self.warning("New Gosling CLI version available: %s", latest_gosling)
                await self.download_gosling_from_github(latest_gosling)
                result["gosling"] = latest_gosling
            else:
                self.info("Gosling CLI is up to date: %s", latest_gosling)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.error("Failed to check/download Gosling CLI: %s", exc)
        try:
            latest_opentofu = self.check_latest_opentofu_version()
            await self.binary_version_service.list_versions("opentofu")
            active_opentofu = self.binary_version_service.active_version
            if active_opentofu is None or active_opentofu.version != latest_opentofu:
                self.warning("New OpenTofu version available: %s", latest_opentofu)
                result["opentofu"] = latest_opentofu
            else:
                self.info("OpenTofu is up to date: %s", latest_opentofu)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.error("Failed to check OpenTofu version: %s", exc)
        return result

    @private
    def _calculate_checksum(self, file_path: str) -> str:
        """Calculate SHA256 checksum of a file."""
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
