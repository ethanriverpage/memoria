#!/usr/bin/env python3
"""Media type definitions and extension constants.

This is the single source of truth for supported file extensions across all processors.
"""

import os
from enum import Enum
from typing import Optional


class MediaType(Enum):
    """Enumeration of supported media types."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"


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


def get_media_type(file_path) -> Optional[MediaType]:
    """Return MediaType.IMAGE, MediaType.VIDEO, MediaType.AUDIO, or None based on extension."""
    ext = os.path.splitext(str(file_path))[1].lower()

    if ext in IMAGE_EXTENSIONS:
        return MediaType.IMAGE
    elif ext in VIDEO_EXTENSIONS:
        return MediaType.VIDEO
    elif ext in AUDIO_EXTENSIONS:
        return MediaType.AUDIO
    else:
        return None


def get_gps_format(file_path) -> str:
    """Return "absolute" for images (with hemisphere refs), "signed" for videos."""
    media_type = get_media_type(file_path)
    return "absolute" if media_type == MediaType.IMAGE else "signed"
