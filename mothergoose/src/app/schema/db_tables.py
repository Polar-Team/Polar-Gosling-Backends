from app.types.ydb_types import (
    YDBBool,
    YDBBytes,
    YDBDouble,
    YDBInt64,
    YDBUtf8,
)
from pydantic import Field
from app.model.pydantic_base_models import PydanticBaseModelORM

# YDB Table schema definitions


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
