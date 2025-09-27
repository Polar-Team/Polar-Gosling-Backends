"""
Pydantic base models for YDB schemas.
"""

from pydantic import BaseModel, ConfigDict


class PydanticBaseModelORM(BaseModel):
    """Pydantic configuration for YDB schemas."""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
    )
