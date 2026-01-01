"""
Audit log models for YDB schema definitions.

This module defines audit log schemas that are used system-wide across
all services (MotherGoose, UglyFox, etc.) for tracking significant actions.
"""
# pylint: disable=duplicate-code

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Tuple, Union

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from app.model.pydantic_base_models import PydanticBaseModelORM
from app.schema.db_tables import YDBTableSchema
from app.types.ydb_types import YDBBytes, YDBType, YDBUtf8


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
# Pydantic Model (for application logic)
# ============================================================================


class AuditLog(PydanticBaseModelORM):
    """
    System-wide audit log for all significant actions.

    Tracks who did what, when, and to which resources for compliance
    and debugging purposes. Used across all services.
    """

    id: str = Field(..., description="Unique audit log entry ID (PK)")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="Action timestamp"
    )
    actor: str = Field(..., description="Who performed the action (user/service)")
    action: str = Field(..., description="Action performed")
    resource_type: str = Field(..., description="Type of resource affected")
    resource_id: str = Field(..., description="ID of the affected resource")
    details: Dict[str, Any] = Field(
        default_factory=dict, description="Additional action details"
    )


# ============================================================================
# YDB Table Schema (for database operations)
# ============================================================================


@dataclass
class AuditLogsTableYDB:
    """
    YDB table schema for audit logs.

    Tracks all significant system actions for compliance and debugging.
    Used by all services in the system.
    """

    table_name: str = "audit_logs"
    columns: Tuple[str, ...] = field(
        default_factory=lambda: (
            "id",
            "timestamp",
            "actor",
            "action",
            "resource_type",
            "resource_id",
            "details",
        ),
    )
    r_type: Tuple[Union[YDBUtf8, YDBBytes], ...] = field(
        default_factory=lambda: (
            make_ydb_type("Utf8"),  # id
            make_ydb_type("Utf8"),  # timestamp (stored as ISO string)
            make_ydb_type("Utf8"),  # actor
            make_ydb_type("Utf8"),  # action
            make_ydb_type("Utf8"),  # resource_type
            make_ydb_type("Utf8"),  # resource_id
            make_ydb_type("String"),  # details (JSON bytes)
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
class AuditModelYDB:  # pylint: disable=too-few-public-methods
    """
    Audit model for YDB schema.

    Contains audit log table schema for system-wide audit tracking.
    Can be used independently or combined with other models.
    """

    tables: list[AuditLogsTableYDB] = Field(
        ..., description="List of audit table schemas to be created in YDB"
    )

    model_name: str = "AuditModel"
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
            raise ValueError("tables must be a non-empty list of audit tables.")
        if self.model_name is None or not isinstance(self.model_name, str):
            raise ValueError("model_name must be a non-empty string.")
        if self.version is None or not isinstance(self.version, str):
            raise ValueError("version must be a non-empty string.")
