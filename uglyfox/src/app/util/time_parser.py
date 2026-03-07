"""Time conversion utilities for UglyFox policy evaluation."""

import re


def parse_duration(duration: str) -> float:
    """Parse a duration string into seconds.

    Supports the following suffixes:
    - ``s`` — seconds
    - ``m`` — minutes
    - ``h`` — hours
    - ``d`` — days

    Args:
        duration: Duration string, e.g. ``"5m"``, ``"24h"``, ``"30s"``, ``"1d"``.

    Returns:
        Duration in seconds as a float.

    Raises:
        ValueError: If the string cannot be parsed.
    """
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd])", duration.strip())
    if not match:
        raise ValueError(f"Cannot parse duration: {duration!r}")
    value, unit = float(match.group(1)), match.group(2)
    multipliers = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
    return value * multipliers[unit]


def hours_to_seconds(hours: float) -> float:
    """Convert hours to seconds.

    Args:
        hours: Duration in hours.

    Returns:
        Duration in seconds.
    """
    return hours * 3600.0


def minutes_to_seconds(minutes: float) -> float:
    """Convert minutes to seconds.

    Args:
        minutes: Duration in minutes.

    Returns:
        Duration in seconds.
    """
    return minutes * 60.0


def seconds_to_hours(seconds: float) -> float:
    """Convert seconds to hours.

    Args:
        seconds: Duration in seconds.

    Returns:
        Duration in hours.
    """
    return seconds / 3600.0
