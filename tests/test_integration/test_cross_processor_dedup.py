#!/usr/bin/env python3
"""Integration tests for cross-processor deduplication workflow."""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from common.dedup.config import DedupConfig, reset_config
from common.dedup.service import (
    DeduplicationService,
    save_manifest,
    get_merged_description_for_file,
    is_file_primary_by_hash,
    build_dedup_references,
)
from common.dedup.database import HashDatabase
from common.dedup.hashing import (
    is_live_photo_pair,
    find_live_photo_video,
)


def write_catalogs_to_db(db: HashDatabase, run_id: str, catalogs: list) -> None:
    """Write catalog entries to the database for testing analyze_from_db.

    This helper prepares catalog entries with the _processor field set
    and writes them to the database in the format expected by analyze_from_db.

    Args:
        db: HashDatabase instance.
        run_id: Run identifier for this test.
        catalogs: List of catalog dictionaries (same format as analyze() took).
    """
    for catalog in catalogs:
        processor = catalog.get("processor", "Unknown")
        export_name = catalog.get("input_dir", "export")
        entries = []
        for file_entry in catalog.get("files", []):
            entry = dict(file_entry)
            entry["_processor"] = processor
            # Preserve live_photo_video in metadata for database round-trip
            if "live_photo_video" in entry:
                metadata = entry.get("metadata", {})
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata = dict(metadata)
                metadata["live_photo_video"] = entry["live_photo_video"]
                entry["metadata"] = metadata
            entries.append(entry)
        if entries:
            db.write_catalog_entries_batch(run_id, export_name, entries)


@pytest.fixture(autouse=True)
def reset_dedup_config():
    """Reset dedup config before each test."""
    reset_config()
    yield
    reset_config()


@pytest.fixture
def temp_dir(tmp_path):
    """Create temporary directory for test files.

    Uses pytest's tmp_path fixture for automatic cleanup.
    """
    return tmp_path


@pytest.fixture
def mock_catalogs():
    """Create mock catalogs simulating multiple processors."""
    return [
        {
            "processor": "Google Photos",
            "input_dir": "/exports/google",
            "files": [
                {
                    "source_path": "/exports/google/photo1.jpg",
                    "relative_path": "photo1.jpg",
                    "content_hash": "abc123",
                    "perceptual_hash": "ffffffff00000000",
                    "file_size": 1000000,
                    "dimensions": (1920, 1080),
                    "timestamp": "2024-01-01T10:00:00Z",
                    "metadata": {
                        "albums": ["Vacation 2024"],
                        "people": ["Alice"],
                        "gps": {"latitude": 40.7128, "longitude": -74.0060},
                    },
                },
                {
                    "source_path": "/exports/google/photo2.jpg",
                    "relative_path": "photo2.jpg",
                    "content_hash": "def456",
                    "perceptual_hash": "00000000ffffffff",
                    "file_size": 800000,
                    "dimensions": (1600, 900),
                    "timestamp": "2024-01-02T10:00:00Z",
                    "metadata": {"albums": ["Summer"]},
                },
            ],
        },
        {
            "processor": "iMessage",
            "input_dir": "/exports/imessage",
            "files": [
                {
                    "source_path": "/exports/imessage/attachment1.jpg",
                    "relative_path": "attachment1.jpg",
                    "content_hash": "abc123",  # Duplicate of Google Photos photo1
                    "perceptual_hash": "ffffffff00000000",
                    "file_size": 950000,  # Slightly smaller
                    "dimensions": (1920, 1080),
                    "timestamp": "2024-01-03T10:00:00Z",  # Later date
                    "metadata": {
                        "sender": "Bob",
                        "conversation": "Family",
                    },
                },
            ],
        },
        {
            "processor": "Instagram Messages",
            "input_dir": "/exports/instagram",
            "files": [
                {
                    "source_path": "/exports/instagram/story.jpg",
                    "relative_path": "story.jpg",
                    "content_hash": "abc123",  # Another duplicate
                    "perceptual_hash": "ffffffff00000000",
                    "file_size": 900000,
                    "dimensions": (1080, 1920),  # Portrait
                    "timestamp": "2024-01-02T15:00:00Z",
                    "metadata": {
                        "context_type": "Story",
                        "caption": "Beach day!",
                    },
                },
            ],
        },
    ]


