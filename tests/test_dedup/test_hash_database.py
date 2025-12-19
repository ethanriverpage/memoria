#!/usr/bin/env python3
"""Unit tests for HashDatabase."""

import tempfile
from pathlib import Path

import pytest

from common.hash_database import HashDatabase


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_dedup.db"
        db = HashDatabase(db_path)
        yield db


class TestHashDatabase:
    """Tests for HashDatabase class."""

    def test_init_creates_database(self, temp_db):
        """Test that initialization creates the database file."""
        assert temp_db.db_path.exists()

    def test_register_new_file(self, temp_db):
        """Test registering a new file."""
        result = temp_db.register(
            content_hash="abc123",
            processor="Google Photos",
            source_path="/path/to/file.jpg",
            file_size=1000,
        )
        assert result is True

    def test_register_duplicate_returns_false(self, temp_db):
        """Test that registering duplicate entry returns False."""
        temp_db.register(
            content_hash="abc123",
            processor="Google Photos",
            source_path="/path/to/file.jpg",
        )
        result = temp_db.register(
            content_hash="abc123",
            processor="Google Photos",
            source_path="/path/to/file.jpg",
        )
        assert result is False

    def test_get_duplicates(self, temp_db):
        """Test retrieving duplicates by content hash."""
        # Register same hash from different processors
        temp_db.register(
            content_hash="abc123",
            processor="Google Photos",
            source_path="/path/google/file.jpg",
            file_size=1000,
        )
        temp_db.register(
            content_hash="abc123",
            processor="iMessage",
            source_path="/path/imessage/file.jpg",
            file_size=1000,
        )

        duplicates = temp_db.get_duplicates("abc123")
        assert len(duplicates) == 2
        processors = {d["processor"] for d in duplicates}
        assert "Google Photos" in processors
        assert "iMessage" in processors

    def test_get_duplicates_no_match(self, temp_db):
        """Test get_duplicates returns empty list for unknown hash."""
        duplicates = temp_db.get_duplicates("nonexistent")
        assert duplicates == []

    def test_get_similar_perceptual(self, temp_db):
        """Test finding similar files by perceptual hash."""
        # Register files with similar perceptual hashes
        temp_db.register(
            content_hash="hash1",
            processor="Google Photos",
            source_path="/path/file1.jpg",
            perceptual_hash="ffffffff00000000",  # 64-bit hash as hex
        )
        temp_db.register(
            content_hash="hash2",
            processor="iMessage",
            source_path="/path/file2.jpg",
            perceptual_hash="ffffffff00000001",  # 1 bit different
        )

        similar = temp_db.get_similar("ffffffff00000000", threshold=0.9)
        assert len(similar) >= 1

    def test_get_by_processor(self, temp_db):
        """Test filtering by processor name."""
        temp_db.register(
            content_hash="hash1",
            processor="Google Photos",
            source_path="/path/google1.jpg",
        )
        temp_db.register(
            content_hash="hash2",
            processor="Google Photos",
            source_path="/path/google2.jpg",
        )
        temp_db.register(
            content_hash="hash3",
            processor="iMessage",
            source_path="/path/imessage.jpg",
        )

        google_files = temp_db.get_by_processor("Google Photos")
        assert len(google_files) == 2

        imessage_files = temp_db.get_by_processor("iMessage")
        assert len(imessage_files) == 1

    def test_set_primary(self, temp_db):
        """Test setting a file as primary."""
        temp_db.register(
            content_hash="abc123",
            processor="Google Photos",
            source_path="/path/google.jpg",
        )
        temp_db.register(
            content_hash="abc123",
            processor="iMessage",
            source_path="/path/imessage.jpg",
        )

        result = temp_db.set_primary("abc123", "/path/google.jpg")
        assert result is True

        duplicates = temp_db.get_duplicates("abc123")
        primary = [d for d in duplicates if d["is_primary"]]
        assert len(primary) == 1
        assert primary[0]["source_path"] == "/path/google.jpg"

    def test_update_metadata(self, temp_db):
        """Test updating file metadata."""
        temp_db.register(
            content_hash="abc123",
            processor="Google Photos",
            source_path="/path/file.jpg",
        )

        new_metadata = {"albums": ["Vacation"], "people": ["Alice", "Bob"]}
        result = temp_db.update_metadata("abc123", "/path/file.jpg", new_metadata)
        assert result is True

        duplicates = temp_db.get_duplicates("abc123")
        assert duplicates[0]["metadata"] == new_metadata

    def test_get_statistics(self, temp_db):
        """Test getting database statistics."""
        # Register some test data
        temp_db.register(
            content_hash="hash1",
            processor="Google Photos",
            source_path="/path/1.jpg",
        )
        temp_db.register(
            content_hash="hash1",
            processor="iMessage",
            source_path="/path/2.jpg",
        )
        temp_db.register(
            content_hash="hash2",
            processor="Discord",
            source_path="/path/3.jpg",
        )

        stats = temp_db.get_statistics()
        assert stats["total_files"] == 3
        assert stats["unique_content_hashes"] == 2
        assert stats["duplicate_groups"] == 1
        assert len(stats["processors"]) == 3

    def test_clear(self, temp_db):
        """Test clearing all data."""
        temp_db.register(
            content_hash="hash1",
            processor="Google Photos",
            source_path="/path/1.jpg",
        )
        temp_db.clear()

        stats = temp_db.get_statistics()
        assert stats["total_files"] == 0

    def test_clear_processor(self, temp_db):
        """Test clearing data for a specific processor."""
        temp_db.register(
            content_hash="hash1",
            processor="Google Photos",
            source_path="/path/1.jpg",
        )
        temp_db.register(
            content_hash="hash2",
            processor="iMessage",
            source_path="/path/2.jpg",
        )

        deleted = temp_db.clear_processor("Google Photos")
        assert deleted == 1

        stats = temp_db.get_statistics()
        assert stats["total_files"] == 1
        assert "iMessage" in stats["processors"]

    def test_dimensions_stored_correctly(self, temp_db):
        """Test that dimensions are stored and retrieved correctly."""
        temp_db.register(
            content_hash="abc123",
            processor="Google Photos",
            source_path="/path/file.jpg",
            dimensions=(1920, 1080),
        )

        duplicates = temp_db.get_duplicates("abc123")
        assert duplicates[0]["dimensions"] == [1920, 1080]

    def test_metadata_json_stored_correctly(self, temp_db):
        """Test that metadata is stored and retrieved as JSON."""
        metadata = {
            "albums": ["Vacation 2024", "Summer"],
            "people": ["Alice", "Bob"],
            "gps": {"latitude": 40.7128, "longitude": -74.0060},
        }

        temp_db.register(
            content_hash="abc123",
            processor="Google Photos",
            source_path="/path/file.jpg",
            metadata=metadata,
        )

        duplicates = temp_db.get_duplicates("abc123")
        assert duplicates[0]["metadata"] == metadata
