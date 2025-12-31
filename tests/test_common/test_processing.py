#!/usr/bin/env python3
"""Unit tests for the `common.processing` package.

Tests:
- Preprocessing cache path generation
- Cache validity checking
- Temporary directory management
- Batch parallel processing
"""

import os
from pathlib import Path
from unittest.mock import patch


from common.processing import (
    get_preprocessing_cache_path,
    is_preprocessing_cache_valid,
    temp_processing_directory,
)


class TestGetPreprocessingCachePath:
    """Tests for get_preprocessing_cache_path function."""

    def test_returns_path_object(self, tmp_path):
        """Test that function returns a Path object."""
        input_dir = tmp_path / "export"
        input_dir.mkdir()

        result = get_preprocessing_cache_path(input_dir, "Discord")

        assert isinstance(result, Path)

    def test_includes_processor_slug(self, tmp_path):
        """Test that path includes processor name slug."""
        input_dir = tmp_path / "my_export"
        input_dir.mkdir()

        result = get_preprocessing_cache_path(input_dir, "Discord")

        assert "discord" in str(result).lower()

    def test_includes_export_dir_name(self, tmp_path):
        """Test that path includes export directory name."""
        input_dir = tmp_path / "google-takeout-2024"
        input_dir.mkdir()

        result = get_preprocessing_cache_path(input_dir, "Google Photos")

        assert "google-takeout-2024" in str(result)

    def test_sanitizes_processor_name(self, tmp_path):
        """Test that processor name with spaces is sanitized."""
        input_dir = tmp_path / "export"
        input_dir.mkdir()

        result = get_preprocessing_cache_path(input_dir, "Google Photos")

        # Spaces should be replaced with underscores
        assert "google_photos" in str(result).lower()

    def test_deterministic_hash(self, tmp_path):
        """Test that same inputs produce same hash."""
        input_dir = tmp_path / "export"
        input_dir.mkdir()

        result1 = get_preprocessing_cache_path(input_dir, "Discord")
        result2 = get_preprocessing_cache_path(input_dir, "Discord")

        assert result1 == result2

    def test_different_dirs_produce_different_hashes(self, tmp_path):
        """Test that different input dirs produce different hashes."""
        dir1 = tmp_path / "export1"
        dir2 = tmp_path / "export2"
        dir1.mkdir()
        dir2.mkdir()

        result1 = get_preprocessing_cache_path(dir1, "Discord")
        result2 = get_preprocessing_cache_path(dir2, "Discord")

        assert result1 != result2


class TestIsPreprocessingCacheValid:
    """Tests for is_preprocessing_cache_valid function."""

    def test_returns_false_for_nonexistent(self, tmp_path):
        """Test that non-existent directory returns False."""
        cache_path = tmp_path / "nonexistent"

        assert is_preprocessing_cache_valid(cache_path) is False

    def test_returns_false_without_metadata(self, tmp_path):
        """Test that directory without metadata.json returns False."""
        cache_path = tmp_path / "cache"
        cache_path.mkdir()
        # No metadata.json

        assert is_preprocessing_cache_valid(cache_path) is False

    def test_returns_true_with_metadata(self, tmp_path):
        """Test that directory with metadata.json returns True."""
        cache_path = tmp_path / "cache"
        cache_path.mkdir()
        (cache_path / "metadata.json").touch()

        assert is_preprocessing_cache_valid(cache_path) is True


class TestTempProcessingDirectory:
    """Tests for temp_processing_directory context manager."""

    def test_creates_directory(self, tmp_path):
        """Test that temp directory is created."""
        with temp_processing_directory(str(tmp_path), "test") as temp_dir:
            assert temp_dir.exists()
            assert temp_dir.is_dir()

    def test_directory_name_includes_prefix(self, tmp_path):
        """Test that directory name includes the prefix."""
        with temp_processing_directory(str(tmp_path), "myprefix") as temp_dir:
            assert "myprefix" in temp_dir.name

    def test_directory_is_unique(self, tmp_path):
        """Test that each call creates unique directory."""
        dirs = []
        for _ in range(3):
            with temp_processing_directory(str(tmp_path), "test") as temp_dir:
                dirs.append(temp_dir)

        # All should be different
        assert len(set(dirs)) == 3

    def test_cleanup_by_default(self, tmp_path):
        """Test that directory is cleaned up after context exits."""
        # Make sure cleanup is not disabled
        with patch.dict(os.environ, {"DISABLE_PREPROCESSING_CLEANUP": ""}, clear=False):
            with temp_processing_directory(str(tmp_path), "test") as temp_dir:
                temp_path = temp_dir
                assert temp_path.exists()

            # Should be cleaned up
            assert not temp_path.exists()

    def test_no_cleanup_when_disabled(self, tmp_path):
        """Test that directory is preserved when cleanup is disabled."""
        # Set DISABLE_PREPROCESSING_CLEANUP to disable cleanup
        with patch.dict(os.environ, {"DISABLE_PREPROCESSING_CLEANUP": "true"}, clear=False):
            with temp_processing_directory(str(tmp_path), "test") as temp_dir:
                temp_path = temp_dir
                assert temp_path.exists()

            # Should still exist
            assert temp_path.exists()

    def test_creates_parent_directories(self, tmp_path):
        """Test that parent directories are created if needed."""
        deep_path = tmp_path / "a" / "b" / "c"

        with temp_processing_directory(str(deep_path), "test") as temp_dir:
            assert temp_dir.exists()
            assert deep_path.exists()

    def test_can_create_files_inside(self, tmp_path):
        """Test that files can be created inside temp directory."""
        with temp_processing_directory(str(tmp_path), "test") as temp_dir:
            test_file = temp_dir / "test.txt"
            test_file.write_text("hello")

            assert test_file.exists()
            assert test_file.read_text() == "hello"
