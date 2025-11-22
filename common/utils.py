#!/usr/bin/env python3
"""
Common utility functions for media processors
"""

import logging
import os
import re
from pathlib import Path
from typing import Optional


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


def setup_logging(
    verbose: bool = False, log_file: Optional[str] = None
) -> logging.Logger:
    """Configure logging for media processing

    Sets up console logging with appropriate level and optional file logging.

    In info mode (verbose=False):
    - Console shows only ERROR messages (clean output with progress bars)
    - Third-party libraries are suppressed to WARNING level

    In verbose mode (verbose=True):
    - Console shows INFO level messages
    - File captures all DEBUG logs
    - Third-party libraries still suppressed to WARNING

    Args:
        verbose: If True, enable verbose console output and file logging
        log_file: Optional path to log file for persistent logging

    Returns:
        Configured root logger

    Example:
        >>> logger = setup_logging(verbose=True, log_file="processing.log")
        >>> logger.info("Processing started")
    """
    # Configure root logger to capture everything
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Clear any existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Create console handler
    # In info mode: only show errors (clean console with progress bars)
    # In verbose mode: show info and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO if verbose else logging.ERROR)

    # Create formatter
    if verbose:
        # More detailed format for verbose mode
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        # Simpler format for normal mode
        formatter = logging.Formatter("%(levelname)s: %(message)s")

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Add file handler if log_file specified
    if log_file:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # Always log everything to file

        # Use detailed format for file logging
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for library in ['PIL', 'urllib3', 'urllib3.connectionpool', 'requests']:
        logging.getLogger(library).setLevel(logging.WARNING)

    return root_logger


def add_export_log_handler(export_name: str, verbose: bool = False) -> Optional[logging.FileHandler]:
    """Add a per-export log file handler to the root logger
    
    Creates a separate log file for a specific export being processed.
    This allows each export to have its own log file while still logging
    to the main log file.
    
    Args:
        export_name: Name of the export (used in log filename)
        verbose: If True, create the log file. If False, skip per-export logging.
        
    Returns:
        The created FileHandler, or None if verbose is False
        
    Example:
        >>> handler = add_export_log_handler("google-user-20250526", verbose=True)
        >>> # Process the export...
        >>> if handler:
        ...     remove_export_log_handler(handler)
    """
    if not verbose:
        return None
    
    from datetime import datetime
    
    # Create logs directory if it doesn't exist
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)
    
    # Create log filename with export name and timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = logs_dir / f"log-{export_name}-{timestamp}.log"
    
    # Create file handler for this export
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # Log everything to file
    
    # Use detailed format for file logging
    file_formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    
    # Add handler to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)
    
    return file_handler


def remove_export_log_handler(handler: logging.FileHandler) -> None:
    """Remove a per-export log file handler from the root logger
    
    Should be called after processing an export to clean up the handler
    and close the file.
    
    Args:
        handler: The FileHandler to remove (returned by add_export_log_handler)
        
    Example:
        >>> handler = add_export_log_handler("google-user-20250526", verbose=True)
        >>> # Process the export...
        >>> if handler:
        ...     remove_export_log_handler(handler)
    """
    if handler is None:
        return
    
    root_logger = logging.getLogger()
    root_logger.removeHandler(handler)
    handler.close()


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
