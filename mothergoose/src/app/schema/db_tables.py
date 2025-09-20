from pydantic import Field
from typing import Any, Tuple, Union

from app.model.pydantic_base_models import PydanticBaseModelORM
from app.types.ydb_types import YDBBool, YDBBytes, YDBDouble, YDBInt64, YDBUtf8

# YDB Table schema definitions
YDBTypes = Union[YDBUtf8 | YDBInt64 | YDBDouble | YDBBytes | YDBBool]


class YDBTableSchema(PydanticBaseModelORM):
    table_name: str = Field(..., description="Name of the YDB table")
    columns: Tuple[str, ...] = Field(
        ..., description="List of key column names for the table"
    )
    r_type: Tuple[YDBTypes, ...] = Field(
        ...,
        description="List of data types for each column in the table",
    )
    primary_key: str = Field(
        ...,
        description="Primary key column name (optional)",
    )
    values_for_operate: Tuple[Any, ...] = Field(
        (), description="List of values to insert into the table"
    )
    indexes: Tuple[str, ...] = Field(
        (), description="List of index names for the table (optional)"
    )


class DynamoDBTableSchema(PydanticBaseModelORM):
    """
    Schema for a DynamoDB table.
    """

    table_name: str = Field(..., description="Name of the DynamoDB table")
    key_schema: list[dict] = Field(
        ..., description="Key schema for the table (list of dicts)"
    )
    attribute_definitions: list[dict] = Field(
        ..., description="Attribute definitions for the table (list of dicts)"
    )
    provisioned_throughput: dict = Field(
        ..., description="Provisioned throughput settings for the table"
    )
    global_secondary_indexes: list[dict] | None = Field(
        None, description="Global secondary indexes for the table (optional)"
    )
    values_for_operate: Tuple[Any, ...] = Field(
        (), description="List of values to insert into the table"
    )
