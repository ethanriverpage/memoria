"""
Detection tests for Instagram processors (Messages, Public Media, Old Format).

Tests verify that each processor correctly identifies valid export
structures and rejects invalid ones.
"""



from tests.fixtures.generators import (
    create_instagram_messages_export,
    create_instagram_public_export,
    create_instagram_old_export,
)


class TestInstagramMessagesDetection:
    """Tests for Instagram Messages processor detection."""

    def test_detect_valid_new_format(self, instagram_messages_processor, temp_export_dir):
        """Should detect a valid Instagram Messages export (new format)."""
        create_instagram_messages_export(temp_export_dir, new_format=True)
        assert instagram_messages_processor.detect(temp_export_dir) is True

    def test_detect_valid_legacy_format(self, instagram_messages_processor, temp_export_dir):
        """Should detect a valid Instagram Messages export (legacy format)."""
        create_instagram_messages_export(temp_export_dir, new_format=False)
        assert instagram_messages_processor.detect(temp_export_dir) is True

    def test_detect_with_multiple_conversations(self, instagram_messages_processor, temp_export_dir):
        """Should detect export with multiple conversations."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "user1_123": {"title": "User 1", "messages": [{"sender": "User 1", "timestamp": "2021-01-01T12:00:00", "content": "Hi"}]},
                "user2_456": {"title": "User 2", "messages": [{"sender": "User 2", "timestamp": "2021-01-02T12:00:00", "content": "Hello"}]},
            }
        )
        assert instagram_messages_processor.detect(temp_export_dir) is True

    def test_reject_missing_inbox_dir(self, instagram_messages_processor, temp_export_dir):
        """Should reject export without inbox directory."""
        assert instagram_messages_processor.detect(temp_export_dir) is False

    def test_reject_empty_inbox(self, instagram_messages_processor, temp_export_dir):
        """Should reject export with empty inbox."""
        inbox_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox"
        inbox_dir.mkdir(parents=True)
        assert instagram_messages_processor.detect(temp_export_dir) is False

    def test_reject_inbox_without_message_html(self, instagram_messages_processor, temp_export_dir):
        """Should reject inbox with conversation folders but no message_N.html files."""
        inbox_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox"
        conv_dir = inbox_dir / "someuser_123"
        conv_dir.mkdir(parents=True)
        # Create non-message HTML file
        (conv_dir / "other.html").write_text("<html></html>")
        assert instagram_messages_processor.detect(temp_export_dir) is False


class TestInstagramPublicDetection:
    """Tests for Instagram Public Media processor detection."""

    def test_detect_valid_export(self, instagram_public_processor, temp_export_dir):
        """Should detect a valid Instagram Public Media export."""
        create_instagram_public_export(temp_export_dir)
        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_detect_with_posts_only(self, instagram_public_processor, temp_export_dir):
        """Should detect export with only posts (no archived)."""
        create_instagram_public_export(
            temp_export_dir,
            posts=[
                {"filename": "202101/photo1.jpg", "caption": "Test", "timestamp": "2021-01-01T12:00:00", "archived": False},
            ],
            include_archived=False
        )
        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_detect_with_archived_only(self, instagram_public_processor, temp_export_dir):
        """Should detect export with only archived posts."""
        create_instagram_public_export(
            temp_export_dir,
            posts=[
                {"filename": "202101/photo1.jpg", "caption": "Test", "timestamp": "2021-01-01T12:00:00", "archived": True},
            ],
            include_archived=True
        )
        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_reject_missing_media_dir(self, instagram_public_processor, temp_export_dir):
        """Should reject export without media directory."""
        assert instagram_public_processor.detect(temp_export_dir) is False

    def test_reject_empty_media_dir(self, instagram_public_processor, temp_export_dir):
        """Should reject export with empty media directory."""
        media_dir = temp_export_dir / "media"
        media_dir.mkdir(parents=True)
        assert instagram_public_processor.detect(temp_export_dir) is False

    def test_reject_media_without_posts_or_archived(self, instagram_public_processor, temp_export_dir):
        """Should reject media directory without posts or archived_posts."""
        media_dir = temp_export_dir / "media"
        other_dir = media_dir / "stories"
        other_dir.mkdir(parents=True)
        (other_dir / "story.jpg").write_bytes(b"test")
        assert instagram_public_processor.detect(temp_export_dir) is False


class TestInstagramOldDetection:
    """Tests for Instagram Old Format processor detection."""

    def test_detect_valid_export(self, instagram_old_processor, temp_export_dir):
        """Should detect a valid Instagram Old Format export."""
        create_instagram_old_export(temp_export_dir)
        assert instagram_old_processor.detect(temp_export_dir) is True

    def test_detect_single_file(self, instagram_old_processor, temp_export_dir):
        """Should reject export with less than 3 UTC-timestamped files."""
        # Detection requires at least 3 files matching the pattern
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "caption": None},
            ]
        )
        # Requires 3+ files for detection
        assert instagram_old_processor.detect(temp_export_dir) is False

    def test_detect_multiple_files(self, instagram_old_processor, temp_export_dir):
        """Should detect export with 3+ UTC-timestamped files."""
        # Detection requires at least 3 files matching YYYY-MM-DD_HH-MM-SS_UTC.ext
        # Note: Carousel suffixes (_1, _2) don't match the strict detection pattern
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "caption": "First"},
                {"timestamp": "2021-01-02_12-00-00", "extension": "jpg", "caption": None},
                {"timestamp": "2021-01-03_12-00-00", "extension": "mp4", "caption": None},
            ]
        )
        assert instagram_old_processor.detect(temp_export_dir) is True

    def test_reject_empty_directory(self, instagram_old_processor, temp_export_dir):
        """Should reject empty directory."""
        assert instagram_old_processor.detect(temp_export_dir) is False

    def test_reject_non_utc_files(self, instagram_old_processor, temp_export_dir):
        """Should reject directory with non-UTC-timestamped files."""
        (temp_export_dir / "regular_photo.jpg").write_bytes(b"test")
        (temp_export_dir / "another_file.png").write_bytes(b"test")
        assert instagram_old_processor.detect(temp_export_dir) is False


class TestInstagramMultiProcessorDetection:
    """Tests for detecting multiple Instagram processors in a single export."""

    def test_detect_messages_and_public(
        self, instagram_messages_processor, instagram_public_processor, temp_export_dir
    ):
        """Should detect both Messages and Public Media in same export."""
        create_instagram_messages_export(temp_export_dir)
        create_instagram_public_export(temp_export_dir)

        assert instagram_messages_processor.detect(temp_export_dir) is True
        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_old_format_exclusive(
        self, instagram_old_processor, instagram_public_processor, temp_export_dir
    ):
        """Old format should be distinct from public media format."""
        create_instagram_old_export(temp_export_dir)

        assert instagram_old_processor.detect(temp_export_dir) is True
        # Public processor should not match old format (no media/posts structure)
        assert instagram_public_processor.detect(temp_export_dir) is False

