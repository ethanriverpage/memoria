#!/usr/bin/env python3
"""File timestamp utilities."""

import logging
import os
from typing import Optional

from common.text.parse import parse_timestamp_flexible

logger = logging.getLogger(__name__)


def update_file_timestamps(
    file_path,
    timestamp_str: Optional[str],
    timestamp_format: str = "%Y-%m-%d %H:%M:%S",
) -> bool:
    """Set file atime/mtime to match the given timestamp string.

    Supports ISO-8601 timestamps (including timezone offsets like +00:00, Z suffix,
    and T separator) as well as legacy formats via the timestamp_format parameter.
    """
    try:
        # Handle None or empty timestamp
        if not timestamp_str:
            return False

        # Use flexible parsing that handles ISO-8601 and legacy formats
        date_obj = parse_timestamp_flexible(timestamp_str, timestamp_format)
        if date_obj is None:
            logger.warning(
                f"Failed to update timestamps for {file_path}: "
                f"could not parse '{timestamp_str}'"
            )
            return False

        # Convert to Unix timestamp
        timestamp = date_obj.timestamp()

        # Update both access time and modification time
        os.utime(file_path, (timestamp, timestamp))
        return True

    except (TypeError, OSError) as e:
        logger.warning(f"Failed to update timestamps for {file_path}: {e}")
        return False
