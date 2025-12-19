"""
Output utilities for console, logging, progress display, and output paths.

This package provides:
- Shared Rich console instance
- Centralized logging configuration
- Progress bar utilities with phase prefixes
- Output path configuration for Platform/User/Service structure
"""

from common.output.console import console, section_header, phase_header, is_subprocess
from common.output.logging import setup_logging, get_logger
from common.output.paths import OutputInfo, build_processor_output_path
from common.output.progress import (
    progress_bar,
    futures_progress,
    chunked_progress,
    PHASE_PREPROCESS,
    PHASE_PROCESS,
    PHASE_EXIF,
    PHASE_CATALOG,
    PHASE_DEDUP,
)

__all__ = [
    # Console
    "console",
    "section_header",
    "phase_header",
    "is_subprocess",
    # Logging
    "setup_logging",
    "get_logger",
    # Paths
    "OutputInfo",
    "build_processor_output_path",
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
