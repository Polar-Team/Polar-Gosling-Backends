"""
Pydantic base models for YDB schemas.
"""

from pydantic import BaseModel, ConfigDict


class PydanticBaseModelORM(BaseModel):
    """Pydantic configuration for DB schemas."""

    model_config = ConfigDict(
        frozen=True,
        arbitrary_types_allowed=True,
    )


class PydanticBaseModelAPI(BaseModel):
    """Pydantic configuraion for API schemas."""

    model_config = ConfigDict(
        frozen=False,
        arbitrary_types_allowed=False,
    )
