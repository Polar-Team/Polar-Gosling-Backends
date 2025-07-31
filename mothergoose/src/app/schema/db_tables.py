from typing import Optional

from pydantic import Field

from app.model.pydantic_base_models import PydanticBaseModelORM


class YDBTableSchema(PydanticBaseModelORM):
    table_name: str = Field(..., description="Name of the YDB table")
    columns: list[str] = Field(
        ..., description="List of key column names for the table"
    )
    rows_type: list[str | int | float | bytes | bool] = Field(
        ..., description="List of data types for each column in the table"
    )
    values_for_insert: list[str | int | float | bytes | bool] = Field(
        [], description="List of values to insert into the table"
    )
    primary_key: Optional[str] = Field(
        None, description="Primary key column name (optional)"
    )
    indexes: list[str] = Field(
        [], description="List of index names for the table (optional)"
    )
