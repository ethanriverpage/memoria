#!/usr/bin/env python3
"""
ExifTool Process Runner Module

This module handles the subprocess execution layer for exiftool operations.
It provides a unified interface for running exiftool with either argfile-based
or direct command-line argument approaches.

This separation prepares for future `-stay_open` mode implementation where
a persistent exiftool process can be reused across multiple operations.
"""

import logging
import os
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExifToolResult:
    """Result of an exiftool subprocess execution."""

    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        """Whether the command completed successfully (exit code 0)."""
        return self.returncode == 0


class ExifToolRunner:
    """
    Runner for exiftool subprocess operations.

    Provides methods for running exiftool with different argument passing
    strategies (argfile vs direct) and handles common patterns like
    stdin suppression and output capture.

    This class is designed to be extended for `-stay_open` mode support,
    where a persistent exiftool process can be maintained for batched
    operations with significantly reduced startup overhead.
    """

    def __init__(self, exiftool_path: str = "exiftool"):
        """
        Initialize the runner.

        Args:
            exiftool_path: Path to exiftool executable. Defaults to "exiftool"
                (found via PATH).
        """
        self.exiftool_path = exiftool_path

    def run_with_argfile(
        self,
        argfile_path: str,
        timeout: Optional[int] = None,
    ) -> ExifToolResult:
        """
        Run exiftool using an argfile for arguments.

        Use this for operations with many files or complex per-file arguments.
        The argfile should use `-execute` separators between file operations.

        Args:
            argfile_path: Path to the argfile containing exiftool arguments.
            timeout: Optional timeout in seconds.

        Returns:
            ExifToolResult with returncode, stdout, and stderr.
        """
        cmd = [self.exiftool_path, "-@", argfile_path]
        return self._run_command(cmd, timeout=timeout)

    def run_direct(
        self,
        args: List[str],
        timeout: Optional[int] = None,
    ) -> ExifToolResult:
        """
        Run exiftool with arguments passed directly on command line.

        Use this for simpler operations with few files where argfile
        overhead isn't worthwhile. Be mindful of ARG_MAX limits.

        Args:
            args: List of arguments to pass to exiftool.
            timeout: Optional timeout in seconds.

        Returns:
            ExifToolResult with returncode, stdout, and stderr.
        """
        cmd = [self.exiftool_path] + args
        return self._run_command(cmd, timeout=timeout)

    def _run_command(
        self,
        cmd: List[str],
        timeout: Optional[int] = None,
    ) -> ExifToolResult:
        """
        Execute an exiftool command and capture output.

        Args:
            cmd: Complete command list including exiftool path.
            timeout: Optional timeout in seconds.

        Returns:
            ExifToolResult with captured output.

        Raises:
            subprocess.TimeoutExpired: If timeout is exceeded.
            FileNotFoundError: If exiftool is not found.
        """
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
        )
        return ExifToolResult(
            returncode=result.returncode,
            stdout=result.stdout or "",
            stderr=result.stderr or "",
        )


# Default runner instance for module-level functions
_default_runner = ExifToolRunner()


def run_exiftool_with_argfile(
    argfile_path: str,
    timeout: Optional[int] = None,
) -> ExifToolResult:
    """
    Run exiftool using an argfile for arguments.

    Module-level convenience function using the default runner.

    Args:
        argfile_path: Path to the argfile containing exiftool arguments.
        timeout: Optional timeout in seconds.

    Returns:
        ExifToolResult with returncode, stdout, and stderr.
    """
    return _default_runner.run_with_argfile(argfile_path, timeout=timeout)


def run_exiftool_direct(
    args: List[str],
    timeout: Optional[int] = None,
) -> ExifToolResult:
    """
    Run exiftool with arguments passed directly on command line.

    Module-level convenience function using the default runner.

    Args:
        args: List of arguments to pass to exiftool.
        timeout: Optional timeout in seconds.

    Returns:
        ExifToolResult with returncode, stdout, and stderr.
    """
    return _default_runner.run_direct(args, timeout=timeout)


@contextmanager
def exiftool_argfile(prefix: str = "exiftool") -> Iterator[str]:
    """
    Context manager for creating and cleaning up exiftool argfiles.

    Creates a temporary argfile with a unique name incorporating the
    process ID. The file is automatically deleted when the context exits.

    Args:
        prefix: Prefix for the temporary file name.

    Yields:
        Path to the created argfile.

    Example:
        with exiftool_argfile() as argfile_path:
            with open(argfile_path, 'w') as f:
                f.write("-DateTimeOriginal=2024:01:15 12:00:00\\n")
                f.write("/path/to/file.jpg\\n")
            result = run_exiftool_with_argfile(argfile_path)
    """
    argfile_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            prefix=f"{prefix}_{os.getpid()}_",
            suffix=".args",
            delete=False,
        ) as argfile:
            argfile_path = argfile.name
        yield argfile_path
    finally:
        if argfile_path is not None:
            try:
                os.unlink(argfile_path)
            except OSError:
                pass
