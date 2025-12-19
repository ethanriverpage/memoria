"""
Output utilities for console, logging, and progress display.

This package provides:
- Shared Rich console instance
- Centralized logging configuration
- Progress bar utilities with phase prefixes
"""

from common.output.console import console, section_header, phase_header
from common.output.logging import setup_logging, get_logger
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
    # Logging
    "setup_logging",
    "get_logger",
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

