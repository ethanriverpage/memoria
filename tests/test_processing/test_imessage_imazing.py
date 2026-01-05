"""
Processing tests for iMazing-format iMessage exports.

Note: iMazing exports are now processed by the unified iMessage processor.

Tests cover edge cases including:
- iMazing filename pattern parsing
- Orphaned media detection (files that don't match pattern)
- Orphaned metadata detection (CSV entries without matching files)
- Detection of iMazing exports (via unified iMessage processor)
- Preprocessed export handling
- Description building
- Live Photo handling
"""

import csv
import json
from pathlib import Path

import pytest

from tests.fixtures.media_samples import write_media_file


@pytest.fixture
def imazing_export_dir(tmp_path):
    """Create a temporary iMazing export directory."""
    export_dir = tmp_path / "imazing_export"
    export_dir.mkdir()
    return export_dir


@pytest.fixture
def imazing_output_dir(tmp_path):
    """Create a temporary output directory."""
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir


def create_imazing_media_file(
    export_dir: Path, timestamp: str, contact: str, original: str
):
    """Create an iMazing-format media file.

    Args:
        export_dir: Export directory path
        timestamp: Timestamp in "YYYY-MM-DD HH MM SS" format
        contact: Contact name
        original: Original filename

    Returns:
        Path to created file
    """
    filename = f"{timestamp} - {contact} - {original}"
    file_path = export_dir / filename
    write_media_file(file_path, "jpeg")
    return file_path


