import functools
import hashlib


def generate_version_id_decorator():
    """
    Decorator to generate a version ID by hashing the concatenation
    of sha256_version and version_name using SHA-256.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            sha256_version, version_name, source = func(*args, **kwargs)
            data = f"{sha256_version}:{version_name}:{source}".encode("utf-8")
            version_id = hashlib.sha256(data).hexdigest()
            return version_id

        return wrapper

    return decorator
