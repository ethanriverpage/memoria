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
    >>> from common.output.logging import setup_logging, get_logger
    >>> setup_logging(verbose=True, log_file="processing.log")
    >>> logger = get_logger(__name__)
    >>> logger.info("Processing started")
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from rich.logging import RichHandler

from common.output.console import console

# =============================================================================
# Format Constants - Single source of truth for log formats
# =============================================================================

LOG_FORMAT_DETAILED = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
"""Detailed format including timestamp and module name, used for verbose/file logging."""

LOG_FORMAT_SIMPLE = "%(levelname)s: %(message)s"
"""Simple format for non-verbose console output."""

LOG_DATE_FORMAT = "%H:%M:%S"
"""Time-only format for log timestamps (no date)."""

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
# Custom Handlers
# =============================================================================


class StreamingFileHandler(logging.FileHandler):
    """FileHandler that flushes after every log entry for real-time streaming.

    By default, Python's FileHandler uses buffered I/O, meaning log entries
    may not appear in the file until the buffer fills or the handler is closed.
    This handler flushes after each emit, ensuring logs are written immediately.
    """

    def emit(self, record: logging.LogRecord) -> None:
        """Emit a record and immediately flush to disk."""
        super().emit(record)
        self.flush()


# =============================================================================
# Main Logging Setup Functions
# =============================================================================


def setup_logging(
    verbosity: int = 0,
    log_file: Optional[str] = None,
    verbose: bool = False,
    file_log_level: Optional[int] = None,
) -> logging.Logger:
    """Configure logging for media processing with tiered verbosity.

    Sets up console logging with appropriate level and optional file logging.

    Verbosity levels:
    - 0 (default): Console shows only ERROR messages (clean output with progress bars)
    - 1 (-v): Console shows INFO level (phase summaries, per-export totals)
    - 2+ (-vv): Console shows DEBUG level (file-level operations, matching details)

    Third-party libraries are always suppressed to WARNING level.

    Args:
        verbosity: Verbosity level (0=errors only, 1=info, 2+=debug)
        log_file: Optional path to log file for persistent logging
        verbose: Deprecated. If True, treated as verbosity=1 for backwards compatibility.
        file_log_level: Optional logging level for file output (default: DEBUG).
                       Use logging.INFO for smaller log files that capture only
                       phase transitions and summaries, not file-level operations.

    Returns:
        Configured root logger

    Example:
        >>> logger = setup_logging(verbosity=1, log_file="processing.log")
        >>> logger.info("Processing started")
        >>> # For smaller logs, use INFO level for file:
        >>> logger = setup_logging(verbosity=1, log_file="processing.log",
        ...                        file_log_level=logging.INFO)
    """
    # Backwards compatibility: treat verbose=True as verbosity=1
    if verbose and verbosity == 0:
        verbosity = 1

    # Configure root logger to capture everything
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Clear any existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # Determine console log level based on verbosity
    if verbosity >= 2:
        console_level = logging.DEBUG
    elif verbosity == 1:
        console_level = logging.INFO
    else:
        console_level = logging.ERROR

    # Create Rich console handler for colorized output
    console_handler = RichHandler(
        console=console,
        show_time=verbosity >= 1,
        show_path=False,  # Reduced noise: don't show file:line in console
        rich_tracebacks=True,
        markup=True,
    )
    console_handler.setLevel(console_level)
    root_logger.addHandler(console_handler)

    # Add file handler if log_file specified
    if log_file:
        file_handler = StreamingFileHandler(log_file, mode="a", encoding="utf-8")
        # Default to DEBUG, but allow INFO for smaller logs
        file_handler.setLevel(file_log_level if file_log_level else logging.DEBUG)
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
    file_handler = StreamingFileHandler(log_file, mode="a", encoding="utf-8")
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

    IMPORTANT: Worker processes should NOT log to console to avoid
    interleaving with the main process's Rich progress displays.
    All worker output goes to the log file only.

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

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if log_filename else logging.INFO)

    # Clear any existing handlers (important for subprocess isolation)
    root_logger.handlers.clear()

    # Add a NullHandler to prevent "No handler found" warnings
    # This ensures logging calls don't fall through to lastResort handler
    root_logger.addHandler(logging.NullHandler())

    if log_filename:
        # Create formatter using shared constants
        formatter = logging.Formatter(LOG_FORMAT_DETAILED, datefmt=LOG_DATE_FORMAT)

        # Add file handler - all output goes to file only
        file_handler = StreamingFileHandler(log_filename)
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


