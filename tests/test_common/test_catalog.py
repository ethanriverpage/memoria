#!/usr/bin/env python3
"""Unit tests for catalog_media_files() in common/catalog.py."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from common.dedup.catalog import catalog_media_files


class TestCatalogMediaFiles:
    """Tests for catalog_media_files() function."""

    @pytest.fixture
    def temp_media_dir(self):
        """Create a temporary directory with test media files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create some test files
            (tmppath / "photo1.jpg").write_bytes(b"fake jpeg content 1")
            (tmppath / "photo2.jpg").write_bytes(b"fake jpeg content 2")
            (tmppath / "video.mp4").write_bytes(b"fake video content")

            yield tmppath

    def test_catalog_structure(self, temp_media_dir):
        """Test that catalog has correct structure."""
        media_files = list(temp_media_dir.glob("*"))

        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        assert "processor" in catalog
        assert "input_dir" in catalog
        assert "files" in catalog
        assert catalog["processor"] == "Test Processor"
        assert catalog["input_dir"] == str(temp_media_dir)
        assert isinstance(catalog["files"], list)

    def test_file_entries_have_required_fields(self, temp_media_dir):
        """Test that each file entry has all required fields."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        required_fields = [
            "source_path",
            "relative_path",
            "content_hash",
            "perceptual_hash",
            "file_size",
            "dimensions",
            "timestamp",
            "metadata",
        ]

        for entry in catalog["files"]:
            for field in required_fields:
                assert field in entry, f"Missing field: {field}"

    def test_content_hash_computed(self, temp_media_dir):
        """Test that content hashes are computed."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        for entry in catalog["files"]:
            assert entry["content_hash"] is not None
            assert len(entry["content_hash"]) == 16  # xxHash64 hex length

    def test_different_files_have_different_hashes(self, temp_media_dir):
        """Test that different files have different content hashes."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        hashes = [entry["content_hash"] for entry in catalog["files"]]
        assert len(hashes) == len(set(hashes)), "Expected unique hashes for each file"

    def test_relative_path_correct(self, temp_media_dir):
        """Test that relative paths are correctly computed."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        for entry in catalog["files"]:
            assert not entry["relative_path"].startswith("/")
            assert entry["relative_path"].endswith(".jpg")

    def test_file_size_correct(self, temp_media_dir):
        """Test that file sizes are correctly recorded."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        for entry in catalog["files"]:
            # File should have non-zero size
            assert entry["file_size"] > 0
            # Verify against actual file
            actual_size = Path(entry["source_path"]).stat().st_size
            assert entry["file_size"] == actual_size

    def test_empty_file_list(self, temp_media_dir):
        """Test handling of empty file list."""
        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=[],
            workers=2,
        )

        assert catalog["files"] == []
        assert catalog["processor"] == "Test Processor"

    def test_metadata_extractor_called(self, temp_media_dir):
        """Test that metadata_extractor callback is called."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        def extract_metadata(file_path: Path):
            return {"custom_field": file_path.name}

        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
            metadata_extractor=extract_metadata,
        )

        for entry in catalog["files"]:
            assert "custom_field" in entry["metadata"]
            # The custom field should be the filename
            expected_name = Path(entry["source_path"]).name
            assert entry["metadata"]["custom_field"] == expected_name

    def test_metadata_extractor_failure_handled(self, temp_media_dir):
        """Test that metadata_extractor failures don't break cataloging."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        def failing_extractor(file_path: Path):
            raise ValueError("Intentional failure")

        # Should not raise
        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
            metadata_extractor=failing_extractor,
        )

        # Files should still be cataloged
        assert len(catalog["files"]) == len(media_files)

    def test_perceptual_hash_respects_config(self, temp_media_dir):
        """Test that perceptual hashing respects config setting."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        # Mock the config to disable perceptual hashing
        with patch("common.dedup.catalog.get_dedup_config") as mock_config:
            mock_config.return_value.use_perceptual = False

            catalog = catalog_media_files(
                processor_name="Test Processor",
                input_path=temp_media_dir,
                media_files=media_files,
                workers=2,
            )

        # Perceptual hash should be None when disabled
        for entry in catalog["files"]:
            assert entry["perceptual_hash"] is None

    def test_handles_missing_files_gracefully(self, temp_media_dir):
        """Test that missing files don't crash cataloging."""
        existing_file = temp_media_dir / "photo1.jpg"
        missing_file = temp_media_dir / "nonexistent.jpg"

        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=[existing_file, missing_file],
            workers=2,
        )

        # Should only have the existing file
        assert len(catalog["files"]) == 1
        assert catalog["files"][0]["source_path"] == str(existing_file)

    def test_nested_directory_structure(self, temp_media_dir):
        """Test cataloging files in nested directories."""
        # Create nested structure
        subdir = temp_media_dir / "subdir" / "deep"
        subdir.mkdir(parents=True)
        nested_file = subdir / "nested.jpg"
        nested_file.write_bytes(b"nested content")

        media_files = [nested_file]

        catalog = catalog_media_files(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        assert len(catalog["files"]) == 1
        assert catalog["files"][0]["relative_path"] == "subdir/deep/nested.jpg"

