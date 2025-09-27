"""Payload schemas for OpenTofu wrapper."""

from pydantic import BaseModel


class OpenTofuPayload(BaseModel):
    """Schema for OpenTofu wrapper payload."""

    config: str  # Adjust fields as needed for your wrapper