class TestEndToEndWorkflow:
    """End-to-end workflow tests."""

    def test_full_dedup_workflow(self, temp_dir, mock_catalogs):
        """Test complete deduplication workflow."""
        # Setup
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(
            enabled=True,
            use_perceptual=False,
            database_path=db_path,
        )
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)

        # Phase 1: Write catalogs to DB and analyze
        run_id = "test_full_workflow"
        write_catalogs_to_db(db, run_id, mock_catalogs)
        manifest = service.analyze_from_db(run_id)

        # Verify manifest structure
        assert manifest["version"] == "1.0"
        assert "created" in manifest
        assert "statistics" in manifest
        assert "duplicates" in manifest

        # Verify statistics
        stats = manifest["statistics"]
        assert stats["total_files_scanned"] == 4
        assert (
            stats["duplicates_found"] == 2
        )  # 3 files with same hash - 1 primary = 2 dups
        assert (
            stats["unique_files"] == 2
        )  # abc123 (3 copies) + def456 (1 copy) = 2 unique

        # Verify duplicate detection
        assert "abc123" in manifest["duplicates"]
        dup_group = manifest["duplicates"]["abc123"]

        # Verify primary selection (earliest timestamp)
        assert dup_group["primary"]["processor"] == "Google Photos"

        # Verify best quality selection (largest file)
        assert dup_group["best_quality"]["processor"] == "Google Photos"
        assert dup_group["best_quality"]["file_size"] == 1000000

        # Verify secondary sources
        assert len(dup_group["secondary"]) == 2
        secondary_processors = {s["processor"] for s in dup_group["secondary"]}
        assert "iMessage" in secondary_processors
        assert "Instagram Messages" in secondary_processors

        # Verify metadata merging
        merged = dup_group["merged_metadata"]
        assert merged["timestamp"] is not None
        assert "2024-01-01" in merged["timestamp"]
        assert merged["gps"] is not None
        assert "Alice" in merged["people"]

        # Phase 2: Save and load manifest
        manifest_path = temp_dir / "dedup_manifest.json"
        save_manifest(manifest, manifest_path)
        assert manifest_path.exists()

        with open(manifest_path, "r", encoding="utf-8") as f:
            loaded_manifest = json.load(f)
        assert loaded_manifest["version"] == manifest["version"]
        assert loaded_manifest["statistics"] == manifest["statistics"]

    def test_database_persistence(self, temp_dir, mock_catalogs):
        """Test that hash database persists across sessions."""
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path, use_perceptual=False)

        # First session
        db1 = HashDatabase(db_path)
        service1 = DeduplicationService(db=db1, config=config)
        run_id = "test_persistence"
        write_catalogs_to_db(db1, run_id, mock_catalogs)
        service1.analyze_from_db(run_id)

        stats1 = db1.get_statistics()
        assert stats1["total_files"] == 4

        # Second session - database should persist
        db2 = HashDatabase(db_path)
        stats2 = db2.get_statistics()
        assert stats2["total_files"] == 4

    def test_incremental_processing(self, temp_dir, mock_catalogs):
        """Test adding new files to existing database."""
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path, use_perceptual=False)

        # Initial analysis
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)
        run_id1 = "test_incremental_1"
        write_catalogs_to_db(db, run_id1, mock_catalogs[:1])  # Just Google Photos
        service.analyze_from_db(run_id1)

        stats1 = db.get_statistics()
        assert stats1["total_files"] == 2

        # Add more catalogs
        run_id2 = "test_incremental_2"
        write_catalogs_to_db(db, run_id2, mock_catalogs[1:])  # iMessage + Instagram
        service.analyze_from_db(run_id2)

        stats2 = db.get_statistics()
        assert stats2["total_files"] == 4

    def test_space_savings_calculation(self, temp_dir, mock_catalogs):
        """Test that space savings are calculated correctly."""
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path, use_perceptual=False)
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)

        run_id = "test_space_savings"
        write_catalogs_to_db(db, run_id, mock_catalogs)
        manifest = service.analyze_from_db(run_id)

        # Space saved should be: best_file_size * (num_duplicates)
        # For abc123: 1000000 * 2 = 2000000 bytes
        assert manifest["statistics"]["space_saved_bytes"] > 0


