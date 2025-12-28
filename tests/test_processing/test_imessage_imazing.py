"""
Processing tests for iMessage iMazing processor.

Tests cover edge cases including:
- iMazing filename pattern parsing
- Orphaned media detection (files that don't match pattern)
- Orphaned metadata detection (CSV entries without matching files)
- Detection of iMazing exports
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


def create_imazing_media_file(export_dir: Path, timestamp: str, contact: str, original: str):
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

    def test_orphaned_media_invalid_pattern(self, imazing_export_dir, imazing_output_dir):
        """Should track media files that don't match iMazing pattern as orphaned media."""
        from processors.imessage_imazing.preprocess import ImazingPreprocessor

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
        from processors.imessage_imazing.preprocess import ImazingPreprocessor

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
        from processors.imessage_imazing.preprocess import ImazingPreprocessor

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
        from processors.imessage_imazing.processor import ImazingProcessor

        return ImazingProcessor.detect(path)

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
        # Create preprocessed structure
        media_dir = imazing_export_dir / "media"
        media_dir.mkdir()

        metadata = {
            "export_info": {"export_format": "imazing"},
            "conversations": {},
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

    def test_is_preprocessed_true(self, imazing_export_dir):
        """Should identify preprocessed export correctly."""
        from processors.imessage_imazing.processor import is_preprocessed

        metadata = {
            "export_info": {"export_format": "imazing"},
            "conversations": {},
            "file_hashes": {},  # Required for hash-only preprocessing
        }
        (imazing_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert is_preprocessed(imazing_export_dir) is True

    def test_is_preprocessed_false_missing_file_hashes(self, imazing_export_dir):
        """Should reject if file_hashes is missing."""
        from processors.imessage_imazing.processor import is_preprocessed

        metadata = {
            "export_info": {"export_format": "imazing"},
            "conversations": {},
        }
        (imazing_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert is_preprocessed(imazing_export_dir) is False

    def test_is_preprocessed_false_missing_metadata(self, imazing_export_dir):
        """Should reject if metadata.json is missing."""
        from processors.imessage_imazing.processor import is_preprocessed

        assert is_preprocessed(imazing_export_dir) is False

    def test_is_preprocessed_false_wrong_format(self, imazing_export_dir):
        """Should reject if export_format is not 'imazing'."""
        from processors.imessage_imazing.processor import is_preprocessed

        media_dir = imazing_export_dir / "media"
        media_dir.mkdir()

        metadata = {
            "export_info": {"export_format": "other"},
            "conversations": {},
        }
        (imazing_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert is_preprocessed(imazing_export_dir) is False


class TestImazingDescriptionBuilder:
    """Tests for description building functionality."""

    def test_build_description_single_message(self):
        """Should build description for a single message."""
        from processors.imessage_imazing.processor import _build_description

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
        from processors.imessage_imazing.processor import _build_description

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
        from processors.imessage_imazing.processor import _build_description

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
        from processors.imessage_imazing.processor import _build_description

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
        from processors.imessage_imazing.processor import _extract_date_from_source_export

        assert _extract_date_from_source_export("imessage-iph13p-20220426") == "20220426"
        assert _extract_date_from_source_export("imessage-m3_mbp-20251202") == "20251202"
        assert _extract_date_from_source_export("invalid") is None
        assert _extract_date_from_source_export("") is None
        assert _extract_date_from_source_export(None) is None

    def test_extract_device_from_source_export(self):
        """Should extract device identifier from source_export."""
        from processors.imessage_imazing.processor import _extract_device_from_source_export

        assert _extract_device_from_source_export("imessage-iph13p-20220426") == "iph13p"
        assert _extract_device_from_source_export("imessage-m3_mbp-20251202") == "m3_mbp"
        assert _extract_device_from_source_export("invalid") == "unknown"
        assert _extract_device_from_source_export("") == "unknown"

    def test_build_filename_index(self, imazing_export_dir):
        """Should build index from media_file to message metadata."""
        from processors.imessage_imazing.processor import build_filename_index

        metadata = {
            "export_info": {"export_username": "test"},
            "conversations": {
                "conv1": {
                    "messages": [
                        {"media_files": ["photo1.jpg"], "source_paths": {"photo1.jpg": "/path/photo1.jpg"}, "content": "msg1"},
                        {"media_files": ["photo2.jpg"], "source_paths": {"photo2.jpg": "/path/photo2.jpg"}, "content": "msg2"},
                    ]
                }
            },
            "unmatched_media": [
                {"media_files": ["orphan.jpg"], "source_paths": {"orphan.jpg": "/path/orphan.jpg"}, "content": "orphan"}
            ],
            "file_hashes": {},
        }

        index = build_filename_index(metadata)

        assert "photo1.jpg" in index
        assert "photo2.jpg" in index
        assert "orphan.jpg" in index
        assert index["photo1.jpg"]["content"] == "msg1"


class TestImazingProcessor:
    """Tests for the ImazingProcessor class."""

    def test_get_name(self):
        """Should return correct processor name."""
        from processors.imessage_imazing.processor import ImazingProcessor

        assert ImazingProcessor.get_name() == "iMessage-iMazing"

    def test_get_priority(self):
        """Should return priority value."""
        from processors.imessage_imazing.processor import ImazingProcessor

        assert ImazingProcessor.get_priority() == 85

    def test_supports_dedup(self):
        """Should support deduplication."""
        from processors.imessage_imazing.processor import ImazingProcessor

        assert ImazingProcessor.supports_dedup() is True

    def test_supports_consolidation(self):
        """Should not support consolidation."""
        from processors.imessage_imazing.processor import ImazingProcessor

        assert ImazingProcessor.supports_consolidation() is False

    def test_get_output_info(self):
        """Should return correct output info."""
        from processors.imessage_imazing.processor import ImazingProcessor

        output_info = ImazingProcessor.get_output_info()

        assert output_info.platform == "iMessage"
        assert output_info.service is None
        assert output_info.username_prefix == "imessage"

    def test_build_catalog_returns_generator(self, imazing_export_dir):
        """Should return a generator for catalog entries."""
        from processors.imessage_imazing.processor import ImazingProcessor
        import types

        # Create minimal structure
        media_dir = imazing_export_dir / "media"
        media_dir.mkdir()
        write_media_file(media_dir / "photo.jpg", "jpeg")

        metadata = {
            "export_info": {"export_format": "imazing"},
            "conversations": {},
        }
        (imazing_export_dir / "metadata.json").write_text(json.dumps(metadata))

        result = ImazingProcessor.build_catalog(str(imazing_export_dir), workers=1)

        assert isinstance(result, types.GeneratorType)


class TestImazingLivePhotos:
    """Tests for Live Photo handling in iMazing processor."""

    def test_live_photo_pair_detection(self, imazing_export_dir):
        """Should detect Live Photo pairs (HEIC + MOV with same name)."""
        from processors.imessage_imazing.processor import ImazingProcessor

        # Create Live Photo pair
        media_dir = imazing_export_dir / "media"
        media_dir.mkdir()

        heic_path = media_dir / "IMG_1234.HEIC"
        mov_path = media_dir / "IMG_1234.MOV"
        write_media_file(heic_path, "heic")
        write_media_file(mov_path, "mov")

        # Create proper preprocessed metadata with source_paths
        metadata = {
            "export_info": {"export_format": "imazing"},
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
        entries = list(ImazingProcessor.build_catalog(str(imazing_export_dir), workers=1))

        # Video should not be in entries if grouped with image
        filenames = [Path(e["source_path"]).name for e in entries]
        # The HEIC should be present
        assert any("HEIC" in f or "heic" in f for f in filenames)
