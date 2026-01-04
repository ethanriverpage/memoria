"""
Common modules shared across all media processors.

This package contains utilities and functionality that is used by multiple processors,
avoiding code duplication and ensuring consistency.

Subpackages:
    - common.config: Configuration (env, runtime, dependencies)
    - common.dedup: Deduplication (hashing, database, service)
    - common.failures: Failure tracking and reporting
    - common.files: File operations (copy, scan, filter, types, naming)
    - common.media: Media processing (exiftool, overlay, encoder)
    - common.output: Output utilities (console, logging, progress, summary)
    - common.processing: Parallel processing and caching
    - common.text: Text normalization and parsing
    - common.upload: Upload utilities (immich, targets)
    - common.validation: Invariant validators
"""

# ============================================================================
# File operations (from files subpackage)
# ============================================================================
from .files.filter import BannedFilesFilter
from .files.scanner import collect_media_files, iter_media_files
from .files.utils import short_path, get_export_name

# Extension constants and media type detection
from .files.types import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    ALL_MEDIA_EXTENSIONS,
    ALL_EXTENSIONS,
    LIVE_PHOTO_IMAGE_EXTENSIONS,
    LIVE_PHOTO_VIDEO_EXTENSIONS,
    get_media_type,
)

# Filename utilities
from .files.naming import sanitize_filename, FilenameGenerator
from .files.timestamps import update_file_timestamps

# ============================================================================
# Configuration (from config subpackage)
# ============================================================================
from .config.runtime import default_thread_count
from .config.dependencies import (
    check_exiftool,
    check_ffmpeg,
    print_exiftool_error,
    print_ffmpeg_error,
)

# ============================================================================
# Text processing (from text subpackage)
# ============================================================================
from .text.normalize import normalize_text

# ============================================================================
# Username detection
# ============================================================================
from .detection import detect_from_dirname

# ============================================================================
# Logging and output (from output subpackage)
# ============================================================================
from .output.logging import setup_logging, get_processor_logger
from .output.progress import (
    progress_bar,
    futures_progress,
    PHASE_PREPROCESS,
    PHASE_PROCESS,
)
from .output.summary import print_processing_summary, print_preprocessing_stats

# ============================================================================
# Processing utilities (from processing subpackage)
# ============================================================================
from .processing import temp_processing_directory

# ============================================================================
# Failure tracking (from failures subpackage)
# ============================================================================
from .failures import FailureTracker

__version__ = "1.0.0"
__all__ = [
    # File scanning
    "BannedFilesFilter",
    "collect_media_files",
    "iter_media_files",
    "short_path",
    "get_export_name",
    # Extension definitions
    "IMAGE_EXTENSIONS",
    "VIDEO_EXTENSIONS",
    "AUDIO_EXTENSIONS",
    "ALL_MEDIA_EXTENSIONS",
    "ALL_EXTENSIONS",
    "LIVE_PHOTO_IMAGE_EXTENSIONS",
    "LIVE_PHOTO_VIDEO_EXTENSIONS",
    # Media type detection
    "get_media_type",
    # Filename utilities
    "sanitize_filename",
    "FilenameGenerator",
    "update_file_timestamps",
    # Text
    "normalize_text",
    # Username detection
    "detect_from_dirname",
    # Configuration
    "default_thread_count",
    # Dependencies
    "check_exiftool",
    "check_ffmpeg",
    "print_exiftool_error",
    "print_ffmpeg_error",
    # Logging
    "setup_logging",
    "get_processor_logger",
    # Progress
    "progress_bar",
    "futures_progress",
    "PHASE_PREPROCESS",
    "PHASE_PROCESS",
    # Processing
    "temp_processing_directory",
    # Summary
    "print_processing_summary",
    "print_preprocessing_stats",
    # Failures
    "FailureTracker",
]
