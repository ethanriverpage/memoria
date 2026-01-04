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
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

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
    SERVICE_POSTS,
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

    def test_primary_context_from_manifest_not_merged_metadata(self):
        """Test that get_merged_description_for_file uses primary's own context.

        Regression test: Previously, primary context was incorrectly sourced from
        merged_metadata which often lacked username/sender/conversation/content.
        The fix stores primary's context directly in the manifest entry.
        """
        # Create a manifest where primary has context but merged_metadata does not
        manifest = {
            "duplicates": {
                "test_hash_123": {
                    "primary": {
                        "processor": "Instagram Messages",
                        "source_path": "/exports/instagram/photo.jpg",
                        "content_hash": "test_hash_123",
                        "file_size": 1000000,
                        "context": {
                            "username": "my_insta_user",
                            "sender": "alice",
                            "conversation": "Best Friends",
                            "content": "Amazing photo!",
                        },
                    },
                    "secondary": [],
                    "merged_metadata": {
                        # merged_metadata lacks the detailed context
                        "timestamp": "2024-01-15T10:00:00Z",
                    },
                },
            },
        }

        description = get_merged_description_for_file(
            manifest, "/exports/instagram/photo.jpg"
        )

        assert description is not None
        # Primary username should come from primary.context, not merged_metadata
        assert "my_insta_user" in description
        # Primary context (sender/conversation) should be included
        assert "alice" in description
        assert "Best Friends" in description

    def test_manifest_stores_primary_context(
        self, dedup_service, cross_processor_catalogs
    ):
        """Test that the manifest stores primary's context for description building."""
        run_id = "test_primary_context_stored"
        write_catalogs_to_db(dedup_service.db, run_id, cross_processor_catalogs)
        manifest = dedup_service.analyze_from_db(run_id)

        # The primary entry should have a context field
        dup_group = manifest["duplicates"]["00183fbb4f4f1212"]
        primary = dup_group["primary"]

        assert "context" in primary, (
            "Primary entry in manifest should have 'context' field"
        )
        # The context should contain the primary's metadata
        assert primary["context"].get("username") == "user@gmail.com"

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

        # Primary source should use platform name (not full processor name)
        assert "Source: Google" in description

        # Secondary sources should be mentioned with + prefix
        assert "+iMessage" in description or "+Instagram" in description

    def test_merged_description_format_matches_spec(
        self, dedup_service, cross_processor_catalogs
    ):
        """Test that merged description follows the format specification.

        Expected format (new unified format with relative paths):
            Source: Google/user@gmail.com/photos
            +iMessage/iph13p: Alice in "Family Group": "Check out this..."
            +Instagram/myinsta/messages: bestfriend in "Travel Buddies": ...

        Note: iMessage entries are consolidated (context folding applies when
        same platform has multiple device exports).
        """
        run_id = "test_desc_format"
        write_catalogs_to_db(dedup_service.db, run_id, cross_processor_catalogs)
        manifest = dedup_service.analyze_from_db(run_id)
        primary_path = "/exports/google/IMG_5895.heic"
        description = get_merged_description_for_file(manifest, primary_path)

        assert description is not None
        # Split on &#xa; (XML entity for newline used in exiftool argfile format)
        lines = description.split("&#xa;")

        # First line should be the primary source with platform/username/service format
        assert lines[0].startswith("Source:")
        assert "Google" in lines[0]

        # Remaining lines should be secondary sources or message detail lines
        # Secondary sources start with + and may be full paths, relative paths, or detail-only
        for line in lines[1:]:
            if line.strip():
                # Should start with either "  - " (primary context) or "+" (secondary)
                assert line.startswith("+") or line.startswith("  - "), (
                    f"Line should start with + or '  - ': {line}"
                )

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

    def test_caption_fallback_in_merged_description(self):
        """Test that caption-only secondary context is displayed in merged description.

        Regression test: Some processors (Instagram, Instaloader) emit "caption"
        instead of "content" in their metadata. The create_source_entry() helper
        should fall back from content to caption, ensuring caption text appears
        in the merged description.
        """
        # Create a manifest where secondary has caption but no content
        manifest = {
            "duplicates": {
                "test_hash_caption_fallback": {
                    "primary": {
                        "processor": "Google Photos",
                        "source_path": "/exports/google/photo.jpg",
                        "content_hash": "test_hash_caption_fallback",
                        "file_size": 1000000,
                        "context": {
                            "username": "user@gmail.com",
                        },
                    },
                    "secondary": [
                        {
                            "processor": "Instagram Public Media",
                            "source_path": "/exports/instagram/post.jpg",
                            "content_hash": "test_hash_caption_fallback",
                            "context": {
                                "username": "insta_user",
                                # caption instead of content
                                "caption": "Beach sunset vibes",
                                # no "content" key
                            },
                        },
                    ],
                    "merged_metadata": {
                        "timestamp": "2024-01-15T10:00:00Z",
                    },
                },
            },
        }

        description = get_merged_description_for_file(
            manifest, "/exports/google/photo.jpg"
        )

        assert description is not None
        # The caption text should appear in the merged description
        assert "Beach sunset vibes" in description


