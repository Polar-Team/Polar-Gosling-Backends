"""YDB types module"""

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, RootModel
from ydb import PrimitiveType


class YDBUtf8(BaseModel):
    """YDB UTF-8 data type."""

    type: Literal["Utf8"] = "Utf8"
    parametarized_type: PrimitiveType = PrimitiveType.Utf8


class YDBInt64(BaseModel):
    """YDB Int64 data type."""

    type: Literal["Int64"] = "Int64"
    parametarized_type: PrimitiveType = PrimitiveType.Int64


class YDBDouble(BaseModel):
    """YDB Double data type."""

    type: Literal["Double"] = "Double"
    parametarized_type: PrimitiveType = PrimitiveType.Double


class YDBBool(BaseModel):
    """YDB Bool data type."""

    type: Literal["Bool"] = "Bool"
    parametarized_type: PrimitiveType = PrimitiveType.Bool


class YDBBytes(BaseModel):
    """YDB Bytes data type."""

    type: Literal["String"] = "String"
    parametarized_type: PrimitiveType = PrimitiveType.String


class YDBType(
    # pylint: disable=too-few-public-methods
    RootModel[
        Union[
            YDBUtf8,
            YDBInt64,
            YDBDouble,
            YDBBool,
            YDBBytes,
        ]
    ]
):
    """Base class for YDB data types."""

    model_config = ConfigDict(model_discriminator="type")
