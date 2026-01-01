#!/usr/bin/env python3
"""Tests for common/media/exiftool_runner.py subprocess execution layer.

Focuses on:
- ExifToolResult dataclass behavior
- ExifToolRunner class command construction
- Module-level convenience functions
- exiftool_argfile context manager lifecycle
"""

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from common.media.exiftool_runner import (
    ExifToolResult,
    ExifToolRunner,
    exiftool_argfile,
    run_exiftool_direct,
    run_exiftool_with_argfile,
)


class TestExifToolResult:
    """Tests for ExifToolResult dataclass."""

    def test_success_true_when_returncode_zero(self) -> None:
        """success property should return True when returncode is 0."""
        result = ExifToolResult(returncode=0, stdout="output", stderr="")
        assert result.success is True

    def test_success_false_when_returncode_nonzero(self) -> None:
        """success property should return False when returncode is non-zero."""
        result = ExifToolResult(returncode=1, stdout="", stderr="error")
        assert result.success is False

    def test_success_false_when_returncode_negative(self) -> None:
        """success property should return False for negative return codes."""
        result = ExifToolResult(returncode=-1, stdout="", stderr="signal")
        assert result.success is False

    def test_stores_stdout_stderr(self) -> None:
        """Result should store stdout and stderr values."""
        result = ExifToolResult(returncode=0, stdout="out", stderr="err")
        assert result.stdout == "out"
        assert result.stderr == "err"


class TestExifToolRunner:
    """Tests for ExifToolRunner class."""

    def test_default_exiftool_path(self) -> None:
        """Default exiftool_path should be 'exiftool'."""
        runner = ExifToolRunner()
        assert runner.exiftool_path == "exiftool"

    def test_custom_exiftool_path(self) -> None:
        """Custom exiftool_path should be stored."""
        runner = ExifToolRunner(exiftool_path="/usr/local/bin/exiftool")
        assert runner.exiftool_path == "/usr/local/bin/exiftool"

    def test_run_with_argfile_constructs_correct_command(self) -> None:
        """run_with_argfile should construct ['exiftool', '-@', argfile_path]."""
        runner = ExifToolRunner()
        captured_cmd = []

        def capture_cmd(*args, **kwargs):
            captured_cmd.append(args[0])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_cmd
        ):
            runner.run_with_argfile("/tmp/test.args")

        assert captured_cmd == [["exiftool", "-@", "/tmp/test.args"]]

    def test_run_with_argfile_uses_custom_path(self) -> None:
        """run_with_argfile should use custom exiftool_path."""
        runner = ExifToolRunner(exiftool_path="/custom/exiftool")
        captured_cmd = []

        def capture_cmd(*args, **kwargs):
            captured_cmd.append(args[0])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_cmd
        ):
            runner.run_with_argfile("/tmp/test.args")

        assert captured_cmd[0][0] == "/custom/exiftool"

    def test_run_direct_constructs_correct_command(self) -> None:
        """run_direct should construct ['exiftool'] + args."""
        runner = ExifToolRunner()
        captured_cmd = []

        def capture_cmd(*args, **kwargs):
            captured_cmd.append(args[0])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_cmd
        ):
            runner.run_direct(["-json", "-DateTimeOriginal", "/path/to/file.jpg"])

        assert captured_cmd == [
            ["exiftool", "-json", "-DateTimeOriginal", "/path/to/file.jpg"]
        ]

    def test_run_direct_uses_custom_path(self) -> None:
        """run_direct should use custom exiftool_path."""
        runner = ExifToolRunner(exiftool_path="/opt/exiftool")
        captured_cmd = []

        def capture_cmd(*args, **kwargs):
            captured_cmd.append(args[0])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_cmd
        ):
            runner.run_direct(["-ver"])

        assert captured_cmd[0] == ["/opt/exiftool", "-ver"]

    def test_run_command_captures_output(self) -> None:
        """_run_command should capture stdout, stderr, returncode."""
        runner = ExifToolRunner()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "exiftool output"
        mock_result.stderr = "warning message"

        with patch(
            "common.media.exiftool_runner.subprocess.run", return_value=mock_result
        ):
            result = runner.run_direct(["-ver"])

        assert result.returncode == 0
        assert result.stdout == "exiftool output"
        assert result.stderr == "warning message"

    def test_run_command_handles_none_output(self) -> None:
        """_run_command should handle None stdout/stderr gracefully."""
        runner = ExifToolRunner()

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = None
        mock_result.stderr = None

        with patch(
            "common.media.exiftool_runner.subprocess.run", return_value=mock_result
        ):
            result = runner.run_direct(["-ver"])

        assert result.stdout == ""
        assert result.stderr == ""

    def test_run_command_passes_timeout(self) -> None:
        """_run_command should pass timeout to subprocess."""
        runner = ExifToolRunner()
        captured_kwargs = {}

        def capture_kwargs(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_kwargs
        ):
            runner.run_direct(["-ver"], timeout=30)

        assert captured_kwargs["timeout"] == 30

    def test_run_command_suppresses_stdin(self) -> None:
        """_run_command should pass stdin=subprocess.DEVNULL."""
        runner = ExifToolRunner()
        captured_kwargs = {}

        def capture_kwargs(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_kwargs
        ):
            runner.run_direct(["-ver"])

        assert captured_kwargs["stdin"] == subprocess.DEVNULL

    def test_run_command_captures_output_flag(self) -> None:
        """_run_command should set capture_output=True."""
        runner = ExifToolRunner()
        captured_kwargs = {}

        def capture_kwargs(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_kwargs
        ):
            runner.run_direct(["-ver"])

        assert captured_kwargs["capture_output"] is True
        assert captured_kwargs["text"] is True
        assert captured_kwargs["check"] is False


