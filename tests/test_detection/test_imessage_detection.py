"""
Detection tests for iMessage processor.

Tests verify that the processor correctly identifies valid export
structures (Mac and iPhone) and rejects invalid ones.
"""

import json


from tests.fixtures.generators import (
    create_imessage_mac_export,
    create_imessage_iphone_export,
)
from tests.fixtures.media_samples import create_imessage_chat_db


class TestIMessageMacDetection:
    """Tests for iMessage Mac export detection."""

    def test_detect_valid_mac_export(self, imessage_processor, temp_export_dir):
        """Should detect a valid iMessage Mac export."""
        create_imessage_mac_export(temp_export_dir)
        assert imessage_processor.detect(temp_export_dir) is True

    def test_detect_mac_export_with_attachments(self, imessage_processor, temp_export_dir):
        """Should detect Mac export with multiple attachments."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Photo 1", "attachment": "00/00/image1.jpg", "date": 631152000000000000},
                        {"text": "Photo 2", "attachment": "00/01/image2.jpg", "date": 631152000000000001},
                    ],
                },
            ]
        )
        assert imessage_processor.detect(temp_export_dir) is True

    def test_reject_missing_chat_db(self, imessage_processor, temp_export_dir):
        """Should reject export without chat.db."""
        attachments_dir = temp_export_dir / "Attachments"
        attachments_dir.mkdir(parents=True)
        assert imessage_processor.detect(temp_export_dir) is False

    def test_reject_missing_attachments_dir(self, imessage_processor, temp_export_dir):
        """Should reject export without Attachments directory."""
        create_imessage_chat_db(temp_export_dir / "chat.db")
        assert imessage_processor.detect(temp_export_dir) is False

    def test_reject_chat_db_only(self, imessage_processor, temp_export_dir):
        """Should reject export with only chat.db (no Attachments dir)."""
        create_imessage_chat_db(temp_export_dir / "chat.db")
        # No Attachments directory
        assert imessage_processor.detect(temp_export_dir) is False


class TestIMessageIPhoneDetection:
    """Tests for iMessage iPhone export detection."""

    def test_detect_valid_iphone_export(self, imessage_processor, temp_export_dir):
        """Should detect a valid iMessage iPhone export."""
        create_imessage_iphone_export(temp_export_dir)
        assert imessage_processor.detect(temp_export_dir) is True

    def test_detect_iphone_export_with_attachments(self, imessage_processor, temp_export_dir):
        """Should detect iPhone export with multiple attachments."""
        create_imessage_iphone_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Photo 1", "attachment": "00/00/image1.jpg", "date": 631152000000000000},
                        {"text": "Video", "attachment": "00/01/video.mp4", "date": 631152000000000001},
                    ],
                },
            ]
        )
        assert imessage_processor.detect(temp_export_dir) is True

    def test_reject_missing_sms_db(self, imessage_processor, temp_export_dir):
        """Should reject export without sms.db."""
        sms_dir = temp_export_dir / "SMS"
        attachments_dir = sms_dir / "Attachments"
        attachments_dir.mkdir(parents=True)
        assert imessage_processor.detect(temp_export_dir) is False

    def test_reject_missing_sms_attachments_dir(self, imessage_processor, temp_export_dir):
        """Should reject export without SMS/Attachments directory."""
        sms_dir = temp_export_dir / "SMS"
        sms_dir.mkdir(parents=True)
        create_imessage_chat_db(sms_dir / "sms.db")
        assert imessage_processor.detect(temp_export_dir) is False

    def test_reject_sms_dir_only(self, imessage_processor, temp_export_dir):
        """Should reject empty SMS directory."""
        sms_dir = temp_export_dir / "SMS"
        sms_dir.mkdir(parents=True)
        assert imessage_processor.detect(temp_export_dir) is False


class TestIMessagePreprocessedDetection:
    """Tests for detecting preprocessed iMessage exports."""

    def test_detect_preprocessed_export(self, imessage_processor, temp_export_dir):
        """Should detect a preprocessed iMessage export."""
        media_dir = temp_export_dir / "media"
        media_dir.mkdir(parents=True)

        metadata = {
            "export_info": {
                "export_paths": ["/path/to/messages_export"],
            },
            "conversations": {
                "+1234567890": {
                    "display_name": "Test User",
                    "messages": [],
                }
            },
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert imessage_processor.detect(temp_export_dir) is True

    def test_detect_preprocessed_with_sms_path(self, imessage_processor, temp_export_dir):
        """Should detect preprocessed export with SMS in path."""
        media_dir = temp_export_dir / "media"
        media_dir.mkdir(parents=True)

        metadata = {
            "export_info": {
                "export_paths": ["/path/to/sms_backup"],
            },
            "conversations": {},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert imessage_processor.detect(temp_export_dir) is True

    def test_reject_preprocessed_without_imessage_markers(self, imessage_processor, temp_export_dir):
        """Should reject preprocessed export without iMessage-specific markers."""
        media_dir = temp_export_dir / "media"
        media_dir.mkdir(parents=True)

        # Generic metadata without iMessage markers
        metadata = {
            "export_info": {
                "export_paths": ["/path/to/other_export"],
            },
            "conversations": {},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert imessage_processor.detect(temp_export_dir) is False


class TestIMessageCrossExportDetection:
    """Tests for cross-export detection scenarios."""

    def test_detect_multiple_mac_exports(self, imessage_processor, tmp_path):
        """Should detect multiple Mac exports for consolidation."""
        export1 = tmp_path / "export1"
        export2 = tmp_path / "export2"

        create_imessage_mac_export(export1)
        create_imessage_mac_export(export2)

        assert imessage_processor.detect(export1) is True
        assert imessage_processor.detect(export2) is True

    def test_detect_mixed_mac_iphone_exports(self, imessage_processor, tmp_path):
        """Should detect both Mac and iPhone exports."""
        mac_export = tmp_path / "mac_export"
        iphone_export = tmp_path / "iphone_export"

        create_imessage_mac_export(mac_export)
        create_imessage_iphone_export(iphone_export)

        assert imessage_processor.detect(mac_export) is True
        assert imessage_processor.detect(iphone_export) is True

    def test_supports_consolidation(self, imessage_processor):
        """iMessage processor should support consolidation."""
        assert imessage_processor.supports_consolidation() is True

