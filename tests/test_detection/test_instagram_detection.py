"""
Detection tests for Instagram processors (Messages, Public Media, Instaloader).

Tests verify that each processor correctly identifies valid export
structures and rejects invalid ones.
"""

from tests.fixtures.generators import (
    create_instagram_messages_export,
    create_instagram_public_export,
    create_instaloader_export,
)


class TestInstagramMessagesDetection:
    """Tests for Instagram Messages processor detection."""

    def test_detect_valid_new_format(
        self, instagram_messages_processor, temp_export_dir
    ):
        """Should detect a valid Instagram Messages export (new format)."""
        create_instagram_messages_export(temp_export_dir, new_format=True)
        assert instagram_messages_processor.detect(temp_export_dir) is True

    def test_detect_valid_legacy_format(
        self, instagram_messages_processor, temp_export_dir
    ):
        """Should detect a valid Instagram Messages export (legacy format)."""
        create_instagram_messages_export(temp_export_dir, new_format=False)
        assert instagram_messages_processor.detect(temp_export_dir) is True

    def test_detect_with_multiple_conversations(
        self, instagram_messages_processor, temp_export_dir
    ):
        """Should detect export with multiple conversations."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "user1_123": {
                    "title": "User 1",
                    "messages": [
                        {
                            "sender": "User 1",
                            "timestamp": "2021-01-01T12:00:00",
                            "content": "Hi",
                        }
                    ],
                },
                "user2_456": {
                    "title": "User 2",
                    "messages": [
                        {
                            "sender": "User 2",
                            "timestamp": "2021-01-02T12:00:00",
                            "content": "Hello",
                        }
                    ],
                },
            },
        )
        assert instagram_messages_processor.detect(temp_export_dir) is True

    def test_reject_missing_inbox_dir(
        self, instagram_messages_processor, temp_export_dir
    ):
        """Should reject export without inbox directory."""
        assert instagram_messages_processor.detect(temp_export_dir) is False

    def test_reject_empty_inbox(self, instagram_messages_processor, temp_export_dir):
        """Should reject export with empty inbox."""
        inbox_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox"
        inbox_dir.mkdir(parents=True)
        assert instagram_messages_processor.detect(temp_export_dir) is False

    def test_reject_inbox_without_message_html(
        self, instagram_messages_processor, temp_export_dir
    ):
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
                {
                    "filename": "202101/photo1.jpg",
                    "caption": "Test",
                    "timestamp": "2021-01-01T12:00:00",
                    "archived": False,
                },
            ],
            include_archived=False,
        )
        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_detect_with_archived_only(
        self, instagram_public_processor, temp_export_dir
    ):
        """Should detect export with only archived posts."""
        create_instagram_public_export(
            temp_export_dir,
            posts=[
                {
                    "filename": "202101/photo1.jpg",
                    "caption": "Test",
                    "timestamp": "2021-01-01T12:00:00",
                    "archived": True,
                },
            ],
            include_archived=True,
        )
        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_reject_missing_media_dir(
        self, instagram_public_processor, temp_export_dir
    ):
        """Should reject export without media directory."""
        assert instagram_public_processor.detect(temp_export_dir) is False

    def test_reject_empty_media_dir(self, instagram_public_processor, temp_export_dir):
        """Should reject export with empty media directory."""
        media_dir = temp_export_dir / "media"
        media_dir.mkdir(parents=True)
        assert instagram_public_processor.detect(temp_export_dir) is False

    def test_reject_media_without_posts_or_archived(
        self, instagram_public_processor, temp_export_dir
    ):
        """Should reject media directory without posts or archived_posts."""
        media_dir = temp_export_dir / "media"
        other_dir = media_dir / "stories"
        other_dir.mkdir(parents=True)
        (other_dir / "story.jpg").write_bytes(b"test")
        assert instagram_public_processor.detect(temp_export_dir) is False

    def test_reject_media_structure_without_html_metadata(
        self, instagram_public_processor, temp_export_dir
    ):
        """Should reject export with media/posts structure but no HTML metadata.

        This tests the fix for the detect/preprocess mismatch bug:
        exports must have HTML metadata directories to be detected.
        """
        # Create media structure with YYYYMM folders
        posts_dir = temp_export_dir / "media" / "posts" / "202101"
        posts_dir.mkdir(parents=True)
        (posts_dir / "photo.jpg").write_bytes(b"test")

        # No HTML metadata directory (your_instagram_activity/media or content)
        assert instagram_public_processor.detect(temp_export_dir) is False

    def test_reject_empty_html_metadata_dir(
        self, instagram_public_processor, temp_export_dir
    ):
        """Should reject export with empty HTML metadata directory.

        HTML metadata directory must contain at least one .html file.
        """
        # Create media structure
        posts_dir = temp_export_dir / "media" / "posts" / "202101"
        posts_dir.mkdir(parents=True)
        (posts_dir / "photo.jpg").write_bytes(b"test")

        # Create empty HTML metadata directory
        html_dir = temp_export_dir / "your_instagram_activity" / "media"
        html_dir.mkdir(parents=True)

        assert instagram_public_processor.detect(temp_export_dir) is False


class TestInstaloaderFormatDetection:
    """Tests for Instaloader format detection via Instagram Public Media processor.

    Note: Instaloader format is now handled by the unified Instagram Public Media processor.
    """

    def test_detect_valid_export(self, instagram_public_processor, temp_export_dir):
        """Should detect valid Instaloader scrape data via Instagram Public Media."""
        create_instaloader_export(temp_export_dir)
        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_detect_single_file(self, instagram_public_processor, temp_export_dir):
        """Should reject export with less than 3 UTC-timestamped files."""
        # Detection requires at least 3 files matching the pattern
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                },
            ],
        )
        # Requires 3+ files for detection
        assert instagram_public_processor.detect(temp_export_dir) is False

    def test_detect_multiple_files(self, instagram_public_processor, temp_export_dir):
        """Should detect Instaloader export with 3+ UTC-timestamped files."""
        # Detection requires at least 3 files matching YYYY-MM-DD_HH-MM-SS_UTC[_N].ext
        # Carousel suffixes (_1, _2) are supported by the detection pattern.
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": "First",
                },
                {
                    "timestamp": "2021-01-02_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                },
                {
                    "timestamp": "2021-01-03_12-00-00",
                    "extension": "mp4",
                    "caption": None,
                },
            ],
        )
        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_reject_empty_directory(self, instagram_public_processor, temp_export_dir):
        """Should reject empty directory for Instaloader format."""
        assert instagram_public_processor.detect(temp_export_dir) is False

    def test_reject_non_utc_files(self, instagram_public_processor, temp_export_dir):
        """Should reject directory with non-UTC-timestamped files."""
        (temp_export_dir / "regular_photo.jpg").write_bytes(b"test")
        (temp_export_dir / "another_file.png").write_bytes(b"test")
        assert instagram_public_processor.detect(temp_export_dir) is False

    def test_detect_png_files(self, instagram_public_processor, temp_export_dir):
        """Should detect Instaloader exports with PNG files."""
        # Detection pattern should support common image formats beyond jpg
        from tests.fixtures.media_samples import write_media_file

        write_media_file(temp_export_dir / "2021-01-01_12-00-00_UTC.png", "png")
        write_media_file(temp_export_dir / "2021-01-02_12-00-00_UTC.png", "png")
        write_media_file(temp_export_dir / "2021-01-03_12-00-00_UTC.png", "png")

        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_detect_webp_files(self, instagram_public_processor, temp_export_dir):
        """Should detect Instaloader exports with WebP files."""
        from tests.fixtures.media_samples import write_media_file

        write_media_file(temp_export_dir / "2021-01-01_12-00-00_UTC.webp", "webp")
        write_media_file(temp_export_dir / "2021-01-02_12-00-00_UTC.webp", "webp")
        write_media_file(temp_export_dir / "2021-01-03_12-00-00_UTC.webp", "webp")

        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_detect_mov_files(self, instagram_public_processor, temp_export_dir):
        """Should detect Instaloader exports with MOV video files."""
        from tests.fixtures.media_samples import write_media_file

        write_media_file(temp_export_dir / "2021-01-01_12-00-00_UTC.mov", "mov")
        write_media_file(temp_export_dir / "2021-01-02_12-00-00_UTC.mov", "mov")
        write_media_file(temp_export_dir / "2021-01-03_12-00-00_UTC.mov", "mov")

        assert instagram_public_processor.detect(temp_export_dir) is True

    def test_detect_mixed_extensions(self, instagram_public_processor, temp_export_dir):
        """Should detect Instaloader exports with mixed file extensions."""
        from tests.fixtures.media_samples import write_media_file

        write_media_file(temp_export_dir / "2021-01-01_12-00-00_UTC.jpg", "jpeg")
        write_media_file(temp_export_dir / "2021-01-02_12-00-00_UTC.png", "png")
        write_media_file(temp_export_dir / "2021-01-03_12-00-00_UTC.mp4", "mp4")

        assert instagram_public_processor.detect(temp_export_dir) is True


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

    def test_instaloader_detected_by_public_processor(
        self, instagram_public_processor, temp_export_dir
    ):
        """Instaloader format should be detected by Instagram Public Media processor."""
        create_instaloader_export(temp_export_dir)

        # Instaloader format is now handled by the unified Instagram Public Media processor
        assert instagram_public_processor.detect(temp_export_dir) is True


class TestInstagramMessagesRealisticFixtures:
    """Tests using committed schema-faithful Instagram Messages fixtures."""

    def test_detect_current_format_fixture(
        self, instagram_messages_processor, temp_export_dir
    ):
        """Should detect current format (2023+) with <h2> sender elements."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "instagram_messages", "current_format_doc_html", temp_export_dir
        )
        assert instagram_messages_processor.detect(temp_export_dir) is True

        # Verify structure matches docs
        inbox_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox"
        assert inbox_dir.exists()
        assert (inbox_dir / "friend_user_123456" / "message_1.html").exists()

    def test_detect_legacy_format_fixture(
        self, instagram_messages_processor, temp_export_dir
    ):
        """Should detect legacy format (pre-2023) with <div> sender elements."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "instagram_messages", "legacy_format_doc_html", temp_export_dir
        )
        assert instagram_messages_processor.detect(temp_export_dir) is True

        # Verify structure matches legacy docs
        inbox_dir = temp_export_dir / "messages" / "inbox"
        assert inbox_dir.exists()
        assert (inbox_dir / "old_friend_789012" / "message_1.html").exists()

    def test_detect_missing_media_reference_fixture(
        self, instagram_messages_processor, temp_export_dir
    ):
        """Should detect fixture with expired/missing media references."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "instagram_messages", "missing_media_reference", temp_export_dir
        )
        assert instagram_messages_processor.detect(temp_export_dir) is True

        # Verify HTML references media that doesn't exist
        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "expired_media_user"
        )
        html_content = (conv_dir / "message_1.html").read_text()
        assert "expired_photo.jpg" in html_content
        # But the file doesn't exist
        assert not (conv_dir / "photos" / "expired_photo.jpg").exists()


