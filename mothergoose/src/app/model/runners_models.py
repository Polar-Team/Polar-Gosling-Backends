"""
Runner table models for YDB schema definitions.

This module defines table schemas for the GitOps Runner Orchestration system,
following the pattern from opentofu_models.py where all data passes through
schema.model.tables[x].values_for_operate.
"""

# pylint: disable=duplicate-code

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple, Union

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from app.model.pydantic_base_models import PydanticBaseModelORM
from app.schema.db_tables import YDBTableSchema
from app.types.ydb_types import YDBBytes, YDBInt64, YDBType, YDBUtf8

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
    egg_name: str = Field(..., description="Name of the Egg this runner belongs to")
    type: RunnerType = Field(..., description="Runner type (serverless/apex/nadir)")
    state: RunnerState = Field(..., description="Current runner state")
    cloud_provider: CloudProvider = Field(
        ..., description="Cloud provider hosting this runner"
    )
    region: str = Field(..., description="Cloud region where runner is deployed")
    gitlab_runner_id: Optional[int] = Field(
        None, description="GitLab runner registration ID"
    )
    deployed_from_commit: str = Field(
        ..., description="Git commit hash that deployed this runner"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="Runner creation timestamp"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow, description="Last update timestamp"
    )
    last_heartbeat: Optional[datetime] = Field(
        None, description="Last heartbeat from runner"
    )
    failure_count: int = Field(default=0, description="Number of consecutive failures")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional runner metadata"
    )


class EggConfig(PydanticBaseModelORM):
    """Cached Egg configuration from Git repository."""

    name: str = Field(..., description="Egg name (PK)")
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
    synced_at: datetime = Field(
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
    r_type: Tuple[Union[YDBUtf8, YDBBytes], ...] = field(
        default_factory=lambda: (
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
    primary_key: str = "name"
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
        if self.primary_key != "name":
            raise ValueError("Primary key must be 'name'.")

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
            make_ydb_type("Utf8"),  # uf_config_synced (stored as "true"/"false")
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
