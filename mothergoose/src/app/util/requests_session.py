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
