from typing import Optional

from pydantic import Field, field_validator
from ydb import credentials_from_env_variables as ydb_cred_function

from app.model.pydantic_base_models import PydanticBaseModelORM
from app.model.opentofu_models import OpenTofuModelYDB, OpenTofuModelDynamoDB


class YDBConfig(PydanticBaseModelORM):
    endpoint: str = Field(..., description="YDB endpoint URL")
    database: str = Field(..., description="YDB database name")
    pool_size: int = Field(10, description="Size of the session pools for YDB")
    credentials: Optional[ydb_cred_function] = Field(
        None, description="Credentials for YDB connection (e.g., token)"
    )
    root_certificates: Optional[str] = Field(
        None, description="Root certificates for secure connection (optional)"
    )

    @field_validator("endpoint", mode="before")
    @classmethod
    def validate_endpoint(cls, value):
        """
        Ensure endpoint is a valid URL string.
        """
        if isinstance(value, str) and value.strip():
            if value.startswith("grpc://"):
                return value
            raise ValueError("endpoint must start with grpc://")
        raise ValueError("endpoint must be a non-empty string.")


class YDBSchema(PydanticBaseModelORM):
    config: YDBConfig = Field(..., description="Setup for YDB connection")
    default_table: str | None = Field(
        None, description="Default table name for operations (optional)"
    )
    version: str = Field("1.0", description="Schema version")
    model: OpenTofuModelYDB | OpenTofuModelDynamoDB = Field(
        ..., description="Model type for integration"
    )
