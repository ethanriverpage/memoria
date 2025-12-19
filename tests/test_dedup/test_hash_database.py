#!/usr/bin/env python3
"""Unit tests for HashDatabase."""

import tempfile
from pathlib import Path

import pytest

from common.dedup.database import HashDatabase


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


class TestCatalogEntries:
    """Tests for streaming catalog entry methods."""

    def test_write_catalog_entry(self, temp_db):
        """Test writing a single catalog entry."""
        entry = {
            "source_path": "/path/to/file.jpg",
            "relative_path": "file.jpg",
            "content_hash": "abc123",
            "perceptual_hash": "fff00000",
            "file_size": 1000,
            "dimensions": (1920, 1080),
            "_processor": "Google Photos",
        }

        result = temp_db.write_catalog_entry("run_001", "export1", entry)
        assert result is True

        # Verify it was written
        stats = temp_db.get_run_statistics("run_001")
        assert stats["total_files"] == 1

    def test_write_catalog_entries_batch(self, temp_db):
        """Test writing multiple catalog entries in a batch."""
        entries = [
            {
                "source_path": f"/path/file{i}.jpg",
                "content_hash": f"hash{i}",
                "_processor": "Google Photos",
            }
            for i in range(5)
        ]

        written = temp_db.write_catalog_entries_batch("run_001", "export1", entries)
        assert written == 5

        stats = temp_db.get_run_statistics("run_001")
        assert stats["total_files"] == 5

    def test_get_duplicate_content_hashes(self, temp_db):
        """Test finding content hashes with duplicates."""
        # Write entries with duplicate hashes
        entries = [
            {"source_path": "/a/1.jpg", "content_hash": "dup_hash", "_processor": "P1"},
            {"source_path": "/b/1.jpg", "content_hash": "dup_hash", "_processor": "P2"},
            {"source_path": "/a/2.jpg", "content_hash": "unique", "_processor": "P1"},
        ]
        temp_db.write_catalog_entries_batch("run_001", "export1", entries)

        dup_hashes = temp_db.get_duplicate_content_hashes("run_001")
        assert len(dup_hashes) == 1
        assert "dup_hash" in dup_hashes

    def test_get_entries_by_hash(self, temp_db):
        """Test retrieving entries by content hash."""
        entries = [
            {"source_path": "/a/1.jpg", "content_hash": "abc123", "_processor": "P1"},
            {"source_path": "/b/1.jpg", "content_hash": "abc123", "_processor": "P2"},
            {"source_path": "/a/2.jpg", "content_hash": "def456", "_processor": "P1"},
        ]
        temp_db.write_catalog_entries_batch("run_001", "export1", entries)

        matching = temp_db.get_entries_by_hash("run_001", "abc123")
        assert len(matching) == 2
        assert all(e["content_hash"] == "abc123" for e in matching)

    def test_iter_all_entries(self, temp_db):
        """Test iterating over all entries."""
        entries = [
            {
                "source_path": f"/path/file{i}.jpg",
                "content_hash": f"hash{i}",
                "_processor": "P",
            }
            for i in range(10)
        ]
        temp_db.write_catalog_entries_batch("run_001", "export1", entries)

        all_entries = list(temp_db.iter_all_entries("run_001", batch_size=3))
        assert len(all_entries) == 10

    def test_iter_entries_with_perceptual_hash(self, temp_db):
        """Test iterating over entries with perceptual hashes."""
        entries = [
            {
                "source_path": "/1.jpg",
                "content_hash": "h1",
                "perceptual_hash": "p1",
                "_processor": "P",
            },
            {
                "source_path": "/2.jpg",
                "content_hash": "h2",
                "perceptual_hash": None,
                "_processor": "P",
            },
            {
                "source_path": "/3.jpg",
                "content_hash": "h3",
                "perceptual_hash": "p3",
                "_processor": "P",
            },
        ]
        temp_db.write_catalog_entries_batch("run_001", "export1", entries)

        with_phash = list(temp_db.iter_entries_with_perceptual_hash("run_001"))
        assert len(with_phash) == 2

    def test_iter_entries_with_exclusions(self, temp_db):
        """Test iterating with content hash exclusions."""
        entries = [
            {
                "source_path": "/1.jpg",
                "content_hash": "h1",
                "perceptual_hash": "p1",
                "_processor": "P",
            },
            {
                "source_path": "/2.jpg",
                "content_hash": "h2",
                "perceptual_hash": "p2",
                "_processor": "P",
            },
            {
                "source_path": "/3.jpg",
                "content_hash": "h3",
                "perceptual_hash": "p3",
                "_processor": "P",
            },
        ]
        temp_db.write_catalog_entries_batch("run_001", "export1", entries)

        # Exclude h1 and h2
        with_phash = list(
            temp_db.iter_entries_with_perceptual_hash(
                "run_001", exclude_hashes={"h1", "h2"}
            )
        )
        assert len(with_phash) == 1
        assert with_phash[0]["content_hash"] == "h3"

    def test_get_run_statistics(self, temp_db):
        """Test getting statistics for a run."""
        entries = [
            {"source_path": "/a/1.jpg", "content_hash": "h1", "_processor": "P1"},
            {"source_path": "/b/1.jpg", "content_hash": "h1", "_processor": "P2"},
            {
                "source_path": "/a/2.jpg",
                "content_hash": "h2",
                "perceptual_hash": "ph",
                "_processor": "P1",
            },
        ]
        temp_db.write_catalog_entries_batch("run_001", "export1", entries)

        stats = temp_db.get_run_statistics("run_001")
        assert stats["run_id"] == "run_001"
        assert stats["total_files"] == 3
        assert stats["unique_content_hashes"] == 2
        assert stats["duplicate_groups"] == 1
        assert stats["files_with_perceptual_hash"] == 1
        assert set(stats["processors"]) == {"P1", "P2"}

    def test_clear_run(self, temp_db):
        """Test clearing entries for a specific run."""
        temp_db.write_catalog_entries_batch(
            "run_001",
            "export1",
            [{"source_path": "/1.jpg", "content_hash": "h1", "_processor": "P"}],
        )
        temp_db.write_catalog_entries_batch(
            "run_002",
            "export2",
            [{"source_path": "/2.jpg", "content_hash": "h2", "_processor": "P"}],
        )

        deleted = temp_db.clear_run("run_001")
        assert deleted == 1

        # run_002 should still exist
        stats = temp_db.get_run_statistics("run_002")
        assert stats["total_files"] == 1

        # run_001 should be empty
        stats = temp_db.get_run_statistics("run_001")
        assert stats["total_files"] == 0

    def test_isolation_between_runs(self, temp_db):
        """Test that different runs are isolated."""
        temp_db.write_catalog_entries_batch(
            "run_A",
            "export",
            [{"source_path": "/a.jpg", "content_hash": "h1", "_processor": "P"}],
        )
        temp_db.write_catalog_entries_batch(
            "run_B",
            "export",
            [{"source_path": "/b.jpg", "content_hash": "h1", "_processor": "P"}],
        )

        # Each run should only see its own entries
        entries_A = list(temp_db.iter_all_entries("run_A"))
        entries_B = list(temp_db.iter_all_entries("run_B"))

        assert len(entries_A) == 1
        assert len(entries_B) == 1
        assert entries_A[0]["source_path"] == "/a.jpg"
        assert entries_B[0]["source_path"] == "/b.jpg"


