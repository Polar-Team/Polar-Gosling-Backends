"""
Runner table models for YDB schema definitions.

This module defines table schemas for the GitOps Runner Orchestration system,
following the pattern from opentofu_models.py where all data passes through
schema.model.tables[x].values_for_operate.
"""

# pylint: disable=duplicate-code

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple, Union

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from app.model.pydantic_base_models import PydanticBaseModelORM
from app.schema.db_tables import YDBTableSchema
from app.types.ydb_types import YDBBytes, YDBInt64, YDBType, YDBUtf8
from app.util.generator import generate_eggconfig_id_decorator

# ============================================================================
# Enumerations
# ============================================================================


class RunnerState(str, Enum):
    """Runner state enumeration"""

    ACTIVE = "active"
    IDLE = "idle"
    BUSY = "busy"
    FAILED = "failed"
    TERMINATED = "terminated"


class RunnerType(str, Enum):
    """Runner type enumeration"""

    SERVERLESS = "serverless"
    APEX = "apex"
    NADIR = "nadir"


class CloudProvider(str, Enum):
    """Cloud provider enumeration"""

    YANDEX = "yandex"
    AWS = "aws"


class DeploymentStatus(str, Enum):
    """Deployment plan status"""

    PENDING = "pending"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


class SyncStatus(str, Enum):
    """Sync operation status"""

    SUCCESS = "success"
    FAILED = "failed"


# ============================================================================
# Helper function for creating YDB types
# ============================================================================


def make_ydb_type(ydb_type: str) -> Any:
    """
    Create a YDB type instance based on the provided type string.

    Args:
        ydb_type (str): The type string to create a YDB type for.

    Returns:
        YDB type instance
    """
    return YDBType({"type": ydb_type}).root  # type: ignore[arg-type]


# ============================================================================
# Pydantic Models (for application logic)
# ============================================================================


