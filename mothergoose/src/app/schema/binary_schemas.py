import re

from pydantic import field_validator

from app.model.pydantic_base_models import PydanticBaseModelORM


class BinFileInfo(PydanticBaseModelORM):
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
