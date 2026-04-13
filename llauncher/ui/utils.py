"""Utility functions for the UI layer."""


def format_uptime(seconds: int) -> str:
    """Format uptime seconds into human-readable string.

    Args:
        seconds: Uptime in seconds.

    Returns:
        Formatted string like "2h 34m" or "5m" or "30s".
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return " ".join(parts)
