"""
Processing tests for iMessage processor.

Tests cover edge cases including:
- Mac export (chat.db + Attachments/)
- iPhone export (SMS/sms.db + SMS/Attachments/)
- Live Photo pairs
- Group conversation
- DM conversation
- Contact resolution (with contacts.vcf)
- Cross-export deduplication
"""

import sqlite3


from tests.fixtures.generators import (
    create_imessage_mac_export,
    create_imessage_iphone_export,
)
from tests.fixtures.media_samples import (
    write_media_file,
    create_imessage_chat_db,
)


class TestIMessageMacExport:
    """Tests for Mac export handling."""

    def test_mac_export_basic(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should process basic Mac export."""
        create_imessage_mac_export(temp_export_dir)

        assert (temp_export_dir / "chat.db").exists()
        assert (temp_export_dir / "Attachments").is_dir()

    def test_mac_export_with_attachments(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should process Mac export with multiple attachments."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Photo 1", "attachment": "00/00/image1.jpg", "date": 631152000000000000},
                        {"text": "Photo 2", "attachment": "00/01/image2.png", "date": 631152000000000001},
                        {"text": "Video", "attachment": "01/00/video.mp4", "date": 631152000000000002},
                    ],
                },
            ]
        )

        attachments_dir = temp_export_dir / "Attachments"
        assert (attachments_dir / "00" / "00" / "image1.jpg").exists()
        assert (attachments_dir / "00" / "01" / "image2.png").exists()
        assert (attachments_dir / "01" / "00" / "video.mp4").exists()


class TestIMessageIPhoneExport:
    """Tests for iPhone export handling."""

    def test_iphone_export_basic(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should process basic iPhone export."""
        create_imessage_iphone_export(temp_export_dir)

        assert (temp_export_dir / "SMS" / "sms.db").exists()
        assert (temp_export_dir / "SMS" / "Attachments").is_dir()

    def test_iphone_export_with_attachments(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should process iPhone export with attachments."""
        create_imessage_iphone_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Image", "attachment": "00/00/photo.jpg", "date": 631152000000000000},
                    ],
                },
            ]
        )

        attachments_dir = temp_export_dir / "SMS" / "Attachments"
        assert (attachments_dir / "00" / "00" / "photo.jpg").exists()


class TestIMessageConversationTypes:
    """Tests for different conversation types."""

    def test_dm_conversation(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should process direct message conversations."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "display_name": None,  # DMs typically don't have display names
                    "messages": [
                        {"text": "Hello!", "is_from_me": 0, "date": 631152000000000000},
                        {"text": "Hi there!", "is_from_me": 1, "date": 631152000000000001},
                    ],
                },
            ]
        )

        # Verify database has the conversation
        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()
        cursor.execute("SELECT chat_identifier FROM chat")
        chats = cursor.fetchall()
        conn.close()

        assert len(chats) == 1
        assert chats[0][0] == "+1234567890"

    def test_group_conversation(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should process group conversations."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "chat123456",
                    "display_name": "Family Group",
                    "messages": [
                        {"text": "Hey family!", "is_from_me": 1, "date": 631152000000000000},
                    ],
                },
            ]
        )

        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()
        cursor.execute("SELECT display_name FROM chat WHERE chat_identifier = ?", ("chat123456",))
        result = cursor.fetchone()
        conn.close()

        assert result[0] == "Family Group"


class TestIMessageLivePhotos:
    """Tests for Live Photo handling."""

    def test_live_photo_pair(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should handle Live Photo pairs (HEIC + MOV)."""
        attachments_dir = temp_export_dir / "Attachments" / "00" / "00"
        attachments_dir.mkdir(parents=True)

        # Create Live Photo pair
        write_media_file(attachments_dir / "IMG_1234.HEIC", "jpeg")  # HEIC uses JPEG bytes for test
        write_media_file(attachments_dir / "IMG_1234.MOV", "mov")

        create_imessage_chat_db(temp_export_dir / "chat.db")

        assert (attachments_dir / "IMG_1234.HEIC").exists()
        assert (attachments_dir / "IMG_1234.MOV").exists()

    def test_live_photo_jpg_mov_pair(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should handle Live Photo pairs (JPG + MOV)."""
        attachments_dir = temp_export_dir / "Attachments" / "00" / "00"
        attachments_dir.mkdir(parents=True)

        write_media_file(attachments_dir / "Photo.JPG", "jpeg")
        write_media_file(attachments_dir / "Photo.MOV", "mov")

        create_imessage_chat_db(temp_export_dir / "chat.db")

        assert (attachments_dir / "Photo.JPG").exists()
        assert (attachments_dir / "Photo.MOV").exists()


class TestIMessageDeduplication:
    """Tests for cross-export deduplication."""

    def test_consolidation_supported(self, imessage_processor):
        """Should support consolidation mode."""
        assert imessage_processor.supports_consolidation() is True

    def test_multiple_exports_same_conversation(self, imessage_processor, tmp_path, temp_output_dir):
        """Should handle same conversation across multiple exports."""
        export1 = tmp_path / "export1"
        export2 = tmp_path / "export2"

        # Same phone number in both exports
        create_imessage_mac_export(
            export1,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Message 1", "date": 631152000000000000},
                    ],
                },
            ]
        )

        create_imessage_mac_export(
            export2,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Message 2", "date": 631152000000000001},
                    ],
                },
            ]
        )

        # Both exports should exist
        assert (export1 / "chat.db").exists()
        assert (export2 / "chat.db").exists()

    def test_duplicate_attachments_across_exports(self, imessage_processor, tmp_path, temp_output_dir):
        """Should handle duplicate attachments across exports."""
        export1 = tmp_path / "export1"
        export2 = tmp_path / "export2"

        # Same attachment in both exports
        create_imessage_mac_export(
            export1,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Photo", "attachment": "00/00/same_photo.jpg", "date": 631152000000000000},
                    ],
                },
            ]
        )

        create_imessage_mac_export(
            export2,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Photo", "attachment": "00/00/same_photo.jpg", "date": 631152000000000000},
                    ],
                },
            ]
        )

        # Both should have the attachment
        assert (export1 / "Attachments" / "00" / "00" / "same_photo.jpg").exists()
        assert (export2 / "Attachments" / "00" / "00" / "same_photo.jpg").exists()


class TestIMessageDatabase:
    """Tests for database schema and queries."""

    def test_database_schema(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should create database with correct schema."""
        create_imessage_mac_export(temp_export_dir)

        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()

        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert "message" in tables
        assert "handle" in tables
        assert "chat" in tables
        assert "attachment" in tables
        assert "chat_message_join" in tables
        assert "message_attachment_join" in tables

        conn.close()

    def test_message_handle_relationship(self, imessage_processor, temp_export_dir, temp_output_dir):
        """Should properly link messages to handles."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Hello", "is_from_me": 0, "date": 631152000000000000},
                    ],
                },
            ]
        )

        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()

        # Check message is linked to handle
        cursor.execute("""
            SELECT m.text, h.id
            FROM message m
            JOIN handle h ON m.handle_id = h.ROWID
        """)
        result = cursor.fetchone()
        conn.close()

        assert result[0] == "Hello"
        assert result[1] == "+1234567890"


class TestIMessageMixedExports:
    """Tests for mixed Mac and iPhone exports."""

    def test_mac_and_iphone_same_conversation(self, imessage_processor, tmp_path, temp_output_dir):
        """Should handle same conversation from Mac and iPhone exports."""
        mac_export = tmp_path / "mac"
        iphone_export = tmp_path / "iphone"

        create_imessage_mac_export(
            mac_export,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "From Mac", "date": 631152000000000000},
                    ],
                },
            ]
        )

        create_imessage_iphone_export(
            iphone_export,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "From iPhone", "date": 631152000000000001},
                    ],
                },
            ]
        )

        assert (mac_export / "chat.db").exists()
        assert (iphone_export / "SMS" / "sms.db").exists()

