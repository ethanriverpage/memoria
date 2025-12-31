#!/usr/bin/env python3
"""Tests for cross-processor dedup merged description building and wiring.

This test module verifies that when files appear in multiple sources (cross-processor
duplicates), the merged description containing provenance from all sources is:
1. Correctly built by the description builder
2. Actually written to output files via batch_write_metadata_requests

BUG REFERENCE: docs/bugs/dedup-merged-description-not-wired.md
The bug is that batch_write_metadata_requests() is called without the dedup_manifest
parameter in processors/base.py, so merged descriptions are never applied.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from common.dedup.config import DedupConfig, reset_config
from common.dedup.database import HashDatabase
from common.dedup.description import (
    PLATFORM_GOOGLE,
    PLATFORM_IMESSAGE,
    PLATFORM_INSTAGRAM,
    PLATFORM_SNAPCHAT,
    SERVICE_MESSAGES,
    SERVICE_MEMORIES,
    SERVICE_PHOTOS,
    SourceEntry,
    build_image_description,
    format_secondary_source,
)
from common.dedup.service import (
    DeduplicationService,
    get_merged_description_for_file,
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
def reset_dedup_config_fixture():
    """Reset dedup config before and after each test."""
    reset_config()
    yield
    reset_config()


@pytest.fixture
def cross_processor_catalogs() -> List[Dict[str, Any]]:
    """Create mock catalogs simulating cross-processor duplicates.

    This fixture represents a common scenario where the same photo appears in:
    - Google Photos (primary - earliest timestamp)
    - iMessage (sent later via multiple devices)
    - Instagram Messages (shared in a conversation)
    """
    return [
        {
            "processor": "Google Photos",
            "input_dir": "/exports/google",
            "files": [
                {
                    "source_path": "/exports/google/IMG_5895.heic",
                    "relative_path": "IMG_5895.heic",
                    "content_hash": "00183fbb4f4f1212",
                    "perceptual_hash": "ffffffff00000000",
                    "file_size": 2500000,
                    "dimensions": (4032, 3024),
                    "timestamp": "2024-01-15T10:30:00Z",
                    "metadata": {
                        "username": "user@gmail.com",
                        "service": SERVICE_PHOTOS,
                        "albums": ["Vacation 2024", "Favorites"],
                        "people": ["Alice", "Bob"],
                        "gps": {"latitude": 40.7128, "longitude": -74.0060},
                    },
                },
            ],
        },
        {
            "processor": "iMessage",
            "input_dir": "/exports/imessage",
            "files": [
                {
                    "source_path": "/exports/imessage/iph13p/attachment_001.heic",
                    "relative_path": "iph13p/attachment_001.heic",
                    "content_hash": "00183fbb4f4f1212",  # Same hash - duplicate
                    "perceptual_hash": "ffffffff00000000",
                    "file_size": 2500000,
                    "dimensions": (4032, 3024),
                    "timestamp": "2024-01-20T15:45:00Z",  # Sent later
                    "metadata": {
                        "username": "iph13p",
                        "sender": "Alice",
                        "conversation": "Family Group",
                        "content": "Check out this amazing view!",
                    },
                },
                {
                    "source_path": "/exports/imessage/m3_mbp/attachment_042.heic",
                    "relative_path": "m3_mbp/attachment_042.heic",
                    "content_hash": "00183fbb4f4f1212",  # Same hash - duplicate
                    "perceptual_hash": "ffffffff00000000",
                    "file_size": 2500000,
                    "dimensions": (4032, 3024),
                    "timestamp": "2024-01-20T15:46:00Z",  # Slightly later (sync)
                    "metadata": {
                        "username": "m3_mbp",
                        "sender": "Alice",
                        "conversation": "Family Group",
                        "content": "Check out this amazing view!",
                    },
                },
            ],
        },
        {
            "processor": "Instagram Messages",
            "input_dir": "/exports/instagram",
            "files": [
                {
                    "source_path": "/exports/instagram/messages/photo_123.jpg",
                    "relative_path": "messages/photo_123.jpg",
                    "content_hash": "00183fbb4f4f1212",  # Same hash - duplicate
                    "perceptual_hash": "ffffffff00000000",
                    "file_size": 2400000,  # Slightly smaller (compressed)
                    "dimensions": (4032, 3024),
                    "timestamp": "2024-01-25T09:00:00Z",
                    "metadata": {
                        "username": "myinsta",
                        "service": SERVICE_MESSAGES,
                        "sender": "bestfriend",
                        "conversation": "Travel Buddies",
                        "content": "Remember this trip?",
                    },
                },
            ],
        },
    ]


@pytest.fixture
def dedup_service(tmp_path) -> DeduplicationService:
    """Create a deduplication service with in-memory database."""
    db_path = tmp_path / "dedup.db"
    config = DedupConfig(
        enabled=True,
        use_perceptual=False,  # Only exact hash for these tests
        database_path=db_path,
    )
    db = HashDatabase(db_path)
    return DeduplicationService(db=db, config=config)


class TestCrossProcessorDescriptionBuilding:
    """Tests for building merged descriptions from cross-processor duplicates."""

    def test_manifest_identifies_cross_processor_duplicates(
        self, dedup_service, cross_processor_catalogs
    ):
        """Test that the manifest correctly identifies duplicates across processors."""
        run_id = "test_cross_processor_duplicates"
        write_catalogs_to_db(dedup_service.db, run_id, cross_processor_catalogs)
        manifest = dedup_service.analyze_from_db(run_id)

        # Verify duplicate was found
        assert "00183fbb4f4f1212" in manifest["duplicates"]

        dup_group = manifest["duplicates"]["00183fbb4f4f1212"]

        # Verify primary is Google Photos (earliest timestamp)
        assert dup_group["primary"]["processor"] == "Google Photos"
        assert dup_group["primary"]["source_path"] == "/exports/google/IMG_5895.heic"

        # Verify all secondary sources are captured
        secondary_processors = {s["processor"] for s in dup_group["secondary"]}
        assert "iMessage" in secondary_processors
        assert "Instagram Messages" in secondary_processors

        # Should have 3 secondary entries (2 iMessage + 1 Instagram)
        assert len(dup_group["secondary"]) == 3

    def test_get_merged_description_includes_all_sources(
        self, dedup_service, cross_processor_catalogs
    ):
        """Test that get_merged_description_for_file includes all source processors."""
        run_id = "test_merged_desc_all_sources"
        write_catalogs_to_db(dedup_service.db, run_id, cross_processor_catalogs)
        manifest = dedup_service.analyze_from_db(run_id)

        # Get merged description for primary file
        primary_path = "/exports/google/IMG_5895.heic"
        description = get_merged_description_for_file(manifest, primary_path)

        assert description is not None

        # Primary source should be present
        assert "Google Photos" in description

        # Secondary sources should be mentioned with + prefix
        assert "+iMessage" in description or "+Instagram" in description

    def test_merged_description_format_matches_spec(
        self, dedup_service, cross_processor_catalogs
    ):
        """Test that merged description follows the format specification.

        Expected format:
            Source: Google Photos
            +iMessage/iph13p: Alice in "Family Group": "Check out this..."
            +iMessage/m3_mbp: ...
            +Instagram Messages/myinsta/messages: ...
        """
        run_id = "test_desc_format"
        write_catalogs_to_db(dedup_service.db, run_id, cross_processor_catalogs)
        manifest = dedup_service.analyze_from_db(run_id)
        primary_path = "/exports/google/IMG_5895.heic"
        description = get_merged_description_for_file(manifest, primary_path)

        assert description is not None
        lines = description.split("\n")

        # First line should be the primary source
        assert lines[0].startswith("Source:")

        # Remaining lines should be secondary sources with + prefix
        for line in lines[1:]:
            if line.strip():
                assert line.startswith("+"), f"Secondary line should start with +: {line}"

    def test_secondary_source_includes_context(
        self, dedup_service, cross_processor_catalogs
    ):
        """Test that secondary source entries include message context."""
        run_id = "test_secondary_context"
        write_catalogs_to_db(dedup_service.db, run_id, cross_processor_catalogs)
        manifest = dedup_service.analyze_from_db(run_id)

        # Get the duplicate group
        dup_group = manifest["duplicates"]["00183fbb4f4f1212"]

        # Find the iMessage secondary with context
        imessage_secondary = None
        for secondary in dup_group["secondary"]:
            if secondary["processor"] == "iMessage":
                imessage_secondary = secondary
                break

        assert imessage_secondary is not None
        context = imessage_secondary.get("context", {})

        # Context should include message metadata
        assert context.get("sender") == "Alice"
        assert context.get("conversation") == "Family Group"

    def test_secondary_description_returns_none_for_non_primary(
        self, dedup_service, cross_processor_catalogs
    ):
        """Test that get_merged_description_for_file returns None for secondary files."""
        run_id = "test_secondary_returns_none"
        write_catalogs_to_db(dedup_service.db, run_id, cross_processor_catalogs)
        manifest = dedup_service.analyze_from_db(run_id)

        # Try to get description for a secondary file
        secondary_path = "/exports/imessage/iph13p/attachment_001.heic"
        description = get_merged_description_for_file(manifest, secondary_path)

        # Should return None for secondary files
        assert description is None


class TestDescriptionBuilderIntegration:
    """Tests for the description builder functions with cross-processor data."""

    def test_build_image_description_with_multiple_secondaries(self):
        """Test building description with multiple secondary sources."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user@gmail.com",
            service=SERVICE_PHOTOS,
            is_primary=True,
        )

        secondary = [
            SourceEntry(
                processor=PLATFORM_IMESSAGE,
                username="iph13p",
                sender="Alice",
                conversation="Family Group",
                content="Check out this view!",
                timestamp=datetime(2024, 1, 20, 15, 45),
            ),
            SourceEntry(
                processor=PLATFORM_IMESSAGE,
                username="m3_mbp",
                sender="Alice",
                conversation="Family Group",
                content="Check out this view!",
                timestamp=datetime(2024, 1, 20, 15, 46),
            ),
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="myinsta",
                service=SERVICE_MESSAGES,
                sender="bestfriend",
                conversation="Travel Buddies",
                content="Remember this trip?",
                timestamp=datetime(2024, 1, 25, 9, 0),
            ),
        ]

        result = build_image_description(primary, secondary)

        # Verify primary source line
        assert "Source: Google/user@gmail.com/photos" in result

        # Verify secondary sources
        assert "+iMessage/iph13p:" in result
        assert "+iMessage/m3_mbp:" in result
        assert "+Instagram/myinsta/messages:" in result

        # Verify context is included
        assert "Alice" in result
        assert "Family Group" in result

    def test_format_secondary_source_with_full_context(self):
        """Test formatting a secondary source with all context fields."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="iph13p",
            sender="Alice",
            conversation="Family Group",
            content="Check out this amazing view from the mountaintop!",
            timestamp=datetime(2024, 1, 20, 15, 45),
        )

        result = format_secondary_source(entry)

        # Should include all parts
        assert "+iMessage/iph13p:" in result
        assert 'Alice in "Family Group"' in result
        assert "Check out" in result  # Content (may be truncated)
        assert "(2024-01-20)" in result


class TestMergedDescriptionWiring:
    """Tests for verifying merged descriptions are wired through to exiftool.

    These tests verify the bug: that batch_write_metadata_requests receives
    the dedup_manifest parameter and uses it to override descriptions.
    """

    def test_batch_write_accepts_dedup_manifest_parameter(self):
        """Test that batch_write_metadata_requests accepts dedup_manifest."""
        from common.media.exiftool import batch_write_metadata_requests
        import inspect

        sig = inspect.signature(batch_write_metadata_requests)
        params = list(sig.parameters.keys())

        # Verify the function accepts dedup_manifest
        assert "dedup_manifest" in params, (
            "batch_write_metadata_requests should accept dedup_manifest parameter"
        )

    def test_get_description_for_file_uses_manifest(self, tmp_path):
        """Test that _get_description_for_file returns merged description when manifest provided."""
        from common.media.exiftool import _get_description_for_file

        # Create a mock manifest with a duplicate group
        manifest = {
            "duplicates": {
                "abc123": {
                    "primary": {
                        "processor": "Google Photos",
                        "source_path": str(tmp_path / "photo.jpg"),
                    },
                    "secondary": [
                        {
                            "processor": "iMessage",
                            "source_path": "/other/path.jpg",
                            "context": {
                                "sender": "Alice",
                                "conversation": "Family",
                            },
                        },
                    ],
                    "merged_metadata": {},
                },
            },
        }

        default_description = "Source: Google Photos"

        result = _get_description_for_file(default_description, manifest)

        # When manifest is provided and file is primary, should return merged description
        # This test documents the expected behavior
        assert result is not None

    def test_get_description_for_file_returns_default_without_manifest(self):
        """Test that _get_description_for_file returns default when no manifest."""
        from common.media.exiftool import _get_description_for_file

        default_description = "Source: Google Photos"
        result = _get_description_for_file(
            default_description,
            dedup_manifest=None,
        )

        assert result == default_description

    def test_get_description_for_file_uses_xml_entity_newlines(self):
        """Test that _get_description_for_file uses &#xa; (not literal newlines) to join lines.

        Regression test for cross-processor-dedup-description-not-embedded bug.
        Literal newlines break exiftool argfile format, causing secondary source
        lines to be silently dropped.
        """
        from common.media.exiftool import _get_description_for_file

        # Create a manifest where iMessage is best quality with Google Photos secondary
        manifest = {
            "duplicates": {
                "abc123": {
                    "primary": {
                        "processor": "iMessage",
                        "source_path": "/exports/imessage/photo.jpg",
                    },
                    "best_quality": {
                        "processor": "iMessage",
                        "source_path": "/exports/imessage/photo.jpg",
                    },
                    "secondary": [
                        {
                            "processor": "Google Photos",
                            "source_path": "/exports/google/photo.jpg",
                            "context": {
                                "username": "user@gmail.com",
                                "service": "photos",
                            },
                        },
                    ],
                    "merged_metadata": {},
                },
            },
        }

        default_description = "Source: iMessage/iph13p"
        result = _get_description_for_file(
            default_description,
            dedup_manifest=manifest,
            content_hash="abc123",
            processor_name="iMessage",
        )

        # Result should contain secondary source
        assert "+Google" in result

        # Must use XML entity for newline, not literal newline
        # Literal \n breaks exiftool argfile format
        assert "&#xa;" in result, (
            "Description must use &#xa; (XML entity) for newlines, not literal \\n. "
            f"Got: {repr(result)}"
        )
        assert "\n" not in result, (
            "Description must not contain literal newlines (breaks exiftool argfile). "
            f"Got: {repr(result)}"
        )

    def test_batch_process_exif_accepts_dedup_manifest_parameter(self):
        """Test that ProcessorBase.batch_process_exif accepts a dedup_manifest parameter."""
        from processors.base import ProcessorBase
        import inspect

        # Check the batch_process_exif signature
        sig = inspect.signature(ProcessorBase.batch_process_exif)
        params = list(sig.parameters.keys())

        # The method should accept a dedup_manifest parameter
        assert "dedup_manifest" in params, (
            "ProcessorBase.batch_process_exif should accept dedup_manifest parameter. "
            "Current parameters: " + ", ".join(params)
        )

    def test_batch_process_exif_passes_manifest_through(self):
        """Test that batch_process_exif actually passes the manifest to the writer.

        This test uses source inspection to verify that when batch_process_exif
        is called with a dedup_manifest, it passes that manifest to
        batch_write_metadata_requests.
        """
        from processors.base import ProcessorBase
        import inspect

        # First, verify the method signature includes dedup_manifest
        sig = inspect.signature(ProcessorBase.batch_process_exif)
        params = list(sig.parameters.keys())

        if "dedup_manifest" not in params:
            pytest.fail(
                "batch_process_exif does not accept dedup_manifest parameter. "
                f"Current parameters: {params}"
            )

        # If signature is correct, verify it's passed through by checking source code
        import processors.base as base_module
        source = inspect.getsource(base_module.ProcessorBase.batch_process_exif)

        # The source should contain a call that passes dedup_manifest
        assert "batch_write_metadata_requests" in source

        # Check that dedup_manifest is actually passed in one of the call sites
        # Split by the function name and check each occurrence after the first (import)
        parts = source.split("batch_write_metadata_requests")
        # At least one occurrence should have dedup_manifest in the next 100 chars
        found_manifest_in_call = any(
            "dedup_manifest" in part[:100] for part in parts[1:]
        )
        assert found_manifest_in_call, (
            "batch_write_metadata_requests is called without passing dedup_manifest"
        )

        # TODO: When bug is fixed, add actual file metadata verification:
        # result = subprocess.run(
        #     ["exiftool", "-ImageDescription", str(test_file)],
        #     capture_output=True, text=True
        # )
        # assert "+iMessage" in result.stdout


class TestMultiProcessorDescriptionCoverage:
    """Tests ensuring all 11 processors can generate merged descriptions."""

    @pytest.mark.parametrize(
        "processor,username,service",
        [
            (PLATFORM_GOOGLE, "user@gmail.com", SERVICE_PHOTOS),
            (PLATFORM_GOOGLE, "user@gmail.com", "chat"),
            (PLATFORM_GOOGLE, "user@gmail.com", "voice"),
            (PLATFORM_IMESSAGE, "iph13p", ""),
            (PLATFORM_IMESSAGE, "m3_mbp", ""),
            (PLATFORM_INSTAGRAM, "myinsta", SERVICE_MESSAGES),
            (PLATFORM_INSTAGRAM, "myinsta", "posts"),
            (PLATFORM_SNAPCHAT, "snapuser", SERVICE_MEMORIES),
            (PLATFORM_SNAPCHAT, "snapuser", SERVICE_MESSAGES),
            ("Discord", "discorduser", ""),
        ],
    )
    def test_secondary_source_format_for_all_processors(
        self, processor, username, service
    ):
        """Test that secondary source format works for all processor types."""
        entry = SourceEntry(
            processor=processor,
            username=username,
            service=service,
            sender="TestSender",
            conversation="TestConversation",
            content="Test message content",
            timestamp=datetime(2024, 1, 15),
        )

        result = format_secondary_source(entry)

        # Should start with + and processor name
        assert result.startswith(f"+{processor}")

        # Should include username if provided
        if username:
            assert username in result

        # Should include some context
        assert "TestSender" in result or "TestConversation" in result


class TestDescriptionContentPreservation:
    """Tests ensuring message content is preserved in descriptions."""

    def test_message_content_included_in_secondary(self):
        """Test that message content from secondary sources is included."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="iph13p",
            sender="Alice",
            conversation="Family Group",
            content="Everyone needs to see this beautiful sunset!",
            timestamp=datetime(2024, 1, 20),
        )

        result = format_secondary_source(entry)

        # Content should be included (possibly truncated)
        assert "sunset" in result.lower() or "everyone" in result.lower() or "beautiful" in result.lower()

    def test_long_content_truncated_appropriately(self):
        """Test that very long content is truncated but meaningful."""
        long_content = "A" * 500  # Very long message

        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="iph13p",
            content=long_content,
        )

        result = format_secondary_source(entry, max_chars=100)

        # Result should be truncated
        assert len(result) <= 100

        # Should end with truncation indicator
        assert "..." in result

    def test_multiline_content_normalized(self):
        """Test that multiline content is normalized to single line."""
        multiline_content = "Line 1\nLine 2\nLine 3"

        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="iph13p",
            content=multiline_content,
        )

        result = format_secondary_source(entry)

        # Should not contain newlines
        assert "\n" not in result

        # Content should be space-separated
        assert "Line 1" in result