# =============================================================================
# Standardized Logging Helpers
# =============================================================================

# Standard separator width for phase headers
SEPARATOR_WIDTH = 50


class ProcessorLogger:
    """Provides standardized logging methods for processors and preprocessors.

    This class wraps a standard logger and provides consistent formatting for
    common operations across all processors. Use this to ensure uniform log
    output regardless of which processor is running.

    All methods use lazy % formatting for optimal performance (the message
    string is only formatted if the log level is enabled).

    Log Level Guidelines:
        DEBUG   - File-level operations, worker counts, internal state
        INFO    - Phase transitions, summary counts, high-level progress
        WARNING - Recoverable issues, skipped files, missing optional data
        ERROR   - Failures that stop processing or require user attention

    Example:
        >>> from common.output.logging import ProcessorLogger
        >>> log = ProcessorLogger(__name__)
        >>> log.processor_start("Google Photos")
        >>> log.loading_metadata("/path/to/metadata.json")
        >>> log.found_files(150, "media")
        >>> log.processing_start("/output/photos")
    """

    def __init__(self, name: str):
        """Initialize with a logger name.

        Args:
            name: Logger name, typically __name__ of the calling module
        """
        self._logger = logging.getLogger(name)

    @property
    def logger(self) -> logging.Logger:
        """Access the underlying logger for custom logging needs."""
        return self._logger

    # -------------------------------------------------------------------------
    # Header / Phase Markers
    # -------------------------------------------------------------------------

    def processor_start(self, processor_name: str) -> None:
        """Log processor start header.

        Args:
            processor_name: Human-readable name of the processor
        """
        self._logger.info("%s", processor_name)
        self._logger.info("=" * SEPARATOR_WIDTH)

    def preprocessor_start(
        self, preprocessor_name: str, export_path: str, output_path: str
    ) -> None:
        """Log preprocessor start with paths.

        Args:
            preprocessor_name: Human-readable name of the preprocessor
            export_path: Path to source export directory
            output_path: Path to output directory
        """
        self._logger.info("Starting %s Preprocessing", preprocessor_name)
        self._logger.info("Export path: %s", export_path)
        self._logger.info("Output path: %s", output_path)

    def phase_separator(self) -> None:
        """Log a phase separator line."""
        self._logger.info("=" * SEPARATOR_WIDTH)

    # -------------------------------------------------------------------------
    # Metadata and Scanning Operations
    # -------------------------------------------------------------------------

    def loading_metadata(self, path: str) -> None:
        """Log metadata loading.

        Args:
            path: Path to metadata file
        """
        self._logger.info("Loading metadata from %s...", path)

    def export_username(self, username: str) -> None:
        """Log detected export username/identifier.

        Args:
            username: Detected username or identifier
        """
        self._logger.info("Export username: %s", username)

    def scanning_directory(self, path: str) -> None:
        """Log directory scan start.

        Args:
            path: Path being scanned
        """
        self._logger.info("Scanning directory: %s", path)

    def found_files(self, count: int, file_type: str = "media") -> None:
        """Log number of files found.

        Args:
            count: Number of files found
            file_type: Type of files (e.g., "media", "conversation", "channel")
        """
        self._logger.info("Found %d %s files", count, file_type)

    def found_items(self, count: int, item_type: str) -> None:
        """Log number of items found (generic).

        Args:
            count: Number of items found
            item_type: Type of items (e.g., "conversations", "channels", "posts")
        """
        self._logger.info("Found %d %s", count, item_type)

    # -------------------------------------------------------------------------
    # Matching and Processing Operations
    # -------------------------------------------------------------------------

    def building_index(self, index_type: str = "filename") -> None:
        """Log index building start.

        Args:
            index_type: Type of index being built
        """
        self._logger.info("Building %s index...", index_type)

    def matched_files(self, matched: int, unmatched: int = 0) -> None:
        """Log file matching results.

        Args:
            matched: Number of successfully matched files
            unmatched: Number of unmatched files
        """
        self._logger.info("Matched %d files", matched)
        if unmatched > 0:
            self._logger.info("Unmatched %d files", unmatched)

    def skipped_secondary(self, count: int) -> None:
        """Log skipped secondary/duplicate files from dedup.

        Args:
            count: Number of skipped files
        """
        if count > 0:
            self._logger.info("Skipped %d secondary files (dedup)", count)

    def processing_start(self, output_dir: str) -> None:
        """Log processing phase start.

        Args:
            output_dir: Output directory path
        """
        self._logger.info("Processing files to %s/", output_dir)
        self._logger.info("=" * SEPARATOR_WIDTH)

    def using_workers(self, count: int) -> None:
        """Log worker count for parallel processing.

        Args:
            count: Number of parallel workers
        """
        self._logger.debug("Using %d parallel workers", count)

    def copied_files(self, success: int, failed: int = 0) -> None:
        """Log file copy results.

        Args:
            success: Number of successfully copied files
            failed: Number of failed copies
        """
        if failed > 0:
            self._logger.info("Copied %d files (%d failed)", success, failed)
        else:
            self._logger.info("Copied %d files", success)

    def created_files(self, success: int, needs_exif: int = 0) -> None:
        """Log file creation results (for processors that create new files).

        Args:
            success: Number of successfully created files
            needs_exif: Number of files needing EXIF processing
        """
        if needs_exif > 0:
            self._logger.info(
                "Created %d files (%d need EXIF processing)", success, needs_exif
            )
        else:
            self._logger.info("Created %d files", success)

    # -------------------------------------------------------------------------
    # EXIF Operations
    # -------------------------------------------------------------------------

    def exif_batch_start(self) -> None:
        """Log EXIF batch processing start."""
        self._logger.info("Batch processing EXIF metadata...")

    def exif_rebuilding(self, count: int) -> None:
        """Log EXIF structure rebuilding.

        Args:
            count: Number of files being rebuilt
        """
        if count > 0:
            self._logger.info("Rebuilding %d corrupted EXIF structures...", count)

    def exif_written(self, count: int) -> None:
        """Log EXIF metadata write completion.

        Args:
            count: Number of files with written metadata
        """
        self._logger.info("EXIF metadata written for %d files", count)

    # -------------------------------------------------------------------------
    # Preprocessor-specific Operations
    # -------------------------------------------------------------------------

    def downloading(self, count: int, item_type: str = "attachments") -> None:
        """Log download start.

        Args:
            count: Number of items to download
            item_type: Type of items being downloaded
        """
        self._logger.info("Downloading %d %s...", count, item_type)

    def parsing(self, item_type: str = "messages") -> None:
        """Log parsing phase start.

        Args:
            item_type: Type of items being parsed
        """
        self._logger.info("Parsing %s...", item_type)

    def loaded_mappings(self, count: int, mapping_type: str) -> None:
        """Log loaded mappings/index.

        Args:
            count: Number of mappings loaded
            mapping_type: Type of mapping (e.g., "channel", "server", "contact")
        """
        self._logger.info("Loaded %d %s mappings", count, mapping_type)

    def saved_metadata(self, path: str) -> None:
        """Log metadata save completion.

        Args:
            path: Path where metadata was saved
        """
        self._logger.info("Saved metadata to %s", path)

    def saved_log(self, path: str) -> None:
        """Log preprocessing log save.

        Args:
            path: Path where log was saved
        """
        self._logger.info("Saved preprocessing log to %s", path)

    def preprocessing_complete(self) -> None:
        """Log preprocessing completion."""
        self._logger.info("Preprocessing complete!")

    # -------------------------------------------------------------------------
    # Input/Output Directory Detection
    # -------------------------------------------------------------------------

    def using_preprocessed(self, path: str) -> None:
        """Log using already preprocessed directory.

        Args:
            path: Path to preprocessed directory
        """
        self._logger.info("Using preprocessed export: %s", path)

    def using_cache(self, path: str) -> None:
        """Log using preprocessing cache.

        Args:
            path: Path to cache directory
        """
        self._logger.info("Using preprocessing cache: %s", path)

    def running_preprocessing(self) -> None:
        """Log preprocessing start."""
        self._logger.info("Running preprocessing...")

    def preprocessing_to(self, path: str) -> None:
        """Log preprocessing output path.

        Args:
            path: Path to preprocessing output
        """
        self._logger.info("Preprocessing to: %s", path)

    def preprocessing_done(self, path: str) -> None:
        """Log preprocessing completion with result path.

        Args:
            path: Path to preprocessed output
        """
        self._logger.info("Preprocessing complete. Using: %s", path)

    def raw_export_detected(self, path: str) -> None:
        """Log raw export detection.

        Args:
            path: Path to raw export
        """
        self._logger.info("Input directory is raw export: %s", path)

    def already_preprocessed(self, path: str) -> None:
        """Log already preprocessed detection.

        Args:
            path: Path to preprocessed directory
        """
        self._logger.info("Input directory is already preprocessed: %s", path)

    # -------------------------------------------------------------------------
    # Errors and Warnings
    # -------------------------------------------------------------------------

    def file_not_found(self, path: str, file_type: str = "File") -> None:
        """Log file not found error.

        Args:
            path: Path to missing file
            file_type: Type of file (e.g., "Metadata file", "Media directory")
        """
        self._logger.error("%s not found: %s", file_type, path)

    def processing_error(self, filename: str, error: Exception) -> None:
        """Log processing error for a specific file.

        Args:
            filename: Name of file that failed
            error: Exception that occurred
        """
        self._logger.error("Error processing %s: %s", filename, error)

    def copy_error(self, filename: str, error: Exception) -> None:
        """Log file copy error.

        Args:
            filename: Name of file that failed to copy
            error: Exception that occurred
        """
        self._logger.error("Failed to copy %s: %s", filename, error)

    def parse_error(self, filename: str, error: Exception) -> None:
        """Log parse error.

        Args:
            filename: Name of file that failed to parse
            error: Exception that occurred
        """
        self._logger.warning("Failed to parse %s: %s", filename, error)

    def missing_file(self, filename: str, context: str = "") -> None:
        """Log missing file warning.

        Args:
            filename: Name of missing file
            context: Optional context about where file was expected
        """
        if context:
            self._logger.warning("Media file not found: %s (%s)", filename, context)
        else:
            self._logger.warning("Media file not found: %s", filename)

    def no_metadata_for_file(self, filename: str) -> None:
        """Log file with no matching metadata.

        Args:
            filename: Name of file without metadata
        """
        self._logger.warning("No metadata for file: %s", filename)

    # -------------------------------------------------------------------------
    # Debug Level Operations
    # -------------------------------------------------------------------------

    def detection_failed(self, processor_name: str, error: Exception) -> None:
        """Log detection failure at debug level.

        Args:
            processor_name: Name of processor that failed detection
            error: Exception that occurred
        """
        self._logger.debug("Detection failed for %s: %s", processor_name, error)

    def generating_filenames(self) -> None:
        """Log filename pre-generation at debug level."""
        self._logger.debug("Pre-generating filenames...")

    def file_operation(self, operation: str, source: str, dest: str = "") -> None:
        """Log file-level operation at debug level.

        Args:
            operation: Operation name (e.g., "Copying", "Moving")
            source: Source file path
            dest: Destination path (optional)
        """
        if dest:
            self._logger.debug("%s %s -> %s", operation, source, dest)
        else:
            self._logger.debug("%s %s", operation, source)


def get_processor_logger(name: str) -> ProcessorLogger:
    """Get a ProcessorLogger instance for standardized processor logging.

    This is the recommended way to get a logger for processors and preprocessors.
    It provides consistent formatting for common operations.

    Args:
        name: Logger name, typically __name__ of the calling module

    Returns:
        ProcessorLogger instance with standardized logging methods

    Example:
        >>> from common.output.logging import get_processor_logger
        >>> log = get_processor_logger(__name__)
        >>> log.processor_start("Google Photos")
        >>> log.loading_metadata("/path/to/metadata.json")
    """
    return ProcessorLogger(name)
