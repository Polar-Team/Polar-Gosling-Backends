import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from functools import wraps


def with_requests_session(
    cls=None,
    retries=3,
    backoff_factor=0.5,
    timeout=10,
    status_forcelist=(502, 503, 504),
):
    def requests_for_init(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            self.session = requests.Session()
            retry = Retry(
                total=retries,
                backoff_factor=backoff_factor,
                status_forcelist=status_forcelist,
            )
            adapter = HTTPAdapter(max_retries=retry)
            self.session.mount("http://", adapter)
            self.session.mount("https://", adapter)

            try:
                return func(self, *args, **kwargs)
            finally:
                self.session.close()

        return wrapper

    def wrap(cls):
        cls.__init__ = requests_for_init(cls.__init__)
        return cls

    return wrap if cls is None else wrap(cls)
