"""
Gosling CLI Binary Lifecycle Manager

Manages the lifecycle of Gosling CLI binaries using S3FS mounted filesystem.
Handles version management, caching, and cleanup of old versions.

Task 12.5: Gosling CLI Binary Lifecycle Management
"""

import os
import shutil
from pathlib import Path
from typing import Optional

from app.services.binary_version_service import BinaryVersionService
from app.services.s3fs_mount_manager import S3FSMountManager
from app.util.base_logging import logged


@logged
class GoslingBinaryManager:
    """Manager for Gosling CLI binary lifecycle operations."""

    # pylint: disable=no-member

    def __init__(
        self,
        binary_version_service: BinaryVersionService,
        s3fs_manager: S3FSMountManager,
        cache_dir: str = "/tmp/gosling",
        max_cached_versions: int = 3,
    ) -> None:
        """
        Initialize Gosling Binary Manager.

        Args:
            binary_version_service: Service for managing binary versions
            s3fs_manager: S3FS mount manager for filesystem access to S3
            cache_dir: Directory for caching downloaded binaries (default: /tmp/gosling)
            max_cached_versions: Maximum number of versions to keep cached (default: 3)
        """
        self.binary_version_service = binary_version_service
        self.s3fs_manager = s3fs_manager
        self.cache_dir = Path(cache_dir)
        self.max_cached_versions = max_cached_versions
        self._active_binary_path: Optional[str] = None

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.info("GoslingBinaryManager initialized with cache_dir: %s", self.cache_dir)

    @property
    def active_binary_path(self) -> Optional[str]:
        """Get the path to the currently active Gosling CLI binary."""
        return self._active_binary_path

    async def download_active_version(self) -> str:
        """
        Download the active Gosling CLI version from S3 (via mounted filesystem) to local cache.

        This method is called on MotherGoose startup to ensure the active
        version is available locally.

        Returns:
            str: Path to the downloaded binary

        Raises:
            RuntimeError: If no active version is found or download fails
        """
        self.info("Downloading active Gosling CLI version...")

        # Get active version from database
        await self.binary_version_service.get_active_version()
        active_version = self.binary_version_service.active_version

        if active_version is None:
            raise RuntimeError(
                "No active Gosling CLI version found in database. "
                "Upload and activate a version first."
            )

        version = active_version.version
        self.info("Active Gosling CLI version: %s", version)

        # Check if already cached
        local_path = self._get_version_path(version)
        if local_path.exists():
            self.info("Gosling CLI v%s already cached at: %s", version, local_path)

            # Verify checksum
            if self.binary_version_service.verify_checksum(
                str(local_path), active_version.sha256_checksum
            ):
                self._active_binary_path = str(local_path)
                self.info("Checksum verified for cached binary")
                return str(local_path)

            self.warning("Cached binary checksum mismatch. Re-downloading from S3...")
            local_path.unlink()

        # Download from S3 via mounted filesystem
        await self.download_and_cache(version, str(local_path))

        # Verify checksum after download
        if not self.binary_version_service.verify_checksum(
            str(local_path), active_version.sha256_checksum
        ):
            raise RuntimeError(
                f"Downloaded binary checksum mismatch for version {version}"
            )

        self._active_binary_path = str(local_path)
        self.info("Active Gosling CLI binary ready at: %s", local_path)

        # Cleanup old versions
        await self._cleanup_old_versions()

        return str(local_path)

    async def download_and_cache(self, version: str, local_path: str) -> None:
        """
        Download a specific Gosling CLI version from S3 (via mounted filesystem) and cache locally.

        Args:
            version: Version to download (e.g., "1.0.0")
            local_path: Local path to save the binary

        Raises:
            RuntimeError: If download fails
        """
        self.info("Downloading Gosling CLI v%s to %s", version, local_path)

        try:
            # Read from S3 via mounted filesystem
            s3_relative_path = f"gosling/{version}/gosling"
            binary_content = self.s3fs_manager.read_bytes(s3_relative_path)

            # Ensure parent directory exists
            Path(local_path).parent.mkdir(parents=True, exist_ok=True)

            # Write to local cache
            with open(local_path, "wb") as f:
                f.write(binary_content)

            # Make binary executable
            Path(local_path).chmod(0o755)

            self.info("Successfully downloaded Gosling CLI v%s", version)

        except Exception as exc:
            self.error("Failed to download Gosling CLI v%s: %s", version, exc)
            raise RuntimeError(
                f"Failed to download Gosling CLI v{version}: {exc}"
            ) from exc

    async def verify_and_activate(self, version: str) -> str:
        """
        Verify and activate a specific Gosling CLI version.

        This method downloads the version if not cached, verifies its checksum,
        and updates the active binary path.

        Args:
            version: Version to activate (e.g., "1.0.0")

        Returns:
            str: Path to the activated binary

        Raises:
            RuntimeError: If version not found or verification fails
        """
        self.info("Verifying and activating Gosling CLI v%s", version)

        # Get version metadata from database
        await self.binary_version_service.list_versions()
        versions = self.binary_version_service.versions_list or []

        version_metadata = next((v for v in versions if v.version == version), None)

        if version_metadata is None:
            raise RuntimeError(
                f"Gosling CLI version {version} not found in database. "
                "Upload the version first."
            )

        # Check if already cached
        local_path = self._get_version_path(version)
        if not local_path.exists():
            self.info("Version %s not cached. Downloading from S3...", version)
            await self.download_and_cache(version, str(local_path))

        # Verify checksum
        if not self.binary_version_service.verify_checksum(
            str(local_path), version_metadata.sha256_checksum
        ):
            self.warning("Checksum mismatch for cached binary. Re-downloading...")
            local_path.unlink()
            await self.download_and_cache(version, str(local_path))

            # Verify again after re-download
            if not self.binary_version_service.verify_checksum(
                str(local_path), version_metadata.sha256_checksum
            ):
                raise RuntimeError(
                    f"Checksum verification failed for Gosling CLI v{version}"
                )

        # Update active binary path
        self._active_binary_path = str(local_path)
        self.info("Activated Gosling CLI v%s at: %s", version, local_path)

        # Update GOSLING_CLI_PATH environment variable
        os.environ["GOSLING_CLI_PATH"] = str(local_path)
        self.info("Updated GOSLING_CLI_PATH environment variable: %s", local_path)

        # Cleanup old versions
        await self._cleanup_old_versions()

        return str(local_path)

    def _get_version_path(self, version: str) -> Path:
        """
        Get the local path for a specific version.

        Args:
            version: Version string (e.g., "1.0.0")

        Returns:
            Path: Local path for the version binary
        """
        return self.cache_dir / version / "gosling"

    async def _cleanup_old_versions(self) -> None:
        """
        Clean up old cached versions, keeping only the most recent N versions.

        Keeps the last max_cached_versions versions based on modification time.
        """
        self.info("Cleaning up old Gosling CLI versions...")

        # Get all version directories
        version_dirs = [d for d in self.cache_dir.iterdir() if d.is_dir()]

        if len(version_dirs) <= self.max_cached_versions:
            self.debug(
                "Only %d versions cached (max: %d). No cleanup needed.",
                len(version_dirs),
                self.max_cached_versions,
            )
            return

        # Sort by modification time (newest first)
        version_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)

        # Remove old versions
        versions_to_remove = version_dirs[self.max_cached_versions :]
        for version_dir in versions_to_remove:
            version_name = version_dir.name
            self.info("Removing old cached version: %s", version_name)

            try:
                shutil.rmtree(version_dir)
                self.debug("Successfully removed: %s", version_dir)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.warning("Failed to remove old version %s: %s", version_name, exc)

        self.info(
            "Cleanup complete. Kept %d most recent versions.",
            self.max_cached_versions,
        )

    def get_binary_path_for_version(self, version: Optional[str] = None) -> str:
        """
        Get the binary path for a specific version or the active version.

        Args:
            version: Specific version to get path for. If None, returns active version path.

        Returns:
            str: Path to the binary

        Raises:
            RuntimeError: If no active version is set and version is None
        """
        if version is None:
            if self._active_binary_path is None:
                raise RuntimeError(
                    "No active Gosling CLI version set. "
                    "Call download_active_version() first."
                )
            return self._active_binary_path

        return str(self._get_version_path(version))


# Global Gosling binary manager instance
# pylint: disable=invalid-name
gosling_binary_manager: Optional[GoslingBinaryManager] = None
