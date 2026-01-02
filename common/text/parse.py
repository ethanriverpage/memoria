#!/usr/bin/env python3
"""Timestamp parsing and normalization utilities.

All preprocessors store timestamps in the unified ISO 8601 format (CANONICAL_TIMESTAMP_FORMAT).
This centralizes timestamp handling and simplifies downstream processing.
"""

from datetime import datetime, timezone
from typing import Optional


# Unified timestamp format for all preprocessed metadata
# ISO 8601 format with UTC timezone: "2021-01-04T23:08:30Z"
CANONICAL_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_timestamp_flexible(timestamp_str: str) -> Optional[datetime]:
    """Parse a timestamp string flexibly, supporting ISO-8601 and common formats.

    Attempts to parse timestamps in the following order:
    1. ISO-8601 format via fromisoformat() (handles +00:00, Z, T separator)
    2. Common fallback format "%Y-%m-%d %H:%M:%S"

    Args:
        timestamp_str: The timestamp string to parse.

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

    # Fallback to common format
    try:
        return datetime.strptime(clean, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    return None


def normalize_timestamp(timestamp_str: Optional[str]) -> Optional[str]:
    """Normalize any timestamp string to canonical ISO 8601 format.

    This is the primary function for preprocessors to convert platform-specific
    timestamps to the unified format stored in metadata.json.

    Args:
        timestamp_str: Timestamp in any supported format (ISO-8601, "YYYY-MM-DD HH:MM:SS",
                      "YYYY-MM-DD HH:MM:SS UTC", etc.)

    Returns:
        Timestamp in canonical format "YYYY-MM-DDTHH:MM:SSZ", or None if parsing fails.
    """
    if not timestamp_str:
        return None

    dt = parse_timestamp_flexible(timestamp_str)
    if dt is None:
        return None

    return format_timestamp_canonical(dt)


def format_timestamp_canonical(dt: Optional[datetime]) -> Optional[str]:
    """Format a datetime object to canonical ISO 8601 format.

    Args:
        dt: datetime object to format.

    Returns:
        Timestamp in canonical format "YYYY-MM-DDTHH:MM:SSZ", or None if dt is None.
    """
    if dt is None:
        return None

    # If datetime has timezone info, convert to UTC
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)

    return dt.strftime(CANONICAL_TIMESTAMP_FORMAT)
