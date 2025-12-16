#!/usr/bin/env python3
"""
File utility functions for media processing

Provides shared file type detection and extension correction functionality
used across multiple preprocessors.
"""

import logging
from pathlib import Path
from typing import Callable, Optional

import magic

logger = logging.getLogger(__name__)

# MIME type to extension mapping
# Comprehensive mapping covering images, videos, and audio formats
MIME_TO_EXTENSION = {
    # Images
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
    # Videos
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/x-m4v": ".m4v",
    # Audio
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
}

# Define which extensions belong to which category (for cross-category validation)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".tiff", ".bmp", ".avif"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a"}


def detect_and_correct_extension(
    file_path: Path,
    original_filename: str,
    log_callback: Optional[Callable[[str, str], None]] = None,
    allow_cross_category: bool = False,
) -> str:
    """
    Detect actual file type using python-magic and return correct extension.

    Uses the python-magic library to analyze file contents and determine
    the actual MIME type, then maps it to the appropriate file extension.

    Args:
        file_path: Path to the file to analyze
        original_filename: Original filename (used for fallback extension)
        log_callback: Optional callable(message, details) for logging corrections
        allow_cross_category: If False, prevents image->video or video->image corrections
                             (default False to avoid false positives)

    Returns:
        Correct extension (with dot), or original extension if detection fails

    Example:
        >>> correct_ext = detect_and_correct_extension(
        ...     Path("/tmp/file.jpg"),
        ...     "photo.jpg",
        ...     log_callback=lambda msg, detail: print(f"{msg}: {detail}")
        ... )
    """
    original_ext = Path(original_filename).suffix.lower()

    try:
        mime = magic.from_file(str(file_path), mime=True)
        detected_ext = MIME_TO_EXTENSION.get(mime)

        if detected_ext and detected_ext != original_ext:
            # Check for cross-category conversion (e.g., image -> video)
            if not allow_cross_category:
                original_is_image = original_ext in IMAGE_EXTENSIONS
                original_is_video = original_ext in VIDEO_EXTENSIONS
                original_is_audio = original_ext in AUDIO_EXTENSIONS
                detected_is_image = detected_ext in IMAGE_EXTENSIONS
                detected_is_video = detected_ext in VIDEO_EXTENSIONS
                detected_is_audio = detected_ext in AUDIO_EXTENSIONS

                # Only allow same-category corrections
                same_category = (
                    (original_is_image and detected_is_image)
                    or (original_is_video and detected_is_video)
                    or (original_is_audio and detected_is_audio)
                )

                if not same_category:
                    logger.debug(
                        f"Skipping cross-category extension correction for {original_filename}: "
                        f"{original_ext} -> {detected_ext}"
                    )
                    return original_ext

            if log_callback:
                log_callback(
                    f"Extension corrected: {original_filename}",
                    f"MIME: {mime}, {original_ext} -> {detected_ext}",
                )
            return detected_ext
        elif detected_ext:
            return original_ext  # Already correct

    except Exception as e:
        logger.debug(f"python-magic failed for {original_filename}: {e}")

    return original_ext


def get_mime_type(file_path: Path) -> Optional[str]:
    """
    Get the MIME type of a file using python-magic.

    Args:
        file_path: Path to the file to analyze

    Returns:
        MIME type string or None if detection fails

    Example:
        >>> mime = get_mime_type(Path("/tmp/photo.jpg"))
        >>> print(mime)  # "image/jpeg"
    """
    try:
        return magic.from_file(str(file_path), mime=True)
    except Exception as e:
        logger.debug(f"Failed to get MIME type for {file_path}: {e}")
        return None

