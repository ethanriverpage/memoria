"""
Common modules shared across all media processors.

This package contains utilities and functionality that is used by multiple processors,
avoiding code duplication and ensuring consistency.
"""

# File scanning and filtering
from .filter_banned_files import BannedFilesFilter
from .file_scanner import collect_media_files, iter_media_files

# Re-export ProcessorBase and ProcessorRegistry from processors package for convenience
try:
    from processors.base import ProcessorBase  # type: ignore
    from processors.registry import ProcessorRegistry  # type: ignore
except Exception:
    pass

# Utility functions
from .utils import (
    extract_username_from_export_dir,
    sanitize_filename,
    default_worker_count,
    is_preprocessed_directory,
    update_file_timestamps,
    get_media_type,
    is_supported_media,
    FilenameGenerator,
    # Extension definitions (single source of truth)
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    ALL_MEDIA_EXTENSIONS,
    ALL_EXTENSIONS,
    LIVE_PHOTO_IMAGE_EXTENSIONS,
    LIVE_PHOTO_VIDEO_EXTENSIONS,
)

# Logging configuration
from .logging_config import setup_logging, get_logger

# Dependency checking
from .dependency_checker import (
    check_exiftool,
    check_ffmpeg,
    print_exiftool_error,
    print_ffmpeg_error,
)

# Processing utilities
from .processing import (
    temp_processing_directory,
    print_processing_summary,
    print_preprocessing_stats,
    process_batches_parallel,
)

# Progress bars
from .progress import (
    progress_bar,
    futures_progress,
    chunked_progress,
    PHASE_PREPROCESS,
    PHASE_PROCESS,
    PHASE_EXIF,
    PHASE_CATALOG,
    PHASE_DEDUP,
)

__version__ = "1.0.0"
__all__ = [
    # File scanning
    "BannedFilesFilter",
    "collect_media_files",
    "iter_media_files",
    # Processors
    "ProcessorBase",
    "ProcessorRegistry",
    # Utilities
    "extract_username_from_export_dir",
    "sanitize_filename",
    "default_worker_count",
    "is_preprocessed_directory",
    "update_file_timestamps",
    "get_media_type",
    "is_supported_media",
    "FilenameGenerator",
    # Extension definitions
    "IMAGE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "AUDIO_EXTENSIONS",
    "ALL_MEDIA_EXTENSIONS",
    "ALL_EXTENSIONS",
    "LIVE_PHOTO_IMAGE_EXTENSIONS",
    "LIVE_PHOTO_VIDEO_EXTENSIONS",
    # Logging
    "setup_logging",
    "get_logger",
    # Dependencies
    "check_exiftool",
    "check_ffmpeg",
    "print_exiftool_error",
    "print_ffmpeg_error",
    # Processing
    "temp_processing_directory",
    "print_processing_summary",
    "print_preprocessing_stats",
    "process_batches_parallel",
    # Progress
    "progress_bar",
    "futures_progress",
    "chunked_progress",
    "PHASE_PREPROCESS",
    "PHASE_PROCESS",
    "PHASE_EXIF",
    "PHASE_CATALOG",
    "PHASE_DEDUP",
]
