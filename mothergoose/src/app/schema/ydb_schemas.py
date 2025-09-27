"""
YDB Configuration and Schema Models
"""

from typing import Optional

from pydantic import Field, field_validator
from ydb import AccessTokenCredentials, AnonymousCredentials, StaticCredentials
from ydb.iam.auth import MetadataUrlCredentials, ServiceAccountCredentials
from ydb.oauth2_token_exchange import Oauth2TokenExchangeCredentials

from app.model.opentofu_models import OpenTofuModelYDB
from app.model.pydantic_base_models import PydanticBaseModelORM


class YDBConfig(PydanticBaseModelORM):
    """Configuration for YDB connection."""

    endpoint: str = Field(..., description="YDB endpoint URL")
    database: str = Field(..., description="YDB database name")
    pool_size: int = Field(10, description="Size of the session pools for YDB")
    credentials: Optional[
        ServiceAccountCredentials
        | Oauth2TokenExchangeCredentials
        | AnonymousCredentials
        | AccessTokenCredentials
        | StaticCredentials
        | ServiceAccountCredentials
        | MetadataUrlCredentials
    ] = Field(None, description="Credentials for YDB connection (e.g., token)")
    root_certificates: Optional[str] = Field(
        None, description="Root certificates for secure connection (optional)"
    )

    @field_validator("endpoint", mode="before")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        """
        Ensure endpoint is a valid URL string.
        """
        if isinstance(value, str) and value.strip():
            if value.startswith("grpc://") or value.startswith("grpcs://"):
                return value
            raise ValueError("endpoint must start with grpc:// or grpcs://")
        raise ValueError("endpoint must be a non-empty string.")


class YDBSchema(PydanticBaseModelORM):
    """Schema for YDB integration."""

    config: YDBConfig = Field(..., description="Setup for YDB connection")
    default_table: str | None = Field(
        None, description="Default table name for operations (optional)"
    )
    version: str = Field("1.0.0", description="Schema version")
    model: OpenTofuModelYDB = Field(
        ...,
        description="Model type for integration",
    )

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
            raise ValueError("version must follow semver format (X.Y.Z)")
        raise ValueError("version must be a non-empty string.")
