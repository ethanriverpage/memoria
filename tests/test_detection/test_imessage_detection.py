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

    def test_detect_mac_export_with_attachments(
        self, imessage_processor, temp_export_dir
    ):
        """Should detect Mac export with multiple attachments."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {
                            "text": "Photo 1",
                            "attachment": "00/00/image1.jpg",
                            "date": 631152000000000000,
                        },
                        {
                            "text": "Photo 2",
                            "attachment": "00/01/image2.jpg",
                            "date": 631152000000000001,
                        },
                    ],
                },
            ],
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

    def test_detect_iphone_export_with_attachments(
        self, imessage_processor, temp_export_dir
    ):
        """Should detect iPhone export with multiple attachments."""
        create_imessage_iphone_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {
                            "text": "Photo 1",
                            "attachment": "00/00/image1.jpg",
                            "date": 631152000000000000,
                        },
                        {
                            "text": "Video",
                            "attachment": "00/01/video.mp4",
                            "date": 631152000000000001,
                        },
                    ],
                },
            ],
        )
        assert imessage_processor.detect(temp_export_dir) is True

    def test_reject_missing_sms_db(self, imessage_processor, temp_export_dir):
        """Should reject export without sms.db."""
        sms_dir = temp_export_dir / "SMS"
        attachments_dir = sms_dir / "Attachments"
        attachments_dir.mkdir(parents=True)
        assert imessage_processor.detect(temp_export_dir) is False

    def test_reject_missing_sms_attachments_dir(
        self, imessage_processor, temp_export_dir
    ):
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
                "export_path": str(temp_export_dir),
                "export_username": "testuser",
                "processed_date": "2021-01-01T00:00:00",
                "platform": "iMessage",
                "service": None,
                "export_paths": ["/path/to/messages_export"],
            },
            "conversations": {
                "+1234567890": {
                    "display_name": "Test User",
                    "messages": [],
                }
            },
            "file_hashes": {},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert imessage_processor.detect(temp_export_dir) is True

    def test_detect_preprocessed_with_sms_path(
        self, imessage_processor, temp_export_dir
    ):
        """Should detect preprocessed export with SMS in path."""
        media_dir = temp_export_dir / "media"
        media_dir.mkdir(parents=True)

        metadata = {
            "export_info": {
                "export_path": str(temp_export_dir),
                "export_username": "testuser",
                "processed_date": "2021-01-01T00:00:00",
                "platform": "iMessage",
                "service": None,
                "export_paths": ["/path/to/sms_backup"],
            },
            "conversations": {},
            "file_hashes": {},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert imessage_processor.detect(temp_export_dir) is True

    def test_reject_preprocessed_without_imessage_markers(
        self, imessage_processor, temp_export_dir
    ):
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


class TestIMessageRealisticFixtures:
    """Tests using committed schema-faithful iMessage fixtures."""

    def test_detect_mac_basic_fixture(self, imessage_processor, temp_export_dir):
        """Should detect the mac_basic fixture."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage", "mac_basic", temp_export_dir)
        assert imessage_processor.detect(temp_export_dir) is True

        # Verify structure matches docs
        assert (temp_export_dir / "chat.db").exists()
        assert (temp_export_dir / "Attachments").is_dir()

    def test_detect_iphone_basic_fixture(self, imessage_processor, temp_export_dir):
        """Should detect the iphone_basic fixture."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage", "iphone_basic", temp_export_dir)
        assert imessage_processor.detect(temp_export_dir) is True

        # Verify structure matches docs
        assert (temp_export_dir / "SMS" / "sms.db").exists()
        assert (temp_export_dir / "SMS" / "Attachments").is_dir()

    def test_detect_live_photo_lp_image_fixture(
        self, imessage_processor, temp_export_dir
    ):
        """Should detect fixture with lp_image Live Photo pair."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage", "live_photo_lp_image_pair", temp_export_dir)
        assert imessage_processor.detect(temp_export_dir) is True

        # Verify both HEIC and MOV exist (MOV not tracked in DB)
        lp_dir = (
            temp_export_dir / "Attachments" / "lp" / "00" / "LIVEPHOTO-UUID"
        )
        assert (lp_dir / "lp_image.HEIC").exists()
        assert (lp_dir / "lp_image.MOV").exists()

    def test_detect_ventura_attributedbody_fixture(
        self, imessage_processor, temp_export_dir
    ):
        """Should detect fixture with attributedBody instead of text."""
        from tests.fixtures.export_tree import copy_export_fixture
        import sqlite3

        copy_export_fixture("imessage", "ventura_attributedbody", temp_export_dir)
        assert imessage_processor.detect(temp_export_dir) is True

        # Verify message has NULL text and populated attributedBody
        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()
        cursor.execute("SELECT text, attributedBody FROM message WHERE ROWID = 1")
        text, attributed_body = cursor.fetchone()
        conn.close()

        assert text is None
        assert attributed_body is not None
        assert len(attributed_body) > 0

    def test_detect_with_contacts_vcf_fixture(
        self, imessage_processor, temp_export_dir
    ):
        """Should detect fixture with contacts.vcf for name resolution."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage", "with_contacts_vcf", temp_export_dir)
        assert imessage_processor.detect(temp_export_dir) is True

        # Verify contacts.vcf exists
        assert (temp_export_dir / "contacts.vcf").exists()

        # Verify it contains our test contact
        vcf_content = (temp_export_dir / "contacts.vcf").read_text()
        assert "John Doe" in vcf_content
        assert "+14045551234" in vcf_content
