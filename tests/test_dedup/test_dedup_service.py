#!/usr/bin/env python3
"""Unit tests for DeduplicationService."""

import pytest

from common.dedup.config import DedupConfig
from common.dedup.service import (
    DeduplicationService,
    filter_primary_files,
    is_file_primary_by_hash,
    get_merged_description_for_file,
    get_merged_metadata_for_hash,
    get_secondary_reference,
    build_dedup_references,
)
from common.dedup.database import HashDatabase


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


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database for testing.

    Uses pytest's tmp_path fixture for automatic cleanup.
    """
    db_path = tmp_path / "test_dedup.db"
    db = HashDatabase(db_path)
    return db


@pytest.fixture
def config():
    """Create a test configuration."""
    return DedupConfig(
        enabled=True,
        use_perceptual=False,  # Disable for unit tests
        perceptual_threshold=0.92,
    )


@pytest.fixture
def service(temp_db, config):
    """Create a DeduplicationService instance."""
    return DeduplicationService(db=temp_db, config=config)


class TestDeduplicationService:
    """Tests for DeduplicationService class."""

    def test_analyze_empty_catalogs(self, service, temp_db):
        """Test analyzing empty catalog list."""
        run_id = "test_empty"
        write_catalogs_to_db(temp_db, run_id, [])
        manifest = service.analyze_from_db(run_id)
        assert manifest["version"] == "1.0"
        assert manifest["statistics"]["total_files_scanned"] == 0
        assert manifest["duplicates"] == {}

    def test_analyze_single_file(self, service, temp_db):
        """Test analyzing a single file."""
        catalog = {
            "processor": "Google Photos",
            "input_dir": "/path/to/export",
            "files": [
                {
                    "source_path": "/path/file.jpg",
                    "content_hash": "abc123",
                    "file_size": 1000,
                }
            ],
        }
        run_id = "test_single"
        write_catalogs_to_db(temp_db, run_id, [catalog])
        manifest = service.analyze_from_db(run_id)

        assert manifest["statistics"]["total_files_scanned"] == 1
        assert manifest["statistics"]["unique_files"] == 1
        assert manifest["statistics"]["duplicates_found"] == 0

    def test_analyze_finds_exact_duplicates(self, service, temp_db):
        """Test that exact hash duplicates are found."""
        catalog1 = {
            "processor": "Google Photos",
            "input_dir": "/path/google",
            "files": [
                {
                    "source_path": "/path/google/file.jpg",
                    "content_hash": "abc123",
                    "file_size": 1000,
                    "metadata": {"timestamp": "2024-01-01T10:00:00Z"},
                }
            ],
        }
        catalog2 = {
            "processor": "iMessage",
            "input_dir": "/path/imessage",
            "files": [
                {
                    "source_path": "/path/imessage/file.jpg",
                    "content_hash": "abc123",  # Same hash
                    "file_size": 1000,
                    "metadata": {"timestamp": "2024-01-02T10:00:00Z"},
                }
            ],
        }

        run_id = "test_duplicates"
        write_catalogs_to_db(temp_db, run_id, [catalog1, catalog2])
        manifest = service.analyze_from_db(run_id)

        assert manifest["statistics"]["total_files_scanned"] == 2
        assert manifest["statistics"]["duplicates_found"] == 1
        assert "abc123" in manifest["duplicates"]

    def test_select_best_file_by_size(self, service):
        """Test that largest file is selected as best."""
        candidates = [
            {"source_path": "/a.jpg", "file_size": 500, "_processor": "A"},
            {"source_path": "/b.jpg", "file_size": 1000, "_processor": "B"},
            {"source_path": "/c.jpg", "file_size": 750, "_processor": "C"},
        ]

        best = service._select_best_file(candidates)
        assert best["file_size"] == 1000

    def test_select_best_file_by_dimensions(self, service):
        """Test that highest resolution is preferred when sizes equal."""
        candidates = [
            {
                "source_path": "/a.jpg",
                "file_size": 1000,
                "dimensions": (800, 600),
                "_processor": "A",
            },
            {
                "source_path": "/b.jpg",
                "file_size": 1000,
                "dimensions": (1920, 1080),
                "_processor": "B",
            },
        ]

        best = service._select_best_file(candidates)
        assert best["dimensions"] == (1920, 1080)

    def test_determine_primary_by_timestamp(self, service):
        """Test that earliest timestamp determines primary."""
        candidates = [
            {
                "source_path": "/new.jpg",
                "timestamp": "2024-06-01T10:00:00Z",
                "_processor": "A",
            },
            {
                "source_path": "/old.jpg",
                "timestamp": "2024-01-01T10:00:00Z",
                "_processor": "B",
            },
        ]

        primary = service._determine_primary_source(candidates)
        assert primary["source_path"] == "/old.jpg"

    def test_merge_metadata_timestamps(self, service):
        """Test that earliest timestamp is used in merged metadata."""
        candidates = [
            {
                "source_path": "/a.jpg",
                "timestamp": "2024-06-01T10:00:00+00:00",
                "_processor": "A",
                "metadata": {},
            },
            {
                "source_path": "/b.jpg",
                "timestamp": "2024-01-01T10:00:00+00:00",
                "_processor": "B",
                "metadata": {},
            },
        ]

        merged = service._merge_metadata(candidates)
        assert "2024-01-01" in merged["timestamp"]

    def test_merge_metadata_gps(self, service):
        """Test that first non-zero GPS is used."""
        candidates = [
            {
                "source_path": "/a.jpg",
                "_processor": "A",
                "metadata": {"gps": {"latitude": 0.0, "longitude": 0.0}},
            },
            {
                "source_path": "/b.jpg",
                "_processor": "B",
                "metadata": {"gps": {"latitude": 40.7, "longitude": -74.0}},
            },
        ]

        merged = service._merge_metadata(candidates)
        assert merged["gps"]["latitude"] == 40.7
        assert merged["gps"]["longitude"] == -74.0

    def test_merge_metadata_people_union(self, service):
        """Test that people tags are unioned."""
        candidates = [
            {
                "source_path": "/a.jpg",
                "_processor": "A",
                "metadata": {"people": ["Alice", "Bob"]},
            },
            {
                "source_path": "/b.jpg",
                "_processor": "B",
                "metadata": {"people": ["Bob", "Charlie"]},
            },
        ]

        merged = service._merge_metadata(candidates)
        assert set(merged["people"]) == {"Alice", "Bob", "Charlie"}

    def test_merge_metadata_albums_with_attribution(self, service):
        """Test that albums are collected with source attribution."""
        candidates = [
            {
                "source_path": "/a.jpg",
                "_processor": "Google Photos",
                "metadata": {"albums": ["Vacation"]},
            },
            {
                "source_path": "/b.jpg",
                "_processor": "iMessage",
                "metadata": {"albums": ["Family"]},
            },
        ]

        merged = service._merge_metadata(candidates)
        albums = merged["albums"]
        assert len(albums) == 2

        sources = {a["source"] for a in albums}
        assert "Google Photos" in sources
        assert "iMessage" in sources

    def test_provenance_recorded(self, service, temp_db):
        """Test that provenance from all sources is recorded."""
        catalog1 = {
            "processor": "Google Photos",
            "input_dir": "/google",
            "files": [
                {
                    "source_path": "/google/a.jpg",
                    "content_hash": "hash1",
                    "file_size": 1000,
                }
            ],
        }
        catalog2 = {
            "processor": "iMessage",
            "input_dir": "/imessage",
            "files": [
                {
                    "source_path": "/imessage/a.jpg",
                    "content_hash": "hash1",
                    "file_size": 1000,
                }
            ],
        }

        run_id = "test_provenance"
        write_catalogs_to_db(temp_db, run_id, [catalog1, catalog2])
        manifest = service.analyze_from_db(run_id)

        dup = manifest["duplicates"]["hash1"]
        assert len(dup["merged_metadata"]["provenance"]) == 2


class TestBuildDuplicateEntry:
    """Tests for _build_duplicate_entry correctness."""

    def test_secondary_excludes_best_not_primary(self, service):
        """Test that secondary excludes best_quality file, not primary.

        Bug regression test: When primary (earliest timestamp) differs from
        best (highest quality), the secondary list should exclude best_quality
        since that's the file being kept. Previously, it incorrectly excluded
        primary, causing best_quality to appear in secondary.
        """
        # Scenario: 3 files with different sizes and timestamps
        # - File A: smallest (500KB), oldest (Jan 2020) -> primary by timestamp
        # - File B: largest (2MB), newest (Dec 2024) -> best by quality
        # - File C: medium (1MB), middle (Jun 2022) -> neither
        files = [
            {
                "source_path": "/old_small.jpg",
                "file_size": 500000,
                "content_hash": "same_hash",
                "timestamp": "2020-01-01T10:00:00Z",
                "_processor": "iMessage",
                "metadata": {},
            },
            {
                "source_path": "/new_large.jpg",
                "file_size": 2000000,
                "content_hash": "same_hash",
                "timestamp": "2024-12-01T10:00:00Z",
                "_processor": "Google Photos",
                "metadata": {},
            },
            {
                "source_path": "/middle_medium.jpg",
                "file_size": 1000000,
                "content_hash": "same_hash",
                "timestamp": "2022-06-15T10:00:00Z",
                "_processor": "Instagram",
                "metadata": {},
            },
        ]

        entry, dup_count, space_saved = service._build_duplicate_entry(
            files, "exact", "same_hash"
        )

        # Verify primary is the oldest file
        assert entry["primary"]["source_path"] == "/old_small.jpg"
        assert entry["primary"]["processor"] == "iMessage"

        # Verify best_quality is the largest file
        assert entry["best_quality"]["source_path"] == "/new_large.jpg"
        assert entry["best_quality"]["processor"] == "Google Photos"

        # Key assertion: secondary should NOT contain best_quality
        secondary_paths = [s["source_path"] for s in entry["secondary"]]
        assert "/new_large.jpg" not in secondary_paths, (
            "best_quality file incorrectly appears in secondary list"
        )

        # Secondary should contain primary and middle (both are not best)
        assert "/old_small.jpg" in secondary_paths
        assert "/middle_medium.jpg" in secondary_paths

        # Duplicate count should be 2 (the two secondary files)
        assert dup_count == 2

        # For exact duplicates, space_saved = best_file_size * duplicate_count
        # This is because exact duplicates are identical, so all have same size
        expected_saved = 2000000 * 2  # best_quality size * dup_count
        assert space_saved == expected_saved

    def test_secondary_when_primary_equals_best(self, service):
        """Test secondary list when primary and best_quality are same file."""
        # When primary = best, secondary should contain all other files
        files = [
            {
                "source_path": "/best_and_oldest.jpg",
                "file_size": 2000000,  # Largest
                "content_hash": "same_hash",
                "timestamp": "2020-01-01T10:00:00Z",  # Oldest
                "_processor": "Google Photos",
                "metadata": {},
            },
            {
                "source_path": "/smaller_newer.jpg",
                "file_size": 500000,
                "content_hash": "same_hash",
                "timestamp": "2024-12-01T10:00:00Z",
                "_processor": "iMessage",
                "metadata": {},
            },
        ]

        entry, dup_count, space_saved = service._build_duplicate_entry(
            files, "exact", "same_hash"
        )

        # Primary and best should be the same
        assert entry["primary"]["source_path"] == "/best_and_oldest.jpg"
        assert entry["best_quality"]["source_path"] == "/best_and_oldest.jpg"

        # Secondary should only contain the other file
        secondary_paths = [s["source_path"] for s in entry["secondary"]]
        assert secondary_paths == ["/smaller_newer.jpg"]
        assert dup_count == 1

    def test_perceptual_space_saved_sums_secondary_sizes(self, service):
        """Test that perceptual duplicates sum actual secondary file sizes.

        For perceptual duplicates, files have different sizes (re-encoded),
        so space_saved should be the sum of actual secondary file sizes,
        not a multiplication like exact duplicates.
        """
        # Scenario: 3 perceptually similar files with different sizes
        files = [
            {
                "source_path": "/original.jpg",
                "file_size": 2000000,  # 2MB - largest (best)
                "content_hash": "hash_a",  # Different hashes (perceptual match)
                "timestamp": "2024-01-01T10:00:00Z",
                "_processor": "Google Photos",
                "metadata": {},
            },
            {
                "source_path": "/reencoded_1.jpg",
                "file_size": 800000,  # 800KB - re-encoded version
                "content_hash": "hash_b",
                "timestamp": "2020-01-01T10:00:00Z",  # Oldest = primary
                "_processor": "iMessage",
                "metadata": {},
            },
            {
                "source_path": "/reencoded_2.jpg",
                "file_size": 600000,  # 600KB - another re-encoded version
                "content_hash": "hash_c",
                "timestamp": "2022-06-15T10:00:00Z",
                "_processor": "Instagram",
                "metadata": {},
            },
        ]

        entry, dup_count, space_saved = service._build_duplicate_entry(
            files,
            "perceptual",  # No content_hash for perceptual
        )

        # Best should be largest file
        assert entry["best_quality"]["source_path"] == "/original.jpg"

        # Primary should be oldest file
        assert entry["primary"]["source_path"] == "/reencoded_1.jpg"

        # Secondary should NOT contain best_quality
        secondary_paths = [s["source_path"] for s in entry["secondary"]]
        assert "/original.jpg" not in secondary_paths
        assert "/reencoded_1.jpg" in secondary_paths
        assert "/reencoded_2.jpg" in secondary_paths

        # For perceptual duplicates, space_saved = sum of secondary file sizes
        expected_saved = 800000 + 600000  # Not best_quality's size
        assert space_saved == expected_saved


class TestAnalyzeFromDb:
    """Tests for analyze_from_db() streaming analysis method."""

    def test_analyze_from_db_empty_run(self, service, temp_db):
        """Test analyzing an empty run."""
        manifest = service.analyze_from_db("empty_run")

        assert manifest["version"] == "1.0"
        assert manifest["statistics"]["total_files_scanned"] == 0
        assert manifest["duplicates"] == {}

    def test_analyze_from_db_finds_duplicates(self, service, temp_db):
        """Test that duplicates are found when analyzing from DB."""
        # Write entries with duplicate hashes
        entries = [
            {
                "source_path": "/google/photo.jpg",
                "content_hash": "abc123",
                "file_size": 1000,
                "_processor": "Google Photos",
                "metadata": {"timestamp": "2024-01-01T10:00:00Z"},
            },
            {
                "source_path": "/imessage/photo.jpg",
                "content_hash": "abc123",
                "file_size": 1000,
                "_processor": "iMessage",
                "metadata": {"timestamp": "2024-01-02T10:00:00Z"},
            },
        ]
        temp_db.write_catalog_entries_batch("run_001", "export1", entries)

        manifest = service.analyze_from_db("run_001")

        assert manifest["statistics"]["total_files_scanned"] == 2
        assert manifest["statistics"]["duplicates_found"] == 1
        assert "abc123" in manifest["duplicates"]

    def test_analyze_from_db_unique_files(self, service, temp_db):
        """Test that unique files are counted correctly."""
        entries = [
            {
                "source_path": "/1.jpg",
                "content_hash": "h1",
                "_processor": "P",
                "file_size": 100,
            },
            {
                "source_path": "/2.jpg",
                "content_hash": "h2",
                "_processor": "P",
                "file_size": 100,
            },
            {
                "source_path": "/3.jpg",
                "content_hash": "h3",
                "_processor": "P",
                "file_size": 100,
            },
        ]
        temp_db.write_catalog_entries_batch("run_001", "export", entries)

        manifest = service.analyze_from_db("run_001")

        assert manifest["statistics"]["total_files_scanned"] == 3
        assert manifest["statistics"]["unique_files"] == 3
        assert manifest["statistics"]["duplicates_found"] == 0

    def test_analyze_from_db_selects_best_file(self, service, temp_db):
        """Test that best quality file is selected."""
        entries = [
            {
                "source_path": "/small.jpg",
                "content_hash": "abc123",
                "file_size": 500,
                "_processor": "P1",
            },
            {
                "source_path": "/large.jpg",
                "content_hash": "abc123",
                "file_size": 2000,
                "_processor": "P2",
            },
        ]
        temp_db.write_catalog_entries_batch("run_001", "export", entries)

        manifest = service.analyze_from_db("run_001")

        dup = manifest["duplicates"]["abc123"]
        assert dup["best_quality"]["file_size"] == 2000
        assert dup["best_quality"]["source_path"] == "/large.jpg"


class TestValidateDuplicatePair:
    """Tests for duplicate validation."""

    def test_validate_gps_within_threshold(self, service):
        """Test GPS validation passes within threshold."""
        file1 = {"metadata": {"gps": {"latitude": 40.7128, "longitude": -74.0060}}}
        file2 = {"metadata": {"gps": {"latitude": 40.7130, "longitude": -74.0062}}}

        # Should pass - very close coordinates
        assert service._validate_duplicate_pair(file1, file2) is True

    def test_validate_gps_exceeds_threshold(self, service):
        """Test GPS validation fails when distance exceeds threshold."""
        file1 = {"metadata": {"gps": {"latitude": 40.7128, "longitude": -74.0060}}}
        file2 = {"metadata": {"gps": {"latitude": 41.0, "longitude": -75.0}}}

        # Should fail - coordinates too far apart
        assert service._validate_duplicate_pair(file1, file2) is False

    def test_validate_no_gps_passes(self, service):
        """Test validation passes when no GPS data available."""
        file1 = {"metadata": {}}
        file2 = {"metadata": {}}

        assert service._validate_duplicate_pair(file1, file2) is True


class TestIsFilePrimaryByHash:
    """Tests for is_file_primary_by_hash function.

    This function enables hash-based deduplication lookup, which works correctly
    even when files are preprocessed to temporary directories.

    IMPORTANT: The function uses best_quality.processor (not primary.processor)
    to determine which processor should copy the file. This ensures the highest
    quality version is always outputted. Primary attribution (earliest timestamp)
    is preserved in merged_metadata for timestamps and descriptions.
    """

    def test_best_quality_processor_returns_true(self):
        """Test that best_quality processor returns True for exact hash match."""
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
                        "processor": "iMessage",  # Earliest timestamp
                        "source_path": "/imessage/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "best_quality": {
                        "processor": "Google Photos",  # Largest file
                        "source_path": "/google/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "secondary": [
                        {
                            "processor": "Instagram",
                            "source_path": "/instagram/photo.jpg",
                            "content_hash": "abc123",
                        }
                    ],
                }
            }
        }

        # Best quality processor should return True
        assert is_file_primary_by_hash(manifest, "abc123", "Google Photos") is True
        # Primary (earliest timestamp) but not best quality should return False
        assert is_file_primary_by_hash(manifest, "abc123", "iMessage") is False

    def test_secondary_processor_returns_false(self):
        """Test that secondary processor returns False for exact hash match."""
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
                        "processor": "iMessage",
                        "source_path": "/imessage/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "best_quality": {
                        "processor": "Google Photos",
                        "source_path": "/google/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "secondary": [
                        {
                            "processor": "Instagram",
                            "source_path": "/instagram/photo.jpg",
                            "content_hash": "abc123",
                        }
                    ],
                }
            }
        }

        # Secondary processor should return False
        assert is_file_primary_by_hash(manifest, "abc123", "Instagram") is False

    def test_same_primary_and_best_quality(self):
        """Test when primary and best_quality are from same processor."""
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
                        "processor": "Google Photos",
                        "source_path": "/google/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "best_quality": {
                        "processor": "Google Photos",
                        "source_path": "/google/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "secondary": [
                        {
                            "processor": "Instagram",
                            "source_path": "/instagram/photo.jpg",
                            "content_hash": "abc123",
                        }
                    ],
                }
            }
        }

        # When primary = best_quality, that processor should return True
        assert is_file_primary_by_hash(manifest, "abc123", "Google Photos") is True
        assert is_file_primary_by_hash(manifest, "abc123", "Instagram") is False

    def test_hash_not_in_manifest(self):
        """Test that unknown hash returns True (not a duplicate)."""
        manifest = {"duplicates": {}}
        assert (
            is_file_primary_by_hash(manifest, "unknown_hash", "Any Processor") is True
        )

    def test_perceptual_hash_group_best_quality(self):
        """Test best_quality detection in perceptual hash groups."""
        manifest = {
            "duplicates": {
                "perceptual_def456": {
                    "match_type": "perceptual",
                    "primary": {
                        "processor": "iMessage",  # Earliest
                        "source_path": "/imessage/photo.jpg",
                        "content_hash": "ghi789",
                    },
                    "best_quality": {
                        "processor": "Google Photos",  # Largest
                        "source_path": "/google/photo.jpg",
                        "content_hash": "def456",
                    },
                    "secondary": [
                        {
                            "processor": "iMessage",
                            "source_path": "/imessage/photo.jpg",
                            "content_hash": "ghi789",
                        }
                    ],
                }
            }
        }

        # Best quality file's hash should return True for best quality processor
        assert is_file_primary_by_hash(manifest, "def456", "Google Photos") is True

    def test_perceptual_hash_group_secondary(self):
        """Test secondary detection in perceptual hash groups."""
        manifest = {
            "duplicates": {
                "perceptual_def456": {
                    "match_type": "perceptual",
                    "primary": {
                        "processor": "iMessage",
                        "source_path": "/imessage/photo.jpg",
                        "content_hash": "ghi789",
                    },
                    "best_quality": {
                        "processor": "Google Photos",
                        "source_path": "/google/photo.jpg",
                        "content_hash": "def456",
                    },
                    "secondary": [
                        {
                            "processor": "iMessage",
                            "source_path": "/imessage/photo.jpg",
                            "content_hash": "ghi789",
                        }
                    ],
                }
            }
        }

        # Secondary/primary file's hash (not best quality) should return False
        assert is_file_primary_by_hash(manifest, "ghi789", "iMessage") is False

    def test_path_agnostic_lookup(self):
        """Test that hash-based lookup works regardless of file path.

        This is the key test - verifying that dedup works even when the
        file has been preprocessed to a different location.
        """
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
                        "processor": "Google Photos",
                        "source_path": "/exports/google/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "best_quality": {
                        "processor": "Google Photos",
                        "source_path": "/exports/google/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "secondary": [
                        {
                            "processor": "Discord",
                            "source_path": "/exports/discord/media/photo.jpg",
                            "content_hash": "abc123",
                        }
                    ],
                }
            }
        }

        # Discord processor checking with the same content hash
        # should return False regardless of current file path
        assert is_file_primary_by_hash(manifest, "abc123", "Discord") is False

        # The file could be at /tmp/discord_xyz/media/photo.jpg now
        # but it still has the same hash and should still be detected as secondary

    def test_different_processor_same_hash_not_best_quality(self):
        """Test that processors without best quality return False."""
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
                        "processor": "Google Photos",
                        "source_path": "/google/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "best_quality": {
                        "processor": "Google Photos",
                        "source_path": "/google/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "secondary": [
                        {
                            "processor": "Instagram",
                            "source_path": "/instagram/photo.jpg",
                            "content_hash": "abc123",
                        },
                        {
                            "processor": "iMessage",
                            "source_path": "/imessage/photo.jpg",
                            "content_hash": "abc123",
                        },
                    ],
                }
            }
        }

        # All non-best-quality processors should return False
        assert is_file_primary_by_hash(manifest, "abc123", "Instagram") is False
        assert is_file_primary_by_hash(manifest, "abc123", "iMessage") is False

    def test_best_quality_wins_over_primary(self):
        """Test that best_quality processor wins even when different from primary.

        This is the key semantic change: we output the highest quality file,
        but use primary's timestamp for attribution.
        """
        # Scenario: iMessage has older file (primary by timestamp)
        # but Google Photos has higher quality version
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
                        "processor": "iMessage",  # Earliest timestamp (2018)
                        "source_path": "/imessage/photo.jpg",
                        "content_hash": "abc123",
                        "file_size": 500000,  # 500KB
                    },
                    "best_quality": {
                        "processor": "Google Photos",  # Larger file
                        "source_path": "/google/photo.jpg",
                        "content_hash": "abc123",
                        "file_size": 2000000,  # 2MB
                    },
                    "secondary": [],
                    "merged_metadata": {
                        "timestamp": "2018-03-15T10:00:00+00:00",  # From primary
                    },
                }
            }
        }

        # Google Photos (best quality) should copy
        assert is_file_primary_by_hash(manifest, "abc123", "Google Photos") is True
        # iMessage (primary but lower quality) should NOT copy
        assert is_file_primary_by_hash(manifest, "abc123", "iMessage") is False


class TestGetMergedMetadataForHash:
    """Tests for get_merged_metadata_for_hash function.

    This function retrieves merged metadata from all sources for a content hash,
    enabling the "super-image" pattern where the best quality file is outputted
    with the earliest timestamp and merged metadata from all sources.
    """

    def test_get_merged_metadata_exact_match(self):
        """Test getting merged metadata for exact hash match."""
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
                        "processor": "iMessage",
                        "source_path": "/imessage/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "best_quality": {
                        "processor": "Google Photos",
                        "source_path": "/google/photo.jpg",
                        "content_hash": "abc123",
                    },
                    "merged_metadata": {
                        "timestamp": "2018-03-15T10:00:00+00:00",
                        "gps": {"latitude": 40.7128, "longitude": -74.0060},
                        "camera_info": {"make": "Apple", "model": "iPhone 12"},
                        "people": ["Alice", "Bob"],
                    },
                }
            }
        }

        result = get_merged_metadata_for_hash(manifest, "abc123")
        assert result is not None
        assert result["timestamp"] == "2018-03-15T10:00:00+00:00"
        assert result["gps"]["latitude"] == 40.7128
        assert "Alice" in result["people"]

    def test_get_merged_metadata_perceptual_match(self):
        """Test getting merged metadata for perceptual hash group."""
        manifest = {
            "duplicates": {
                "perceptual_def456": {
                    "match_type": "perceptual",
                    "primary": {
                        "processor": "iMessage",
                        "content_hash": "ghi789",
                    },
                    "best_quality": {
                        "processor": "Google Photos",
                        "content_hash": "def456",
                    },
                    "merged_metadata": {
                        "timestamp": "2020-01-01T00:00:00+00:00",
                        "gps": {"latitude": 51.5074, "longitude": -0.1278},
                    },
                }
            }
        }

        # Should find by best_quality content hash
        result = get_merged_metadata_for_hash(manifest, "def456")
        assert result is not None
        assert result["gps"]["latitude"] == 51.5074

    def test_get_merged_metadata_not_found(self):
        """Test that unknown hash returns None."""
        manifest = {"duplicates": {}}
        result = get_merged_metadata_for_hash(manifest, "unknown_hash")
        assert result is None

    def test_get_merged_metadata_no_merged_data(self):
        """Test when group exists but has no merged_metadata."""
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {"processor": "Google Photos"},
                    "best_quality": {"processor": "Google Photos"},
                }
            }
        }

        result = get_merged_metadata_for_hash(manifest, "abc123")
        assert result is None


class TestMergedDescription:
    """Tests for get_merged_description_for_file."""

    def test_merged_description_for_primary(self):
        """Test getting merged description for a primary file."""
        manifest = {
            "duplicates": {
                "hash1": {
                    "primary": {
                        "source_path": "/google/photo.jpg",
                        "processor": "Google Photos",
                    },
                    "secondary": [
                        {
                            "source_path": "/instagram/photo.jpg",
                            "processor": "Instagram",
                            "context": {
                                "type": "Story",
                                "content": "Great photo!",
                            },
                        }
                    ],
                    "merged_metadata": {
                        "albums": [
                            {"name": "Vacation", "source": "Google Photos"},
                        ],
                    },
                }
            }
        }

        desc = get_merged_description_for_file(manifest, "/google/photo.jpg")
        assert desc is not None
        # Uses platform name (Google) not processor name (Google Photos)
        assert "Source: Google" in desc
        assert "Instagram" in desc

    def test_merged_description_for_secondary_returns_none(self):
        """Test that secondary files return None."""
        manifest = {
            "duplicates": {
                "hash1": {
                    "primary": {"source_path": "/primary.jpg", "processor": "A"},
                    "secondary": [{"source_path": "/secondary.jpg", "processor": "B"}],
                    "merged_metadata": {},
                }
            }
        }

        desc = get_merged_description_for_file(manifest, "/secondary.jpg")
        assert desc is None

    def test_merged_description_for_unknown_file(self):
        """Test that unknown files return None."""
        manifest = {"duplicates": {}}
        desc = get_merged_description_for_file(manifest, "/unknown.jpg")
        assert desc is None


class TestSecondaryReferences:
    """Tests for secondary reference functions."""

    def test_get_secondary_reference(self):
        """Test getting secondary reference for a duplicate file."""
        manifest = {
            "duplicates": {
                "hash1": {
                    "primary": {
                        "source_path": "/google/photo.jpg",
                        "processor": "Google Photos",
                    },
                    "secondary": [
                        {
                            "source_path": "/instagram/photo.jpg",
                            "processor": "Instagram",
                            "context": {"type": "Story"},
                        }
                    ],
                }
            }
        }

        ref = get_secondary_reference(manifest, "/instagram/photo.jpg")
        assert ref is not None
        assert ref["deduplicated"] is True
        assert ref["canonical_processor"] == "Google Photos"
        assert ref["canonical_location"] == "/google/photo.jpg"
        assert ref["original_filename"] == "photo.jpg"
        assert ref["original_context"] == {"type": "Story"}

    def test_get_secondary_reference_for_primary_returns_none(self):
        """Test that primary files return None."""
        manifest = {
            "duplicates": {
                "hash1": {
                    "primary": {"source_path": "/primary.jpg", "processor": "A"},
                    "secondary": [{"source_path": "/secondary.jpg", "processor": "B"}],
                }
            }
        }

        ref = get_secondary_reference(manifest, "/primary.jpg")
        assert ref is None

    def test_build_dedup_references(self):
        """Test building dedup references for multiple skipped files."""
        manifest = {
            "duplicates": {
                "hash1": {
                    "primary": {
                        "source_path": "/google/a.jpg",
                        "processor": "Google Photos",
                    },
                    "secondary": [
                        {"source_path": "/ig/a.jpg", "processor": "Instagram"}
                    ],
                },
                "hash2": {
                    "primary": {
                        "source_path": "/google/b.jpg",
                        "processor": "Google Photos",
                    },
                    "secondary": [
                        {"source_path": "/ig/b.jpg", "processor": "Instagram"}
                    ],
                },
            }
        }

        skipped = ["/ig/a.jpg", "/ig/b.jpg"]
        refs = build_dedup_references(
            manifest=manifest,
            skipped_files=skipped,
            processor_name="Instagram",
        )

        assert len(refs) == 2
        assert all(r["skipped_by_processor"] == "Instagram" for r in refs)
        assert all(r["deduplicated"] is True for r in refs)


class TestFilterPrimaryFiles:
    """Tests for filter_primary_files() utility function."""

    def test_returns_all_files_when_no_manifest(self, tmp_path):
        """Test that all files are returned when manifest is None."""
        # Create some test files
        file1 = tmp_path / "a.jpg"
        file2 = tmp_path / "b.jpg"
        file1.write_bytes(b"content1")
        file2.write_bytes(b"content2")

        files = [file1, file2]
        filtered, skipped = filter_primary_files(files, None, "Test Processor")

        assert filtered == files
        assert skipped == 0

    def test_returns_all_files_when_empty_manifest(self, tmp_path):
        """Test that all files are returned when manifest has no duplicates."""
        file1 = tmp_path / "a.jpg"
        file1.write_bytes(b"content1")

        manifest = {"duplicates": {}}
        files = [file1]
        filtered, skipped = filter_primary_files(files, manifest, "Test Processor")

        assert filtered == files
        assert skipped == 0

    def test_filters_secondary_files(self, tmp_path):
        """Test that secondary files are filtered out."""
        # Create test files with known content for hashing
        primary_file = tmp_path / "primary.jpg"
        secondary_file = tmp_path / "secondary.jpg"
        primary_file.write_bytes(b"primary content")
        secondary_file.write_bytes(b"secondary content")

        # Compute actual hashes
        from common.dedup.hashing import compute_content_hash

        # primary_hash not needed for this test, but compute to ensure different files
        _ = compute_content_hash(primary_file)
        secondary_hash = compute_content_hash(secondary_file)

        # Build manifest where secondary_file is not primary for "Test Processor"
        manifest = {
            "duplicates": {
                secondary_hash: {
                    "primary": {
                        "processor": "Other Processor",
                        "source_path": "/other/file.jpg",
                        "content_hash": secondary_hash,
                    },
                    "secondary": [
                        {
                            "processor": "Test Processor",
                            "source_path": str(secondary_file),
                            "content_hash": secondary_hash,
                        }
                    ],
                }
            }
        }

        files = [primary_file, secondary_file]
        filtered, skipped = filter_primary_files(files, manifest, "Test Processor")

        assert len(filtered) == 1
        assert primary_file in filtered
        assert secondary_file not in filtered
        assert skipped == 1

    def test_keeps_primary_files_for_processor(self, tmp_path):
        """Test that primary files for this processor are kept."""
        file1 = tmp_path / "a.jpg"
        file1.write_bytes(b"unique content abc")

        from common.dedup.hashing import compute_content_hash

        file_hash = compute_content_hash(file1)

        # This processor has the best quality file
        manifest = {
            "duplicates": {
                file_hash: {
                    "primary": {
                        "processor": "Test Processor",
                        "source_path": str(file1),
                        "content_hash": file_hash,
                    },
                    "best_quality": {
                        "processor": "Test Processor",
                        "source_path": str(file1),
                        "content_hash": file_hash,
                    },
                    "secondary": [
                        {
                            "processor": "Other Processor",
                            "source_path": "/other/file.jpg",
                            "content_hash": file_hash,
                        }
                    ],
                }
            }
        }

        files = [file1]
        filtered, skipped = filter_primary_files(files, manifest, "Test Processor")

        assert len(filtered) == 1
        assert file1 in filtered
        assert skipped == 0

    def test_handles_empty_file_list(self):
        """Test that empty file list returns empty results."""
        manifest = {"duplicates": {"hash1": {"primary": {}, "secondary": []}}}
        filtered, skipped = filter_primary_files([], manifest, "Test Processor")

        assert filtered == []
        assert skipped == 0


class TestLivePhotoVideoLinking:
    """Tests for Live Photo video component linking in deduplication."""

    def test_live_photo_video_follows_image_primary(self, tmp_path):
        """Test that Live Photo video follows its image's primary decision."""
        from common.dedup.hashing import compute_content_hash

        # Create image and video files
        image_file = tmp_path / "photo.heic"
        video_file = tmp_path / "photo.mov"
        image_file.write_bytes(b"image content")
        video_file.write_bytes(b"video content")

        image_hash = compute_content_hash(image_file)

        # Manifest where this processor has the best quality image
        manifest = {
            "duplicates": {
                image_hash: {
                    "primary": {
                        "processor": "Test Processor",
                        "source_path": str(image_file),
                        "content_hash": image_hash,
                    },
                    "best_quality": {
                        "processor": "Test Processor",
                        "source_path": str(image_file),
                        "content_hash": image_hash,
                    },
                    "secondary": [],
                }
            },
            "live_photo_videos": {
                str(video_file): image_hash,
            },
        }

        # Video should be included since its image is best quality for this processor
        files = [video_file]
        filtered, skipped = filter_primary_files(files, manifest, "Test Processor")

        assert len(filtered) == 1
        assert video_file in filtered
        assert skipped == 0

    def test_live_photo_video_skipped_when_image_not_best_quality(self, tmp_path):
        """Test that Live Photo video is skipped when its image is not best quality."""
        from common.dedup.hashing import compute_content_hash

        # Create image and video files
        image_file = tmp_path / "photo.heic"
        video_file = tmp_path / "photo.mov"
        image_file.write_bytes(b"image content 2")
        video_file.write_bytes(b"video content 2")

        image_hash = compute_content_hash(image_file)

        # Manifest where another processor has the best quality image
        manifest = {
            "duplicates": {
                image_hash: {
                    "primary": {
                        "processor": "Test Processor",  # Earliest timestamp
                        "source_path": str(image_file),
                        "content_hash": image_hash,
                    },
                    "best_quality": {
                        "processor": "Other Processor",  # Best quality is elsewhere
                        "source_path": "/other/photo.heic",
                        "content_hash": image_hash,
                    },
                    "secondary": [
                        {
                            "processor": "Test Processor",
                            "source_path": str(image_file),
                            "content_hash": image_hash,
                        }
                    ],
                }
            },
            "live_photo_videos": {
                str(video_file): image_hash,
            },
        }

        # Video should be skipped since its image is not best quality for this processor
        files = [video_file]
        filtered, skipped = filter_primary_files(files, manifest, "Test Processor")

        assert len(filtered) == 0
        assert skipped == 1

    def test_manifest_includes_live_photo_video_map(self, temp_db, config):
        """Test that analyze_from_db() includes live_photo_videos in manifest."""
        service = DeduplicationService(db=temp_db, config=config)

        catalog = {
            "processor": "Google Photos",
            "input_dir": "/path/to/export",
            "files": [
                {
                    "source_path": "/path/photo.heic",
                    "content_hash": "image_hash_123",
                    "file_size": 1000,
                    "live_photo_video": "/path/photo.mov",
                },
            ],
        }

        run_id = "test_live_photo"
        write_catalogs_to_db(temp_db, run_id, [catalog])
        manifest = service.analyze_from_db(run_id)

        assert "live_photo_videos" in manifest
        assert manifest["live_photo_videos"] == {
            "/path/photo.mov": "image_hash_123",
        }

    def test_regular_files_not_affected_by_live_photo_map(self, tmp_path):
        """Test that regular files are not affected by live_photo_videos map."""

        # Create a regular file (not a Live Photo video)
        regular_file = tmp_path / "regular.mp4"
        regular_file.write_bytes(b"regular video content")

        # Manifest with some live_photo_videos but not this file
        manifest = {
            "duplicates": {},
            "live_photo_videos": {
                "/some/other/video.mov": "some_image_hash",
            },
        }

        # Regular file should be treated as primary (not in manifest)
        files = [regular_file]
        filtered, skipped = filter_primary_files(files, manifest, "Test Processor")

        assert len(filtered) == 1
        assert regular_file in filtered
        assert skipped == 0