class Runner(PydanticBaseModelORM):
    """Runtime state for active runners."""

    id: str = Field(..., description="Unique runner identifier (PK)")
    egg_name: str = Field(
        ...,
        description="Name of the Egg this runner belongs to",
    )
    type: RunnerType = Field(
        ...,
        description="Runner type (serverless/apex/nadir)",
    )
    state: RunnerState = Field(..., description="Current runner state")
    cloud_provider: CloudProvider = Field(
        ..., description="Cloud provider hosting this runner"
    )
    region: str = Field(
        ...,
        description="Cloud region where runner is deployed",
    )
    gitlab_runner_id: Optional[int] = Field(
        None, description="GitLab runner registration ID"
    )
    deployed_from_commit: str = Field(
        ..., description="Git commit hash that deployed this runner"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Runner creation timestamp",
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Last update timestamp",
    )
    last_heartbeat: Optional[datetime] = Field(
        None, description="Last heartbeat from runner"
    )
    failure_count: int = Field(
        default=0,
        description="Number of consecutive failures",
    )
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional runner metadata"
    )

    @field_validator("failure_count")
    @classmethod
    def validate_failure_count(cls, value: int) -> int:
        """Ensure failure_count is non-negative."""
        if value < 0:
            raise ValueError("failure_count must be non-negative")
        return value

    @field_validator(
        "metadata",
        mode="before",
    )
    @classmethod
    def validate_metadata(
        cls,
        value: Dict[str, Any] | str | bytes,
    ) -> Dict[str, Any]:
        """Convert metadata from storage format (JSON bytes/string) to dict."""
        if isinstance(value, dict):
            return value
        if isinstance(value, bytes):
            # From YDB storage (String type returns bytes)
            return json.loads(value.decode("utf-8")) if value else {}
        if isinstance(value, str):
            # From JSON string
            return json.loads(value) if value else {}
        raise ValueError("Invalid metadata format")

    @field_validator(
        "created_at",
        "updated_at",
        "last_heartbeat",
        mode="before",
    )
    @classmethod
    def validate_datetime(
        cls,
        value: datetime | str | None,
    ) -> datetime | None:
        """Convert datetime from storage format (ISO string) to datetime object."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise ValueError("Invalid datetime format")

    @field_validator("gitlab_runner_id", mode="before")
    @classmethod
    def validate_gitlab_runner_id_from_storage(
        cls, value: Optional[int]
    ) -> Optional[int]:
        """Convert gitlab_runner_id from storage (0 → None for optional field)."""
        if value == 0:
            return None
        if value is not None and value < 0:
            raise ValueError("gitlab_runner_id must be positive if provided")
        return value

    def to_storage_dict(self) -> Dict[str, Any]:
        """
        Convert Runner model to storage format for YDB.

        Converts:
        - datetime → ISO string
        - dict → JSON bytes
        - None → 0 for optional int fields (YDB doesn't support NULL in some contexts)

        Returns:
            Dictionary with values in YDB storage format
        """
        data = self.model_dump()

        # Convert datetime to ISO string
        for key in ("created_at", "updated_at", "last_heartbeat"):
            if isinstance(data[key], datetime):
                data[key] = data[key].isoformat()

        # Convert metadata dict to JSON bytes
        if isinstance(data["metadata"], dict):
            data["metadata"] = json.dumps(data["metadata"]).encode("utf-8")

        # Convert None to 0 for gitlab_runner_id (YDB Int64 doesn't support NULL)
        if data["gitlab_runner_id"] is None:
            data["gitlab_runner_id"] = 0

        return data


class EggConfig(PydanticBaseModelORM):
    """Cached Egg configuration from Git repository."""

    id: str = Field(..., description="Unique egg config ID (PK)")
    name: str = Field(..., description="Egg name (PK)")
    project_id: int = Field(0, description="GitLab project ID")
    group_id: int = Field(0, description="GitLab group ID")

    config: Dict[str, Any] = Field(..., description="Parsed .fly configuration")
    git_commit: str = Field(..., description="Git commit hash this config came from")
    git_repo_url_secret: str = Field(
        ..., description="Secret URI for Git repository URL"
    )
    gitlab_token_secret_uri: str = Field(
        ..., description="Secret URI for GitLab runner token"
    )
    gitlab_webhook_secret_uri: str = Field(
        ..., description="Secret URI for webhook validation"
    )
    synced_at: datetime = Field(
        default_factory=datetime.utcnow, description="Last sync from Git"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Config creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Last update timestamp"
    )

    @field_validator(
        "created_at",
        "updated_at",
        "synced_at",
        mode="before",
    )
    @classmethod
    def validate_datetime(
        cls,
        value: datetime | str | None,
    ) -> datetime | None:
        """Convert datetime from storage format (ISO string) to datetime object."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            return datetime.fromisoformat(value)
        raise ValueError("Invalid datetime format")

    def to_storage_dict(self) -> Dict[str, Any]:
        """
        Convert Runner model to storage format for YDB.

        Converts:
        - datetime → ISO string
        - dict → JSON bytes
        - None → 0 for optional int fields (YDB doesn't support NULL in some contexts)

        Returns:
            Dictionary with values in YDB storage format
        """
        data = self.model_dump()

        # Convert datetime to ISO string
        for key in ("created_at", "updated_at", "synced_at"):
            if isinstance(data[key], datetime):
                data[key] = data[key].isoformat()

        # Convert config dict to JSON bytes
        if isinstance(data["config"], dict):
            data["config"] = json.dumps(data["config"]).encode("utf-8")

        return data


# pylint: disable=too-many-arguments,too-many-positional-arguments,unused-argument
def generate_new_eggconfig(
    name: str,
    git_commit: str,
    git_repo_url_secret: str,
    gitlab_token_secret_uri: str,
    gitlab_webhook_secret_uri: str,
    project_id: Optional[int] = None,
    group_id: Optional[int] = None,
    config: Optional[Dict[str, Any]] = None,
    synced_at: Optional[datetime] = None,
    created_at: Optional[datetime] = None,
    updated_at: Optional[datetime] = None,
) -> EggConfig:
    """
    Generate a new EggConfig instance with default values.
    Args:
        name (str): Egg name.
        git_commit (str): Git commit hash.
        git_repo_url_secret (str): Secret URI for Git repository URL.
        gitlab_token_secret_uri (str): Secret URI for GitLab runner token.
        gitlab_webhook_secret_uri (str): Secret URI for webhook validation.
        project_id (Optional[int]): GitLab project ID. Defaults to 0.
        group_id (Optional[int]): GitLab group ID. Defaults to 0.
    Returns:
        EggConfig: New EggConfig instance.
    return EggConfig(
        name=name,
        project_id=project_id or 0,
        group_id=group_id or 0,
        config={},
        git_commit=git_commit,
        git_repo_url_secret=git_repo_url_secret,
        gitlab_token_secret_uri=gitlab_token_secret_uri,
        gitlab_webhook_secret_uri=gitlab_webhook_secret_uri,
    )
    """

    @generate_eggconfig_id_decorator()
    def generate_id() -> str:
        """Generate unique egg config ID based on egg name."""
        return name

    egg_id = generate_id()
    return EggConfig(
        id=egg_id,
        name=name,
        project_id=project_id or 0,
        group_id=group_id or 0,
        config=config or {},
        git_commit=git_commit,
        git_repo_url_secret=git_repo_url_secret,
        gitlab_token_secret_uri=gitlab_token_secret_uri,
        gitlab_webhook_secret_uri=gitlab_webhook_secret_uri,
    )


class SyncHistory(PydanticBaseModelORM):
    """Git sync history audit trail."""

    id: str = Field(..., description="Unique sync history entry ID (PK)")
    git_commit: str = Field(..., description="Git commit hash that was synced")
    sync_type: str = Field(..., description="Type of sync (periodic/webhook/manual)")
    status: SyncStatus = Field(..., description="Sync operation status")
    changes_detected: int = Field(
        default=0, description="Number of configuration changes detected"
    )
    eggs_synced: int = Field(default=0, description="Number of Eggs synced")
    jobs_synced: int = Field(default=0, description="Number of Jobs synced")
    uf_config_synced: bool = Field(
        default=False, description="Whether UF config was synced"
    )
    error_message: Optional[str] = Field(
        None, description="Error message if sync failed"
    )
    synced_at: datetime | str = Field(
        default_factory=datetime.utcnow, description="Sync timestamp"
    )
    duration_ms: Optional[int] = Field(
        None, description="Sync duration in milliseconds"
    )


# ============================================================================
# YDB Table Schemas (for database operations)
# ============================================================================


@dataclass
class RunnersTableYDB:
    """
    YDB table schema for runners.

    This class extends YDBTableSchema to include runner information.
    Follows the pattern from OpenTofuVersionTableYDB.
    """

    table_name: str = "runners"
    columns: Tuple[str, ...] = field(
        default_factory=lambda: (
            "id",
            "egg_name",
            "type",
            "state",
            "cloud_provider",
            "region",
            "gitlab_runner_id",
            "deployed_from_commit",
            "created_at",
            "updated_at",
            "last_heartbeat",
            "failure_count",
            "metadata",
        ),
    )
    r_type: Tuple[Union[YDBUtf8, YDBInt64, YDBBytes], ...] = field(
        default_factory=lambda: (
            make_ydb_type("Utf8"),  # id
            make_ydb_type("Utf8"),  # egg_name
            make_ydb_type("Utf8"),  # type
            make_ydb_type("Utf8"),  # state
            make_ydb_type("Utf8"),  # cloud_provider
            make_ydb_type("Utf8"),  # region
            make_ydb_type("Int64"),  # gitlab_runner_id
            make_ydb_type("Utf8"),  # deployed_from_commit
            make_ydb_type("Utf8"),  # created_at (stored as ISO string)
            make_ydb_type("Utf8"),  # updated_at (stored as ISO string)
            make_ydb_type("Utf8"),  # last_heartbeat (stored as ISO string)
            make_ydb_type("Int64"),  # failure_count
            make_ydb_type("String"),  # metadata (JSON bytes)
        ),
    )
    primary_key: str = "id"
    values_for_operate: Tuple[Any, ...] = field(
        default_factory=lambda: (),
    )

    def __post_init__(self) -> None:
        """Validate table schema."""
        if len(self.columns) != len(self.r_type):
            raise ValueError(
                "The number of columns must match the number of row types."
            )
        if len(self.values_for_operate) != 0 and len(self.values_for_operate) != len(
            self.columns
        ):
            raise ValueError("The number of values for operate must match columns.")
        if self.primary_key != "id":
            raise ValueError("Primary key must be 'id'.")

        # Validate with YDBTableSchema
        YDBTableSchema(  # type: ignore[call-arg]
            table_name=self.table_name,
            columns=self.columns,
            r_type=self.r_type,  # type: ignore[arg-type]
            primary_key=self.primary_key,
            values_for_operate=self.values_for_operate,
        )


@dataclass
class EggConfigsTableYDB:
    """
    YDB table schema for egg configurations.

    Stores cached Egg configurations from Git repository.
    """

    table_name: str = "egg_configs"
    columns: Tuple[str, ...] = field(
        default_factory=lambda: (
            "id",
            "project_id",
            "group_id",
            "name",
            "config",
            "git_commit",
            "git_repo_url_secret",
            "gitlab_token_secret_uri",
            "gitlab_webhook_secret_uri",
            "synced_at",
            "created_at",
            "updated_at",
        ),
    )
    r_type: Tuple[Union[YDBUtf8, YDBBytes, YDBInt64], ...] = field(
        default_factory=lambda: (
            make_ydb_type("Utf8"),  # id
            make_ydb_type("Int64"),  # project_id
            make_ydb_type("Int64"),  # group_id
            make_ydb_type("Utf8"),  # name
            make_ydb_type("String"),  # config (JSON bytes)
            make_ydb_type("Utf8"),  # git_commit
            make_ydb_type("Utf8"),  # git_repo_url_secret
            make_ydb_type("Utf8"),  # gitlab_token_secret_uri
            make_ydb_type("Utf8"),  # gitlab_webhook_secret_uri
            make_ydb_type("Utf8"),  # synced_at (stored as ISO string)
            make_ydb_type("Utf8"),  # created_at (stored as ISO string)
            make_ydb_type("Utf8"),  # updated_at (stored as ISO string)
        ),
    )
    primary_key: str = "id"
    values_for_operate: Tuple[Any, ...] = field(
        default_factory=lambda: (),
    )

    def __post_init__(self) -> None:
        """Validate table schema."""
        if len(self.columns) != len(self.r_type):
            raise ValueError(
                "The number of columns must match the number of row types."
            )
        if len(self.values_for_operate) != 0 and len(self.values_for_operate) != len(
            self.columns
        ):
            raise ValueError("The number of values for operate must match columns.")
        if self.primary_key != "id":
            raise ValueError("Primary key must be 'id'.")

        # Validate with YDBTableSchema
        YDBTableSchema(  # type: ignore[call-arg]
            table_name=self.table_name,
            columns=self.columns,
            r_type=self.r_type,  # type: ignore[arg-type]
            primary_key=self.primary_key,
            values_for_operate=self.values_for_operate,
        )


@dataclass
class SyncHistoryTableYDB:
    """
    YDB table schema for sync history.

    Stores audit trail of Git sync operations.
    """

    table_name: str = "sync_history"
    columns: Tuple[str, ...] = field(
        default_factory=lambda: (
            "id",
            "git_commit",
            "sync_type",
            "status",
            "changes_detected",
            "eggs_synced",
            "jobs_synced",
            "uf_config_synced",
            "error_message",
            "synced_at",
            "duration_ms",
        ),
    )
    r_type: Tuple[Union[YDBUtf8, YDBInt64, YDBBytes], ...] = field(
        default_factory=lambda: (
            make_ydb_type("Utf8"),  # id
            make_ydb_type("Utf8"),  # git_commit
            make_ydb_type("Utf8"),  # sync_type
            make_ydb_type("Utf8"),  # status
            make_ydb_type("Int64"),  # changes_detected
            make_ydb_type("Int64"),  # eggs_synced
            make_ydb_type("Int64"),  # jobs_synced
            # uf_config_synced (stored as "true"/"false")
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),  # error_message
            make_ydb_type("Utf8"),  # synced_at (stored as ISO string)
            make_ydb_type("Int64"),  # duration_ms
        ),
    )
    primary_key: str = "id"
    values_for_operate: Tuple[Any, ...] = field(
        default_factory=lambda: (),
    )

    def __post_init__(self) -> None:
        """Validate table schema."""
        if len(self.columns) != len(self.r_type):
            raise ValueError(
                "The number of columns must match the number of row types."
            )
        if len(self.values_for_operate) != 0 and len(self.values_for_operate) != len(
            self.columns
        ):
            raise ValueError("The number of values for operate must match columns.")
        if self.primary_key != "id":
            raise ValueError("Primary key must be 'id'.")

        # Validate with YDBTableSchema
        YDBTableSchema(  # type: ignore[call-arg]
            table_name=self.table_name,
            columns=self.columns,
            r_type=self.r_type,  # type: ignore[arg-type]
            primary_key=self.primary_key,
            values_for_operate=self.values_for_operate,
        )


