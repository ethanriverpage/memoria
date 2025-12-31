#!/usr/bin/env python3
"""Timestamp parsing utilities."""

from datetime import datetime
from typing import Optional


def parse_timestamp_flexible(
    timestamp_str: str,
    fallback_format: str = "%Y-%m-%d %H:%M:%S",
) -> Optional[datetime]:
    """Parse a timestamp string flexibly, supporting ISO-8601 and legacy formats.

    Attempts to parse timestamps in the following order:
    1. ISO-8601 format via fromisoformat() (handles +00:00, Z, T separator)
    2. Fallback format via strptime()

    Args:
        timestamp_str: The timestamp string to parse.
        fallback_format: Format string for strptime fallback.

    Returns:
        datetime object if parsing succeeded, None otherwise.
    """
    if not timestamp_str:
        return None

    # Clean common suffixes
    clean = timestamp_str.strip()
    if clean.endswith(" UTC"):
        clean = clean[:-4]

    # Try ISO-8601 first (handles Z, +00:00, T separator)
    try:
        # Normalize 'Z' to '+00:00' for fromisoformat compatibility
        iso_str = clean.replace("Z", "+00:00") if clean.endswith("Z") else clean
        return datetime.fromisoformat(iso_str)
    except ValueError:
        pass

    # Fallback to provided format
    try:
        return datetime.strptime(clean, fallback_format)
    except ValueError:
        pass

    return None
