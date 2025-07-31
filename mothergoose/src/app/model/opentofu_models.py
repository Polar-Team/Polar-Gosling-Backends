from dataclasses import dataclass, field
from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass
from typing import List, Any
from app.schema.db_tables import YDBTableSchema


# YDB models setup for OpenTofu versioning


@dataclass
class OpenTofuVersionTableYDB:
    """
    Represents a table schema for OpenTofu versioning.
    This class extends YDBTableSchema to include versioning information.
    """

    table_name: str = "opentofu_version"
    columns: List[str] = field(
        default_factory=lambda: ["version", "downloaded_at", "sha256_hash"],
    )
    rows_type: List[str] = field(
        default_factory=lambda: ["str", "str", "str"],
    )
    values_for_insert: List[Any] = field(
        default_factory=lambda: [],
    )

    def __post_init__(self) -> None:
        """Post-initialization logic can be added here if needed."""
        if len(self.columns) != len(self.rows_type):
            raise ValueError(
                "The number of columns must match the number of row types."
            )
        elif (
            len(self.values_for_insert) != len(self.columns)
            and len(self.values_for_insert) != len(self.rows_type)
            and len(self.values_for_insert) != 0
        ):
            raise ValueError(
                "The number of values for insert must match columns and types."
            )
        elif self.table_name.startswith("opentofu_version") is False:
            raise ValueError("Table name must start with 'opentofu_version'.")
        elif self.columns[0] != "version":
            raise ValueError("The first column must be 'version'.")
        elif self.columns[1] != "downloaded_at":
            raise ValueError("The second column must be 'downloaded_at'.")
        elif self.columns[2] != "sha256_hash":
            raise ValueError("The third column must be 'sha256_hash'.")
        elif (
            self.rows_type[0] != "str"
            or self.rows_type[1] != "str"
            or self.rows_type[2] != "str"
        ):
            raise ValueError("Rows types list must be 'str'.")
        if schema := YDBTableSchema(
            table_name=self.table_name,
            columns=self.columns,
            rows_type=self.rows_type,
            values_for_insert=self.values_for_insert,
        ):
            self.table_name = schema.table_name
            self.columns = schema.columns
            self.rows_type = schema.rows_type
            self.values_for_insert = schema.values_for_insert


@pydantic_dataclass(config=ConfigDict(frozen=True))
class OpenTofuModelYDB:
    """
    Base class for OpenTofu models.
    This class can be extended to create specific OpenTofu models.
    """

    tables: list[OpenTofuVersionTableYDB] = Field(
        ..., description="List of table schemas to be created in YDB"
    )

    model_name: str = "OpenTofuModel"
    version: str = "1.0.0"

    @field_validator("version", mode="before")
    @classmethod
    def validate_semver(cls, value: str) -> str:
        """
        Validate that the version follows semantic versioning format.
        """
        if isinstance(value, str) and value.strip():
            parts = value.split(".")
            if len(parts) == 3 and all(part.isdigit() for part in parts):
                return value
            raise ValueError("version must follow semver format (X.Y.Z).")
        raise ValueError("version must be a non-empty string.")

    def __post_init__(self) -> None:
        """Post-initialization logic can be added here if needed."""
        if self.tables is None or not isinstance(self.tables, list):
            raise ValueError("tables must be a non-empty list of tofu tables.")
        elif self.model_name is None or not isinstance(self.model_name, str):
            raise ValueError("model_name must be a non-empty string.")
        elif self.version is None or not isinstance(self.version, str):
            raise ValueError("version must be a non-empty string.")


# DynamoDB models setup for OpenTofu versioning


@pydantic_dataclass(config=ConfigDict(frozen=True))
class OpenTofuModelDynamoDB:
    """
    Base class for OpenTofu models specific to DynamoDB.
    This class can be extended to create specific OpenTofu models for DynamoDB.
    """

    model_name: str = "OpenTofuModel"
    version: str = "1.0"

    def __post_init__(self) -> None:
        """Post-initialization logic can be added here if needed."""
        pass
