#!/usr/bin/env python3
"""
Username Detection Module

Provides a common mechanism for resolving export usernames across processors.
Each processor defines its own detection functions (policy), while this module
provides the resolver mechanism that tries detectors in order.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    """Result of username detection."""

    username: Optional[str]
    source: str  # Which method succeeded (for logging/debugging)


# A detector is a callable: (export_path) -> Optional[str]
# Returns username if detected, None otherwise
Detector = Callable[[Path], Optional[str]]


def resolve_username(
    export_path: Path,
    detectors: List[Tuple[str, Detector]],
) -> DetectionResult:
    """
    Try each detector in order until one succeeds.

    Args:
        export_path: Path to export directory
        detectors: List of (name, detector_function) tuples

    Returns:
        DetectionResult with username and source
    """
    for name, detector in detectors:
        try:
            username = detector(export_path)
            if username:
                logger.info("Username '%s' detected via %s", username, name)
                return DetectionResult(username=username, source=name)
        except Exception as e:
            logger.debug("Detector '%s' failed: %s", name, e)
            continue

    logger.warning("All username detection methods failed for %s", export_path)
    return DetectionResult(username=None, source="none")


def detect_from_dirname(
    export_path: Path,
    prefix: str,
) -> Optional[str]:
    """
    Extract username from export directory name.

    Common patterns:
    - platform-username-YYYYMMDD
    - platform-username_-YYYYMMDD (Instagram with trailing underscore)

    Args:
        export_path: Path to export directory
        prefix: Platform prefix (e.g., "instagram", "discord")

    Returns:
        Username if found, None otherwise
    """
    dir_name = export_path.name
    escaped_prefix = re.escape(prefix)

    # Try patterns in order of specificity
    patterns = [
        # Instagram old format with trailing underscore
        (
            (r"instagram-(.+?)_-(\d{4}-?\d{2}-?\d{2})", lambda m: m.group(1) + "_")
            if prefix == "instagram"
            else None
        ),
        # Standard pattern with date
        (rf"{escaped_prefix}-(.+?)-(\d{{4}}-?\d{{2}}-?\d{{2}})", lambda m: m.group(1)),
        # Fallback: anything after prefix with date stripped
        (
            rf"{escaped_prefix}-(.+)",
            lambda m: re.sub(r"_?-?\d{4}-?\d{2}-?\d{2}$", "", m.group(1)) or None,
        ),
    ]

    for pattern_tuple in patterns:
        if pattern_tuple is None:
            continue
        pattern, extract_fn = pattern_tuple
        match = re.match(pattern, dir_name)
        if match:
            result = extract_fn(match)
            if result:
                return result

    return None
