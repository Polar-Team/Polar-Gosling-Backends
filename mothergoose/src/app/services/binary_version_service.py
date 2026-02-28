"""
Binary Version Service

Task 12.4: Binary Version Management API Endpoints
Manages binary versions (Gosling CLI and OpenTofu) in the database and S3.
Uses the existing gosling_version / opentofu_version YDB tables.
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

# Maps binary_name to YDB table name
_TABLE_FOR_BINARY: Dict[str, str] = {
    "gosling": "gosling_version",
    "opentofu": "opentofu_version",
}

# Maps binary_name to S3 path template
_S3_PATH_FOR_BINARY: Dict[str, str] = {
    "gosling": "gosling/{version}/gosling",
    "opentofu": "tofu/{version}/tofu",
}


def _table_name_for(binary_name: str) -> str:
    try:
        return _TABLE_FOR_BINARY[binary_name]
    except KeyError as exc:
        raise ValueError(
            f"Unknown binary '{binary_name}'. Must be one of: {list(_TABLE_FOR_BINARY)}"
        ) from exc


def _row_to_binary_version(row: Any, binary_name: str) -> BinaryVersion:
    """Convert a YDB row (gosling_version / opentofu_version schema) to BinaryVersion."""
    downloaded_at: datetime = (
        datetime.fromisoformat(str(row.downloaded_at))
        if row.downloaded_at
        else datetime.now(timezone.utc)
    )
    return BinaryVersion(
        id=row.version_id,
        binary_name=binary_name,
        version=row.version,
        s3_path=_S3_PATH_FOR_BINARY.get(binary_name, "").format(version=row.version),
        sha256_checksum=row.sha256_hash,
        is_active=bool(row.active),
        uploaded_at=downloaded_at,
        activated_at=downloaded_at if bool(row.active) else None,
    )


@logged
class BinaryVersionService:
    """Service for managing binary versions in the database and S3."""

    # pylint: disable=no-member, too-many-positional-arguments, too-many-arguments

    def __init__(
        self,
        schema: YDBSchema | DynamoDBSchema,
        s3fs_manager: S3FSMountManager,
    ) -> None:
        self.schema = schema
        self.s3fs_manager = s3fs_manager
        self.__versions_list: Optional[List[BinaryVersion]] = None
        self.__active_version: Optional[BinaryVersion] = None

    @property
    def versions_list(self) -> Optional[List[BinaryVersion]]:
        """Get the cached list of binary versions."""
        return self.__versions_list

    @property
    def active_version(self) -> Optional[BinaryVersion]:
        """Get the cached active binary version."""
        return self.__active_version

    def verify_checksum(self, file_path: str, expected_checksum: str) -> bool:
        """Verify the SHA256 checksum of a file."""
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    sha256_hash.update(chunk)
            return sha256_hash.hexdigest() == expected_checksum
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.error("Failed to verify checksum for %s: %s", file_path, exc)
            return False

    async def list_versions(
        self,
        binary_name: str = "gosling",
    ) -> None:
        """Populate versions_list with all versions for binary_name from the DB."""
        self.info("Listing versions for %s...", binary_name)
        _table_name_for(binary_name)
        operation = AsyncYDBOperations(
            self.schema,  # type: ignore[arg-type]
            AsyncYDBFunctionsCollections.select_parameterized_query,
        )
        await operation.process(
            selected_columns=[
                "version_id",
                "version",
                "source",
                "downloaded_at",
                "sha256_hash",
                "active",
            ],
            searching_columns=[],
            searching_values=[],
        )
        rows = (
            operation.result[0][0].rows
            if operation.result and operation.result[0][0].rows
            else []
        )
        self.__versions_list = [
            _row_to_binary_version(row, binary_name) for row in rows
        ]

    async def get_active_version(
        self,
        binary_name: str = "gosling",
    ) -> None:
        """Populate active_version with the currently active version for binary_name."""
        self.info("Getting active version for %s...", binary_name)
        operation = AsyncYDBOperations(
            self.schema,  # type: ignore[arg-type]
            AsyncYDBFunctionsCollections.select_parameterized_query,
        )
        await operation.process(
            selected_columns=[
                "version_id",
                "version",
                "source",
                "downloaded_at",
                "sha256_hash",
                "active",
            ],
            searching_columns=["active"],
            searching_values=[True],
        )
        rows = (
            operation.result[0][0].rows
            if operation.result and operation.result[0][0].rows
            else []
        )
        self.__active_version = (
            _row_to_binary_version(rows[0], binary_name) if rows else None
        )

    async def activate_version(
        self,
        version: str,
        binary_name: str = "gosling",
        actor: str = "system",
    ) -> None:
        """Activate version for binary_name, deactivating the current active version."""
        self.info("Activating %s v%s...", binary_name, version)
        table_name = _table_name_for(binary_name)

        await self.list_versions(binary_name=binary_name)
        versions = self.__versions_list or []

        target = next((v for v in versions if v.version == version), None)
        if target is None:
            raise ValueError(f"{binary_name} v{version} not found in database")

        now = datetime.now(timezone.utc).isoformat()

        for v in versions:
            if v.is_active:
                for table in self.schema.model.tables:
                    if table.table_name == table_name:
                        table.values_for_operate = (
                            v.id,
                            v.version,
                            "other",
                            (
                                v.uploaded_at.isoformat()
                                if isinstance(v.uploaded_at, datetime)
                                else (v.uploaded_at or now)
                            ),
                            v.sha256_checksum,
                            False,
                        )
                if isinstance(self.schema, YDBSchema):
                    op = AsyncYDBOperations(
                        self.schema,  # type: ignore[arg-type]
                        AsyncYDBFunctionsCollections.upsert_query,
                    )
                    await op.process(table_name=table_name)

        for table in self.schema.model.tables:
            if table.table_name == table_name:
                table.values_for_operate = (
                    target.id,
                    target.version,
                    "other",
                    (
                        target.uploaded_at.isoformat()
                        if isinstance(target.uploaded_at, datetime)
                        else (target.uploaded_at or now)
                    ),
                    target.sha256_checksum,
                    True,
                )
        if isinstance(self.schema, YDBSchema):
            op = AsyncYDBOperations(
                self.schema,  # type: ignore[arg-type]
                AsyncYDBFunctionsCollections.upsert_query,
            )
            await op.process(table_name=table_name)

        await self._create_audit_log(
            actor=actor,
            action="activate_version",
            resource_type="binary_version",
            resource_id=target.id,
            details={"binary_name": binary_name, "version": version},
        )
        self.info("Activated %s v%s", binary_name, version)

    async def upload_version(
        self,
        version: str,
        file_path: str,
        checksum: str,
        binary_name: str = "gosling",
    ) -> str:
        """Verify checksum and copy already-downloaded binary to S3."""
        self.info("Uploading %s version %s to S3...", binary_name, version)

        if not self.verify_checksum(file_path, checksum):
            raise RuntimeError(
                f"Checksum verification failed for {binary_name} v{version}"
            )

        s3_path = _S3_PATH_FOR_BINARY[binary_name].format(version=version)
        self.s3fs_manager.copy_from_local(file_path, s3_path)
        self.info("Uploaded %s v%s to S3 path: %s", binary_name, version, s3_path)

        return s3_path

    async def _create_audit_log(
        self,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: Dict[str, Any],
    ) -> None:
        """Create an audit log entry if the audit_logs table is present."""
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


# pylint: disable=invalid-name
binary_version_service: Optional[BinaryVersionService] = None
