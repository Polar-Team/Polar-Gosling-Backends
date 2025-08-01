from typing import Literal, Union

from pydantic import BaseModel, Field, RootModel, ConfigDict
from ydb import PrimitiveType
from app.model.pydantic_base_models import PydanticBaseModelORM

# YDB Table schema definitions


class YDBUtf8(BaseModel):
    """YDB UTF-8 data type."""

    type: Literal["Utf8"] = "Utf8"
    parametarized_type: PrimitiveType = PrimitiveType.Utf8


class YDBInt64(BaseModel):
    """YDB Int64 data type."""

    type: Literal["Int64"] = "Int64"
    parametarized_type: PrimitiveType = PrimitiveType.Int64


class YDBDouble(BaseModel):
    """YDB Double data type."""

    type: Literal["Double"] = "Double"
    parametarized_type: PrimitiveType = PrimitiveType.Double


class YDBBool(BaseModel):
    """YDB Bool data type."""

    type: Literal["Bool"] = "Bool"
    parametarized_type: PrimitiveType = PrimitiveType.Bool


class YDBBytes(BaseModel):
    """YDB Bytes data type."""

    type: Literal["String"] = "String"
    parametarized_type: PrimitiveType = PrimitiveType.String


class YDBType(
    RootModel[
        Union[
            YDBUtf8,
            YDBInt64,
            YDBDouble,
            YDBBool,
            YDBBytes,
        ]
    ]
):
    """Base class for YDB data types."""

    model_config = ConfigDict(model_discriminator="type")


class YDBTableSchema(PydanticBaseModelORM):
    table_name: str = Field(..., description="Name of the YDB table")
    columns: list[str] = Field(
        ..., description="List of key column names for the table"
    )
    r_type: list[YDBUtf8 | YDBInt64 | YDBDouble | YDBBytes | YDBBool] = Field(
        ...,
        description="List of data types for each column in the table",
    )
    primary_key: str = Field(
        ...,
        description="Primary key column name (optional)",
    )
    values_for_operate: list[str | int | float | bytes | bool] = Field(
        [], description="List of values to insert into the table"
    )
    indexes: list[str] = Field(
        [], description="List of index names for the table (optional)"
    )
