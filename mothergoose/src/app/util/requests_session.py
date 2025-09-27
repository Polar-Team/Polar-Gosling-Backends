"""Utility to add a requests session with retries to a class."""

from functools import wraps
from typing import Any, Callable, Tuple, TypeVar

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

F = TypeVar("F", bound=Callable[..., Any])


def with_requests_session(
    cls: Any = None,
    retries: int = 3,
    backoff_factor: float = 0.5,
    timeout: int = 10,
    status_forcelist: Tuple[int, ...] = (502, 503, 504),
) -> Any:
    """Class decorator to add a requests session with retries to the class.
    The session is initialized in the __init__ method of the class and closed
    when the method completes.
    Args:
        cls: The class to decorate.
        retries: Number of retry attempts for failed requests.
        backoff_factor: A backoff factor to apply between retry attempts.
        timeout: Timeout for each request in seconds.
        status_forcelist: A set of HTTP status codes that
                          we should force a retry on.
    Returns:
        The decorated class with a session attribute.
    """

    def requests_for_init(func: F) -> Any:
        @wraps(func)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            self.session = requests.Session()
            retry = Retry(
                total=retries,
                backoff_factor=backoff_factor,
                status_forcelist=status_forcelist,
            )
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

            if timeout is not None:
                self.session.timeout = timeout
            try:
                return func(self, *args, **kwargs)
            finally:
                self.session.close()

        return wrapper

    def wrap(cls: Any) -> Any:
        cls.__init__ = requests_for_init(cls.__init__)
        return cls

    return wrap if cls is None else wrap(cls)
