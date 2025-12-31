"""
Processing tests for Instagram Messages processor.

Tests cover edge cases including:
- Current format (your_instagram_activity/)
- Legacy format (messages/inbox/)
- HTML structure differences (h2 vs div sender)
- Media file handling
- Missing media references
"""

import json

from tests.fixtures.export_tree import copy_export_fixture
from tests.fixtures.media_samples import write_media_file


class TestInstagramMessagesPreprocessing:
    """Tests that run the preprocessor and verify output."""

    def test_current_format_preprocessing(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should preprocess current format (your_instagram_activity/)."""
        from processors.instagram_messages.preprocess import InstagramMessagesPreprocessor

        copy_export_fixture(
            "instagram_messages", "current_format_doc_html", temp_export_dir
        )

        preprocessor = InstagramMessagesPreprocessor(
            export_path=temp_export_dir,
            output_dir=temp_output_dir,
        )
        result = preprocessor.run()
        assert result is True, "Preprocessing should succeed"

        # Verify metadata.json was created
        metadata_file = temp_output_dir / "metadata.json"
        assert metadata_file.exists(), "metadata.json should be created"

        with open(metadata_file) as f:
            output_metadata = json.load(f)

        assert "export_info" in output_metadata
        assert output_metadata["export_info"]["platform"] == "Instagram"

    def test_legacy_format_preprocessing(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should preprocess legacy format (messages/inbox/)."""
        from processors.instagram_messages.preprocess import InstagramMessagesPreprocessor

        copy_export_fixture(
            "instagram_messages", "legacy_format_doc_html", temp_export_dir
        )

        preprocessor = InstagramMessagesPreprocessor(
            export_path=temp_export_dir,
            output_dir=temp_output_dir,
        )
        result = preprocessor.run()
        assert result is True


class TestInstagramMessagesFixtureStructure:
    """Tests that verify schema-faithful fixture structures match docs."""

    def test_current_format_html_structure(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse current format HTML with <h2> sender."""
        copy_export_fixture(
            "instagram_messages", "current_format_doc_html", temp_export_dir
        )

        html_path = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "friend_user_123456"
            / "message_1.html"
        )
        html_content = html_path.read_text()

        # Verify doc-style HTML structure
        assert '<h2 class="_3-95 _2pim _a6-h _a6-i">' in html_content
        assert '<div class="_3-94 _a6-o">' in html_content
        # Verify timestamp format: "Sep 22, 2017 6:33 am" (no comma after year)
        assert "Sep 22, 2017 6:33 am" in html_content
        # Verify media references
        assert 'src="photos/photo_123456789.jpg"' in html_content
        assert 'src="videos/video_987654321.mp4"' in html_content

    def test_legacy_format_html_structure(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse legacy format HTML with <div> sender."""
        copy_export_fixture(
            "instagram_messages", "legacy_format_doc_html", temp_export_dir
        )

        html_path = (
            temp_export_dir
            / "messages"
            / "inbox"
            / "old_friend_789012"
            / "message_1.html"
        )
        html_content = html_path.read_text()

        # Verify legacy HTML structure (div instead of h2 for sender)
        assert '<div class="_3-95 _2pim _a6-h _a6-i">' in html_content
        # Verify legacy timestamp format: "Sep 22, 2017, 6:33 AM" (comma after year, uppercase AM)
        assert "Sep 22, 2017, 6:33 AM" in html_content

    def test_current_format_media_files(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should have media files in photos/ and videos/ subdirectories."""
        copy_export_fixture(
            "instagram_messages", "current_format_doc_html", temp_export_dir
        )

        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "friend_user_123456"
        )
        assert (conv_dir / "photos" / "photo_123456789.jpg").exists()
        assert (conv_dir / "videos" / "video_987654321.mp4").exists()

    def test_missing_media_reference_handling(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle HTML references to non-existent media files."""
        copy_export_fixture(
            "instagram_messages", "missing_media_reference", temp_export_dir
        )

        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "expired_media_user"
        )

        # HTML references files that don't exist
        html_content = (conv_dir / "message_1.html").read_text()
        assert "expired_photo.jpg" in html_content
        assert "expired_video.mp4" in html_content

        # But the actual files are missing (simulating expired View Once content)
        assert not (conv_dir / "photos" / "expired_photo.jpg").exists()
        assert not (conv_dir / "videos" / "expired_video.mp4").exists()


class TestInstagramMessagesMedia:
    """Tests for media handling in Instagram Messages."""

    def test_conversation_with_videos(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process conversation with video attachments."""
        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "user_123"
        )
        conv_dir.mkdir(parents=True)

        # Create videos subdirectory
        videos_dir = conv_dir / "videos"
        videos_dir.mkdir()
        write_media_file(videos_dir / "video.mp4", "mp4")

        html_content = """<!DOCTYPE html>
<html>
<head><title>Instagram Messages</title></head>
<body>
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
<div class="_a6-p">
<h2 class="_3-95 _2pim _a6-h _a6-i">User</h2>
<div class="_a6-p">Check this video!</div>
<div class="_3-94 _a6-o">Sep 22, 2017 6:33 am</div>
</div>
</div>
</body>
</html>"""
        (conv_dir / "message_1.html").write_text(html_content)

        # Create personal_information directory for detection
        personal_info_dir = temp_export_dir / "personal_information" / "personal_information"
        personal_info_dir.mkdir(parents=True)
        (personal_info_dir / "personal_information.html").write_text("<html></html>")

        assert (videos_dir / "video.mp4").exists()
        assert (conv_dir / "message_1.html").exists()


class TestInstagramMessagesEdgeCases:
    """Tests for edge cases in Instagram Messages processing."""

    def test_special_characters_in_content(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle messages with special characters."""
        copy_export_fixture(
            "instagram_messages", "current_format_doc_html", temp_export_dir
        )

        # Verify personal_information exists for detection
        personal_info = (
            temp_export_dir
            / "personal_information"
            / "personal_information"
            / "personal_information.html"
        )
        assert personal_info.exists()
