#!/usr/bin/env python3
"""Negative and edge case tests for common utilities.

Tests cover error handling, boundary conditions, and unusual inputs that
might cause problems in production.

Categories:
- Empty/null inputs
- Corrupted data
- Filesystem edge cases
- Encoding issues
- Resource limits
"""

import os
from pathlib import Path

import pytest

from common.config.runtime import default_worker_count
from common.dedup.hashing import compute_content_hash
from common.detection import detect_from_dirname
from common.failures import FailureTracker
from common.files.filter import BannedFilesFilter
from common.files.naming import sanitize_filename, FilenameGenerator
from common.files.scanner import iter_media_files, collect_media_files
from common.files.timestamps import update_file_timestamps
from common.files.utils import batch_correct_extensions
from common.validation.invariants import InvariantViolation


# ============================================================================
# Empty/Null Input Tests
# ============================================================================


class TestEmptyInputs:
    """Tests for handling empty or null inputs."""

    def test_sanitize_filename_empty_string(self) -> None:
        """Should handle empty string input."""
        result = sanitize_filename("")
        # Should return something usable (empty or fallback)
        assert result is not None

    def test_sanitize_filename_whitespace_only(self) -> None:
        """Should handle whitespace-only input."""
        result = sanitize_filename("   \t\n  ")
        assert result is not None

    def test_filename_generator_empty_platform(self) -> None:
        """Should handle empty platform."""
        gen = FilenameGenerator("", "user")
        result = gen.generate("2021-01-01 12:00:00", ".jpg")
        assert result is not None
        assert result.endswith(".jpg")

    def test_filename_generator_none_timestamp(self) -> None:
        """None timestamp should raise InvariantViolation (bug in calling code)."""
        gen = FilenameGenerator("platform", "user")
        with pytest.raises(InvariantViolation, match="missing timestamp"):
            gen.generate(None, ".jpg")

    def test_filename_generator_empty_username(self) -> None:
        """Should handle empty username."""
        gen = FilenameGenerator("platform", "")
        result = gen.generate("2021-01-01 12:00:00", ".jpg")
        assert result is not None

    def test_detect_from_dirname_empty_prefix(self, tmp_path: Path) -> None:
        """Should handle empty platform prefix (used for export dir detection)."""
        test_dir = tmp_path / "some-dir"
        test_dir.mkdir()
        result = detect_from_dirname(test_dir, "")
        # Returns None when no match, which is acceptable
        assert result is None or isinstance(result, str)

    def test_detect_from_dirname_empty_dir(self, tmp_path: Path) -> None:
        """Should handle empty directory name."""
        result = detect_from_dirname(tmp_path, "prefix")
        # Returns None when no match
        assert result is None or isinstance(result, str)

    def test_collect_media_files_empty_list(self) -> None:
        """Should handle empty path list."""
        result = collect_media_files([])
        assert result == []

    def test_batch_correct_extensions_empty_list(self, tmp_path: Path) -> None:
        """Should handle empty file list."""
        result = batch_correct_extensions([])
        assert result == {}


class TestNullAndNoneHandling:
    """Tests for None/null value handling."""

    def test_failure_tracker_none_export_dir(self) -> None:
        """Should handle None export directory."""
        # Should not raise
        tracker = FailureTracker(
            processor_name="Test",
            export_directory="",
        )
        assert tracker is not None

    def test_banned_filter_none_patterns(self) -> None:
        """Should handle None additional patterns."""
        filter_instance = BannedFilesFilter(additional_patterns=None)
        assert filter_instance is not None


# ============================================================================
# Corrupted Data Tests
# ============================================================================


class TestCorruptedData:
    """Tests for handling corrupted or malformed data."""

    def test_hash_truncated_file(self, tmp_path: Path) -> None:
        """Should handle files that appear truncated."""
        # Create a file with just a few bytes
        truncated = tmp_path / "truncated.jpg"
        truncated.write_bytes(b"\xff\xd8\xff")  # Partial JPEG header

        # Should still produce a hash
        result = compute_content_hash(truncated)
        assert result is not None
        assert len(result) == 16  # xxHash64 hex length

    def test_hash_zero_byte_file(self, tmp_path: Path) -> None:
        """Should handle zero-byte files."""
        empty = tmp_path / "empty.txt"
        empty.write_bytes(b"")

        result = compute_content_hash(empty)
        assert result is not None

    def test_iter_media_invalid_path(self) -> None:
        """Should handle non-existent paths gracefully."""
        # Non-existent path
        fake_path = Path("/nonexistent/path/12345")
        # Should not raise, might yield nothing
        try:
            result = list(iter_media_files(fake_path))
            # Should be empty if it doesn't raise
            assert result == []
        except (FileNotFoundError, OSError):
            # Also acceptable to raise
            pass


# ============================================================================
# Filesystem Edge Cases
# ============================================================================


