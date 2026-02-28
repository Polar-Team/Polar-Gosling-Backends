"""
Version Resolution Service

Task 12.7: Implements version resolution logic for
    Gosling CLI and OpenTofu binaries.
Resolution order: Egg-specific > Active > Fail
"""

from typing import Optional

from app.model.runners_models import BinaryVersion
from app.schema.dynamodb_schemas import DynamoDBSchema
from app.schema.ydb_schemas import YDBSchema
from app.services.binary_version_service import BinaryVersionService
from app.services.s3fs_mount_manager import S3FSMountManager
from app.util.base_logging import logged
from app.util.exceptions import BinaryVersionNotFoundError


@logged
class VersionResolver:
    """
    Service for resolving binary versions for Egg deployments.

    Implements version resolution logic:
    1. If Egg specifies a version, use that version (must exist)
    2. If Egg doesn't specify a version, use the active version
    3. If required version doesn't exist, fail with descriptive error
    """

    # pylint: disable=no-member

    def __init__(
        self,
        schema: YDBSchema | DynamoDBSchema,
        s3fs_manager: S3FSMountManager,
    ) -> None:
        """
        Initialize version resolver with database schema and S3FS manager.

        Args:
            schema: YDB or DynamoDB schema containing table definitions
            s3fs_manager: S3FS mount manager for filesystem access to S3
        """
        self.schema = schema
        self.binary_version_service = BinaryVersionService(schema, s3fs_manager)

    async def resolve_gosling_version(
        self, egg_version: Optional[str] = None
    ) -> BinaryVersion:
        """
        Resolve Gosling CLI version for an Egg deployment.

        Resolution order:
        1. If egg_version is specified, use that version
            (must exist in binary_versions)
        2. If egg_version is None, use the active version
        3. If required version doesn't exist, raise BinaryVersionNotFoundError

        Args:
            egg_version: Optional Gosling CLI version specified by Egg

        Returns:
            BinaryVersion: Resolved Gosling CLI version

        Raises:
            BinaryVersionNotFoundError: If required version is not available
        """
        if egg_version:
            # Egg specifies a version - must exist
            self.debug(
                f"Egg requires Gosling CLI version: {egg_version}, "
                "checking availability"
            )
            await self.binary_version_service.list_versions(binary_name="gosling")
            versions = self.binary_version_service.versions_list or []

            for version in versions:
                if version.version == egg_version:
                    self.info(
                        f"Resolved Gosling CLI version: {egg_version}."
                        "This is egg-specific version specified by Egg."
                    )
                    return version

            # Version not found - fail deployment
            self.error(
                "Egg requires Gosling CLI version %s, but it is not available",
                egg_version,
            )
            raise BinaryVersionNotFoundError(
                f"Gosling CLI version {egg_version} "
                "required by Egg is not available. "
                "Please upload this version before deploying."
            )

        # No Egg-specific version - use active version
        self.debug("No Egg-specific Gosling CLI version, using active version")
        await self.binary_version_service.get_active_version(binary_name="gosling")
        active_version = self.binary_version_service.active_version

        if not active_version:
            self.error("No active Gosling CLI version found")
            raise BinaryVersionNotFoundError(
                "No active Gosling CLI version found. "
                "Please activate a version before deploying."
            )

        self.info(
            "Resolved Gosling CLI version: %s (Active)",
            active_version.version,
        )
        return active_version

    async def resolve_opentofu_version(
        self, egg_version: Optional[str] = None
    ) -> BinaryVersion:
        """
        Resolve OpenTofu version for an Egg deployment.

        Resolution order:
        1. If egg_version is specified, use that version
            (must exist in binary_versions)
        2. If egg_version is None, use the active version
        3. If required version doesn't exist, raise BinaryVersionNotFoundError

        Args:
            egg_version: Optional OpenTofu version specified by Egg

        Returns:
            BinaryVersion: Resolved OpenTofu version

        Raises:
            BinaryVersionNotFoundError: If required version is not available
        """
        if egg_version:
            # Egg specifies a version - must exist
            self.debug(
                "Egg requires OpenTofu version: %s, checking availability",
                egg_version,
            )
            await self.binary_version_service.list_versions(binary_name="opentofu")
            versions = self.binary_version_service.versions_list or []

            for version in versions:
                if version.version == egg_version:
                    self.info(
                        "Resolved OpenTofu version: %s (Egg-specific)",
                        egg_version,
                    )
                    return version

            # Version not found - fail deployment
            self.error(
                "Egg requires OpenTofu version %s, but it is not available",
                egg_version,
            )
            raise BinaryVersionNotFoundError(
                f"OpenTofu version {egg_version} "
                "required by Egg is not available. "
                "Please upload this version before deploying."
            )

        # No Egg-specific version - use active version
        self.debug("No Egg-specific OpenTofu version, using active version")
        await self.binary_version_service.get_active_version(binary_name="opentofu")
        active_version = self.binary_version_service.active_version

        if not active_version:
            self.error("No active OpenTofu version found")
            raise BinaryVersionNotFoundError(
                "No active OpenTofu version found. "
                "Please activate a version before deploying."
            )

        self.info(
            "Resolved OpenTofu version: %s (Active)",
            active_version.version,
        )
        return active_version
