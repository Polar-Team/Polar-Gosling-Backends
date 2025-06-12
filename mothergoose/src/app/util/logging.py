"""This module contains decorator functions."""

import logging
from functools import wraps

logger = logging.getLogger(__name__)


def log(cls=None, *, name=""):
    """Function for logger initialization"""

    def logged_for_init(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            logger_name = name or f"{self.__class__.__name__}-{func.__name__}"
            self.log = logging.getLogger(logger_name)
            return func(self, *args, **kwargs)

        return wrapper

    def wrap(cls):
        cls.__init__ = logged_for_init(cls.__init__)
        return cls

    return wrap if cls is None else wrap(cls)
