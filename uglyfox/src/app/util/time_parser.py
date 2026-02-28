"""Time conversion utilities for UglyFox policy evaluation."""


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
