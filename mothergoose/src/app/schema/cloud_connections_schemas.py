"""Cloud connection schemas for multi-cloud support."""

from pydantic import ConfigDict, Field, field_validator
from pydantic.dataclasses import dataclass


@dataclass(config=ConfigDict(frozen=True))
class YandexCloudConnectionInfo:
    """Yandex Cloud connection information."""

    yc_token: str | None = Field(None, description="Yandex Cloud OAuth token")
    folder_id: str | None = Field(None, description="Yandex Cloud folder ID")
    server_api: str = Field(
        "http://169.254.169.254/computeMetadata/v1",
        description="Yandex Cloud metadata server API URL",
    )

    @field_validator("server_api", mode="before")
    @classmethod
    def validate_server_api(cls, value: str) -> str:
        """
        Ensure server_api is a valid URL string.
        """
        if isinstance(value, str) and value.strip():
            if value.startswith("http://169.254.169.254") or value.startswith(
                "https://"
            ):
                return value
            raise ValueError("server_api must start with http:// or https://")
        raise ValueError("server_api must be a non-empty string.")
