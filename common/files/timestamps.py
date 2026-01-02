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
) -> bool:
    """Set file atime/mtime to match the given timestamp string.

    All preprocessors store timestamps in canonical ISO 8601 format
    (YYYY-MM-DDTHH:MM:SSZ), parsed automatically by parse_timestamp_flexible.
    """
    try:
        # Handle None or empty timestamp
        if not timestamp_str:
            return False

        # Parse canonical ISO-8601 format
        date_obj = parse_timestamp_flexible(timestamp_str)
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
