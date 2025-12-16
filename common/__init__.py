"""
Common modules shared across all media processors.

This package contains utilities and functionality that is used by multiple processors,
avoiding code duplication and ensuring consistency.
"""

from .filter_banned_files import BannedFilesFilter

# Re-export ProcessorBase and ProcessorRegistry from processors package for convenience
try:
    from processors.base import ProcessorBase  # type: ignore
    from processors.registry import ProcessorRegistry  # type: ignore
except Exception:
    pass
from .utils import extract_username_from_export_dir
from .logging_config import setup_logging
from .dependency_checker import (
    check_exiftool,
    check_ffmpeg,
    print_exiftool_error,
    print_ffmpeg_error,
)

__version__ = "1.0.0"
__all__ = [
    "BannedFilesFilter",
    "ProcessorBase",
    "ProcessorRegistry",
    "extract_username_from_export_dir",
    "setup_logging",
    "check_exiftool",
    "check_ffmpeg",
    "print_exiftool_error",
    "print_ffmpeg_error",
]
