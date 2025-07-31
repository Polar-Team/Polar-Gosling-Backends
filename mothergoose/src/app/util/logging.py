"""This module contains decorator functions."""

import logging
from functools import wraps
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])


def logged(cls: Any = None, *, name: str = "") -> Any:
    def logged_for_init(func: F) -> Any:
        @wraps(func)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            logger_name = name or f"{self.__class__.__name__}-{func.__name__}"
            self.log = logging.getLogger(logger_name)
            for method_name in (
                "debug",
                "info",
                "warning",
                "error",
                "critical",
                "exception",
            ):
                method = getattr(self.log, method_name)
                setattr(self, method_name, method)
            return func(self, *args, **kwargs)

        return wrapper

    def wrap(cls: Any) -> Any:
        cls.__init__ = logged_for_init(cls.__init__)
        return cls

    return wrap if cls is None else wrap(cls)
