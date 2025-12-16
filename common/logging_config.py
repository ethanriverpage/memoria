"""
Centralized Logging Configuration

This module provides unified logging configuration for all media processors.
It ensures consistent log formats, levels, and behavior across the entire project.

Log Level Conventions:
    DEBUG   - File-by-file operations, internal state, matching details
    INFO    - Phase transitions, counts, high-level progress
    WARNING - Recoverable issues, skipped files, missing optional data
    ERROR   - Failures that stop processing or require user attention

Example:
    >>> from common.logging_config import setup_logging, get_logger
    >>> setup_logging(verbose=True, log_file="processing.log")
    >>> logger = get_logger(__name__)
    >>> logger.info("Processing started")
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# =============================================================================
# Format Constants - Single source of truth for log formats
# =============================================================================

LOG_FORMAT_DETAILED = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
"""Detailed format including timestamp and module name, used for verbose/file logging."""

LOG_FORMAT_SIMPLE = "%(levelname)s: %(message)s"
"""Simple format for non-verbose console output."""

LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
"""Standard date format for all log timestamps."""

# =============================================================================
# Suppressed Loggers - Third-party libraries that are too noisy
# =============================================================================

SUPPRESSED_LOGGERS: List[str] = [
    "PIL",
    "urllib3",
    "urllib3.connectionpool",
    "requests",
]
"""List of third-party logger names to suppress to WARNING level."""


# =============================================================================
# Main Logging Setup Functions
# =============================================================================


def setup_logging(
    verbose: bool = False, log_file: Optional[str] = None
) -> logging.Logger:
    """Configure logging for media processing.

    Sets up console logging with appropriate level and optional file logging.

    In info mode (verbose=False):
    - Console shows only ERROR messages (clean output with progress bars)
    - Third-party libraries are suppressed to WARNING level

    In verbose mode (verbose=True):
    - Console shows INFO level messages
    - File captures all DEBUG logs
    - Third-party libraries still suppressed to WARNING

    Args:
        verbose: If True, enable verbose console output and file logging
        log_file: Optional path to log file for persistent logging

    Returns:
        Configured root logger

    Example:
        >>> logger = setup_logging(verbose=True, log_file="processing.log")
        >>> logger.info("Processing started")
    """
    # Configure root logger to capture everything
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Clear any existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Create console handler
    # In info mode: only show errors (clean console with progress bars)
    # In verbose mode: show info and above
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO if verbose else logging.ERROR)

    # Create formatter based on verbosity
    if verbose:
        formatter = logging.Formatter(LOG_FORMAT_DETAILED, datefmt=LOG_DATE_FORMAT)
    else:
        formatter = logging.Formatter(LOG_FORMAT_SIMPLE)

    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Add file handler if log_file specified
    if log_file:
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # Always log everything to file
        file_formatter = logging.Formatter(LOG_FORMAT_DETAILED, datefmt=LOG_DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for library in SUPPRESSED_LOGGERS:
        logging.getLogger(library).setLevel(logging.WARNING)

    return root_logger


def add_export_log_handler(
    export_name: str, verbose: bool = False
) -> Optional[logging.FileHandler]:
    """Add a per-export log file handler to the root logger.

    Creates a separate log file for a specific export being processed.
    This allows each export to have its own log file while still logging
    to the main log file.

    Args:
        export_name: Name of the export (used in log filename)
        verbose: If True, create the log file. If False, skip per-export logging.

    Returns:
        The created FileHandler, or None if verbose is False

    Example:
        >>> handler = add_export_log_handler("google-user-20250526", verbose=True)
        >>> # Process the export...
        >>> if handler:
        ...     remove_export_log_handler(handler)
    """
    if not verbose:
        return None

    # Create logs directory if it doesn't exist
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    # Create log filename with export name and timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"log-{export_name}-{timestamp}.log"

    # Create file handler for this export
    file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # Log everything to file

    # Use detailed format for file logging
    file_formatter = logging.Formatter(LOG_FORMAT_DETAILED, datefmt=LOG_DATE_FORMAT)
    file_handler.setFormatter(file_formatter)

    # Add handler to root logger
    root_logger = logging.getLogger()
    root_logger.addHandler(file_handler)

    return file_handler


def remove_export_log_handler(handler: logging.FileHandler) -> None:
    """Remove a per-export log file handler from the root logger.

    Should be called after processing an export to clean up the handler
    and close the file.

    Args:
        handler: The FileHandler to remove (returned by add_export_log_handler)

    Example:
        >>> handler = add_export_log_handler("google-user-20250526", verbose=True)
        >>> # Process the export...
        >>> if handler:
        ...     remove_export_log_handler(handler)
    """
    if handler is None:
        return

    root_logger = logging.getLogger()
    root_logger.removeHandler(handler)
    handler.close()


# =============================================================================
# Worker Process Logging
# =============================================================================


def init_worker_logging(
    log_filename: Optional[str] = None,
    app_modules: Optional[List[str]] = None,
) -> None:
    """Initialize logging for worker processes (multiprocessing).

    Worker processes don't inherit the parent's logging configuration,
    so this function sets up logging in each worker. Uses the same
    format constants as the main setup for consistency.

    Args:
        log_filename: Path to the log file for verbose output. If None,
                     only suppresses third-party loggers.
        app_modules: List of application module names to set to DEBUG level.
                    Defaults to common modules if not specified.

    Example:
        >>> # In Pool initializer
        >>> with Pool(
        ...     processes=num_workers,
        ...     initializer=init_worker_logging,
        ...     initargs=(log_filename, ["processors.snapchat_messages"]),
        ... ) as pool:
        ...     results = pool.map(worker_fn, tasks)
    """
    # Default application modules to enable DEBUG for
    if app_modules is None:
        app_modules = [
            "common.overlay",
            "common.video_encoder",
            "common.exiftool_batch",
        ]

    if log_filename:
        # Create formatter using shared constants
        formatter = logging.Formatter(LOG_FORMAT_DETAILED, datefmt=LOG_DATE_FORMAT)

        # Set root logger to INFO (to avoid third-party library spam)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        # Add file handler
        file_handler = logging.FileHandler(log_filename)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # Set application modules to DEBUG level
        for module in app_modules:
            logging.getLogger(module).setLevel(logging.DEBUG)

    # Always suppress verbose third-party library logging
    for library in SUPPRESSED_LOGGERS:
        logging.getLogger(library).setLevel(logging.WARNING)


# =============================================================================
# Logger Factory
# =============================================================================


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the given name.

    This is a simple wrapper around logging.getLogger() that provides
    a consistent interface and can be extended in the future for
    additional context or configuration.

    Args:
        name: Logger name, typically __name__ of the calling module

    Returns:
        Logger instance

    Example:
        >>> logger = get_logger(__name__)
        >>> logger.info("Processing started")
    """
    return logging.getLogger(name)

