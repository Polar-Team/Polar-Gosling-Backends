"""
Gosling models for versioning and schema definitions.
"""

# pylint: disable=duplicate-code

from dataclasses import dataclass, field
from typing import Any, Tuple, Union

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass

from app.schema.db_tables import YDBTableSchema
from app.types.ydb_types import YDBBool, YDBType, YDBUtf8

# YDB models setup for Gosling CLI versioning


def make_ydb_type(ydb_type: str) -> Any:
    """
    Create a YDB type instance based on the provided type string.
    Args:
        ydb_type (str): The type string to create a YDB type for.
    Returns:
        Tuple[YDBBool | YDBUtf8, ...]: A tuple containing the YDB type.
    """

    return YDBType({"type": ydb_type}).root  # type: ignore[arg-type]


@dataclass
class GoslingVersionTableYDB:
    """
    Represents a table schema for Gosling CLI versioning.
    This class extends YDBTableSchema to include versioning information.
    """

    # pylint: disable=too-few-public-methods

    table_name: str = "gosling_version"
    columns: Tuple[str, ...] = field(
        default_factory=lambda: (
            "version_id",
            "version",
            "source",
            "downloaded_at",
            "sha256_hash",
            "active",
        ),
    )
    r_type: Tuple[YDBUtf8 | YDBBool, ...] = field(
        default_factory=lambda: (
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Utf8"),
            make_ydb_type("Bool"),
        ),
    )
    primary_key: str = "version_id"
    values_for_operate: Tuple[Any, ...] = field(
        default_factory=lambda: (),
    )

    def __post_init__(self) -> None:
        """Post-initialization logic can be added here if needed."""
        if len(self.columns) != len(self.r_type):
            raise ValueError(
                "The number of columns must match the number of row types."
            )
        if (
            len(self.values_for_operate) != len(self.columns)
            and len(self.values_for_operate) != len(self.r_type)
            and len(self.values_for_operate) != 0
        ):
            raise ValueError(
                "The number of values foroinsert must match columns and types."
            )
        if self.table_name.startswith("gosling_version") is False:
            raise ValueError("Table name must start with 'gosling_version'.")
        if self.primary_key != "version_id":
            raise ValueError("Primary key must be 'version_id'.")
        if self.columns[0] != "version_id":
            raise ValueError("The first column must be 'version_id'.")
        if self.columns[1] != "version":
            raise ValueError("The second column must be 'version'.")
        if self.columns[2] != "source":
            raise ValueError("The third column must be 'source'.")
        if self.columns[3] != "downloaded_at":
            raise ValueError("The fourth column must be 'downloaded_at'.")
        if self.columns[4] != "sha256_hash":
            raise ValueError("The fith column must be 'sha256_hash'.")
        if self.columns[5] != "active":
            raise ValueError("The sixth column must be 'active'.")
        if (
            self.r_type[0].type != "Utf8"
            or self.r_type[1].type != "Utf8"
            or self.r_type[2].type != "Utf8"
            or self.r_type[3].type != "Utf8"
            or self.r_type[4].type != "Utf8"
        ):
            # fmt: off
            raise ValueError("""
                All rows except 'active'='Bool' must be Utf8 type.
                """)
            # fmt: on
        if self.r_type[5].type != "Bool":
            raise ValueError("The 'active' column must be of type 'Bool'.")
        YDBTableSchema(  # type: ignore[call-arg]
            table_name=self.table_name,
            columns=self.columns,
            r_type=self.r_type,  # type: ignore[arg-type]
            primary_key=self.primary_key,
            values_for_operate=self.values_for_operate,
        )


@pydantic_dataclass(config=ConfigDict(frozen=True))
class GoslingModelYDB:
    """
    Base class for Gosling models.
    This class can be extended to create specific Gosling models.
    """

    # pylint: disable=too-few-public-methods

    tables: list[Union[GoslingVersionTableYDB]] = Field(
        ..., description="List of table schemas to be created in YDB"
    )

    model_name: str = "GoslingModel"
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
            raise ValueError("tables must be a non-empty list of gosling tables.")
        if self.model_name is None or not isinstance(self.model_name, str):
            raise ValueError("model_name must be a non-empty string.")
        if self.version is None or not isinstance(self.version, str):
            raise ValueError("version must be a non-empty string.")
