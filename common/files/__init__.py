"""
File operation utilities for media processing.

This package provides:
- File copying with reflink (copy-on-write) optimization
- MIME type detection and extension correction
- Directory scanning with filtering
- Banned file pattern matching
"""

from common.files.utils import (
    copy_file,
    copy_file_safe,
    batch_correct_extensions,
    get_mime_type,
    MIME_TO_EXTENSION,
)
from common.files.scanner import (
    iter_media_files,
    collect_media_files,
)
from common.files.filter import BannedFilesFilter

__all__ = [
    # File utilities
    "copy_file",
    "copy_file_safe",
    "batch_correct_extensions",
    "get_mime_type",
    "MIME_TO_EXTENSION",
    # Scanner
    "iter_media_files",
    "collect_media_files",
    # Filter
    "BannedFilesFilter",
]
