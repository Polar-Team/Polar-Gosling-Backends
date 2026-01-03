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

    def __init__(self, message: str = None):
        self.message = message
        super().__init__(self.message)