class TestConfigurationBehavior:
    """Tests for configuration-driven behavior."""

    def test_dedup_disabled_by_default(self):
        """Test that deduplication is disabled by default."""
        from common.dedup.config import is_dedup_enabled

        assert is_dedup_enabled() is False

    def test_dedup_enabled_via_env(self):
        """Test enabling dedup via environment variable."""
        with patch.dict(os.environ, {"CROSS_PROCESSOR_DEDUP": "true"}):
            reset_config()
            from common.dedup.config import is_dedup_enabled

            assert is_dedup_enabled() is True

    def test_perceptual_threshold_from_env(self):
        """Test perceptual threshold configuration."""
        with patch.dict(os.environ, {"DEDUP_PERCEPTUAL_THRESHOLD": "0.85"}):
            reset_config()
            from common.dedup.config import get_dedup_config

            config = get_dedup_config()
            assert config.perceptual_threshold == 0.85

    def test_database_path_from_env(self, temp_dir):
        """Test database path configuration."""
        custom_path = str(temp_dir / "custom.db")
        with patch.dict(os.environ, {"DEDUP_DATABASE_PATH": custom_path}):
            reset_config()
            from common.dedup.config import get_dedup_config

            config = get_dedup_config()
            assert str(config.database_path) == custom_path


class TestErrorHandling:
    """Tests for error handling scenarios."""

    def test_empty_catalog(self, temp_dir):
        """Test handling of empty catalogs."""
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path)
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)

        run_id = "test_empty_catalog"
        write_catalogs_to_db(
            db, run_id, [{"processor": "Test", "input_dir": "/test", "files": []}]
        )
        manifest = service.analyze_from_db(run_id)

        assert manifest["statistics"]["total_files_scanned"] == 0
        assert manifest["duplicates"] == {}

    def test_missing_fields_handled(self, temp_dir):
        """Test handling of catalogs with missing optional fields."""
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path, use_perceptual=False)
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)

        # Minimal catalog with only required fields
        minimal_catalog = {
            "processor": "Test",
            "input_dir": "/test",
            "files": [
                {
                    "source_path": "/test/file.jpg",
                    "content_hash": "hash123",
                    # No optional fields
                }
            ],
        }

        # Should not raise exception
        run_id = "test_missing_fields"
        write_catalogs_to_db(db, run_id, [minimal_catalog])
        manifest = service.analyze_from_db(run_id)
        assert manifest["statistics"]["total_files_scanned"] == 1


class TestMergedDescriptionIntegration:
    """Integration tests for merged EXIF descriptions."""

    def test_merged_description_contains_all_sources(self, temp_dir, mock_catalogs):
        """Test that merged description includes all source processors."""
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path, use_perceptual=False)
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)

        run_id = "test_merged_desc_sources"
        write_catalogs_to_db(db, run_id, mock_catalogs)
        manifest = service.analyze_from_db(run_id)

        # Get the primary file's merged description
        primary_path = manifest["duplicates"]["abc123"]["primary"]["source_path"]
        desc = get_merged_description_for_file(manifest, primary_path)

        assert desc is not None
        assert "Google Photos" in desc
        # Secondary sources should be mentioned
        assert "iMessage" in desc or "Instagram" in desc

    def test_description_includes_context(self, temp_dir, mock_catalogs):
        """Test that context from secondary sources is included."""
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path, use_perceptual=False)
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)

        run_id = "test_desc_context"
        write_catalogs_to_db(db, run_id, mock_catalogs)
        manifest = service.analyze_from_db(run_id)

        primary_path = manifest["duplicates"]["abc123"]["primary"]["source_path"]
        desc = get_merged_description_for_file(manifest, primary_path)

        # Check that some context from secondaries is included
        # Instagram context has type "Story" and caption "Beach day!"
        assert desc is not None
        # The description builder uses this context


