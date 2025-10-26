"""
URL authentication schema.
"""

from pydantic import ValidationInfo, field_validator

from app.model.pydantic_base_models import PydanticBaseModelORM


class URLAuthSchema(PydanticBaseModelORM):
    """Data schema for URL authentication."""

    bearer: bool = True
    auth_header: str = "Authorization"
    token: str

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: str, info: ValidationInfo) -> str:
        """Validate token format: JWT, Bearer, GitLab, GitHub tokens."""

        if info.data.get("bearer"):
            if not value or not isinstance(value, str):
                raise ValueError("Bearer token must be a non-empty string")
        elif info.data.get("auth_header") == "PRIVATE-TOKEN":
            if not value or not isinstance(value, str):
                raise ValueError(
                    "GitLab PRIVATE-TOKEN must be a non-empty string",
                )
            if not value.startswith("glpat-") or len(value) < 20:
                raise ValueError(
                    "GitLab PRIVATE-TOKEN must start with 'glpat-'"
                    "and be at least 60 characters"
                )
        elif info.data.get(
            "auth_header",
        ) == "Authorization" and value.startswith("ghp_"):
            if len(value) < 40:
                raise ValueError(
                    "GitHub token must be at least 40 characters",
                )
        else:
            parts = value.split(".")
            if len(parts) == 3 and all(parts):
                # JWT token
                return value
            raise ValueError(
                "Token must be a Bearer, JWT, Gitlab, or GitHub token",
            )
        return value
