#!/usr/bin/env python3
"""Unit tests for DeduplicationService."""


import pytest

from common.dedup.config import DedupConfig
from common.dedup.service import (
    DeduplicationService,
    filter_primary_files,
    is_file_primary,
    is_file_primary_by_hash,
    get_primary_for_file,
    get_merged_description_for_file,
    get_secondary_reference,
    build_dedup_references,
    get_skipped_secondary_files,
)
from common.dedup.database import HashDatabase


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

    def test_analyze_empty_catalogs(self, service):
        """Test analyzing empty catalog list."""
        manifest = service.analyze([])
        assert manifest["version"] == "1.0"
        assert manifest["statistics"]["total_files_scanned"] == 0
        assert manifest["duplicates"] == {}

    def test_analyze_single_file(self, service):
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
        manifest = service.analyze([catalog])

        assert manifest["statistics"]["total_files_scanned"] == 1
        assert manifest["statistics"]["unique_files"] == 1
        assert manifest["statistics"]["duplicates_found"] == 0

    def test_analyze_finds_exact_duplicates(self, service):
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

        manifest = service.analyze([catalog1, catalog2])

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

    def test_provenance_recorded(self, service):
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

        manifest = service.analyze([catalog1, catalog2])

        dup = manifest["duplicates"]["hash1"]
        assert len(dup["merged_metadata"]["provenance"]) == 2


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


class TestManifestHelpers:
    """Tests for manifest helper functions."""

    def test_is_file_primary_true(self):
        """Test is_file_primary returns True for primary file."""
        manifest = {
            "duplicates": {
                "hash1": {
                    "primary": {"source_path": "/primary.jpg"},
                    "secondary": [{"source_path": "/secondary.jpg"}],
                }
            }
        }

        assert is_file_primary(manifest, "/primary.jpg") is True

    def test_is_file_primary_false(self):
        """Test is_file_primary returns False for secondary file."""
        manifest = {
            "duplicates": {
                "hash1": {
                    "primary": {"source_path": "/primary.jpg"},
                    "secondary": [{"source_path": "/secondary.jpg"}],
                }
            }
        }

        assert is_file_primary(manifest, "/secondary.jpg") is False

    def test_is_file_primary_not_in_manifest(self):
        """Test is_file_primary returns True for file not in manifest."""
        manifest = {"duplicates": {}}
        assert is_file_primary(manifest, "/unknown.jpg") is True

    def test_get_primary_for_file(self):
        """Test get_primary_for_file returns correct primary path."""
        manifest = {
            "duplicates": {
                "hash1": {
                    "primary": {"source_path": "/primary.jpg"},
                    "secondary": [{"source_path": "/secondary.jpg"}],
                }
            }
        }

        assert get_primary_for_file(manifest, "/secondary.jpg") == "/primary.jpg"
        assert get_primary_for_file(manifest, "/primary.jpg") == "/primary.jpg"

    def test_get_primary_for_file_not_found(self):
        """Test get_primary_for_file returns None for unknown file."""
        manifest = {"duplicates": {}}
        assert get_primary_for_file(manifest, "/unknown.jpg") is None


class TestIsFilePrimaryByHash:
    """Tests for is_file_primary_by_hash function.

    This function enables hash-based deduplication lookup, which works correctly
    even when files are preprocessed to temporary directories.
    """

    def test_primary_by_hash_exact_match(self):
        """Test that primary processor returns True for exact hash match."""
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
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

        # Primary processor should return True
        assert is_file_primary_by_hash(manifest, "abc123", "Google Photos") is True

    def test_secondary_by_hash_exact_match(self):
        """Test that secondary processor returns False for exact hash match."""
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
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

    def test_hash_not_in_manifest(self):
        """Test that unknown hash returns True (not a duplicate)."""
        manifest = {"duplicates": {}}
        assert (
            is_file_primary_by_hash(manifest, "unknown_hash", "Any Processor") is True
        )

    def test_perceptual_hash_group_primary(self):
        """Test primary detection in perceptual hash groups."""
        manifest = {
            "duplicates": {
                "perceptual_def456": {
                    "match_type": "perceptual",
                    "primary": {
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

        # Primary file's hash should return True for primary processor
        assert is_file_primary_by_hash(manifest, "def456", "Google Photos") is True

    def test_perceptual_hash_group_secondary(self):
        """Test secondary detection in perceptual hash groups."""
        manifest = {
            "duplicates": {
                "perceptual_def456": {
                    "match_type": "perceptual",
                    "primary": {
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

        # Secondary file's hash should return False
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
                        "source_path": "/exports/google/photo.jpg",  # Original path
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

    def test_different_processor_same_hash_not_primary(self):
        """Test that a different processor with matching secondary hash returns False."""
        manifest = {
            "duplicates": {
                "abc123": {
                    "match_type": "exact",
                    "primary": {
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

        # All secondary processors should return False
        assert is_file_primary_by_hash(manifest, "abc123", "Instagram") is False
        assert is_file_primary_by_hash(manifest, "abc123", "iMessage") is False


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
        assert "Google Photos" in desc
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

    def test_get_skipped_secondary_files(self):
        """Test getting list of secondary files for a processor."""
        manifest = {
            "duplicates": {
                "hash1": {
                    "primary": {
                        "source_path": "/google/a.jpg",
                        "processor": "Google Photos",
                    },
                    "secondary": [
                        {"source_path": "/ig/a.jpg", "processor": "Instagram"},
                        {"source_path": "/imsg/a.jpg", "processor": "iMessage"},
                    ],
                },
                "hash2": {
                    "primary": {"source_path": "/imsg/b.jpg", "processor": "iMessage"},
                    "secondary": [
                        {"source_path": "/ig/b.jpg", "processor": "Instagram"}
                    ],
                },
            }
        }

        ig_skipped = get_skipped_secondary_files(manifest, "Instagram")
        assert len(ig_skipped) == 2
        assert "/ig/a.jpg" in ig_skipped
        assert "/ig/b.jpg" in ig_skipped

        imsg_skipped = get_skipped_secondary_files(manifest, "iMessage")
        assert len(imsg_skipped) == 1
        assert "/imsg/a.jpg" in imsg_skipped


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

        # This processor is the primary
        manifest = {
            "duplicates": {
                file_hash: {
                    "primary": {
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

        # Manifest where this processor owns the primary image
        manifest = {
            "duplicates": {
                image_hash: {
                    "primary": {
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

        # Video should be included since its image is primary for this processor
        files = [video_file]
        filtered, skipped = filter_primary_files(files, manifest, "Test Processor")

        assert len(filtered) == 1
        assert video_file in filtered
        assert skipped == 0

    def test_live_photo_video_skipped_when_image_secondary(self, tmp_path):
        """Test that Live Photo video is skipped when its image is secondary."""
        from common.dedup.hashing import compute_content_hash

        # Create image and video files
        image_file = tmp_path / "photo.heic"
        video_file = tmp_path / "photo.mov"
        image_file.write_bytes(b"image content 2")
        video_file.write_bytes(b"video content 2")

        image_hash = compute_content_hash(image_file)

        # Manifest where another processor owns the primary image
        manifest = {
            "duplicates": {
                image_hash: {
                    "primary": {
                        "processor": "Other Processor",
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

        # Video should be skipped since its image is not primary for this processor
        files = [video_file]
        filtered, skipped = filter_primary_files(files, manifest, "Test Processor")

        assert len(filtered) == 0
        assert skipped == 1

    def test_manifest_includes_live_photo_video_map(self, temp_db, config):
        """Test that analyze() includes live_photo_videos in manifest."""
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

        manifest = service.analyze([catalog])

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
