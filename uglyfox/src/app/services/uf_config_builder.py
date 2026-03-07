"""Shared UFConfig builder utility for UglyFox tasks.

Centralises the logic for resolving a UFConfig from either a pre-parsed
dict (from the DB cache) or from UglyFoxSettings defaults.
"""

from typing import Any, Dict, Optional

from app.core.config import settings
from app.model.policy_models import PruningPolicy, UFConfig
from app.services.policy_parser import PolicyParser


def build_uf_config(uf_config_dict: Optional[Dict[str, Any]] = None) -> UFConfig:
    """Build UFConfig from a dict or fall back to UglyFoxSettings defaults.

    Args:
        uf_config_dict: Optional pre-parsed UF config dict from DB cache.
                        If None, falls back to settings-based defaults.

    Returns:
        UFConfig instance ready for use by LifecycleService / PolicyEngine.
    """
    if uf_config_dict:
        return PolicyParser().parse_from_dict(uf_config_dict)
    return UFConfig(
        pruning=PruningPolicy(
            failed_threshold=settings.failed_threshold,
            max_age=settings.max_age,
            check_interval=settings.check_interval,
        )
    )
