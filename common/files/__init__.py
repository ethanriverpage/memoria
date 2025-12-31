"""
File operation utilities for media processing.

This package provides:
- File copying with reflink (copy-on-write) optimization
- MIME type detection and extension correction
- Directory scanning with filtering
- Banned file pattern matching
- Media type definitions and extension constants
- Filename generation and sanitization
- File timestamp utilities
"""

from common.files.utils import (
    copy_file,
    batch_correct_extensions,
    get_mime_type,
    MIME_TO_EXTENSION,
    short_path,
    get_export_name,
)
from common.files.scanner import (
    iter_media_files,
    iter_files,
    iter_subdirs,
    collect_media_files,
)
from common.files.filter import BannedFilesFilter
from common.files.types import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    ALL_MEDIA_EXTENSIONS,
    ALL_EXTENSIONS,
    LIVE_PHOTO_IMAGE_EXTENSIONS,
    LIVE_PHOTO_VIDEO_EXTENSIONS,
    EXIF_BEARING_EXTENSIONS,
    get_media_type,
    is_supported_media,
    get_gps_format,
)
from common.files.timestamps import update_file_timestamps
from common.files.naming import sanitize_filename, FilenameGenerator

__all__ = [
    # File utilities
    "copy_file",
    "batch_correct_extensions",
    "get_mime_type",
    "MIME_TO_EXTENSION",
    "short_path",
    "get_export_name",
    # Scanner
    "iter_media_files",
    "iter_files",
    "iter_subdirs",
    "collect_media_files",
    # Filter
    "BannedFilesFilter",
    # Types / Extensions
    "IMAGE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "AUDIO_EXTENSIONS",
    "ALL_MEDIA_EXTENSIONS",
    "ALL_EXTENSIONS",
    "LIVE_PHOTO_IMAGE_EXTENSIONS",
    "LIVE_PHOTO_VIDEO_EXTENSIONS",
    "EXIF_BEARING_EXTENSIONS",
    "get_media_type",
    "is_supported_media",
    "get_gps_format",
    # Timestamps
    "update_file_timestamps",
    # Naming
    "sanitize_filename",
    "FilenameGenerator",
]