class TestInstagramPublicRealisticFixtures:
    """Tests using committed schema-faithful Instagram Public fixtures."""

    def test_detect_posts_and_archived_posts_fixture(
        self, instagram_public_processor, temp_export_dir
    ):
        """Should detect fixture with both posts and archived_posts."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "instagram_public", "posts_and_archived_posts", temp_export_dir
        )
        assert instagram_public_processor.detect(temp_export_dir) is True

        # Verify YYYYMM folder structure
        media_dir = temp_export_dir / "media"
        assert (media_dir / "posts" / "202301" / "photo_123.jpg").exists()
        assert (media_dir / "posts" / "202302" / "video_456.mp4").exists()
        assert (media_dir / "archived_posts" / "202212" / "archived_photo.jpg").exists()

    def test_posts_json_metadata_format(
        self, instagram_public_processor, temp_export_dir
    ):
        """Should have JSON metadata files with documented fields."""
        from tests.fixtures.export_tree import copy_export_fixture
        import json

        copy_export_fixture(
            "instagram_public", "posts_and_archived_posts", temp_export_dir
        )

        json_path = (
            temp_export_dir / "media" / "posts" / "202301" / "photo_123.jpg.json"
        )
        metadata = json.loads(json_path.read_text())

        assert "caption" in metadata
        assert "taken_at" in metadata
        assert "#photography" in metadata["caption"]
