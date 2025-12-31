#!/usr/bin/env python3
"""
Common utility functions for media processors
"""

import logging
import os
import re
from datetime import datetime
from typing import Optional, Set

from common.validation.invariants import InvariantViolation

# Re-export logging functions from centralized logging module for backwards compatibility


# ============================================================================
# Media Type Detection - Single Source of Truth for Extensions
# ============================================================================

# Supported image extensions (used across all processors)
# Note: Other modules (file_utils, perceptual_hash) import from here
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".tiff",
    ".tif",
    ".dng",
    ".avif",
    ".bmp",
}

# Supported video extensions
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v"}

# Supported audio extensions (for messaging processors)
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".caf"}

# Combined extension sets for convenience
ALL_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
ALL_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

# Live Photo file extensions (for Apple Live Photos detection)
LIVE_PHOTO_IMAGE_EXTENSIONS = {".heic", ".heif", ".jpg", ".jpeg"}
LIVE_PHOTO_VIDEO_EXTENSIONS = {".mov"}

# Extensions that support EXIF structure validation/rebuild
# Note: PNGs use tEXt chunks, videos use container atoms - not EXIF
EXIF_BEARING_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".heif", ".tif", ".tiff"}


def get_media_type(file_path) -> Optional[str]:
    """Return "image", "video", or None based on file extension."""
    ext = os.path.splitext(str(file_path))[1].lower()

    if ext in IMAGE_EXTENSIONS:
        return "image"
    elif ext in VIDEO_EXTENSIONS:
        return "video"
    else:
        return None


def is_supported_media(file_path) -> bool:
    ext = os.path.splitext(str(file_path))[1].lower()
    return ext in ALL_MEDIA_EXTENSIONS


def get_gps_format(file_path) -> str:
    """Return "absolute" for images (with hemisphere refs), "signed" for videos."""
    media_type = get_media_type(file_path)
    return "absolute" if media_type == "image" else "signed"


# Unicode characters to normalize to ASCII equivalents
_TEXT_REPLACEMENTS = {
    # Quotation marks
    "\u2018": "'",  # LEFT SINGLE QUOTATION MARK
    "\u2019": "'",  # RIGHT SINGLE QUOTATION MARK
    "\u201a": "'",  # SINGLE LOW-9 QUOTATION MARK
    "\u201b": "'",  # SINGLE HIGH-REVERSED-9 QUOTATION MARK
    "\u201c": '"',  # LEFT DOUBLE QUOTATION MARK
    "\u201d": '"',  # RIGHT DOUBLE QUOTATION MARK
    "\u201e": '"',  # DOUBLE LOW-9 QUOTATION MARK
    "\u201f": '"',  # DOUBLE HIGH-REVERSED-9 QUOTATION MARK
    "\u2032": "'",  # PRIME (single)
    "\u2033": '"',  # DOUBLE PRIME
    # Dashes
    "\u2013": "--",  # EN DASH
    "\u2014": "--",  # EM DASH
    # Whitespace
    "\u2028": " ",  # LINE SEPARATOR
    "\u2029": " ",  # PARAGRAPH SEPARATOR
    # Emoji modifiers
    "\ufe0f": "",  # VARIATION SELECTOR-16 (emoji modifier)
}


def normalize_text(text: str) -> str:
    """Normalize Unicode characters in text to ASCII equivalents.

    Replaces common "smart" or "curly" quotes, dashes, and special whitespace
    with standard ASCII equivalents. Also removes emoji modifiers for
    consistent text storage and display across systems.

    Args:
        text: The input text to normalize.

    Returns:
        Text with Unicode characters replaced by ASCII equivalents.
    """
    if not text:
        return text

    for unicode_char, ascii_char in _TEXT_REPLACEMENTS.items():
        text = text.replace(unicode_char, ascii_char)

    return text


def sanitize_filename(name: str, max_length: int = 50) -> str:
    """Sanitize a string for safe use in filenames."""
    # Replace whitespace with underscores
    sanitized = name.replace(" ", "_")

    # Remove special characters except alphanumeric, hyphens, underscores
    # This removes: / \ : * ? " < > | and other special characters
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", sanitized)

    # Collapse multiple consecutive underscores/hyphens into single character
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = re.sub(r"-+", "-", sanitized)

    # Remove leading/trailing underscores and hyphens
    sanitized = sanitized.strip("_-")

    # Truncate to max length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    # Convert to lowercase for consistency
    sanitized = sanitized.lower()

    # If sanitization resulted in empty string, return a default
    if not sanitized:
        sanitized = "unknown"

    return sanitized


