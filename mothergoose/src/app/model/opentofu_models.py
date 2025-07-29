from pydantic.dataclasses import dataclass, Field
from app.schema.ydb_tables import YDBTableSchema


@dataclass
class OpenTofuModelYDB:
    """
    Base class for OpenTofu models.
    This class can be extended to create specific OpenTofu models.
    """

    tables: list[YDBTableSchema] = Field(
        ..., description="List of table schemas to be created in YDB"
    )

    model_name: str = "OpenTofuModel"
    version: str = "1.0"

    def __post_init__(self):
        """Post-initialization logic can be added here if needed."""
        pass


@dataclass
class OpenTofuModelDynamoDB:
    """
    Base class for OpenTofu models specific to DynamoDB.
    This class can be extended to create specific OpenTofu models for DynamoDB.
    """

    model_name: str = "OpenTofuModel"
    version: str = "1.0"

    def __post_init__(self):
        """Post-initialization logic can be added here if needed."""
        pass