class TestHashCache:
    """Tests for hash caching functionality."""

    def test_cache_and_retrieve_hashes(self, temp_db):
        """Test basic cache storage and retrieval."""
        # Cache a file's hashes
        result = temp_db.cache_hashes(
            source_path="/path/to/file.jpg",
            file_size=1000,
            mtime=1234567890.123,
            content_hash="abc123def456",
            perceptual_hash="fff00000",
            dimensions=(1920, 1080),
        )
        assert result is True

        # Retrieve with matching file stats
        cached = temp_db.get_cached_hashes(
            source_path="/path/to/file.jpg",
            file_size=1000,
            mtime=1234567890.123,
        )
        assert cached is not None
        assert cached["content_hash"] == "abc123def456"
        assert cached["perceptual_hash"] == "fff00000"
        assert cached["dimensions"] == [1920, 1080]

    def test_cache_miss_on_size_change(self, temp_db):
        """Test cache miss when file size changes."""
        temp_db.cache_hashes(
            source_path="/path/to/file.jpg",
            file_size=1000,
            mtime=1234567890.123,
            content_hash="abc123",
        )

        # Different file size should not match
        cached = temp_db.get_cached_hashes(
            source_path="/path/to/file.jpg",
            file_size=2000,  # Changed
            mtime=1234567890.123,
        )
        assert cached is None

    def test_cache_miss_on_mtime_change(self, temp_db):
        """Test cache miss when file mtime changes."""
        temp_db.cache_hashes(
            source_path="/path/to/file.jpg",
            file_size=1000,
            mtime=1234567890.123,
            content_hash="abc123",
        )

        # Different mtime should not match
        cached = temp_db.get_cached_hashes(
            source_path="/path/to/file.jpg",
            file_size=1000,
            mtime=1234567899.999,  # Changed
        )
        assert cached is None

    def test_cache_miss_on_unknown_path(self, temp_db):
        """Test cache miss for unknown file path."""
        cached = temp_db.get_cached_hashes(
            source_path="/unknown/file.jpg",
            file_size=1000,
            mtime=1234567890.123,
        )
        assert cached is None

    def test_cache_update_on_file_change(self, temp_db):
        """Test that cache is updated when file changes."""
        # Initial cache
        temp_db.cache_hashes(
            source_path="/path/to/file.jpg",
            file_size=1000,
            mtime=1234567890.0,
            content_hash="old_hash",
        )

        # File was modified - cache with new stats
        temp_db.cache_hashes(
            source_path="/path/to/file.jpg",
            file_size=2000,
            mtime=1234567999.0,
            content_hash="new_hash",
        )

        # Old stats should not match
        cached_old = temp_db.get_cached_hashes(
            source_path="/path/to/file.jpg",
            file_size=1000,
            mtime=1234567890.0,
        )
        assert cached_old is None

        # New stats should match
        cached_new = temp_db.get_cached_hashes(
            source_path="/path/to/file.jpg",
            file_size=2000,
            mtime=1234567999.0,
        )
        assert cached_new is not None
        assert cached_new["content_hash"] == "new_hash"

    def test_cache_without_perceptual_hash(self, temp_db):
        """Test caching files without perceptual hash."""
        temp_db.cache_hashes(
            source_path="/path/to/video.mp4",
            file_size=50000,
            mtime=1234567890.0,
            content_hash="video_hash",
            perceptual_hash=None,
            dimensions=None,
        )

        cached = temp_db.get_cached_hashes(
            source_path="/path/to/video.mp4",
            file_size=50000,
            mtime=1234567890.0,
        )
        assert cached is not None
        assert cached["content_hash"] == "video_hash"
        assert cached["perceptual_hash"] is None
        assert cached["dimensions"] is None

    def test_cache_hashes_batch(self, temp_db):
        """Test batch caching of multiple files."""
        entries = [
            {
                "source_path": f"/path/file{i}.jpg",
                "file_size": 1000 + i,
                "mtime": 1234567890.0 + i,
                "content_hash": f"hash{i}",
                "perceptual_hash": f"phash{i}",
                "dimensions": (100 + i, 200 + i),
            }
            for i in range(5)
        ]

        cached_count = temp_db.cache_hashes_batch(entries)
        assert cached_count == 5

        # Verify each entry
        for i, entry in enumerate(entries):
            cached = temp_db.get_cached_hashes(
                source_path=entry["source_path"],
                file_size=entry["file_size"],
                mtime=entry["mtime"],
            )
            assert cached is not None
            assert cached["content_hash"] == f"hash{i}"

    def test_get_hash_cache_statistics(self, temp_db):
        """Test getting cache statistics."""
        # Add some cached entries
        temp_db.cache_hashes(
            source_path="/1.jpg",
            file_size=1000,
            mtime=1234567890.0,
            content_hash="h1",
            perceptual_hash="p1",
        )
        temp_db.cache_hashes(
            source_path="/2.jpg",
            file_size=2000,
            mtime=1234567890.0,
            content_hash="h2",
            perceptual_hash=None,
        )
        temp_db.cache_hashes(
            source_path="/3.jpg",
            file_size=3000,
            mtime=1234567890.0,
            content_hash="h3",
            perceptual_hash="p3",
        )

        stats = temp_db.get_hash_cache_statistics()
        assert stats["total_entries"] == 3
        assert stats["with_perceptual_hash"] == 2

    def test_clear_hash_cache(self, temp_db):
        """Test clearing the hash cache."""
        temp_db.cache_hashes(
            source_path="/1.jpg",
            file_size=1000,
            mtime=1234567890.0,
            content_hash="h1",
        )
        temp_db.cache_hashes(
            source_path="/2.jpg",
            file_size=2000,
            mtime=1234567890.0,
            content_hash="h2",
        )

        deleted = temp_db.clear_hash_cache()
        assert deleted == 2

        stats = temp_db.get_hash_cache_statistics()
        assert stats["total_entries"] == 0

    def test_prune_hash_cache_with_valid_paths(self, temp_db):
        """Test pruning cache with a set of valid paths."""
        temp_db.cache_hashes(
            source_path="/keep/file1.jpg",
            file_size=1000,
            mtime=1234567890.0,
            content_hash="h1",
        )
        temp_db.cache_hashes(
            source_path="/keep/file2.jpg",
            file_size=2000,
            mtime=1234567890.0,
            content_hash="h2",
        )
        temp_db.cache_hashes(
            source_path="/remove/file3.jpg",
            file_size=3000,
            mtime=1234567890.0,
            content_hash="h3",
        )

        # Only keep files in /keep/
        valid_paths = {"/keep/file1.jpg", "/keep/file2.jpg"}
        removed = temp_db.prune_hash_cache(valid_paths=valid_paths)
        assert removed == 1

        stats = temp_db.get_hash_cache_statistics()
        assert stats["total_entries"] == 2
