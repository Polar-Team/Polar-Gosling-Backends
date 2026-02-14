"""S3 Artifact Caching Service for OpenTofu provider plugins and modules.

This service manages caching of OpenTofu artifacts in S3 to avoid repeated downloads:
- Provider plugins
- Terraform modules
- .terraform directories

Task 16: MotherGoose Backend - OpenTofu Integration for Runner Deployment
"""

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from typing import Any, Dict, Optional

import aioboto3
from botocore.exceptions import ClientError

from app.util.base_logging import logged


@logged
class S3ArtifactCache:
    """
    Manages caching of OpenTofu artifacts in S3.

    Caches:
    - Provider plugins (per version)
    - Terraform modules (per version)
    - .terraform directories (per Egg)
    - Lock files for version consistency
    """

    # pylint: disable=no-member,too-many-arguments,too-many-positional-arguments

    def __init__(
        self,
        bucket_name: str,
        region: str,
        endpoint_url: Optional[str] = None,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
    ):
        """
        Initialize S3 artifact cache.

        Args:
            bucket_name: S3 bucket name for artifact storage
            region: AWS/YC region
            endpoint_url: Custom S3 endpoint (for Yandex Cloud)
            aws_access_key_id: AWS access key ID (optional)
            aws_secret_access_key: AWS secret access key (optional)
        """
        self.bucket_name = bucket_name
        self.region = region
        self.endpoint_url = endpoint_url
        self.aws_access_key_id = aws_access_key_id
        self.aws_secret_access_key = aws_secret_access_key
        self._session: Optional[aioboto3.Session] = None  # type: ignore[no-any-unimported]

    def _get_session(self) -> aioboto3.Session:  # type: ignore[no-any-unimported]
        """Get or create aioboto3 session."""
        if self._session is None:
            session_kwargs: dict = {"region_name": self.region}
            if self.aws_access_key_id and self.aws_secret_access_key:
                session_kwargs["aws_access_key_id"] = self.aws_access_key_id
                session_kwargs["aws_secret_access_key"] = self.aws_secret_access_key
            self._session = aioboto3.Session(**session_kwargs)
        return self._session

    async def _get_s3_client(self) -> Any:  # type: ignore[misc]
        """Create S3 client with proper configuration."""
        session = self._get_session()
        client_kwargs: dict = {}
        if self.endpoint_url:
            client_kwargs["endpoint_url"] = self.endpoint_url
        return session.client("s3", **client_kwargs)

    async def cache_provider_plugin(
        self,
        provider_name: str,
        provider_version: str,
        provider_source: str,
        plugin_path: str,
    ) -> str:
        """
        Cache a provider plugin in S3.

        Args:
            provider_name: Provider name (e.g., "aws", "yandex")
            provider_version: Provider version (e.g., "5.0.0")
            provider_source: Provider source (e.g., "hashicorp/aws")
            plugin_path: Local path to plugin file

        Returns:
            S3 key where plugin was cached
        """
        s3_key = (
            f"terraform-plugins/{provider_source}/{provider_version}/"
            f"{os.path.basename(plugin_path)}"
        )

        self.info(
            f"Caching provider plugin {provider_name} v{provider_version} to S3: {s3_key}"
        )

        async with await self._get_s3_client() as s3:
            try:
                # Upload plugin file
                with open(plugin_path, "rb") as f:
                    await s3.upload_fileobj(
                        f,
                        self.bucket_name,
                        s3_key,
                        ExtraArgs={
                            "Metadata": {
                                "provider-name": provider_name,
                                "provider-version": provider_version,
                                "provider-source": provider_source,
                                "cached-at": datetime.now(UTC).isoformat(),
                            }
                        },
                    )

                self.info(f"Successfully cached provider plugin: {s3_key}")
                return s3_key

            except ClientError as e:
                self.error(f"Failed to cache provider plugin: {e}")
                raise

    async def get_cached_provider_plugin(
        self,
        provider_source: str,
        provider_version: str,
        plugin_filename: str,
        download_path: str,
    ) -> bool:
        """
        Retrieve cached provider plugin from S3.

        Args:
            provider_source: Provider source (e.g., "hashicorp/aws")
            provider_version: Provider version (e.g., "5.0.0")
            plugin_filename: Plugin filename
            download_path: Local path to download plugin

        Returns:
            True if plugin was found and downloaded, False otherwise
        """
        s3_key = (
            f"terraform-plugins/{provider_source}/{provider_version}/{plugin_filename}"
        )

        self.info(f"Checking for cached provider plugin: {s3_key}")

        async with await self._get_s3_client() as s3:
            try:
                # Check if plugin exists
                await s3.head_object(Bucket=self.bucket_name, Key=s3_key)

                # Download plugin
                os.makedirs(os.path.dirname(download_path), exist_ok=True)
                with open(download_path, "wb") as f:
                    await s3.download_fileobj(self.bucket_name, s3_key, f)

                self.info(f"Successfully retrieved cached provider plugin: {s3_key}")
                return True

            except ClientError as e:
                if e.response["Error"]["Code"] == "404":
                    self.info(f"Provider plugin not found in cache: {s3_key}")
                    return False
                self.error(f"Failed to retrieve cached provider plugin: {e}")
                raise

    # pylint: disable=too-many-locals
    async def cache_module(
        self,
        module_name: str,
        module_version: str,
        module_path: str,
    ) -> str:
        """
        Cache a Terraform module in S3.

        Args:
            module_name: Module name (e.g., "compute-module")
            module_version: Module version (e.g., "1.0.0")
            module_path: Local path to module directory

        Returns:
            S3 key prefix where module was cached
        """
        s3_prefix = f"modules/{module_name}/{module_version}/"

        self.info(f"Caching module {module_name} v{module_version} to S3: {s3_prefix}")

        async with await self._get_s3_client() as s3:
            try:
                # Upload all files in module directory
                for root, _, files in os.walk(module_path):
                    for file in files:
                        local_path = os.path.join(root, file)
                        relative_path = os.path.relpath(local_path, module_path)
                        s3_key = f"{s3_prefix}{relative_path}"

                        with open(local_path, "rb") as f:
                            await s3.upload_fileobj(
                                f,
                                self.bucket_name,
                                s3_key,
                                ExtraArgs={
                                    "Metadata": {
                                        "module-name": module_name,
                                        "module-version": module_version,
                                        "cached-at": datetime.now(UTC).isoformat(),
                                    }
                                },
                            )

                self.info(f"Successfully cached module: {s3_prefix}")
                return s3_prefix

            except ClientError as e:
                self.error(f"Failed to cache module: {e}")
                raise

    async def get_cached_module(
        self,
        module_name: str,
        module_version: str,
        download_path: str,
    ) -> bool:
        """
        Retrieve cached module from S3.

        Args:
            module_name: Module name (e.g., "compute-module")
            module_version: Module version (e.g., "1.0.0")
            download_path: Local path to download module

        Returns:
            True if module was found and downloaded, False otherwise
        """
        s3_prefix = f"modules/{module_name}/{module_version}/"

        self.info(f"Checking for cached module: {s3_prefix}")

        async with await self._get_s3_client() as s3:
            try:
                # List all objects with prefix
                paginator = s3.get_paginator("list_objects_v2")
                pages = paginator.paginate(Bucket=self.bucket_name, Prefix=s3_prefix)

                found_objects = False
                async for page in pages:
                    if "Contents" not in page:
                        continue

                    found_objects = True
                    for obj in page["Contents"]:
                        s3_key = obj["Key"]
                        relative_path = s3_key[len(s3_prefix) :]
                        local_path = os.path.join(download_path, relative_path)

                        # Create directory if needed
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)

                        # Download file
                        with open(local_path, "wb") as f:
                            await s3.download_fileobj(self.bucket_name, s3_key, f)

                if not found_objects:
                    self.info(f"Module not found in cache: {s3_prefix}")
                    return False

                self.info(f"Successfully retrieved cached module: {s3_prefix}")
                return True

            except ClientError as e:
                self.error(f"Failed to retrieve cached module: {e}")
                raise

    async def cache_terraform_dir(
        self,
        egg_name: str,
        terraform_dir: str,
    ) -> str:
        """
        Cache .terraform directory for an Egg in S3.

        Args:
            egg_name: Egg name
            terraform_dir: Local path to .terraform directory

        Returns:
            S3 key prefix where .terraform directory was cached
        """
        s3_prefix = f".terraform/{egg_name}/"

        self.info(f"Caching .terraform directory for {egg_name} to S3: {s3_prefix}")

        async with await self._get_s3_client() as s3:
            try:
                # Upload all files in .terraform directory
                for root, _, files in os.walk(terraform_dir):
                    for file in files:
                        local_path = os.path.join(root, file)
                        relative_path = os.path.relpath(local_path, terraform_dir)
                        s3_key = f"{s3_prefix}{relative_path}"

                        with open(local_path, "rb") as f:
                            await s3.upload_fileobj(
                                f,
                                self.bucket_name,
                                s3_key,
                                ExtraArgs={
                                    "Metadata": {
                                        "egg-name": egg_name,
                                        "cached-at": datetime.now(UTC).isoformat(),
                                    }
                                },
                            )

                self.info(f"Successfully cached .terraform directory: {s3_prefix}")
                return s3_prefix

            except ClientError as e:
                self.error(f"Failed to cache .terraform directory: {e}")
                raise

    async def get_cached_terraform_dir(
        self,
        egg_name: str,
        download_path: str,
    ) -> bool:
        """
        Retrieve cached .terraform directory from S3.

        Args:
            egg_name: Egg name
            download_path: Local path to download .terraform directory

        Returns:
            True if .terraform directory was found and downloaded, False otherwise
        """
        s3_prefix = f".terraform/{egg_name}/"

        self.info(f"Checking for cached .terraform directory: {s3_prefix}")

        async with await self._get_s3_client() as s3:
            try:
                # List all objects with prefix
                paginator = s3.get_paginator("list_objects_v2")
                pages = paginator.paginate(Bucket=self.bucket_name, Prefix=s3_prefix)

                found_objects = False
                async for page in pages:
                    if "Contents" not in page:
                        continue

                    found_objects = True
                    for obj in page["Contents"]:
                        s3_key = obj["Key"]
                        relative_path = s3_key[len(s3_prefix) :]
                        local_path = os.path.join(download_path, relative_path)

                        # Create directory if needed
                        os.makedirs(os.path.dirname(local_path), exist_ok=True)

                        # Download file
                        with open(local_path, "wb") as f:
                            await s3.download_fileobj(self.bucket_name, s3_key, f)

                if not found_objects:
                    self.info(f".terraform directory not found in cache: {s3_prefix}")
                    return False

                self.info(
                    f"Successfully retrieved cached .terraform directory: {s3_prefix}"
                )
                return True

            except ClientError as e:
                self.error(f"Failed to retrieve cached .terraform directory: {e}")
                raise

    async def cache_lock_file(
        self,
        lock_type: str,  # "provider" or "module"
        lock_data: Dict[str, Any],
    ) -> str:
        """
        Cache lock file in S3.

        Args:
            lock_type: Type of lock file ("provider" or "module")
            lock_data: Lock file data as dictionary

        Returns:
            S3 key where lock file was cached
        """
        s3_key = (
            "terraform-plugins/lock.json"
            if lock_type == "provider"
            else "modules/lock.json"
        )

        self.info(f"Caching {lock_type} lock file to S3: {s3_key}")

        async with await self._get_s3_client() as s3:
            try:
                # Upload lock file as JSON
                lock_json = json.dumps(lock_data, indent=2)
                await s3.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=lock_json.encode("utf-8"),
                    ContentType="application/json",
                    Metadata={
                        "lock-type": lock_type,
                        "cached-at": datetime.now(UTC).isoformat(),
                    },
                )

                self.info(f"Successfully cached lock file: {s3_key}")
                return s3_key

            except ClientError as e:
                self.error(f"Failed to cache lock file: {e}")
                raise

    async def get_cached_lock_file(
        self,
        lock_type: str,  # "provider" or "module"
    ) -> Optional[Dict[str, Any]]:
        """
        Retrieve cached lock file from S3.

        Args:
            lock_type: Type of lock file ("provider" or "module")

        Returns:
            Lock file data as dictionary, or None if not found
        """
        s3_key = (
            "terraform-plugins/lock.json"
            if lock_type == "provider"
            else "modules/lock.json"
        )

        self.info(f"Checking for cached {lock_type} lock file: {s3_key}")

        async with await self._get_s3_client() as s3:
            try:
                # Download lock file
                response = await s3.get_object(Bucket=self.bucket_name, Key=s3_key)
                lock_json = await response["Body"].read()
                lock_data = json.loads(lock_json.decode("utf-8"))

                self.info(f"Successfully retrieved cached lock file: {s3_key}")
                return lock_data

            except ClientError as e:
                if e.response["Error"]["Code"] == "NoSuchKey":
                    self.info(f"Lock file not found in cache: {s3_key}")
                    return None
                self.error(f"Failed to retrieve cached lock file: {e}")
                raise

    async def invalidate_cache(
        self,
        cache_type: str,  # "provider", "module", "terraform", or "all"
        # provider_source/version, module_name/version, or egg_name
        identifier: Optional[str] = None,
    ) -> None:
        """
        Invalidate cached artifacts.

        Args:
            cache_type: Type of cache to invalidate
            identifier: Specific identifier to invalidate (optional)
        """
        self.info(
            f"Invalidating {cache_type} cache"
            + (f" for {identifier}" if identifier else "")
        )

        async with await self._get_s3_client() as s3:
            try:
                if cache_type == "all":
                    # Delete all cached artifacts
                    prefixes = ["terraform-plugins/", "modules/", ".terraform/"]
                elif cache_type == "provider":
                    prefixes = (
                        [f"terraform-plugins/{identifier}/"]
                        if identifier
                        else ["terraform-plugins/"]
                    )
                elif cache_type == "module":
                    prefixes = (
                        [f"modules/{identifier}/"] if identifier else ["modules/"]
                    )
                elif cache_type == "terraform":
                    prefixes = (
                        [f".terraform/{identifier}/"] if identifier else [".terraform/"]
                    )
                else:
                    self.error(f"Invalid cache type: {cache_type}")
                    raise ValueError(f"Invalid cache type: {cache_type}")

                for prefix in prefixes:
                    # List and delete all objects with prefix
                    paginator = s3.get_paginator("list_objects_v2")
                    pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)

                    async for page in pages:
                        if "Contents" not in page:
                            continue

                        objects_to_delete = [
                            {"Key": obj["Key"]} for obj in page["Contents"]
                        ]
                        if objects_to_delete:
                            await s3.delete_objects(
                                Bucket=self.bucket_name,
                                Delete={"Objects": objects_to_delete},
                            )

                self.info(f"Successfully invalidated {cache_type} cache")

            except ClientError as e:
                self.error(f"Failed to invalidate cache: {e}")
                raise

    def compute_checksum(self, file_path: str) -> str:
        """
        Compute SHA256 checksum of a file.

        Args:
            file_path: Path to file

        Returns:
            SHA256 checksum as hex string
        """
        sha256_hash = hashlib.sha256()
        with open(file_path, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()

    async def verify_cache_integrity(
        self,
        s3_key: str,
        expected_checksum: str,
    ) -> bool:
        """
        Verify integrity of cached artifact by comparing checksums.

        Args:
            s3_key: S3 key of cached artifact
            expected_checksum: Expected SHA256 checksum

        Returns:
            True if checksums match, False otherwise
        """
        self.info(f"Verifying cache integrity for: {s3_key}")

        async with await self._get_s3_client() as s3:
            try:
                # Get object metadata
                response = await s3.head_object(Bucket=self.bucket_name, Key=s3_key)

                # Check if checksum is stored in metadata
                metadata = response.get("Metadata", {})
                stored_checksum = metadata.get("sha256-checksum")

                if stored_checksum:
                    matches = stored_checksum == expected_checksum
                    if matches:
                        self.info(f"Cache integrity verified: {s3_key}")
                    else:
                        self.warning(f"Cache integrity check failed: {s3_key}")
                    return matches

                # If no checksum in metadata, download and compute
                self.warning(
                    f"No checksum in metadata for {s3_key}, downloading to verify"
                )

                with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
                    await s3.download_fileobj(self.bucket_name, s3_key, tmp_file)
                    tmp_path = tmp_file.name

                computed_checksum = self.compute_checksum(tmp_path)
                os.unlink(tmp_path)

                matches = computed_checksum == expected_checksum
                if matches:
                    self.info(f"Cache integrity verified: {s3_key}")
                else:
                    self.warning(f"Cache integrity check failed: {s3_key}")
                return matches

            except ClientError as e:
                self.error(f"Failed to verify cache integrity: {e}")
                raise