# ============================================================================
# Pydantic Model for YDB Schema
# ============================================================================


@pydantic_dataclass(config=ConfigDict(frozen=True))
class RunnerModelYDB:  # pylint: disable=too-few-public-methods
    """
    Runner model for YDB schema.

    Contains table schemas for the GitOps Runner Orchestration system.
    Follows the pattern from OpenTofuModelYDB.

    Note: AuditLogsTableYDB is defined in audit_models.py and can be
    combined with this model when creating the full schema.
    """

    tables: list[Union[RunnersTableYDB, EggConfigsTableYDB, SyncHistoryTableYDB]] = (
        Field(..., description="List of table schemas to be created in YDB")
    )

    model_name: str = "RunnerModel"
    version: str = "1.0.0"

    @field_validator("version", mode="before")
    @classmethod
    def validate_semver(cls, value: str) -> str:
        """Validate that the version follows semantic versioning format."""
        if isinstance(value, str) and value.strip():
            parts = value.split(".")
            if len(parts) == 3 and all(part.isdigit() for part in parts):
                return value
            raise ValueError("version must follow semver format (X.Y.Z).")
        raise ValueError("version must be a non-empty string.")

    def __post_init__(self) -> None:
        """Post-initialization validation."""
        if self.tables is None or not isinstance(self.tables, list):
            raise ValueError("tables must be a non-empty list of runner tables.")
        if self.model_name is None or not isinstance(self.model_name, str):
            raise ValueError("model_name must be a non-empty string.")
        if self.version is None or not isinstance(self.version, str):
            raise ValueError("version must be a non-empty string.")
