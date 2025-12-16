#!/usr/bin/env python3
"""
Common utility functions for media processors
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional

# Re-export logging functions from centralized logging module for backwards compatibility


# ============================================================================
# Media Type Detection
# ============================================================================

# Supported media extensions
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".tiff", ".tif", ".dng", ".avif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v"}
ALL_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS


def get_media_type(file_path) -> Optional[str]:
    """Determine media type from file extension
    
    Args:
        file_path: Path to the file (string or Path object)
        
    Returns:
        "image" if image file, "video" if video file, None if unsupported
        
    Example:
        >>> get_media_type("photo.jpg")
        'image'
        >>> get_media_type("video.mp4")
        'video'
        >>> get_media_type("document.pdf")
        None
    """
    ext = os.path.splitext(str(file_path))[1].lower()
    
    if ext in IMAGE_EXTENSIONS:
        return "image"
    elif ext in VIDEO_EXTENSIONS:
        return "video"
    else:
        return None


def is_supported_media(file_path) -> bool:
    """Check if file is a supported media type
    
    Args:
        file_path: Path to the file (string or Path object)
        
    Returns:
        True if file is a supported image or video, False otherwise
        
    Example:
        >>> is_supported_media("photo.jpg")
        True
        >>> is_supported_media("document.pdf")
        False
    """
    ext = os.path.splitext(str(file_path))[1].lower()
    return ext in ALL_MEDIA_EXTENSIONS


def get_gps_format(file_path) -> str:
    """Get GPS coordinate format for file type
    
    Different file types require different GPS coordinate formats in exiftool:
    - Images: Use absolute values with explicit hemisphere reference fields
    - Videos: Use signed coordinates (exiftool auto-sets hemisphere)
    
    Args:
        file_path: Path to the file (string or Path object)
        
    Returns:
        "absolute" for images, "signed" for videos
        
    Example:
        >>> get_gps_format("photo.jpg")
        'absolute'
        >>> get_gps_format("video.mp4")
        'signed'
    """
    media_type = get_media_type(file_path)
    return "absolute" if media_type == "image" else "signed"


# ============================================================================
# Username Extraction
# ============================================================================


def extract_username_from_export_dir(input_dir: str, prefix: str) -> str:
    """Extract username from export directory name

    Handles various naming patterns:
    - google-{username}-YYYYMMDD
    - snapchat-{username}-YYYY-MM-DD
    - instagram-{username}-YYYY-MM-DD
    - instagram-{username}_-YYYY-MM-DD (old format with trailing underscore)

    Args:
        input_dir: Path to the input directory
        prefix: Expected prefix (e.g., "google", "snapchat", "instagram")

    Returns:
        Extracted username, or "unknown" if pattern doesn't match

    Examples:
        >>> extract_username_from_export_dir("google-username-20250526", "google")
        'username'
        >>> extract_username_from_export_dir("instagram-john-doe-2025-10-07", "instagram")
        'john-doe'
        >>> extract_username_from_export_dir("instagram-user123-2021-07-25", "instagram")
        'user123'
    """
    dir_name = Path(input_dir).name
    escaped_prefix = re.escape(prefix)
    
    # Try patterns in order of specificity
    patterns = [
        # Instagram old format with trailing underscore
        (r"instagram-(.+?)_-(\d{4}-?\d{2}-?\d{2})", lambda m: m.group(1) + "_") if prefix == "instagram" else None,
        # Standard pattern with date
        (rf"{escaped_prefix}-(.+?)-(\d{{4}}-?\d{{2}}-?\d{{2}})", lambda m: m.group(1)),
        # Fallback: anything after prefix with date stripped
        (rf"{escaped_prefix}-(.+)", lambda m: re.sub(r"_?-?\d{4}-?\d{2}-?\d{2}$", "", m.group(1)) or "unknown"),
    ]
    
    for pattern_tuple in patterns:
        if pattern_tuple is None:
            continue
        pattern, extract_fn = pattern_tuple
        match = re.match(pattern, dir_name)
        if match:
            return extract_fn(match)
    
    return "unknown"


def sanitize_filename(name: str, max_length: int = 50) -> str:
    """Sanitize a string for safe use in filenames

    Removes or replaces problematic characters that are not allowed in filenames
    on various operating systems (Windows, macOS, Linux).

    Args:
        name: String to sanitize (conversation title, username, etc.)
        max_length: Maximum length for sanitized name (default: 50)

    Returns:
        Sanitized string safe for use in filenames

    Example:
        >>> sanitize_filename("John Doe / Jane Smith")
        'john_doe_jane_smith'
        >>> sanitize_filename("Group: Friends & Family!")
        'group_friends_family'
    """
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


def default_worker_count() -> int:
    """Compute default worker count as CPU count minus one, minimum 1."""
    from multiprocessing import cpu_count

    return max(1, cpu_count() - 1)


def parse_bool_env(value: str) -> bool:
    """Parse boolean from environment variable string.
    
    Args:
        value: String value from environment variable
        
    Returns:
        True if value is truthy ("true", "1", "yes", "on"), False otherwise
        
    Example:
        >>> parse_bool_env("true")
        True
        >>> parse_bool_env("1")
        True
        >>> parse_bool_env("false")
        False
    """
    return value.lower() in ("true", "1", "yes", "on")


def should_cleanup_temp() -> bool:
    """Check if temporary directory cleanup should be performed.
    
    Reads DISABLE_TEMP_CLEANUP environment variable to determine whether
    temporary preprocessing directories should be cleaned up after processing.
    
    Returns:
        False if DISABLE_TEMP_CLEANUP is set to a truthy value (1, true, yes),
        True otherwise (cleanup by default).
        
    Example:
        >>> os.environ['DISABLE_TEMP_CLEANUP'] = '1'
        >>> should_cleanup_temp()
        False
        >>> os.environ.pop('DISABLE_TEMP_CLEANUP')
        >>> should_cleanup_temp()
        True
    """
    return not parse_bool_env(os.getenv("DISABLE_TEMP_CLEANUP", ""))


# ============================================================================
# Directory and File Utilities
# ============================================================================


def is_preprocessed_directory(input_dir: str) -> bool:
    """Check if directory has been preprocessed.

    A preprocessed directory contains:
    - metadata.json: Consolidated metadata from the preprocessing step
    - media/: Directory containing copied/organized media files

    This is used by processors to determine whether to run preprocessing
    or use an already-preprocessed directory.

    Args:
        input_dir: Path to the directory to check

    Returns:
        True if directory contains metadata.json and media/, False otherwise

    Example:
        >>> is_preprocessed_directory("/path/to/raw_export")
        False
        >>> is_preprocessed_directory("/path/to/preprocessed_export")
        True
    """
    input_path = Path(input_dir)
    metadata_file = input_path / "metadata.json"
    media_dir = input_path / "media"

    return metadata_file.exists() and media_dir.exists()


def update_file_timestamps(
    file_path,
    timestamp_str: Optional[str],
    timestamp_format: str = "%Y-%m-%d %H:%M:%S",
) -> bool:
    """Update filesystem access and modification timestamps to match content date.

    Sets both the access time and modification time of a file to match
    a timestamp extracted from the file's metadata (e.g., capture date).

    Args:
        file_path: Path to the file (string or Path object)
        timestamp_str: Timestamp string to parse (e.g., "2024-01-15 10:30:00"), or None
        timestamp_format: strptime format string for parsing timestamp_str
                         Common formats:
                         - "%Y-%m-%d %H:%M:%S" (default)
                         - "%Y-%m-%d %H:%M:%S UTC" (with UTC suffix)
                         - "%Y-%m-%dT%H:%M:%SZ" (ISO 8601)

    Returns:
        True if timestamps were updated successfully, False otherwise

    Example:
        >>> update_file_timestamps(
        ...     "/path/to/photo.jpg",
        ...     "2024-01-15 10:30:00",
        ...     "%Y-%m-%d %H:%M:%S"
        ... )
        True
    """
    from datetime import datetime

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