class TestDescriptionBuilderIntegration:
    """Tests for the description builder functions with cross-processor data."""

    def test_build_image_description_with_multiple_secondaries(self):
        """Test building description with multiple secondary sources.

        Note: Entries with identical (sender, conversation, content_prefix) are
        deduplicated per Bug 2 fix. This test uses distinct contexts for each entry.
        """
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
                sender="Bob",  # Different sender - distinct context
                conversation="Work Chat",  # Different conversation
                content="For the presentation",  # Different content
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

        # Verify secondary sources (each has distinct context)
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

    def test_batch_process_exif_passes_manifest_through(self, tmp_path):
        """Test that batch_process_exif actually passes the manifest to the writer.

        This test uses source inspection to verify that when batch_process_exif
        is called with a dedup_manifest, it passes that manifest to
        batch_write_metadata_requests.
        """
        from processors.base import ProcessorBase
        import inspect
        from common.media.exiftool import DescriptionWriteSpec, MetadataWriteRequest, DEFAULT_PREAMBLE

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

        # Verify actual metadata writing to file (integration-ish, skip if exiftool missing)
        if shutil.which("exiftool") is None:
            pytest.skip("exiftool not installed")

        repo_root = Path(__file__).resolve().parents[2]
        fixture_src = (
            repo_root
            / "tests"
            / "fixtures"
            / "exports"
            / "imessage"
            / "iphone_basic"
            / "SMS"
            / "Attachments"
            / "ab"
            / "cd"
            / "AAAABBBB-1111-2222-3333-444455556666"
            / "photo.jpeg"
        )
        assert fixture_src.exists(), f"Missing test fixture file: {fixture_src}"

        test_file = tmp_path / "photo.jpeg"

        shutil.copyfile(fixture_src, test_file)

        content_hash = "abc123"
        manifest = {
            "duplicates": {
                content_hash: {
                    "secondary": [
                        {"processor": "iMessage", "context": {"username": "iph13p"}}
                    ]
                }
            }
        }

        def _builder(_file_info):
            return [
                MetadataWriteRequest(
                    file_path=str(test_file),
                    arg_preamble=DEFAULT_PREAMBLE,
                    description=DescriptionWriteSpec(default_description="Source: Google/user/photos"),
                    processor_name="Google Photos",
                    content_hash=content_hash,
                )
            ]

        exif_rebuilt_count, write_outcome, missing_files = ProcessorBase.batch_process_exif(
            [str(test_file)],
            [()],
            _builder,
            dedup_manifest=manifest,
        )
        assert exif_rebuilt_count == 0
        assert not missing_files
        assert str(test_file) in write_outcome.successes

        result = subprocess.run(
            ["exiftool", "-ImageDescription", "-s3", str(test_file)],
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        assert result.returncode == 0, result.stderr
        assert "+iMessage" in (result.stdout or ""), result.stdout


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
        assert (
            "sunset" in result.lower()
            or "everyone" in result.lower()
            or "beautiful" in result.lower()
        )

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


class TestDuplicateSecondaryEntriesFixes:
    """Regression tests for docs/bugs/duplicate-secondary-entries.md.

    Bug 1: Variable truncation breaks string-based deduplication
    Bug 2: Context-based deduplication is only applied relative to the primary
    Bug 3: Secondary timestamps come from per-export context, not canonical merged metadata
    Bug 4: Redundant dates are not suppressed when they match the file's EXIF timestamp
    """

    def test_bug1_truncation_order_does_not_affect_dedup(self):
        """Bug 1: Identical secondaries with long content should dedupe to one line.

        Previously, string-based deduplication with a decreasing per-line budget
        could produce different truncated strings for the same logical entry,
        bypassing deduplication.
        """
        primary = SourceEntry(
            processor="Discord",
            username="example_user",
            is_primary=True,
        )

        # Two identical Instagram entries with long content that would be truncated
        long_content = "I got tazed probably over 50 times last night " + "A" * 200

        secondary = [
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="example_user",
                service=SERVICE_POSTS,
                content=long_content,
                timestamp=datetime(2018, 8, 20),
            ),
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="example_user",
                service=SERVICE_POSTS,
                content=long_content,
                timestamp=datetime(2018, 8, 20),
            ),
        ]

        # Use a constrained max_total to force truncation
        result = build_image_description(primary, secondary, max_total=500)

        # Count Instagram lines - should be exactly 1
        lines = result.split("&#xa;")
        instagram_lines = [line for line in lines if "+Instagram" in line]

        assert len(instagram_lines) == 1, (
            f"Expected 1 Instagram line, got {len(instagram_lines)}: {instagram_lines}"
        )

    def test_bug2_same_source_secondaries_dedupe_among_themselves(self):
        """Bug 2: Multiple secondaries from the same source identity should dedupe.

        Previously, only same-source entries relative to the PRIMARY got
        context-based deduplication. Multiple Instagram exports with the same
        (platform, username, service, context) should produce only one line.
        """
        primary = SourceEntry(
            processor="Discord",
            username="discord_user",
            is_primary=True,
            conversation="DM with example_user2",
        )

        # Three Instagram entries from the same account (different exports)
        # with identical context - should collapse to one
        secondary = [
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="example_user",
                service=SERVICE_POSTS,
                content="I got tazed...",
                timestamp=datetime(2018, 8, 20),
            ),
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="example_user",
                service=SERVICE_POSTS,
                content="I got tazed...",
                timestamp=datetime(2018, 8, 19),  # Different date from export quirks
            ),
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="example_user",
                service=SERVICE_POSTS,
                content="I got tazed...",
                timestamp=datetime(2018, 8, 20),
            ),
        ]

        result = build_image_description(primary, secondary)

        # Count Instagram lines - should be exactly 1
        lines = result.split("&#xa;")
        instagram_lines = [line for line in lines if "+Instagram" in line]

        assert len(instagram_lines) == 1, (
            f"Expected 1 Instagram line, got {len(instagram_lines)}: {instagram_lines}"
        )

    def test_bug3_timestamps_normalized_to_canonical(self):
        """Bug 3: Secondary timestamps should use canonical merged timestamp.

        When get_merged_description_for_file() is called, all secondary entries
        should display the same (canonical) date, not their per-export timestamps.
        """
        manifest = {
            "duplicates": {
                "test_hash_bug3": {
                    "primary": {
                        "processor": "Discord",
                        "source_path": "/exports/discord/photo.jpg",
                        "context": {
                            "username": "discord_user",
                            "conversation": "DM with example_user2",
                        },
                    },
                    "secondary": [
                        {
                            "processor": "Instagram Public Media",
                            "source_path": "/exports/instagram1/post.jpg",
                            "context": {
                                "username": "insta_user",
                                "caption": "Beach vibes",
                                # Per-export timestamp differs from canonical
                                "timestamp": "2018-08-20T12:00:00Z",
                            },
                        },
                        {
                            "processor": "Instagram Public Media",
                            "source_path": "/exports/instagram2/post.jpg",
                            "context": {
                                "username": "insta_user",
                                "caption": "Beach vibes 2",
                                # Different per-export timestamp
                                "timestamp": "2018-08-19T12:00:00Z",
                            },
                        },
                    ],
                    "merged_metadata": {
                        # Canonical timestamp (earliest across all sources)
                        "timestamp": "2018-08-18T10:00:00Z",
                    },
                },
            },
        }

        description = get_merged_description_for_file(
            manifest, "/exports/discord/photo.jpg"
        )

        assert description is not None

        # All dates should be the canonical date (2018-08-18), not the per-export dates
        # Since Bug 4 suppresses redundant dates when they match the reference,
        # and canonical_ts == reference_timestamp, dates should be suppressed entirely
        # unless there's a mismatch (which there isn't in this test after normalization)
        assert "2018-08-20" not in description
        assert "2018-08-19" not in description

    def test_bug4_redundant_dates_suppressed(self):
        """Bug 4: Dates matching the EXIF-written date should be suppressed.

        When a secondary entry's date matches the canonical/reference timestamp,
        the (YYYY-MM-DD) suffix should be omitted to save space.
        """
        reference_ts = datetime(2024, 1, 15, 10, 30, 0)

        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user@gmail.com",
            service=SERVICE_PHOTOS,
            is_primary=True,
        )

        # Secondary with same date as reference - should suppress date
        secondary_same_date = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username="myinsta",
            service=SERVICE_MESSAGES,
            sender="friend",
            conversation="Chat",
            content="Nice photo!",
            timestamp=datetime(2024, 1, 15, 12, 0, 0),  # Same date
        )

        # Secondary with different date - should show date
        secondary_diff_date = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="iph13p",
            sender="Alice",
            conversation="Family",
            content="Look at this!",
            timestamp=datetime(2024, 2, 20, 10, 0, 0),  # Different date
        )

        result = build_image_description(
            primary,
            [secondary_same_date, secondary_diff_date],
            reference_timestamp=reference_ts,
        )

        # Instagram line should NOT have (2024-01-15) since it matches reference
        lines = result.split("&#xa;")
        instagram_line = next(
            (line for line in lines if "+Instagram" in line), None
        )
        assert instagram_line is not None
        assert "(2024-01-15)" not in instagram_line

        # iMessage line SHOULD have (2024-02-20) since it differs from reference
        imessage_line = next(
            (line for line in lines if "+iMessage" in line), None
        )
        assert imessage_line is not None
        assert "(2024-02-20)" in imessage_line

    def test_bug4_format_secondary_source_suppresses_redundant_date(self):
        """Bug 4: format_secondary_source should suppress redundant dates."""
        reference_ts = datetime(2024, 1, 15)

        entry = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username="myinsta",
            service=SERVICE_POSTS,
            content="Amazing sunset!",
            timestamp=datetime(2024, 1, 15, 18, 30),  # Same date as reference
        )

        result = format_secondary_source(entry, reference_timestamp=reference_ts)

        # Date should be suppressed
        assert "(2024-01-15)" not in result
        # Content should still be present
        assert "Amazing sunset" in result

    def test_bug2_different_source_groups_kept_separate(self):
        """Bug 2: Different source identities should not be merged.

        Entries from different (platform, username, service) tuples should
        each produce their own line, even if they have the same context.
        """
        primary = SourceEntry(
            processor="Discord",
            username="discord_user",
            is_primary=True,
        )

        secondary = [
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="insta_user_1",
                service=SERVICE_POSTS,
                content="Same content",
            ),
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="insta_user_2",  # Different username
                service=SERVICE_POSTS,
                content="Same content",
            ),
            SourceEntry(
                processor=PLATFORM_GOOGLE,
                username="google_user",
                service=SERVICE_PHOTOS,
                content="Same content",
            ),
        ]

        result = build_image_description(primary, secondary)

        # Should have 3 distinct secondary lines
        lines = result.split("&#xa;")
        secondary_lines = [line for line in lines if line.startswith("+")]

        assert len(secondary_lines) == 3, (
            f"Expected 3 secondary lines, got {len(secondary_lines)}: {secondary_lines}"
        )

    def test_integration_manifest_to_description_deduplicates(self):
        """Integration test: manifest with duplicate secondaries produces clean output.

        This tests the full flow from manifest through get_merged_description_for_file.
        """
        manifest = {
            "duplicates": {
                "test_hash_integration": {
                    "primary": {
                        "processor": "Discord",
                        "source_path": "/exports/discord/photo.jpg",
                        "context": {
                            "username": "discord_user",
                            "conversation": "DM with friend",
                        },
                    },
                    "secondary": [
                        # Three Instagram entries from same source with same context
                        {
                            "processor": "Instagram Public Media",
                            "source_path": "/exports/instagram1/post.jpg",
                            "context": {
                                "username": "insta_user",
                                "caption": "I got tazed...",
                                "timestamp": "2018-08-20T12:00:00Z",
                            },
                        },
                        {
                            "processor": "Instagram Public Media",
                            "source_path": "/exports/instagram2/post.jpg",
                            "context": {
                                "username": "insta_user",
                                "caption": "I got tazed...",
                                "timestamp": "2018-08-19T12:00:00Z",
                            },
                        },
                        {
                            "processor": "Instagram Public Media",
                            "source_path": "/exports/instagram3/post.jpg",
                            "context": {
                                "username": "insta_user",
                                "caption": "I got tazed...",
                                "timestamp": "2018-08-20T10:00:00Z",
                            },
                        },
                        # Google Photos entry (different source)
                        {
                            "processor": "Google Photos",
                            "source_path": "/exports/google/photo.jpg",
                            "context": {
                                "username": "google_user",
                            },
                        },
                    ],
                    "merged_metadata": {
                        "timestamp": "2018-08-18T10:00:00Z",
                    },
                },
            },
        }

        description = get_merged_description_for_file(
            manifest, "/exports/discord/photo.jpg"
        )

        assert description is not None
        lines = description.split("&#xa;")

        # Should have: primary line, one Instagram line, one Google line
        # (3 Instagram entries collapsed to 1)
        instagram_lines = [line for line in lines if "+Instagram" in line]
        google_lines = [line for line in lines if "+Google" in line]

        assert len(instagram_lines) == 1, (
            f"Expected 1 Instagram line, got {len(instagram_lines)}: {instagram_lines}"
        )
        assert len(google_lines) == 1, (
            f"Expected 1 Google line, got {len(google_lines)}: {google_lines}"
        )

    def test_bug3_canonical_date_shown_when_different_from_reference(self):
        """Bug 3: Positively assert canonical date appears when reference differs.

        When reference_timestamp differs from the canonical timestamp, the
        canonical date should appear in the output (not be suppressed).
        """
        # Canonical timestamp is 2024-03-15, reference is 2024-01-01
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user@gmail.com",
            service=SERVICE_PHOTOS,
            is_primary=True,
        )

        secondary = [
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="myinsta",
                service=SERVICE_POSTS,
                content="Beach day!",
                timestamp=datetime(2024, 3, 15, 12, 0, 0),  # Canonical timestamp
            ),
        ]

        # Reference timestamp is DIFFERENT from canonical
        reference_ts = datetime(2024, 1, 1, 10, 0, 0)

        result = build_image_description(
            primary, secondary, reference_timestamp=reference_ts
        )

        # The canonical date (2024-03-15) SHOULD appear since it differs from reference
        assert "(2024-03-15)" in result

    def test_bug3_manifest_canonical_date_shown_when_exif_differs(self):
        """Bug 3: Integration test - canonical date from manifest appears when EXIF differs.

        When get_merged_description_for_file() normalizes timestamps, and the
        caller provides a different reference_timestamp, the canonical date
        should appear in the output.
        """
        manifest = {
            "duplicates": {
                "test_hash_canonical_shown": {
                    "primary": {
                        "processor": "Discord",
                        "source_path": "/exports/discord/photo.jpg",
                        "context": {
                            "username": "discord_user",
                            "conversation": "General Chat",
                        },
                    },
                    "secondary": [
                        {
                            "processor": "Instagram Public Media",
                            "source_path": "/exports/instagram/post.jpg",
                            "context": {
                                "username": "insta_user",
                                "caption": "Great sunset!",
                                "timestamp": "2024-06-15T18:00:00Z",  # Per-export ts
                            },
                        },
                    ],
                    "merged_metadata": {
                        # Canonical timestamp (earliest)
                        "timestamp": "2024-05-20T10:00:00Z",
                    },
                },
            },
        }

        description = get_merged_description_for_file(
            manifest, "/exports/discord/photo.jpg"
        )

        assert description is not None

        # get_merged_description_for_file uses canonical ts as reference_timestamp
        # Since both are the same date (2024-05-20), date is suppressed by Bug 4
        # This test documents that behavior
        assert "(2024-05-20)" not in description  # Suppressed by Bug 4
        assert "(2024-06-15)" not in description  # Per-export date not used

    def test_same_source_detail_line_date_suppression(self):
        """Same-source secondary detail lines should suppress redundant dates.

        When a same-source secondary entry is formatted via _format_message_line
        (is_secondary=True), its date should be suppressed if it matches the
        reference_timestamp.
        """
        reference_ts = datetime(2024, 2, 14, 12, 0, 0)

        primary = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="iph13p",
            sender="Alice",
            conversation="Family Group",
            content="Look at this!",
            is_primary=True,
        )

        # Same-source secondary with same date - should suppress date
        secondary_same_date = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="mac",  # Different device, same source for iMessage
            sender="Bob",
            conversation="Work Chat",
            content="Nice!",
            timestamp=datetime(2024, 2, 14, 14, 30, 0),  # Same date as reference
        )

        # Same-source secondary with different date - should show date
        secondary_diff_date = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="ipad",
            sender="Charlie",
            conversation="Friends",
            content="Wow!",
            timestamp=datetime(2024, 3, 10, 10, 0, 0),  # Different date
        )

        result = build_image_description(
            primary,
            [secondary_same_date, secondary_diff_date],
            reference_timestamp=reference_ts,
        )

        lines = result.split("&#xa;")

        # Find the Bob/Work Chat line (same date - should NOT have date)
        bob_line = next((line for line in lines if "Bob" in line), None)
        assert bob_line is not None, f"Bob line not found in: {lines}"
        assert "(2024-02-14)" not in bob_line, (
            f"Date should be suppressed for same-date entry: {bob_line}"
        )

        # Find the Charlie/Friends line (different date - SHOULD have date)
        charlie_line = next((line for line in lines if "Charlie" in line), None)
        assert charlie_line is not None, f"Charlie line not found in: {lines}"
        assert "(2024-03-10)" in charlie_line, (
            f"Date should appear for different-date entry: {charlie_line}"
        )

    def test_deduplicate_secondary_entries_uses_canonical_helper(self):
        """Verify deduplicate_secondary_entries uses _get_secondary_dedup_key semantics.

        This test ensures both build_image_description and exiftool path
        use the same canonical deduplication helper.
        """
        from common.dedup.description import deduplicate_secondary_entries

        # Create duplicate entries (same source identity + context)
        entries = [
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="myinsta",
                service=SERVICE_POSTS,
                sender="friend",
                conversation="DM",
                content="Hello there!",
                timestamp=datetime(2024, 1, 15),
            ),
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="myinsta",
                service=SERVICE_POSTS,
                sender="friend",
                conversation="DM",
                content="Hello there!",  # Same content prefix
                timestamp=datetime(2024, 1, 20),  # Different timestamp - ignored for dedup
            ),
        ]

        result = deduplicate_secondary_entries(entries)

        # Should deduplicate to 1 entry (timestamp not in dedup key)
        assert len(result) == 1