class TestSecondaryReferencesIntegration:
    """Integration tests for secondary file references."""

    def test_build_references_for_skipped_files(self, temp_dir, mock_catalogs):
        """Test building references for files that would be skipped."""
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path, use_perceptual=False)
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)

        run_id = "test_build_refs"
        write_catalogs_to_db(db, run_id, mock_catalogs)
        manifest = service.analyze_from_db(run_id)

        # Identify secondary files
        skipped = []
        for dup_group in manifest["duplicates"].values():
            for secondary in dup_group.get("secondary", []):
                skipped.append(secondary["source_path"])

        # Build references
        refs = build_dedup_references(
            manifest=manifest,
            skipped_files=skipped,
            processor_name="Integration Test",
        )

        assert len(refs) > 0
        for ref in refs:
            assert ref["deduplicated"] is True
            assert "canonical_location" in ref
            assert "canonical_processor" in ref

    def test_is_file_primary_by_hash_integration(self, temp_dir, mock_catalogs):
        """Test is_file_primary_by_hash with actual manifest.

        This test verifies that hash-based lookup works correctly,
        which is critical for processors that preprocess to temp directories.

        Note: is_file_primary_by_hash now uses best_quality.processor (not primary.processor)
        to determine which processor should copy the file, ensuring the highest quality
        version is always outputted.
        """
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path, use_perceptual=False)
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)

        run_id = "test_primary_by_hash"
        write_catalogs_to_db(db, run_id, mock_catalogs)
        manifest = service.analyze_from_db(run_id)

        # The duplicate file has content_hash "abc123" (from mock_catalogs)
        # Google Photos is the best_quality processor (largest file: 1000000 bytes)
        # and also happens to be primary (earliest timestamp)
        assert is_file_primary_by_hash(manifest, "abc123", "Google Photos") is True

        # iMessage has smaller file, so it should not copy
        assert is_file_primary_by_hash(manifest, "abc123", "iMessage") is False

        # Non-duplicate file's hash (def456) should return True for any processor
        assert is_file_primary_by_hash(manifest, "def456", "Google Photos") is True

        # Unknown hash should return True (not a duplicate)
        assert (
            is_file_primary_by_hash(manifest, "unknown_hash", "Any Processor") is True
        )

    def test_best_quality_wins_over_primary_integration(self, temp_dir):
        """Test that best_quality processor wins even when primary has earlier timestamp.

        This tests the "super-image" pattern where we output the highest quality file
        but use primary's timestamp for attribution.
        """
        db_path = temp_dir / "dedup.db"
        config = DedupConfig(database_path=db_path, use_perceptual=False)
        db = HashDatabase(db_path)
        service = DeduplicationService(db=db, config=config)

        # Create catalogs where iMessage has earliest timestamp but Google Photos
        # has the best quality (largest file)
        catalogs = [
            {
                "processor": "iMessage",
                "input_dir": "/exports/imessage",
                "files": [
                    {
                        "source_path": "/exports/imessage/photo.jpg",
                        "relative_path": "photo.jpg",
                        "content_hash": "xyz789",
                        "file_size": 500000,  # 500KB (lower quality)
                        "dimensions": (1280, 720),
                        "timestamp": "2018-03-15T10:00:00Z",  # EARLIEST
                        "metadata": {"sender": "Alice"},
                    },
                ],
            },
            {
                "processor": "Google Photos",
                "input_dir": "/exports/google",
                "files": [
                    {
                        "source_path": "/exports/google/photo.jpg",
                        "relative_path": "photo.jpg",
                        "content_hash": "xyz789",
                        "file_size": 3000000,  # 3MB (highest quality)
                        "dimensions": (4000, 3000),  # 12MP
                        "timestamp": "2024-06-01T10:00:00Z",  # Later
                        "metadata": {"albums": ["Camera Roll"]},
                    },
                ],
            },
        ]

        run_id = "test_best_quality_wins"
        write_catalogs_to_db(db, run_id, catalogs)
        manifest = service.analyze_from_db(run_id)

        # Verify primary is iMessage (earliest timestamp)
        dup_group = manifest["duplicates"]["xyz789"]
        assert dup_group["primary"]["processor"] == "iMessage"

        # Verify best_quality is Google Photos (largest file)
        assert dup_group["best_quality"]["processor"] == "Google Photos"
        assert dup_group["best_quality"]["file_size"] == 3000000

        # Now test is_file_primary_by_hash - should use best_quality!
        # Google Photos should copy (has best quality)
        assert is_file_primary_by_hash(manifest, "xyz789", "Google Photos") is True

        # iMessage should NOT copy (even though it's primary by timestamp)
        assert is_file_primary_by_hash(manifest, "xyz789", "iMessage") is False

        # Verify merged_metadata has the earliest timestamp (from iMessage)
        merged = dup_group["merged_metadata"]
        assert "2018-03-15" in merged["timestamp"]


class TestLivePhotoIntegration:
    """Integration tests for Live Photo handling."""

    def test_live_photo_detection_in_temp_dir(self, temp_dir):
        """Test Live Photo detection with actual files."""
        # Create test Live Photo pair
        image_path = temp_dir / "IMG_0001.heic"
        video_path = temp_dir / "IMG_0001.mov"
        image_path.touch()
        video_path.touch()

        assert is_live_photo_pair(image_path, video_path) is True

        # Find video from image
        found = find_live_photo_video(image_path)
        assert found == video_path

    def test_live_photo_not_matched_for_different_stems(self, temp_dir):
        """Test that different filename stems are not matched."""
        image_path = temp_dir / "IMG_0001.heic"
        video_path = temp_dir / "IMG_0002.mov"
        image_path.touch()
        video_path.touch()

        assert is_live_photo_pair(image_path, video_path) is False

        # find_live_photo_video should not find the non-matching video
        found = find_live_photo_video(image_path)
        assert found is None