class TestModuleLevelFunctions:
    """Tests for module-level convenience functions."""

    def test_run_exiftool_with_argfile_delegates_to_runner(self) -> None:
        """run_exiftool_with_argfile should use default runner."""
        captured_cmd = []

        def capture_cmd(*args, **kwargs):
            captured_cmd.append(args[0])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_cmd
        ):
            result = run_exiftool_with_argfile("/tmp/args.txt")

        assert captured_cmd == [["exiftool", "-@", "/tmp/args.txt"]]
        assert isinstance(result, ExifToolResult)

    def test_run_exiftool_with_argfile_passes_timeout(self) -> None:
        """run_exiftool_with_argfile should pass timeout parameter."""
        captured_kwargs = {}

        def capture_kwargs(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_kwargs
        ):
            run_exiftool_with_argfile("/tmp/args.txt", timeout=60)

        assert captured_kwargs["timeout"] == 60

    def test_run_exiftool_direct_delegates_to_runner(self) -> None:
        """run_exiftool_direct should use default runner."""
        captured_cmd = []

        def capture_cmd(*args, **kwargs):
            captured_cmd.append(args[0])
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_cmd
        ):
            result = run_exiftool_direct(["-json", "-s", "file.jpg"])

        assert captured_cmd == [["exiftool", "-json", "-s", "file.jpg"]]
        assert isinstance(result, ExifToolResult)

    def test_run_exiftool_direct_passes_timeout(self) -> None:
        """run_exiftool_direct should pass timeout parameter."""
        captured_kwargs = {}

        def capture_kwargs(*args, **kwargs):
            captured_kwargs.update(kwargs)
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool_runner.subprocess.run", side_effect=capture_kwargs
        ):
            run_exiftool_direct(["-ver"], timeout=120)

        assert captured_kwargs["timeout"] == 120


class TestExiftoolArgfile:
    """Tests for exiftool_argfile context manager."""

    def test_creates_temp_file_with_pid_in_name(self) -> None:
        """Argfile should include process ID in filename."""
        with exiftool_argfile() as argfile_path:
            filename = Path(argfile_path).name
            assert str(os.getpid()) in filename

    def test_creates_temp_file_with_args_suffix(self) -> None:
        """Argfile should have .args extension."""
        with exiftool_argfile() as argfile_path:
            assert argfile_path.endswith(".args")

    def test_creates_temp_file_with_custom_prefix(self) -> None:
        """Argfile should use custom prefix."""
        with exiftool_argfile(prefix="myprefix") as argfile_path:
            filename = Path(argfile_path).name
            assert filename.startswith("myprefix_")

    def test_yields_writable_path(self) -> None:
        """Yielded path should be writable."""
        with exiftool_argfile() as argfile_path:
            # Should be able to write to the file
            with open(argfile_path, "w") as f:
                f.write("-DateTimeOriginal=2024:01:15 12:00:00\n")
                f.write("/path/to/file.jpg\n")

            # Verify content was written
            with open(argfile_path, "r") as f:
                content = f.read()
            assert "-DateTimeOriginal=2024:01:15 12:00:00" in content
            assert "/path/to/file.jpg" in content

    def test_cleans_up_on_normal_exit(self) -> None:
        """Argfile should be deleted after context exits."""
        argfile_path = None
        with exiftool_argfile() as path:
            argfile_path = path
            assert Path(argfile_path).exists()

        # File should be deleted after context exits
        assert not Path(argfile_path).exists()

    def test_cleans_up_on_exception(self) -> None:
        """Argfile should be deleted even if exception occurs."""
        argfile_path = None
        with pytest.raises(ValueError):
            with exiftool_argfile() as path:
                argfile_path = path
                assert Path(argfile_path).exists()
                raise ValueError("Test exception")

        # File should still be deleted
        assert not Path(argfile_path).exists()

    def test_file_exists_during_context(self) -> None:
        """File should exist while context is active."""
        with exiftool_argfile() as argfile_path:
            assert Path(argfile_path).exists()
            assert Path(argfile_path).is_file()

