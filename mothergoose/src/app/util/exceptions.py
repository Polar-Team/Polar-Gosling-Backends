"""
Module defining custom exceptions
for handling absent reply information.
"""


class AbsentReplyError(Exception):
    """Exception raised when required reply information is absent.

    Attributes:
        message: Explanation of the error

    Example:
        raise AbsentInfoError('Token is required but not provided.')
    """

    def __init__(self, message: str | None = None):
        self.message = message
        super().__init__(self.message)


class BinaryVersionNotFoundError(Exception):
    """
    Exception raised when a required binary version is not available.

    Task 12.7: Used by version resolver when Egg-specific or active version
    is not found in binary_versions table.

    Attributes:
        message: Explanation of the error

    Example:
        raise BinaryVersionNotFoundError('Gosling CLI version 1.2.3 is not available.')
    """

    def __init__(self, message: str | None = None):
        self.message = message
        super().__init__(self.message)
