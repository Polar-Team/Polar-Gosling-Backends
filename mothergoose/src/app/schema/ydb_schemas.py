from pydantic import BaseModel, Field


class PydanticBaseModel(BaseModel):
    """Pydantic configuration for YDB schemas."""

    class Config:
        """Pydantic configuration for YDB schemas."""

        orm_mode = True
        arbitrary_types_allowed = True
        allow_mutation = False


class YDBConfig(PydanticBaseModel):
    endpoint: str = Field(..., description="YDB endpoint URL")
    database: str = Field(..., description="YDB database name")
    root_certificates: str | None = Field(
        None, description="Root certificates for secure connection (optional)"
    )


class YDBTableSchema(PydanticBaseModel):
    table_name: str = Field(..., description="Name of the YDB table")
    key_columns: list[str] = Field(
        ..., description="List of key column names for the table"
    )
    value_columns: list[str] = Field(
        ..., description="List of value column names for the table"
    )
    primary_key: str | None = Field(
        None, description="Primary key column name (optional)"
    )
    indexes: list[str] = Field(
        [], description="List of index names for the table (optional)"
    )


class YDBSchema(PydanticBaseModel):
    config: YDBConfig = Field(..., description="Configuration for YDB connection")
    tables: list[YDBTableSchema] = Field(
        ..., description="List of table schemas to be created in YDB"
    )
    default_table: str | None = Field(
        None, description="Default table name for operations (optional)"
    )
    version: str = Field("1.0", description="Schema version")