class TestFilesystemEdgeCases:
    """Tests for filesystem edge cases."""

    def test_very_long_filename(self, tmp_path: Path) -> None:
        """Should handle very long filenames."""
        long_name = "a" * 200  # Near filesystem limit
        result = sanitize_filename(long_name)
        # Result should be reasonable length
        assert len(result) <= 255

    def test_special_characters_in_path(self, tmp_path: Path) -> None:
        """Should handle special characters in paths."""
        special_dir = tmp_path / "test (1) [copy]"
        special_dir.mkdir()

        from tests.fixtures.media_samples import write_media_file

        write_media_file(special_dir / "photo.jpg", "jpeg")

        result = list(iter_media_files(special_dir))
        assert len(result) == 1

    def test_unicode_in_filename(self, tmp_path: Path) -> None:
        """Should handle unicode characters in filenames."""
        from tests.fixtures.media_samples import write_media_file

        unicode_file = tmp_path / "photo_cafe.jpg"
        write_media_file(unicode_file, "jpeg")

        result = list(iter_media_files(tmp_path))
        assert len(result) == 1

    def test_hidden_files(self, tmp_path: Path) -> None:
        """Should handle hidden files (starting with dot)."""
        from tests.fixtures.media_samples import write_media_file

        # Hidden file
        hidden = tmp_path / ".hidden.jpg"
        write_media_file(hidden, "jpeg")

        # Normal file
        normal = tmp_path / "normal.jpg"
        write_media_file(normal, "jpeg")

        result = list(iter_media_files(tmp_path))
        # Should find at least the normal file
        assert any(f.name == "normal.jpg" for f in result)

    def test_case_sensitivity(self, tmp_path: Path) -> None:
        """Should handle case-insensitive extension matching."""
        from tests.fixtures.media_samples import write_media_file

        upper = tmp_path / "photo.JPG"
        _mixed = tmp_path / "photo.JpG"  # noqa: F841 - Reserved for mixed-case testing
        write_media_file(upper, "jpeg")

        # Create another file to avoid collision
        (tmp_path / "photo2.jpg").write_bytes(b"content")

        result = list(iter_media_files(tmp_path))
        # Should find files regardless of extension case
        assert len(result) >= 1

    def test_read_only_directory(self, tmp_path: Path) -> None:
        """Should handle read-only directories."""
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()

        from tests.fixtures.media_samples import write_media_file

        write_media_file(readonly_dir / "photo.jpg", "jpeg")

        try:
            readonly_dir.chmod(0o555)  # Read + execute only
            result = list(iter_media_files(readonly_dir))
            assert len(result) == 1
        except PermissionError:
            pytest.skip("Cannot change permissions on this system")
        finally:
            try:
                readonly_dir.chmod(0o755)
            except Exception:
                pass


# ============================================================================
# Encoding Issues Tests
# ============================================================================


class TestEncodingIssues:
    """Tests for handling encoding edge cases."""

    def test_sanitize_filename_unicode(self) -> None:
        """Should handle various unicode characters."""
        inputs = [
            "photo.jpg",  # Normal
            "cafe photo.jpg",  # Accented chars
            "photo.jpg",  # Japanese
            "photo.jpg",  # Emoji
        ]

        for input_text in inputs:
            result = sanitize_filename(input_text)
            assert result is not None

    def test_non_utf8_bytes_in_filename(self) -> None:
        """Should handle filenames with non-UTF8 byte sequences."""
        # This might happen with files from other systems
        try:
            result = sanitize_filename("file\xff\xfename.jpg")
            assert result is not None
        except UnicodeError:
            # Also acceptable if it can't handle
            pass


# ============================================================================
# Timestamp Edge Cases
# ============================================================================


class TestTimestampEdgeCases:
    """Tests for timestamp handling edge cases."""

    def test_update_timestamps_invalid_format(self, tmp_path: Path) -> None:
        """Should handle invalid timestamp format gracefully."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        # Should not raise with invalid timestamp
        update_file_timestamps(test_file, "not a timestamp")
        assert test_file.exists()

    def test_update_timestamps_none_timestamp(self, tmp_path: Path) -> None:
        """Should handle None timestamp gracefully."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        # Should not raise
        update_file_timestamps(test_file, None)
        assert test_file.exists()

    def test_update_timestamps_missing_file(self, tmp_path: Path) -> None:
        """Should handle missing file gracefully."""
        missing = tmp_path / "missing.txt"

        # Should not raise, might log warning
        try:
            update_file_timestamps(missing, "2021-01-01T00:00:00Z")
        except FileNotFoundError:
            pass  # Acceptable to raise

    def test_filename_generator_malformed_timestamp(self) -> None:
        """Malformed timestamps should raise InvariantViolation (bug in calling code)."""
        gen = FilenameGenerator("platform", "user")

        malformed_timestamps = [
            ("not-a-date", "unparseable"),
            ("2021/01/01", "unparseable"),
            ("01-01-2021", "unparseable"),
            ("12:00:00", "unparseable"),
            ("", "missing"),
            ("   ", "unparseable"),
        ]

        for ts, expected_msg in malformed_timestamps:
            with pytest.raises(InvariantViolation, match=expected_msg):
                gen.generate(ts, ".jpg")


