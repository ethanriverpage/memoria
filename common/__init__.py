"""
Common modules shared across all media processors.

This package contains utilities and functionality that is used by multiple processors,
avoiding code duplication and ensuring consistency.

Subpackages:
    - common.dedup: Deduplication (hashing, database, service)
    - common.media: Media processing (exiftool, overlay, encoder)
    - common.files: File operations (copy, scan, filter)
    - common.config: Configuration (processor, env)
    - common.output: Output utilities (console, logging, progress)
    - common.upload: Upload utilities (immich, targets)
"""

# File scanning and filtering (from files subpackage)
from .files.filter import BannedFilesFilter
from .files.scanner import collect_media_files, iter_media_files
from .files.utils import short_path, get_export_name


# Username detection (from detection module)
from .detection import detect_from_dirname

# Utility functions (kept in common root)
from .utils import (
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

# Logging configuration (from output subpackage)
from .output.logging import setup_logging, get_logger, get_processor_logger

# Dependency checking (kept in common root)
from .dependency_checker import (
    check_exiftool,
    check_ffmpeg,
    print_exiftool_error,
    print_ffmpeg_error,
)

# Processing utilities (kept in common root)
from .processing import (
    temp_processing_directory,
    print_processing_summary,
    print_preprocessing_stats,
    process_batches_parallel,
)

# Progress bars (from output subpackage)
from .output.progress import (
    progress_bar,
    futures_progress,
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
    "short_path",
    "get_export_name",
    # Utilities
    "detect_from_dirname",
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
    "get_processor_logger",
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
    "PHASE_PREPROCESS",
    "PHASE_PROCESS",
    "PHASE_EXIF",
    "PHASE_CATALOG",
    "PHASE_DEDUP",
]
