"""Schemas for OpenTofu backend options and binary file information."""

import re
from typing import Optional

from pydantic import Field, field_validator

from app.model.pydantic_base_models import PydanticBaseModelORM


class TofuBackendS3Options(PydanticBaseModelORM):
    """Data schema for OpenTofu S3 backend options."""

    bucket: str
    key: str
    region: str
    endpoint: Optional[str] = Field(None, description="Custom S3 endpoint URL")
    profile: Optional[str] = Field(None, description="AWS profile name")
    role_arn: Optional[str] = Field(
        None,
        description="AWS Role ARN for access",
    )
    dynamodb_table: Optional[str] = Field(
        None, description="DynamoDB table for state locking"
    )

    @field_validator("bucket", "key", "region")
    @classmethod
    def validate_non_empty(cls, value: str) -> str:
        """Validate that the field is a non-empty string."""
        if not value or not isinstance(value, str):
            raise ValueError("This field must be a non-empty string")
        return value

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str | None) -> str | None:
        """Validate that the endpoint is a valid URL if provided."""
        if value is not None:
            url_pattern = r"^(https?://)[^\s/$.?#].[^\s]*$"
            if not re.match(url_pattern, value):
                raise ValueError(f"Invalid endpoint URL: {value}")
        return value

    @field_validator("role_arn")
    @classmethod
    def validate_role_arn(cls, value: str | None) -> str | None:
        """Validate that the role ARN follows AWS ARN format if provided."""
        if value is not None:
            arn_pattern = r"^arn:aws:iam::\d{12}:role\/[a-zA-Z_0-9+=,.@\-_/]+$"
            if not re.match(arn_pattern, value):
                raise ValueError(f"Invalid AWS Role ARN: {value}")
        return value


class TofuProvidersVer(PydanticBaseModelORM):
    """Data schema for OpenTofu providers constraints."""

    name: str
    version: str
    source: str

    @field_validator("version")
    @classmethod
    def validate_version_constraint(cls, value: str) -> str:
        """Validate that the version constraint follows Terraform syntax."""
        constraint_pattern = r"""
        ^(=|!=|>=|<=|>|<)?\s*\d+(\.\d+){0,2}(-[a-zA-Z0-9]+)?(\+[a-zA-Z0-9]+)?$
        """
        if not re.match(constraint_pattern, value, re.VERBOSE):
            raise ValueError(f"Invalid version constraint: {value}")
        return value

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        """Validate that the source is a valid Terraform provider source."""
        source_pattern = r"^[a-zA-Z0-9._/-]+(/[a-zA-Z0-9._/-]+)?$"
        if not re.match(source_pattern, value):
            raise ValueError(f"Invalid provider source: {value}")
        return value


class OpenTofuBinFileInfo(PydanticBaseModelORM):
    """Data schema for OpenTofu binary files information."""

    bin_version: str
    bin_sha256: str
    bin_url: str

    @field_validator("bin_version")
    @classmethod
    def validate_semver(cls, value: str) -> str:
        """Validate that the version follows semantic versioning."""
        semver_pattern = r"^\d+\.\d+\.\d+(-[a-zA-Z0-9]+)?(\+[a-zA-Z0-9]+)?$"
        if not re.match(semver_pattern, value):
            raise ValueError(f"Invalid semantic version: {value}")
        return value

    @field_validator("bin_sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        """Validate that the SHA256 hash is a valid hexadecimal string."""
        sha256_pattern = r"^[a-fA-F0-9]{64}$"
        if not re.match(sha256_pattern, value):
            raise ValueError(f"Invalid SHA256 hash: {value}")
        return value

    @field_validator("bin_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Validate that the URL is a valid HTTP or HTTPS URL."""
        url_pattern = r"^(https?://)[^\s/$.?#].[^\s]*$"
        if not re.match(url_pattern, value):
            raise ValueError(f"Invalid URL: {value}")
        return value
