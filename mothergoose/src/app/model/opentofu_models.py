from dataclasses import dataclass, field
from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass as pydantic_dataclass
from typing import List, Any, Literal
from app.schema.db_tables import (
    YDBTableSchema,
    YDBBool,
    YDBUtf8,
    YDBType,
)

# Define the source types for OpenTofu versioning

GitHubSource = Literal["github"]
OtherSource = Literal["other"]

# YDB models setup for OpenTofu versioning


@dataclass
class OpenTofuVersionTableYDB:
    """
    Represents a table schema for OpenTofu versioning.
    This class extends YDBTableSchema to include versioning information.
    """

    __source: GitHubSource | OtherSource = field(init=False, default="github")

    table_name: str = "opentofu_version"
    columns: List[str] = field(
        default_factory=lambda: [
            "version_id",
            "version",
            "source",
            "downloaded_at",
            "sha256_hash",
            "active",
        ],
    )
    r_type: list[YDBUtf8 | YDBBool | GitHubSource | OtherSource] = field(
        default_factory=lambda: [
            YDBType({"type": "Utf8"}).root,
            YDBType({"type": "Utf8"}).root,
            "github",
            YDBType({"type": "Utf8"}).root,
            YDBType({"type": "Utf8"}).root,
            YDBType({"type": "Bool"}).root,
        ],
    )
    primary_key: str = "version_id"
    values_for_operate: List[Any] = field(
        default_factory=lambda: [],
    )

    @property
    def source(self) -> GitHubSource | OtherSource:
        """
        Returns the source of the version.
        This property can be either 'github' or 'other'.
        """
        return self.__source

    @source.setter
    def source(self, value: GitHubSource | OtherSource) -> None:
        """
        Sets the source of the version.
        Raises ValueError if the value is not 'github' or 'other'.
        """
        if value not in ["github", "other"]:
            raise ValueError("Source must be either 'github' or 'other'.")
        self.__source = value

    def __post_init__(self) -> None:
        """Post-initialization logic can be added here if needed."""
        if len(self.columns) != len(self.r_type):
            raise ValueError(
                "The number of columns must match the number of row types."
            )
        elif (
            len(self.values_for_operate) != len(self.columns)
            and len(self.values_for_operate) != len(self.r_type)
            and len(self.values_for_operate) != 0
        ):
            raise ValueError(
                "The number of values foroinsert must match columns and types."
            )
        elif self.table_name.startswith("opentofu_version") is False:
            raise ValueError("Table name must start with 'opentofu_version'.")
        elif self.primary_key != "version_id":
            raise ValueError("Primary key must be 'version_id'.")
        elif self.columns[0] != "version_id":
            raise ValueError("The first column must be 'version_id'.")
        elif self.columns[1] != "version":
            raise ValueError("The second column must be 'version'.")
        elif self.columns[2] != "source":
            raise ValueError("The third column must be 'source'.")
        elif self.columns[3] != "downloaded_at":
            raise ValueError("The fourth column must be 'downloaded_at'.")
        elif self.columns[4] != "sha256_hash":
            raise ValueError("The fith column must be 'sha256_hash'.")
        elif self.columns[5] != "active":
            raise ValueError("The sixth column must be 'active'.")
        elif (
            self.r_type[0].type != "Utf8"
            or self.r_type[1].type != "Utf8"
            or self.r_type[3].type != "Utf8"
            or self.r_type[4].type != "Utf8"
            or self.r_type[5].type != "Bool"
        ):
            raise ValueError(
                """
                All rows except 'active'='Bool' and 'source'='github|other'
                Must be  'Utf8'
                """
            )
        common_columns_types = [
            col for col in self.r_type if col != "github" and col != "other"
        ]
        if schema := YDBTableSchema(
            table_name=self.table_name,
            columns=self.columns,
            r_type=common_columns_types,
            primary_key=self.primary_key,
            values_for_operate=self.values_for_operate,
        ):
            self.table_name = schema.table_name
            self.columns = schema.columns
            self.primary_key = schema.primary_key
            self.values_for_operate = schema.values_for_operate


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
