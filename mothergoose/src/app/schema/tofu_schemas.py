from dataclasses import dataclass


@dataclass
class OpenTofuBackendOptions:
    """Data schema for OpenTofu backend options."""

    lock_address: str
    address: str
    unlock_address: str
    lock_method: str = "POST"
    unlock_method: str = "DELETE"
    retry_wait_min: int = 5
