"""
Binary Version Service

Task 12.4: Binary Version Management API Endpoints
Manages binary versions (Gosling CLI and OpenTofu) in the database and S3.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from app.db.manage_db import AsyncYDBFunctionsCollections
from app.db.ydb_connection import AsyncYDBOperations
from app.model.runners_models import BinaryVersion
from app.schema.dynamodb_schemas import DynamoDBSchema
from app.schema.ydb_schemas import YDBSchema
from app.services.s3fs_mount_manager import S3FSMountManager
from app.util.base_logging import logged


@logged
class BinaryVersionService:
    """Service for managing binary versions in the database and S3."""

    # pylint: disable=no-member

    __versions_list: Optional[List[BinaryVersion]] = None
    __active_version: Optional[BinaryVersion] = None

    def __init__(
        self,
        schema: YDBSchema | DynamoDBSchema,
        s3fs_manager: S3FSMountManager,
    ) -> None:
        """
        Initialize Binary Version Service.

        Args:
            schema: YDB or DynamoDB schema containing table definitions
            s3fs_manager: S3FS mount manager for filesystem access to S3
        """
        self.schema = schema
        self.s3fs_manager = s3fs_manager
        self.__versions_list = None
        self.__active_version = None

    @property
    def versions_list(self) -> Optional[List[BinaryVersion]]:
        """Get the cached list of binary versions."""
        return self.__versions_list

    @property
    def active_version(self) -> Optional[BinaryVersion]:
        """Get the cached active binary version."""
        return self.__active_version

    def verify_checksum(self, file_path: str, expected_checksum: str) -> bool:
        """
        Verify the SHA256 checksum of a file.

        Args:
            file_path: Path to the file to verify
            expected_checksum: Expected SHA256 checksum

        Returns:
            bool: True if checksum matches, False otherwise
        """
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256_hash.update(chunk)
            actual_checksum = sha256_hash.hexdigest()
            return actual_checksum == expected_checksum
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.error("Failed to verify checksum for %s: %s", file_path, exc)
            return False

    async def upload_version(
        self,
        version: str,
        file_path: str,
        checksum: str,
        binary_name: str = "gosling",
    ) -> str:
        """
        Upload a binary version to S3 and record it in the database.

        Args:
            version: Version string (e.g., "1.0.0")
            file_path: Local path to the binary file
            checksum: SHA256 checksum of the binary
            binary_name: Binary name ("gosling" or "opentofu", default: "gosling")

        Returns:
            str: S3 path where the binary was stored

        Raises:
            RuntimeError: If upload fails or checksum verification fails
        """
        self.info("Uploading %s version %s to S3...", binary_name, version)

        # Verify checksum before uploading
        if not self.verify_checksum(file_path, checksum):
            raise RuntimeError(
                f"Checksum verification failed for {binary_name} v{version}"
            )

        # Determine S3 path
        if binary_name == "gosling":
            s3_path = f"gosling/{version}/gosling"
        else:
            s3_path = f"tofu/{version}/tofu"

        # Copy to S3 via mounted filesystem
        self.s3fs_manager.copy_from_local(file_path, s3_path)
        self.info("Uploaded %s v%s to S3 path: %s", binary_name, version, s3_path)

        # Record in database
        version_id = f"{binary_name}-{version}"
        now = datetime.now(timezone.utc).isoformat()

        for table in self.schema.model.tables:
            if table.table_name == "binary_versions":
                table.values_for_operate = (
                    version_id,
                    binary_name,
                    version,
                    s3_path,
                    checksum,
                    False,  # is_active
                    now,  # uploaded_at
                    "",  # activated_at (empty = NULL)
                )

        if isinstance(self.schema, YDBSchema):
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="binary_versions")
        elif isinstance(self.schema, DynamoDBSchema):
            raise NotImplementedError("DynamoDB is not supported yet.")

        self.info("Recorded %s v%s in database", binary_name, version)
        return s3_path

    async def list_versions(
        self,
        binary_name: str = "gosling",
    ) -> None:
        """
        List all binary versions from the database.

        Args:
            binary_name: Binary name to filter by (default: "gosling")

        Sets self.__versions_list with the results.
        """
        self.info("Listing %s versions from database...", binary_name)

        if isinstance(self.schema, YDBSchema):
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.select_parameterized_query,
            )
            await operation.process(
                selected_columns=[
                    "id",
                    "binary_name",
                    "version",
                    "s3_path",
                    "sha256_checksum",
                    "is_active",
                    "uploaded_at",
                    "activated_at",
                ],
                searching_columns=["binary_name"],
                searching_values=[binary_name],
            )
            result = operation.result
            versions = []
            table_idx = next(
                (
                    i
                    for i, t in enumerate(self.schema.model.tables)
                    if t.table_name == "binary_versions"
                ),
                0,
            )
            if result and result[table_idx][0].rows:
                for row in result[table_idx][0].rows:
                    try:
                        bv = BinaryVersion(
                            id=row.id,
                            binary_name=row.binary_name,
                            version=row.version,
                            s3_path=row.s3_path,
                            sha256_checksum=row.sha256_checksum,
                            is_active=bool(row.is_active),
                            uploaded_at=(
                                datetime.fromisoformat(row.uploaded_at)
                                if isinstance(row.uploaded_at, str)
                                else (row.uploaded_at or datetime.now(timezone.utc))
                            ),
                            activated_at=row.activated_at if row.activated_at else None,
                        )
                        versions.append(bv)
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        self.warning("Failed to parse binary version row: %s", exc)
            self.__versions_list = versions
        elif isinstance(self.schema, DynamoDBSchema):
            raise NotImplementedError("DynamoDB is not supported yet.")

    async def get_active_version(
        self,
        binary_name: str = "gosling",
    ) -> None:
        """
        Get the active binary version from the database.

        Args:
            binary_name: Binary name to get active version for (default: "gosling")

        Sets self.__active_version with the result (or None if not found).
        """
        self.info("Getting active %s version from database...", binary_name)

        if isinstance(self.schema, YDBSchema):
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.select_parameterized_query,
            )
            await operation.process(
                selected_columns=[
                    "id",
                    "binary_name",
                    "version",
                    "s3_path",
                    "sha256_checksum",
                    "is_active",
                    "uploaded_at",
                    "activated_at",
                ],
                searching_columns=["binary_name", "is_active"],
                searching_values=[binary_name, True],
            )
            result = operation.result
            table_idx = next(
                (
                    i
                    for i, t in enumerate(self.schema.model.tables)
                    if t.table_name == "binary_versions"
                ),
                0,
            )
            if result and result[table_idx][0].rows:
                row = result[table_idx][0].rows[0]
                try:
                    self.__active_version = BinaryVersion(
                        id=row.id,
                        binary_name=row.binary_name,
                        version=row.version,
                        s3_path=row.s3_path,
                        sha256_checksum=row.sha256_checksum,
                        is_active=bool(row.is_active),
                        uploaded_at=(
                            datetime.fromisoformat(row.uploaded_at)
                            if isinstance(row.uploaded_at, str)
                            else (row.uploaded_at or datetime.now(timezone.utc))
                        ),
                        activated_at=row.activated_at if row.activated_at else None,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.warning("Failed to parse active version row: %s", exc)
                    self.__active_version = None
            else:
                self.__active_version = None
        elif isinstance(self.schema, DynamoDBSchema):
            raise NotImplementedError("DynamoDB is not supported yet.")

    async def activate_version(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        version: str,
        binary_name: str = "gosling",
        actor: str = "system",
    ) -> None:
        """
        Activate a specific binary version.

        Deactivates the current active version and activates the specified one.

        Args:
            version: Version to activate (e.g., "1.0.0")
            binary_name: Binary name (default: "gosling")
            actor: Who is performing the activation (default: "system")

        Raises:
            RuntimeError: If version not found or activation fails
        """
        self.info("Activating %s version %s...", binary_name, version)

        # First, deactivate all currently active versions
        await self.list_versions(binary_name=binary_name)
        versions = self.__versions_list or []

        for v in versions:
            if v.is_active:
                self.info("Deactivating %s v%s...", binary_name, v.version)
                for table in self.schema.model.tables:
                    if table.table_name == "binary_versions":
                        table.values_for_operate = (
                            v.id,
                            v.binary_name,
                            v.version,
                            v.s3_path,
                            v.sha256_checksum,
                            False,  # is_active = False
                            (
                                v.uploaded_at.isoformat()
                                if isinstance(v.uploaded_at, datetime)
                                else v.uploaded_at
                            ),
                            (
                                v.activated_at.isoformat()
                                if isinstance(v.activated_at, datetime)
                                else (v.activated_at or "")
                            ),
                        )
                if isinstance(self.schema, YDBSchema):
                    operation = AsyncYDBOperations(
                        self.schema,
                        AsyncYDBFunctionsCollections.upsert_query,
                    )
                    await operation.process(table_name="binary_versions")

        # Find the version to activate
        target = next((v for v in versions if v.version == version), None)
        if target is None:
            raise RuntimeError(
                f"{binary_name} version {version} not found in database. "
                "Upload the version first."
            )

        # Activate the target version
        now = datetime.now(timezone.utc).isoformat()
        for table in self.schema.model.tables:
            if table.table_name == "binary_versions":
                table.values_for_operate = (
                    target.id,
                    target.binary_name,
                    target.version,
                    target.s3_path,
                    target.sha256_checksum,
                    True,  # is_active = True
                    (
                        target.uploaded_at.isoformat()
                        if isinstance(target.uploaded_at, datetime)
                        else target.uploaded_at
                    ),
                    now,  # activated_at
                )
        if isinstance(self.schema, YDBSchema):
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="binary_versions")

        # Create audit log
        await self._create_audit_log(
            actor=actor,
            action="activate_version",
            resource_type=binary_name,
            resource_id=version,
            details={"version": version, "binary_name": binary_name},
        )

        self.info("Activated %s v%s", binary_name, version)

    async def _create_audit_log(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: Dict[str, Any],
    ) -> None:
        """
        Create an audit log entry.

        Args:
            actor: Who performed the action
            action: Action performed
            resource_type: Type of resource affected
            resource_id: ID of the affected resource
            details: Additional action details
        """
        # Only write audit log if audit_logs table exists in schema
        has_audit_table = any(
            t.table_name == "audit_logs" for t in self.schema.model.tables
        )
        if not has_audit_table:
            self.debug("No audit_logs table in schema, skipping audit log.")
            return

        log_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()

        for table in self.schema.model.tables:
            if table.table_name == "audit_logs":
                table.values_for_operate = (
                    log_id,
                    now,
                    actor,
                    action,
                    resource_type,
                    resource_id,
                    json.dumps(details).encode("utf-8"),
                )

        if isinstance(self.schema, YDBSchema):
            operation = AsyncYDBOperations(
                self.schema,
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await operation.process(table_name="audit_logs")


# Global binary version service instance
# pylint: disable=invalid-name
binary_version_service: Optional[BinaryVersionService] = None
