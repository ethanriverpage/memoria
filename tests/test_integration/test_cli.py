"""
CLI integration tests for memoria.py.

Tests cover:
- Single export processing
- Output directory structure validation
- Error handling for invalid inputs
"""

import subprocess
import sys


from tests.fixtures.generators import (
    create_google_photos_export,
    create_snapchat_memories_export,
    create_discord_export,
)


class TestCLIBasic:
    """Basic CLI functionality tests."""

    def test_list_processors(self, project_root, tmp_path):
        """Should list all available processors."""
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"
        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                "--list-processors",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "Available processors" in result.stdout
        assert "Google Photos" in result.stdout

    def test_help_output(self, project_root, tmp_path):
        """Should display help text."""
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"
        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                "--help",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0
        assert "usage:" in result.stdout.lower() or "Usage:" in result.stdout
        assert "input_dir" in result.stdout

    def test_no_input_error(self, project_root, tmp_path):
        """Should error when no input provided."""
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"
        result = subprocess.run(
            [sys.executable, str(memoria_script), "--env-file", str(env_file)],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        # Should fail with error
        assert result.returncode != 0

    def test_invalid_input_path(self, project_root, tmp_path):
        """Should error on non-existent input path."""
        fake_path = tmp_path / "non_existent_dir"
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(fake_path),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "does not exist" in result.stdout or "ERROR" in result.stdout


class TestCLIDetection:
    """Tests for processor detection via CLI."""

    def test_detect_google_photos(self, project_root, tmp_path):
        """Should detect Google Photos export."""
        export_dir = tmp_path / "google_export"
        output_dir = tmp_path / "output"
        create_google_photos_export(export_dir)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(export_dir),
                "-o",
                str(output_dir),
                "--skip-upload",
                "--verbose",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should detect the processor (may fail processing due to missing exiftool)
        assert "Google Photos" in result.stdout or "Detected" in result.stdout

    def test_detect_snapchat_memories(self, project_root, tmp_path):
        """Should detect Snapchat Memories export."""
        import os

        export_dir = tmp_path / "snapchat_export"
        output_dir = tmp_path / "output"
        create_snapchat_memories_export(export_dir)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        # Disable cross-processor dedup for faster detection-only test
        env = os.environ.copy()
        env["CROSS_PROCESSOR_DEDUP"] = "false"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(export_dir),
                "-o",
                str(output_dir),
                "--skip-upload",
                "--verbose",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )

        assert "Snapchat Memories" in result.stdout or "Detected" in result.stdout

    def test_detect_discord(self, project_root, tmp_path):
        """Should detect Discord export."""
        export_dir = tmp_path / "discord_export"
        output_dir = tmp_path / "output"
        create_discord_export(export_dir)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(export_dir),
                "-o",
                str(output_dir),
                "--skip-upload",
                "--verbose",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert "Discord" in result.stdout or "Detected" in result.stdout

    def test_no_processor_match(self, project_root, tmp_path):
        """Should report when no processor matches."""
        # Create empty directory
        empty_dir = tmp_path / "empty_export"
        empty_dir.mkdir()
        output_dir = tmp_path / "output"
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(empty_dir),
                "-o",
                str(output_dir),
                "--skip-upload",
                "--verbose",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # In dedup mode, empty directories are handled gracefully with "No files to process"
        # Otherwise expect "No processors matched" or non-zero return
        assert (
            "No processors matched" in result.stdout
            or "No files to process" in result.stdout
            or result.returncode != 0
        )


class TestCLIOutputDirectory:
    """Tests for output directory handling."""

    def test_custom_output_directory(self, project_root, tmp_path):
        """Should use custom output directory when specified."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        create_google_photos_export(export_dir)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(export_dir),
                "-o",
                str(output_dir),
                "--skip-upload",
                "--verbose",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Output directory should be mentioned
        assert str(output_dir) in result.stdout or "output" in result.stdout.lower()


class TestCLIFlags:
    """Tests for CLI flag handling."""

    def test_verbose_flag(self, project_root, tmp_path):
        """Should accept verbose flag."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        create_google_photos_export(export_dir)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(export_dir),
                "-o",
                str(output_dir),
                "--verbose",
                "--skip-upload",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should not error on verbose flag
        # May still fail for other reasons, but flag should be accepted
        assert (
            "--verbose" not in result.stderr
            or "unrecognized" not in result.stderr.lower()
        )

    def test_workers_flag(self, project_root, tmp_path):
        """Should accept workers flag."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        create_google_photos_export(export_dir)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(export_dir),
                "-o",
                str(output_dir),
                "--workers",
                "2",
                "--skip-upload",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should not error on workers flag
        assert (
            "--workers" not in result.stderr
            or "unrecognized" not in result.stderr.lower()
        )

    def test_skip_upload_flag(self, project_root, tmp_path):
        """Should accept skip-upload flag."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        create_google_photos_export(export_dir)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(export_dir),
                "-o",
                str(output_dir),
                "--skip-upload",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should not error on skip-upload flag
        assert (
            "--skip-upload" not in result.stderr
            or "unrecognized" not in result.stderr.lower()
        )


class TestCLIProcessorFilter:
    """Tests for --processor filter flag."""

    def test_processor_filter(self, project_root, tmp_path):
        """Should accept processor filter flag."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        create_google_photos_export(export_dir)
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(export_dir),
                "-o",
                str(output_dir),
                "--processor",
                "Google Photos",
                "--skip-upload",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should accept the flag (may still fail processing)
        assert "unrecognized" not in result.stderr.lower()

    def test_invalid_processor_filter(self, project_root, tmp_path):
        """Should error on invalid processor name."""
        export_dir = tmp_path / "export"
        export_dir.mkdir()
        output_dir = tmp_path / "output"
        env_file = tmp_path / "empty.env"
        env_file.write_text("", encoding="utf-8")
        memoria_script = project_root / "memoria.py"

        result = subprocess.run(
            [
                sys.executable,
                str(memoria_script),
                "--env-file",
                str(env_file),
                str(export_dir),
                "-o",
                str(output_dir),
                "--processor",
                "NonExistentProcessor",
                "--skip-upload",
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode != 0 or "Unknown processor" in result.stdout
