"""Schemas for OpenTofu backend options and binary file information."""

import re

from pydantic import BaseModel, field_validator


class OpenTofuBackendS3Options(BaseModel):
    """Data schema for OpenTofu S3 backend options."""

    bucket: str
    key: str
    region: str
    endpoint: str | None = None
    profile: str | None = None
    endpoint: str | None = None
    role_arn: str | None = None
    dynamodb_table: str | None = None

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


class OpenTofuProvidersConstraints(BaseModel):
    """Data schema for OpenTofu providers constraints."""

    name: str
    version: str
    source: str

    @field_validator("version")
    @classmethod
    def validate_version_constraint(cls, value: str) -> str:
        """Validate that the version constraint follows Terraform syntax."""
        constraint_pattern = (
            r"^(=|!=|>=|<=|>|<)?\s*\d+(\.\d+){0,2}(-[a-zA-Z0-9]+)?(\+[a-zA-Z0-9]+)?$"  # noqa
        )
        if not re.match(constraint_pattern, value):
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


class OpenTofuBinFileInfo(BaseModel):
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
