import inspect
from typing import Callable, TypeVar, Any
from functools import wraps

F = TypeVar("F", bound=Callable[..., Any])


def only_called_by(*allowed_classes: str) -> Any:
    allowed_set = set(allowed_classes)

    def decorator(method: F) -> Any:
        @wraps(method)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            stack = inspect.stack()
            caller = None
            try:
                # Skip this method and immediate caller
                for frame_info in stack[2:]:
                    caller_self = frame_info.frame.f_locals.get("self")
                    if caller_self and caller_self != self:
                        caller_class_name = caller_self.__class__.__name__
                        if caller_class_name in allowed_set:
                            caller = caller_self
                            break
            finally:
                del stack

            if caller is None:
                raise PermissionError(
                    f"Access denied: method '{method.__name__}' "
                    f"can only be called by: {', '.join(allowed_set)}"
                )

            # Pass the caller as a kwarg if method accepts it
            if "caller" in method.__code__.co_varnames:
                return method(self, *args, caller=caller, **kwargs)
            return method(self, *args, **kwargs)

        return wrapper

    return decorator