def _parse_timestamp_flexible(
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


class FilenameGenerator:
    """Generate unique filenames with sequence numbers for duplicates.

    Tracks used filenames to ensure uniqueness by appending _2, _3, etc.
    Filenames follow the Platform/User/Service order used across outputs:
      - Single-service: {platform}-{username}-{date}
      - Multi-service:  {platform}-{username}-{service}-{date}
    """

    def __init__(
        self,
        platform: str,
        username: str,
        service: Optional[str] = None,
        timestamp_format: str = "%Y-%m-%d %H:%M:%S",
        sequence_start: int = 2,
    ):
        # Sanitize components for safe filenames (lowercase, no special chars)
        self.platform = sanitize_filename(platform)
        self.username = sanitize_filename(username)
        self.service = sanitize_filename(service) if service else None
        self.timestamp_format = timestamp_format
        self.sequence_start = sequence_start
        self.used_filenames: Set[str] = set()

    def generate(
        self,
        timestamp_str: Optional[str],
        extension: str,
    ) -> str:
        """Generate a unique filename following Platform/User/Service order."""
        date_key = self._parse_date(timestamp_str)

        if self.service:
            base = (
                f"{self.platform}-{self.username}-{self.service}-{date_key}{extension}"
            )
        else:
            base = f"{self.platform}-{self.username}-{date_key}{extension}"

        return self._ensure_unique(base, extension)

    def _parse_date(self, timestamp_str: Optional[str]) -> str:
        """Parse timestamp to YYYYMMDD format.

        Raises:
            InvariantViolation: If timestamp is missing or unparseable.
                This indicates a bug in the calling code - processors must
                ensure valid timestamps before calling generate().
        """
        if not timestamp_str:
            raise InvariantViolation(
                f"FilenameGenerator.generate() called with missing timestamp "
                f"(platform={self.platform}, username={self.username})"
            )
        dt = _parse_timestamp_flexible(timestamp_str, self.timestamp_format)
        if dt:
            return dt.strftime("%Y%m%d")
        raise InvariantViolation(
            f"FilenameGenerator.generate() called with unparseable timestamp "
            f"'{timestamp_str}' (platform={self.platform}, username={self.username})"
        )

    def _ensure_unique(self, base: str, extension: str) -> str:
        """Add sequence number if filename already used."""
        if base not in self.used_filenames:
            self.used_filenames.add(base)
            return base

        # Handle empty extension case to avoid "empty separator" ValueError
        if extension:
            stem = base.rsplit(extension, 1)[0]
        else:
            stem = base

        sequence = self.sequence_start
        while True:
            candidate = f"{stem}_{sequence}{extension}"
            if candidate not in self.used_filenames:
                self.used_filenames.add(candidate)
                return candidate
            sequence += 1


def default_worker_count() -> int:
    """Compute default worker count as CPU count minus one, minimum 1."""
    from multiprocessing import cpu_count

    return max(1, cpu_count() - 1)


def parse_bool_env(value: Optional[str], default: bool = False) -> bool:
    """Parse a boolean from string value, with default for None."""
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def should_cleanup_preprocessing() -> bool:
    """Return False if DISABLE_PREPROCESSING_CLEANUP is set, True otherwise."""
    return not parse_bool_env(os.getenv("DISABLE_PREPROCESSING_CLEANUP", ""))


def update_file_timestamps(
    file_path,
    timestamp_str: Optional[str],
    timestamp_format: str = "%Y-%m-%d %H:%M:%S",
) -> bool:
    """Set file atime/mtime to match the given timestamp string.

    Supports ISO-8601 timestamps (including timezone offsets like +00:00, Z suffix,
    and T separator) as well as legacy formats via the timestamp_format parameter.
    """
    logger = logging.getLogger(__name__)

    try:
        # Handle None or empty timestamp
        if not timestamp_str:
            return False

        # Use flexible parsing that handles ISO-8601 and legacy formats
        date_obj = _parse_timestamp_flexible(timestamp_str, timestamp_format)
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
