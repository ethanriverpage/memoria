#!/usr/bin/env python3
"""Phase timing utilities for coarse-grained performance measurement.

Provides a simple context manager for logging phase execution times,
useful for identifying performance bottlenecks across processing stages.
"""

import logging
import time
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)


@contextmanager
def phase_timer(phase_name: str, log_level: int = logging.INFO):
    """Context manager for logging phase execution time.

    Usage:
        with phase_timer("Processing files"):
            process_all_files()
        # Logs: "Starting Processing files..."
        # Logs: "Completed Processing files in 12.34s"

    Args:
        phase_name: Human-readable name for the phase being timed.
        log_level: Logging level for timing messages. Defaults to INFO.

    Yields:
        None
    """
    start = time.perf_counter()
    logger.log(log_level, "Starting %s...", phase_name)
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        logger.log(log_level, "Completed %s in %.2fs", phase_name, elapsed)


def format_duration(seconds: float) -> str:
    """Format duration in human-readable format.

    Args:
        seconds: Duration in seconds.

    Returns:
        Formatted string like "1h 23m 45s" or "12.34s" for short durations.
    """
    if seconds < 60:
        return f"{seconds:.2f}s"

    minutes, secs = divmod(int(seconds), 60)
    hours, mins = divmod(minutes, 60)

    if hours > 0:
        return f"{hours}h {mins}m {secs}s"
    else:
        return f"{mins}m {secs}s"


class PhaseTimer:
    """Class-based timer for more complex timing scenarios.

    Supports nested phases and accumulating timing data.

    Usage:
        timer = PhaseTimer("Main process")
        timer.start()
        # ... do work ...
        timer.stop()
        print(timer.elapsed_formatted)
    """

    def __init__(self, name: str, log_level: int = logging.INFO):
        """Initialize a phase timer.

        Args:
            name: Name of the phase being timed.
            log_level: Logging level for timing messages.
        """
        self.name = name
        self.log_level = log_level
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None

    def start(self, log: bool = True) -> "PhaseTimer":
        """Start the timer.

        Args:
            log: Whether to log the start message.

        Returns:
            Self for method chaining.
        """
        self._start_time = time.perf_counter()
        if log:
            logger.log(self.log_level, "Starting %s...", self.name)
        return self

    def stop(self, log: bool = True) -> float:
        """Stop the timer and return elapsed time.

        Args:
            log: Whether to log the completion message.

        Returns:
            Elapsed time in seconds.
        """
        self._end_time = time.perf_counter()
        elapsed = self.elapsed
        if log:
            logger.log(
                self.log_level,
                "Completed %s in %s",
                self.name,
                format_duration(elapsed),
            )
        return elapsed

    @property
    def elapsed(self) -> float:
        """Get elapsed time in seconds."""
        if self._start_time is None:
            return 0.0
        end = self._end_time if self._end_time is not None else time.perf_counter()
        return end - self._start_time

    @property
    def elapsed_formatted(self) -> str:
        """Get elapsed time as formatted string."""
        return format_duration(self.elapsed)

    def __enter__(self) -> "PhaseTimer":
        """Enter context manager."""
        return self.start()

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager."""
        self.stop()

