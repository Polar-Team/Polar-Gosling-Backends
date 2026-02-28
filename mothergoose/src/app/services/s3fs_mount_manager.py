"""
S3FS Mount Manager

Manages mounting S3 buckets as filesystems using s3fs.
This allows direct filesystem access to S3 objects without explicit upload/download.

Task 12.3: Binary Version Management System
"""

from pathlib import Path
from typing import Any, Dict, Optional

import s3fs

from app.util.base_logging import logged


@logged
class S3FSMountManager:
    """Manager for mounting S3 buckets using s3fs."""

    # pylint: disable=no-member

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        s3_bucket: str,
        mount_point: str = "/mnt",
        s3_endpoint_url: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
    ) -> None:
        """
        Initialize S3FS Mount Manager.

        Args:
            s3_bucket: S3 bucket name to mount
            mount_point: Local mount point path (default: /mnt/s3-binaries)
            s3_endpoint_url: Custom S3 endpoint (for Yandex Cloud)
            aws_access_key_id: AWS access key ID (optional)
            aws_secret_access_key: AWS secret access key (optional)
        """
        self.s3_bucket = s3_bucket
        self.mount_point = Path(mount_point)
        self.s3_endpoint_url = s3_endpoint_url
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self._fs: Optional[Any] = None

    @property
    def fs(self) -> Any:
        """Get or create S3 filesystem instance."""
        if self._fs is None:
            self._mount()
        return self._fs  # type: ignore[return-value]

    def _mount(self) -> None:
        """
        Mount S3 bucket as filesystem using s3fs.

        Creates S3FileSystem instance with proper configuration.
        """
        self.info("Mounting S3 bucket %s at %s", self.s3_bucket, self.mount_point)

        # Prepare s3fs configuration
        fs_kwargs: Dict[str, Any] = {
            "anon": False,  # Require authentication
        }

        if self.s3_endpoint_url:
            fs_kwargs["client_kwargs"] = {"endpoint_url": self.s3_endpoint_url}

        if self.aws_access_key_id and self.aws_secret_access_key:
            fs_kwargs["key"] = self.aws_access_key_id
            fs_kwargs["secret"] = self.aws_secret_access_key

        try:
            self._fs = s3fs.S3FileSystem(**fs_kwargs)
            self.info("S3 filesystem mounted successfully")

            # Ensure mount point directory exists locally
            self.mount_point.mkdir(parents=True, exist_ok=True)
            self.debug("Mount point directory created: %s", self.mount_point)

        except Exception as exc:
            self.error("Failed to mount S3 filesystem: %s", exc)
            raise RuntimeError(f"Failed to mount S3 filesystem: {exc}") from exc

    def get_s3_path(self, relative_path: str) -> str:
        """
        Get full S3 path for a relative path.

        Args:
            relative_path: Relative path within bucket (e.g., "gosling/1.0.0/gosling")

        Returns:
            str: Full S3 path (e.g., "bucket-name/gosling/1.0.0/gosling")
        """
        return f"{self.s3_bucket}/{relative_path}"

    def get_local_path(self, relative_path: str) -> Path:
        """
        Get local mount point path for a relative path.

        Args:
            relative_path: Relative path within bucket (e.g., "gosling/1.0.0/gosling")

        Returns:
            Path: Local path (e.g., "/mnt/s3-binaries/gosling/1.0.0/gosling")
        """
        return self.mount_point / relative_path

    def exists(self, relative_path: str) -> bool:
        """
        Check if a file exists in the mounted S3 bucket.

        Args:
            relative_path: Relative path within bucket

        Returns:
            bool: True if file exists, False otherwise
        """
        s3_path = self.get_s3_path(relative_path)
        try:
            return self.fs.exists(s3_path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.warning("Error checking if %s exists: %s", s3_path, exc)
            return False

    def read_bytes(self, relative_path: str) -> bytes:
        """
        Read file content as bytes from mounted S3 bucket.

        Args:
            relative_path: Relative path within bucket

        Returns:
            bytes: File content

        Raises:
            FileNotFoundError: If file doesn't exist
            RuntimeError: If read fails
        """
        s3_path = self.get_s3_path(relative_path)
        self.debug("Reading file from S3: %s", s3_path)

        try:
            with self.fs.open(s3_path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            self.error("File not found: %s", s3_path)
            raise
        except Exception as exc:
            self.error("Failed to read file %s: %s", s3_path, exc)
            raise RuntimeError(f"Failed to read file {s3_path}: {exc}") from exc

    def write_bytes(self, relative_path: str, content: bytes) -> None:
        """
        Write bytes to file in mounted S3 bucket.

        Args:
            relative_path: Relative path within bucket
            content: File content as bytes

        Raises:
            RuntimeError: If write fails
        """
        s3_path = self.get_s3_path(relative_path)
        self.debug("Writing file to S3: %s", s3_path)

        try:
            # Ensure parent directory exists
            parent_path = "/".join(s3_path.split("/")[:-1])
            self.fs.makedirs(parent_path, exist_ok=True)

            # Write file
            with self.fs.open(s3_path, "wb") as f:
                f.write(content)

            self.info("Successfully wrote file to S3: %s", s3_path)

        except Exception as exc:
            self.error("Failed to write file %s: %s", s3_path, exc)
            raise RuntimeError(f"Failed to write file {s3_path}: {exc}") from exc

    def copy_from_local(self, local_path: str, relative_path: str) -> None:
        """
        Copy a local file to the mounted S3 bucket.

        Args:
            local_path: Local file path
            relative_path: Relative path within bucket

        Raises:
            FileNotFoundError: If local file doesn't exist
            RuntimeError: If copy fails
        """
        if not Path(local_path).exists():
            raise FileNotFoundError(f"Local file not found: {local_path}")

        s3_path = self.get_s3_path(relative_path)
        self.info("Copying %s to S3: %s", local_path, s3_path)

        try:
            # Read local file
            with open(local_path, "rb") as f:
                content = f.read()

            # Write to S3
            self.write_bytes(relative_path, content)

            self.info("Successfully copied file to S3")

        except Exception as exc:
            self.error("Failed to copy file to S3: %s", exc)
            raise RuntimeError(f"Failed to copy file to S3: {exc}") from exc

    def list_directory(self, relative_path: str = "") -> list[str]:
        """
        List files in a directory in the mounted S3 bucket.

        Args:
            relative_path: Relative directory path within bucket (default: root)

        Returns:
            list[str]: List of file paths relative to bucket root
        """
        s3_path = self.get_s3_path(relative_path) if relative_path else self.s3_bucket
        self.debug("Listing directory: %s", s3_path)

        try:
            files = self.fs.ls(s3_path)
            # Remove bucket prefix from paths
            return [f.replace(f"{self.s3_bucket}/", "") for f in files]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.warning("Error listing directory %s: %s", s3_path, exc)
            return []


# Global S3FS mount manager instance
# pylint: disable=invalid-name
s3fs_mount_manager: Optional[S3FSMountManager] = None