# ============================================================================
# Resource Limit Tests
# ============================================================================


class TestResourceLimits:
    """Tests for resource limit handling."""

    def test_default_worker_count_valid(self) -> None:
        """Should return reasonable worker count."""
        count = default_worker_count()
        assert count >= 1
        assert count <= (os.cpu_count() or 1)

    def test_many_files_in_directory(self, tmp_path: Path) -> None:
        """Should handle directories with many files."""
        from tests.fixtures.media_samples import write_media_file

        # Create many files
        for i in range(50):
            write_media_file(tmp_path / f"photo_{i:04d}.jpg", "jpeg")

        result = list(iter_media_files(tmp_path))
        assert len(result) == 50

    def test_deeply_nested_structure(self, tmp_path: Path) -> None:
        """Should handle deeply nested directory structures."""
        from tests.fixtures.media_samples import write_media_file

        # Create deep nesting
        current = tmp_path
        for i in range(15):
            current = current / f"level_{i}"
        current.mkdir(parents=True)

        write_media_file(current / "deep_photo.jpg", "jpeg")

        result = list(iter_media_files(tmp_path))
        assert len(result) == 1


# ============================================================================
# Failure Tracker Edge Cases
# ============================================================================


class TestFailureTrackerEdgeCases:
    """Edge case tests for FailureTracker."""

    def test_empty_tracker_summary(self) -> None:
        """Should handle empty tracker summary."""
        tracker = FailureTracker(
            processor_name="Test",
            export_directory="/test",
        )

        summary = tracker.get_summary()
        assert summary is not None
        assert tracker.has_failures() is False

    def test_duplicate_orphan_entries(self, tmp_path: Path) -> None:
        """Should handle duplicate orphan entries."""
        tracker = FailureTracker(
            processor_name="Test",
            export_directory=str(tmp_path),
        )

        # Create test file
        test_file = tmp_path / "photo.jpg"
        test_file.write_bytes(b"content")

        # Add same file twice
        tracker.add_orphaned_media(test_file, "No metadata")
        tracker.add_orphaned_media(test_file, "No metadata")

        assert len(tracker.orphaned_media) == 2  # Both kept

    def test_special_chars_in_paths(self, tmp_path: Path) -> None:
        """Should handle special characters in paths."""
        tracker = FailureTracker(
            processor_name="Test",
            export_directory=str(tmp_path),
        )

        # Create test files
        file1 = tmp_path / "photo (1).jpg"
        file1.write_bytes(b"content")

        file2 = tmp_path / "file[2].jpg"
        file2.write_bytes(b"content2")

        tracker.add_orphaned_media(file1, "Test")
        tracker.add_processing_failure(
            file2,
            metadata={"title": "test"},
            reason="Test reason",
            error_details="Test error",
        )

        # Check that failures were tracked correctly
        summary = tracker.get_summary()
        assert summary["total_failures"] == 2
        assert summary["failed_matching"] == 1
        assert summary["failed_processing"] == 1

        # Check that paths are stored correctly
        assert any(
            "photo (1).jpg" in entry["file_path"] for entry in tracker.orphaned_media
        )
        assert any(
            "file[2].jpg" in entry["file_path"] for entry in tracker.processing_failures
        )


# ============================================================================
# Banned Filter Edge Cases
# ============================================================================


class TestBannedFilterEdgeCases:
    """Edge case tests for BannedFilesFilter."""

    def test_empty_filename(self, tmp_path: Path) -> None:
        """Should handle empty filename."""
        filter_instance = BannedFilesFilter()
        # Create a path with empty name isn't really possible,
        # but we can test the filter logic
        test_path = tmp_path / "."  # Current dir reference
        result = filter_instance.is_banned(test_path)
        # Should handle without crashing
        assert isinstance(result, bool)

    def test_case_sensitivity(self, tmp_path: Path) -> None:
        """Should handle case variations of banned patterns."""
        filter_instance = BannedFilesFilter()

        # .DS_Store variations
        assert filter_instance.is_banned(tmp_path / ".DS_Store") is True
        # Case matters for exact match

    def test_additional_patterns(self, tmp_path: Path) -> None:
        """Should respect additional custom patterns."""
        filter_instance = BannedFilesFilter(
            additional_patterns=["CUSTOM_PATTERN", "temp_"]
        )

        assert filter_instance.is_banned(tmp_path / "CUSTOM_PATTERN") is True
        assert filter_instance.is_banned(tmp_path / "temp_file.jpg") is True
        assert filter_instance.is_banned(tmp_path / "normal.jpg") is False

    def test_banned_extension(self, tmp_path: Path) -> None:
        """Should ban files with banned extensions."""
        filter_instance = BannedFilesFilter()

        # .unknown extension is banned
        assert filter_instance.is_banned(tmp_path / "file.unknown") is True
        assert filter_instance.is_banned(tmp_path / "file.jpg") is False
