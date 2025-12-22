#!/usr/bin/env python3
"""
Common utility functions for media processors
"""

import logging
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Set

from common.detection import detect_from_dirname

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
    """Check if file is a supported media type."""
    ext = os.path.splitext(str(file_path))[1].lower()
    return ext in ALL_MEDIA_EXTENSIONS


def get_gps_format(file_path) -> str:
    """Return "absolute" for images (with hemisphere refs), "signed" for videos."""
    media_type = get_media_type(file_path)
    return "absolute" if media_type == "image" else "signed"


# ============================================================================
# Username Extraction
# ============================================================================


def extract_username_from_export_dir(input_dir: str, prefix: str) -> str:
    """Extract username from export directory name like "prefix-username-YYYYMMDD"."""
    result = detect_from_dirname(Path(input_dir), prefix)
    return result if result else "unknown"


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


class FilenameGenerator:
    """Generate unique filenames with sequence numbers for duplicates.

    Tracks used filenames to ensure uniqueness by appending _2, _3, etc.
    """

    def __init__(
        self,
        prefix: str,
        timestamp_format: str = "%Y-%m-%d %H:%M:%S",
        sequence_start: int = 2,
    ):
        self.prefix = prefix
        self.timestamp_format = timestamp_format
        self.sequence_start = sequence_start
        self.used_filenames: Set[str] = set()

    def generate(
        self,
        timestamp_str: Optional[str],
        identifier: str,
        extension: str,
    ) -> str:
        """Generate a unique filename like "prefix-identifier-20240115.ext"."""
        date_key = self._parse_date(timestamp_str)
        base = f"{self.prefix}-{identifier}-{date_key}{extension}"
        return self._ensure_unique(base, extension)

    def _parse_date(self, timestamp_str: Optional[str]) -> str:
        """Parse timestamp to YYYYMMDD format, or "00000000" if unparseable."""
        if not timestamp_str:
            return "00000000"
        try:
            dt = datetime.strptime(timestamp_str, self.timestamp_format)
            return dt.strftime("%Y%m%d")
        except ValueError:
            return "00000000"

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


def should_cleanup_temp() -> bool:
    """Return False if DISABLE_TEMP_CLEANUP is set, True otherwise."""
    return not parse_bool_env(os.getenv("DISABLE_TEMP_CLEANUP", ""))


# ============================================================================
# Directory and File Utilities
# ============================================================================


def is_preprocessed_directory(input_dir: str) -> bool:
    """Return True if directory contains metadata.json and media/."""
    input_path = Path(input_dir)
    metadata_file = input_path / "metadata.json"
    media_dir = input_path / "media"

    return metadata_file.exists() and media_dir.exists()


def update_file_timestamps(
    file_path,
    timestamp_str: Optional[str],
    timestamp_format: str = "%Y-%m-%d %H:%M:%S",
) -> bool:
    """Set file atime/mtime to match the given timestamp string."""
    logger = logging.getLogger(__name__)

    try:
        # Handle None or empty timestamp
        if not timestamp_str:
            return False

        # Handle common timestamp suffixes
        clean_timestamp = timestamp_str
        if clean_timestamp.endswith(" UTC"):
            clean_timestamp = clean_timestamp[:-4]
        if clean_timestamp.endswith("Z"):
            clean_timestamp = clean_timestamp[:-1]
            # Adjust format if it was ISO 8601
            if "T" in clean_timestamp and timestamp_format == "%Y-%m-%d %H:%M:%S":
                timestamp_format = "%Y-%m-%dT%H:%M:%S"

        # Parse the timestamp
        date_obj = datetime.strptime(clean_timestamp, timestamp_format)

        # Convert to Unix timestamp
        timestamp = date_obj.timestamp()

        # Update both access time and modification time
        os.utime(file_path, (timestamp, timestamp))
        return True

    except (ValueError, TypeError, OSError) as e:
        logger.warning(f"Failed to update timestamps for {file_path}: {e}")
        return False
