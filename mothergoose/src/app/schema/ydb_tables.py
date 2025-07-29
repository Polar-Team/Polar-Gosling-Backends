from typing import Optional
from pydantic import Field
from app.model.pydantic_base_models import PydanticBaseModelORM


class YDBTableSchema(PydanticBaseModelORM):
    table_name: str = Field(..., description="Name of the YDB table")
    key_columns: list[str] = Field(
        ..., description="List of key column names for the table"
    )
    value_columns: list[str] = Field(
        ..., description="List of value column names for the table"
    )
    primary_key: Optional[str] = Field(
        None, description="Primary key column name (optional)"
    )
    indexes: list[str] = Field(
        [], description="List of index names for the table (optional)"
    )
