from pydantic import Field, field_validator

from app.model.pydantic_base_models import PydanticBaseModelORM
from app.model.opentofu_models import OpenTofuModelDynamoDB


class DynamoDBConfig(PydanticBaseModelORM):
    """
    Configuration for DynamoDB connection.
    """

    region_name: str = Field(..., description="AWS region name")
    endpoint_url: str | None = Field(
        None, description="DynamoDB endpoint URL (optional)"
    )
    aws_access_key_id: str | None = Field(
        None, description="AWS access key ID (optional)"
    )
    aws_secret_access_key: str | None = Field(
        None, description="AWS secret access key (optional)"
    )
    aws_session_token: str | None = Field(
        None, description="AWS session token (optional)"
    )
    botocore_config: dict | None = Field(
        None, description="Botocore configuration options (optional)"
    )

    @field_validator("botocore_config", mode="before")
    @classmethod
    def validate_botocore_config(cls, value: dict | None) -> dict:
        """
        Validate and convert botocore_config to a dictionary if it is not None.
        """
        if value is None:
            return {}
        elif isinstance(value, dict):
            return value
        raise ValueError("botocore_config must be a dictionary or None.")

    @field_validator("endpoint_url", mode="before")
    @classmethod
    def validate_endpoint_url(cls, value: str | None) -> str | None:
        """
        Ensure endpoint_url is either None or a valid HTTP(S) URL string.
        """
        if value is None:
            return value
        elif isinstance(value, str) and value.strip():
            if value.startswith("https://"):
                return value
            raise ValueError("endpoint_url must start with https://")
        raise ValueError("endpoint_url must be a non-empty string or None.")


class DynamoDBSchema(PydanticBaseModelORM):
    """Schema for DynamoDB configuration and table definitions."""

    config: DynamoDBConfig = Field(
        ...,
        description="DynamoDB connection configuration",
    )
    default_table: str | None = Field(
        None, description="Default table name for operations (optional)"
    )
    version: str = Field("1.0", description="Schema version")
    model: OpenTofuModelDynamoDB = Field(
        ...,
        description="Table schema definition",
    )
