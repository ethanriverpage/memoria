"""
Processing tests for iMessage iMazing processor.

Tests cover edge cases including:
- iMazing filename pattern parsing
- Orphaned media detection (files that don't match pattern)
- Orphaned metadata detection (CSV entries without matching files)
"""

import csv
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
        preprocessor.process()

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
        preprocessor.process()

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
        preprocessor.process()

        # No orphans should be tracked
        assert len(preprocessor.failure_tracker.orphaned_media) == 0
        assert len(preprocessor.failure_tracker.orphaned_metadata) == 0