def create_imazing_csv(export_dir: Path, contact: str, messages: list):
    """Create an iMazing Messages CSV file.

    Args:
        export_dir: Export directory path
        contact: Contact name (used in filename)
        messages: List of message dicts with keys:
            - message_date: Timestamp string
            - attachment: Attachment filename
            - text: Message text
            - type: "Incoming" or "Outgoing"
    """
    csv_path = export_dir / f"Messages - {contact}.csv"

    fieldnames = [
        "Chat Session",
        "Message Date",
        "Delivered Date",
        "Read Date",
        "Service",
        "Type",
        "Sender ID",
        "Sender Name",
        "Status",
        "Replying to",
        "Subject",
        "Text",
        "Attachment",
        "Attachment type",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for msg in messages:
            row = {
                "Chat Session": contact,
                "Message Date": msg.get("message_date", ""),
                "Delivered Date": "",
                "Read Date": "",
                "Service": "iMessage",
                "Type": msg.get("type", "Incoming"),
                "Sender ID": "",
                "Sender Name": contact if msg.get("type") != "Outgoing" else "Me",
                "Status": "Delivered",
                "Replying to": "",
                "Subject": "",
                "Text": msg.get("text", ""),
                "Attachment": msg.get("attachment", ""),
                "Attachment type": "Image",
            }
            writer.writerow(row)

    return csv_path


class TestImazingFilenamePattern:
    """Tests for iMazing filename pattern parsing."""

    def test_valid_pattern_parsed(self, imazing_export_dir, imazing_output_dir):
        """Should parse valid iMazing filename pattern."""
        create_imazing_media_file(
            imazing_export_dir,
            "2021-01-15 14 30 00",
            "John Doe",
            "photo.jpg",
        )

        assert (
            imazing_export_dir / "2021-01-15 14 30 00 - John Doe - photo.jpg"
        ).exists()


class TestImazingOrphanDetection:
    """Tests for orphaned media/metadata detection."""

    def test_orphaned_media_invalid_pattern(
        self, imazing_export_dir, imazing_output_dir
    ):
        """Should track media files that don't match iMazing pattern as orphaned media."""
        from processors.imessage.preprocess_imazing import ImazingPreprocessor

        # Create a file that doesn't match the pattern
        invalid_file = imazing_export_dir / "random_photo.jpg"
        write_media_file(invalid_file, "jpeg")

        # Also create a valid file so preprocessor has something to process
        create_imazing_media_file(
            imazing_export_dir,
            "2021-01-15 14 30 00",
            "John Doe",
            "valid.jpg",
        )

        preprocessor = ImazingPreprocessor(
            export_path=imazing_export_dir,
            output_dir=imazing_output_dir,
            owner_name="testuser",  # Provide username to satisfy invariant
        )
        preprocessor.run()

        # Check orphaned media was tracked
        orphaned_paths = [
            entry["file_path"] for entry in preprocessor.failure_tracker.orphaned_media
        ]
        assert any("random_photo.jpg" in path for path in orphaned_paths)

    def test_orphaned_csv_metadata_missing_file(
        self, imazing_export_dir, imazing_output_dir
    ):
        """Should track CSV entries without matching media as orphaned metadata."""
        from processors.imessage.preprocess_imazing import ImazingPreprocessor

        # Create a valid media file
        create_imazing_media_file(
            imazing_export_dir,
            "2021-01-15 14 30 00",
            "John Doe",
            "exists.jpg",
        )

        # Create CSV with an entry that references a non-existent file
        create_imazing_csv(
            imazing_export_dir,
            "John Doe",
            [
                {
                    "message_date": "2021-01-15 14:30:00",
                    "attachment": "exists.jpg",
                    "text": "Here's a photo",
                    "type": "Outgoing",
                },
                {
                    "message_date": "2021-01-16 10:00:00",
                    "attachment": "missing.jpg",  # This file doesn't exist
                    "text": "Another photo",
                    "type": "Incoming",
                },
            ],
        )

        preprocessor = ImazingPreprocessor(
            export_path=imazing_export_dir,
            output_dir=imazing_output_dir,
            owner_name="testuser",  # Provide username to satisfy invariant
        )
        preprocessor.run()

        # Check orphaned metadata was tracked
        assert len(preprocessor.failure_tracker.orphaned_metadata) >= 1
        orphaned_attachments = [
            entry["metadata_entry"].get("attachment")
            for entry in preprocessor.failure_tracker.orphaned_metadata
        ]
        assert "missing.jpg" in orphaned_attachments

    def test_no_orphans_when_all_matched(self, imazing_export_dir, imazing_output_dir):
        """Should not track orphans when all files and CSV entries match."""
        from processors.imessage.preprocess_imazing import ImazingPreprocessor

        # Create matching media file and CSV entry
        create_imazing_media_file(
            imazing_export_dir,
            "2021-01-15 14 30 00",
            "John Doe",
            "photo.jpg",
        )

        create_imazing_csv(
            imazing_export_dir,
            "John Doe",
            [
                {
                    "message_date": "2021-01-15 14:30:00",
                    "attachment": "photo.jpg",
                    "text": "Matching photo",
                    "type": "Outgoing",
                },
            ],
        )

        preprocessor = ImazingPreprocessor(
            export_path=imazing_export_dir,
            output_dir=imazing_output_dir,
        )
        preprocessor.run()

        # No orphans should be tracked
        assert len(preprocessor.failure_tracker.orphaned_media) == 0
        assert len(preprocessor.failure_tracker.orphaned_metadata) == 0


class TestImazingDetection:
    """Tests for iMazing export detection."""

    @staticmethod
    def _detect(path):
        """Wrapper to call static detect method."""
        from processors.imessage.processor import IMessageProcessor

        return IMessageProcessor.detect(path)

    def test_detects_device_info_txt(self, imazing_export_dir):
        """Should detect iMazing export by Device-Info.txt content."""
        # Create Device-Info.txt with iMazing reference
        device_info = imazing_export_dir / "Device-Info.txt"
        device_info.write_text("Exported by iMazing 2.17.0\nDevice: iPhone")

        assert self._detect(imazing_export_dir) is True

    def test_detects_digidna_reference(self, imazing_export_dir):
        """Should detect iMazing export by DigiDNA reference."""
        device_info = imazing_export_dir / "Device-Info.txt"
        device_info.write_text("Exported by DigiDNA iMazing")

        assert self._detect(imazing_export_dir) is True

    def test_detects_vcf_with_imazing(self, imazing_export_dir):
        """Should detect iMazing export by vCard PRODID."""
        vcf_file = imazing_export_dir / "Contact - John Doe.vcf"
        vcf_file.write_text("BEGIN:VCARD\nPRODID:iMazing\nEND:VCARD")

        assert self._detect(imazing_export_dir) is True

    def test_detects_by_filename_pattern(self, imazing_export_dir):
        """Should detect iMazing export by filename pattern."""
        # Create Messages CSV
        csv_path = imazing_export_dir / "Messages - John Doe.csv"
        csv_path.write_text("header\n")

        # Create media with iMazing pattern
        create_imazing_media_file(
            imazing_export_dir,
            "2021-01-15 14 30 00",
            "John Doe",
            "photo.jpg",
        )

        assert self._detect(imazing_export_dir) is True

    def test_detects_preprocessed_export(self, imazing_export_dir):
        """Should detect preprocessed iMazing export."""
        # Create preprocessed structure with required schema fields
        media_dir = imazing_export_dir / "media"
        media_dir.mkdir()

        metadata = {
            "export_info": {
                "export_path": str(imazing_export_dir),
                "export_username": "testuser",
                "processed_date": "2021-01-01T00:00:00",
                "platform": "iMessage",
                "service": None,
            },
            "conversations": {},
            "file_hashes": {},
        }
        (imazing_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert self._detect(imazing_export_dir) is True

    def test_rejects_empty_directory(self, imazing_export_dir):
        """Should reject empty directory."""
        assert self._detect(imazing_export_dir) is False

    def test_rejects_non_imazing_export(self, imazing_export_dir):
        """Should reject directory without iMazing markers."""
        # Create some random files
        (imazing_export_dir / "random.txt").write_text("random content")
        write_media_file(imazing_export_dir / "photo.jpg", "jpeg")

        assert self._detect(imazing_export_dir) is False


class TestImazingPreprocessed:
    """Tests for preprocessed iMazing export handling."""

    def _is_preprocessed_imazing(self, path):
        """Check if path is a preprocessed iMessage export."""
        from processors.imessage.processor import IMessageProcessor

        # Normalized schema no longer has export_format field
        return IMessageProcessor.is_preprocessed_export_dir(
            path,
            container_key="conversations",
        )

    def test_is_preprocessed_true(self, imazing_export_dir):
        """Should identify preprocessed export correctly."""
        metadata = {
            "export_info": {
                "export_path": str(imazing_export_dir),
                "export_username": "testuser",
                "processed_date": "2021-01-01T00:00:00",
                "platform": "iMessage",
                "service": None,
            },
            "conversations": {},
            "file_hashes": {},  # Required for hash-only preprocessing
        }
        (imazing_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert self._is_preprocessed_imazing(imazing_export_dir) is True

    def test_is_preprocessed_false_missing_file_hashes(self, imazing_export_dir):
        """Should reject if file_hashes is missing."""
        metadata = {
            "export_info": {
                "export_path": str(imazing_export_dir),
                "export_username": "testuser",
                "processed_date": "2021-01-01T00:00:00",
                "platform": "iMessage",
                "service": None,
            },
            "conversations": {},
        }
        (imazing_export_dir / "metadata.json").write_text(json.dumps(metadata))

        # Schema validation will fail without file_hashes
        assert self._is_preprocessed_imazing(imazing_export_dir) is False

    def test_is_preprocessed_false_missing_metadata(self, imazing_export_dir):
        """Should reject if metadata.json is missing."""
        assert self._is_preprocessed_imazing(imazing_export_dir) is False


class TestImazingPreprocessorNormalization:
    """Tests that ImazingPreprocessor produces normalized metadata schema."""

    def test_preprocessor_output_has_no_export_format(
        self, imazing_export_dir, imazing_output_dir
    ):
        """Preprocessor output should NOT contain export_format field."""
        from processors.imessage.preprocess_imazing import ImazingPreprocessor

        # Create minimal valid iMazing export
        device_info = imazing_export_dir / "Device-Info.txt"
        device_info.write_text("Exported by iMazing 2.17.0\nDevice: iPhone 13 Pro")

        # Create a valid media file with iMazing naming
        write_media_file(
            imazing_export_dir / "2021-01-15 14 30 00 - John Doe - photo.jpg", "jpeg"
        )

        preprocessor = ImazingPreprocessor(
            export_path=imazing_export_dir,
            output_dir=imazing_output_dir,
            owner_name="testuser",
        )
        preprocessor.run()

        # Load and verify metadata
        metadata_file = imazing_output_dir / "metadata.json"
        assert metadata_file.exists()

        metadata = json.loads(metadata_file.read_text())
        export_info = metadata.get("export_info", {})

        # Assert NO export_format field
        assert "export_format" not in export_info

        # Assert required platform/service fields
        assert export_info.get("platform") == "iMessage"
        assert export_info.get("service") is None


class TestImazingDescriptionBuilder:
    """Tests for description building functionality."""

    def test_build_description_single_message(self):
        """Should build description for a single message."""
        from processors.imessage.processor import _build_description

        message = {
            "conversation_type": "dm",
            "conversation_id": "+1234567890",
            "conversation_title": "John",
            "sender": "John",
            "content": "Hello!",
            "source_export": "imessage-iph13p-20220426",
        }

        description = _build_description(message, "iph13p")

        assert "iMessage" in description
        assert "iph13p" in description
        assert "John" in description

    def test_build_description_group_conversation(self):
        """Should build description for group conversation."""
        from processors.imessage.processor import _build_description

        message = {
            "conversation_type": "group",
            "conversation_id": "chat12345",
            "conversation_title": "Family Group",
            "sender": "Mom",
            "content": "Family photo",
            "source_export": "imessage-iph13p-20220426",
        }

        description = _build_description(message, "iph13p")

        assert "Family Group" in description
        assert "Mom" in description

    def test_build_description_merged_messages(self):
        """Should build description for merged duplicate messages."""
        from processors.imessage.processor import _build_description

        message = {
            "primary_created": "2022-04-26 10:30:00 UTC",
            "messages": [
                {
                    "conversation_type": "dm",
                    "conversation_id": "+1234567890",
                    "conversation_title": "John",
                    "sender": "John",
                    "content": "First",
                    "source_export": "imessage-iph13p-20220426",
                },
                {
                    "conversation_type": "dm",
                    "conversation_id": "+1234567890",
                    "conversation_title": "John",
                    "sender": "Me",
                    "content": "Second",
                    "source_export": "imessage-m3_mbp-20251202",
                },
            ],
        }

        description = _build_description(message, "unknown")

        # Should contain context from both messages
        assert "iMessage" in description

    def test_build_description_live_photo_video(self):
        """Should append Live Photo marker for video component."""
        from processors.imessage.processor import _build_description

        message = {
            "conversation_type": "dm",
            "conversation_id": "+1234567890",
            "sender": "John",
            "is_live_photo_video": True,
            "source_export": "imessage-iph13p-20220426",
        }

        description = _build_description(message, "iph13p")

        assert "[Live Photo Video]" in description


class TestImazingHelpers:
    """Tests for helper functions in the processor."""

    def test_extract_date_from_source_export(self):
        """Should extract date string from source_export."""
        from processors.imessage.processor import (
            _extract_date_from_source_export,
        )

        assert (
            _extract_date_from_source_export("imessage-iph13p-20220426") == "20220426"
        )
        assert (
            _extract_date_from_source_export("imessage-m3_mbp-20251202") == "20251202"
        )
        assert _extract_date_from_source_export("invalid") is None
        assert _extract_date_from_source_export("") is None
        assert _extract_date_from_source_export(None) is None

    def test_extract_device_from_source_export(self):
        """Should extract device identifier from source_export."""
        from processors.imessage.processor import (
            _extract_device_from_source_export,
        )

        assert (
            _extract_device_from_source_export("imessage-iph13p-20220426") == "iph13p"
        )
        assert (
            _extract_device_from_source_export("imessage-m3_mbp-20251202") == "m3_mbp"
        )
        assert _extract_device_from_source_export("invalid") == "unknown"
        assert _extract_device_from_source_export("") == "unknown"

    def test_build_filename_index(self, imazing_export_dir):
        """Should build index from media_file to message metadata."""
        from processors.imessage.processor import build_filename_index

        metadata = {
            "export_info": {"export_username": "test"},
            "conversations": {
                "conv1": {
                    "messages": [
                        {
                            "media_files": ["photo1.jpg"],
                            "source_paths": {"photo1.jpg": "/path/photo1.jpg"},
                            "content": "msg1",
                        },
                        {
                            "media_files": ["photo2.jpg"],
                            "source_paths": {"photo2.jpg": "/path/photo2.jpg"},
                            "content": "msg2",
                        },
                    ]
                }
            },
            "unmatched_media": [
                {
                    "media_files": ["orphan.jpg"],
                    "source_paths": {"orphan.jpg": "/path/orphan.jpg"},
                    "content": "orphan",
                }
            ],
            "file_hashes": {},
        }

        index = build_filename_index(metadata)

        assert "photo1.jpg" in index
        assert "photo2.jpg" in index
        assert "orphan.jpg" in index
        assert index["photo1.jpg"]["content"] == "msg1"


class TestIMessageProcessorWithImazingFormat:
    """Tests for unified IMessageProcessor handling iMazing format exports."""

    def test_get_name(self):
        """Should return correct processor name."""
        from processors.imessage.processor import IMessageProcessor

        assert IMessageProcessor.get_name() == "iMessage"

    def test_get_priority(self):
        """Should return priority value."""
        from processors.imessage.processor import IMessageProcessor

        assert IMessageProcessor.get_priority() == 49

    def test_supports_dedup(self):
        """Should support deduplication."""
        from processors.imessage.processor import IMessageProcessor

        assert IMessageProcessor.supports_dedup() is True

    def test_supports_consolidation(self):
        """Should support consolidation."""
        from processors.imessage.processor import IMessageProcessor

        assert IMessageProcessor.supports_consolidation() is True

    def test_get_output_info(self):
        """Should return correct output info."""
        from processors.imessage.processor import IMessageProcessor

        output_info = IMessageProcessor.get_output_info()

        assert output_info.platform == "iMessage"
        assert output_info.service is None
        assert output_info.username_prefix == "imessage"

    def test_build_catalog_returns_generator(self, imazing_export_dir):
        """Should return a generator for catalog entries."""
        from processors.imessage.processor import IMessageProcessor
        import types

        # Create minimal structure
        media_dir = imazing_export_dir / "media"
        media_dir.mkdir()
        write_media_file(media_dir / "photo.jpg", "jpeg")

        metadata = {
            "export_info": {
                "export_path": str(imazing_export_dir),
                "export_username": "testuser",
                "processed_date": "2021-01-01T00:00:00",
                "platform": "iMessage",
                "service": None,
            },
            "conversations": {},
            "file_hashes": {},
        }
        (imazing_export_dir / "metadata.json").write_text(json.dumps(metadata))

        result = IMessageProcessor.build_catalog(str(imazing_export_dir), threads=1)

        assert isinstance(result, types.GeneratorType)


class TestImazingLivePhotos:
    """Tests for Live Photo handling in iMazing processor."""

    def test_live_photo_pair_detection(self, imazing_export_dir):
        """Should detect Live Photo pairs (HEIC + MOV with same name)."""
        from processors.imessage.processor import IMessageProcessor

        # Create Live Photo pair
        media_dir = imazing_export_dir / "media"
        media_dir.mkdir()

        heic_path = media_dir / "IMG_1234.HEIC"
        mov_path = media_dir / "IMG_1234.MOV"
        write_media_file(heic_path, "heic")
        write_media_file(mov_path, "mov")

        # Create proper preprocessed metadata with source_paths
        metadata = {
            "export_info": {
                "export_path": str(imazing_export_dir),
                "export_username": "testuser",
                "processed_date": "2021-01-01T00:00:00",
                "platform": "iMessage",
                "service": None,
            },
            "conversations": {
                "test_conv": {
                    "conversation_title": "Test",
                    "messages": [
                        {
                            "media_files": ["IMG_1234.HEIC", "IMG_1234.MOV"],
                            "source_paths": {
                                "IMG_1234.HEIC": str(heic_path),
                                "IMG_1234.MOV": str(mov_path),
                            },
                            "timestamp": "2024-01-01T00:00:00Z",
                        }
                    ],
                }
            },
            "file_hashes": {},
        }
        (imazing_export_dir / "metadata.json").write_text(json.dumps(metadata))

        # Build catalog - should group video with image
        entries = list(
            IMessageProcessor.build_catalog(str(imazing_export_dir), threads=1)
        )

        # Video should not be in entries if grouped with image
        filenames = [Path(e["source_path"]).name for e in entries]
        # The HEIC should be present
        assert any("HEIC" in f or "heic" in f for f in filenames)

    def test_live_photo_pairs_with_timestamp_mismatch(
        self, imazing_export_dir, imazing_output_dir
    ):
        """Live Photo HEIC+MOV should pair even when iMazing prefix timestamps differ.

        This tests the robustness of stem-based pairing. iMazing sometimes exports
        the HEIC and MOV components with slightly different timestamps (1-2 seconds).
        The pairing should still work based on the original filename stem.
        """
        from processors.imessage.preprocess_imazing import ImazingPreprocessor

        # Create Device-Info.txt for iMazing detection
        device_info = imazing_export_dir / "Device-Info.txt"
        device_info.write_text("Exported by iMazing 2.17.0\nDevice: iPhone 13 Pro")

        # Create Live Photo pair with DIFFERENT iMazing prefix timestamps
        # but SAME original filename stem (IMG_1234)
        # Note: iMazing filename format is "YYYY-MM-DD HH MM SS - Contact - originalfile.ext"
        heic_filename = "2021-01-15 14 30 00 - John Doe - IMG_1234.HEIC"
        mov_filename = (
            "2021-01-15 14 30 02 - John Doe - IMG_1234.MOV"  # 2 seconds later
        )

        heic_path = imazing_export_dir / heic_filename
        mov_path = imazing_export_dir / mov_filename
        write_media_file(heic_path, "heic")
        write_media_file(mov_path, "mov")

        # Run preprocessor
        preprocessor = ImazingPreprocessor(
            export_path=imazing_export_dir,
            output_dir=imazing_output_dir,
            owner_name="testuser",
        )
        result = preprocessor.run()
        assert result is True

        # Load metadata
        metadata_file = imazing_output_dir / "metadata.json"
        assert metadata_file.exists()
        metadata = json.loads(metadata_file.read_text())

        # Find the conversation and messages
        conversations = metadata.get("conversations", {})
        assert len(conversations) > 0

        # Collect all messages
        all_messages = []
        for conv_data in conversations.values():
            all_messages.extend(conv_data.get("messages", []))

        # Find HEIC and MOV messages
        heic_messages = [
            m
            for m in all_messages
            if any("HEIC" in f for f in m.get("media_files", []))
        ]
        mov_messages = [
            m for m in all_messages if any("MOV" in f for f in m.get("media_files", []))
        ]

        assert len(heic_messages) >= 1, "Should have HEIC message"
        assert len(mov_messages) >= 1, "Should have MOV message"

        heic_msg = heic_messages[0]
        mov_msg = mov_messages[0]

        # Both should have live_photo_pair_keys
        heic_pair_keys = heic_msg.get("live_photo_pair_keys", [])
        mov_pair_keys = mov_msg.get("live_photo_pair_keys", [])

        assert len(heic_pair_keys) > 0, "HEIC should have live_photo_pair_keys"
        assert len(mov_pair_keys) > 0, "MOV should have live_photo_pair_keys"

        # The pair keys should overlap (same key present in both)
        assert set(heic_pair_keys) & set(mov_pair_keys), (
            f"HEIC and MOV should share a pair key: {heic_pair_keys} vs {mov_pair_keys}"
        )

        # MOV should be marked as live photo video
        assert mov_msg.get("is_live_photo_video") is True, (
            "MOV should be marked as live photo video"
        )

        # Both should have live_photo_image_hash populated
        heic_hash = heic_msg.get("live_photo_image_hash")
        mov_hash = mov_msg.get("live_photo_image_hash")
        assert heic_hash is not None, "HEIC should have live_photo_image_hash"
        assert mov_hash is not None, (
            "MOV should have live_photo_image_hash (carrying HEIC hash)"
        )

        # They should share the same hash (MOV carries HEIC hash for cross-export pairing)
        assert heic_hash == mov_hash, (
            f"Both should share same image hash: {heic_hash} vs {mov_hash}"
        )


class TestImazingEmptyExport:
    """Tests for empty export handling (Policy A: export-level soft-fail)."""

    def test_empty_export_produces_failure_report(
        self, imazing_export_dir, imazing_output_dir
    ):
        """Empty export should produce failure-report.json with export_failures."""
        from processors.imessage.preprocess_imazing import ImazingPreprocessor

        # Create minimal iMazing structure without media files
        # Need CSV to satisfy detection, but no media files
        csv_content = (
            "Date,Sender,Text,Attachment Name,Attachment Type\n"
            "2024-01-01 12:00:00,+1234567890,Hello,,\n"  # No attachment
        )
        (imazing_export_dir / "messages.csv").write_text(csv_content)

        # Run preprocessor
        preprocessor = ImazingPreprocessor(
            export_path=imazing_export_dir,
            output_dir=imazing_output_dir,
            owner_name="testuser",
        )
        result = preprocessor.run()

        # Should return False (FAILED)
        assert result is False

        # Should produce failure-report.json with export_failures
        report_path = imazing_output_dir / "issues" / "failure-report.json"
        assert report_path.exists(), "failure-report.json should be created"

        report = json.loads(report_path.read_text())
        assert "export_failures" in report
        assert len(report["export_failures"]) > 0
        assert report["export_failures"][0]["reason"] == "no_media_files"
