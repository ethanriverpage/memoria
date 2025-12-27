"""
Processing tests for Instagram Messages processor.

Tests cover edge cases including:
- Current format (your_instagram_activity/)
- Legacy format (messages/inbox/)
- Conversation with photos
- Conversation with videos
- Group conversation
- Missing media files
"""

from tests.fixtures.generators import create_instagram_messages_export
from tests.fixtures.media_samples import write_media_file


class TestInstagramMessagesFormats:
    """Tests for different Instagram Messages export formats."""

    def test_current_format_detection(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process current format (your_instagram_activity/)."""
        create_instagram_messages_export(temp_export_dir, new_format=True)

        inbox_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox"
        assert inbox_dir.exists()

    def test_legacy_format_detection(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process legacy format (messages/inbox/)."""
        create_instagram_messages_export(temp_export_dir, new_format=False)

        inbox_dir = temp_export_dir / "messages" / "inbox"
        assert inbox_dir.exists()


class TestInstagramMessagesMedia:
    """Tests for media handling in Instagram Messages."""

    def test_conversation_with_photos(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process conversation with photo attachments."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "user_123": {
                    "title": "Test User",
                    "messages": [
                        {
                            "sender": "Test User",
                            "timestamp": "2021-01-01T12:00:00",
                            "content": "Check this photo!",
                            "media": "photo.jpg",
                        },
                    ],
                }
            },
            new_format=True,
        )

        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "user_123"
        )
        assert (conv_dir / "photo.jpg").exists()

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

        write_media_file(conv_dir / "video.mp4", "mp4")

        html_content = """<!DOCTYPE html>
<html>
<head><title>Instagram Messages</title></head>
<body>
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
<div class="_a6-p">
<div class="_3-95 _a6-o">User</div>
<div class="_a6-p">Check this video!</div>
<div class="_3-94 _a6-o">2021-01-01T12:00:00</div>
</div>
</div>
</body>
</html>"""
        (conv_dir / "message_1.html").write_text(html_content)

        assert (conv_dir / "video.mp4").exists()
        assert (conv_dir / "message_1.html").exists()

    def test_missing_media_file(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle conversation referencing missing media."""
        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "user_123"
        )
        conv_dir.mkdir(parents=True)

        # Create HTML that references non-existent media
        html_content = """<!DOCTYPE html>
<html>
<head><title>Instagram Messages</title></head>
<body>
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
<div class="_a6-p">
<div class="_3-95 _a6-o">User</div>
<div class="_a6-p">Media expired or unavailable</div>
<div class="_3-94 _a6-o">2021-01-01T12:00:00</div>
</div>
</div>
</body>
</html>"""
        (conv_dir / "message_1.html").write_text(html_content)

        # No media file created - simulates expired/unavailable media
        assert (conv_dir / "message_1.html").exists()
        assert not (conv_dir / "photo.jpg").exists()


class TestInstagramMessagesConversationTypes:
    """Tests for different conversation types."""

    def test_dm_conversation(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process direct message conversations."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "friend_user_123": {
                    "title": "Friend User",
                    "messages": [
                        {
                            "sender": "Friend User",
                            "timestamp": "2021-01-01T12:00:00",
                            "content": "Hello!",
                        },
                        {
                            "sender": "testuser",
                            "timestamp": "2021-01-01T12:01:00",
                            "content": "Hi there!",
                        },
                    ],
                }
            },
            new_format=True,
        )

        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "friend_user_123"
        )
        assert conv_dir.exists()
        assert (conv_dir / "message_1.html").exists()

    def test_group_conversation(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process group conversations."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "groupchat_456": {
                    "title": "Friend Group",
                    "messages": [
                        {
                            "sender": "User One",
                            "timestamp": "2021-01-01T12:00:00",
                            "content": "Hey everyone!",
                        },
                        {
                            "sender": "User Two",
                            "timestamp": "2021-01-01T12:01:00",
                            "content": "Hello!",
                        },
                        {
                            "sender": "testuser",
                            "timestamp": "2021-01-01T12:02:00",
                            "content": "Hi all!",
                        },
                    ],
                }
            },
            new_format=True,
        )

        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "groupchat_456"
        )
        assert conv_dir.exists()


class TestInstagramMessagesMultipleConversations:
    """Tests for multiple conversation handling."""

    def test_multiple_conversations(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process multiple conversations."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "user1_111": {
                    "title": "User One",
                    "messages": [
                        {
                            "sender": "User One",
                            "timestamp": "2021-01-01T12:00:00",
                            "content": "Hi",
                        }
                    ],
                },
                "user2_222": {
                    "title": "User Two",
                    "messages": [
                        {
                            "sender": "User Two",
                            "timestamp": "2021-01-02T12:00:00",
                            "content": "Hello",
                        }
                    ],
                },
                "user3_333": {
                    "title": "User Three",
                    "messages": [
                        {
                            "sender": "User Three",
                            "timestamp": "2021-01-03T12:00:00",
                            "content": "Hey",
                        }
                    ],
                },
            },
            new_format=True,
        )

        inbox_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox"
        assert (inbox_dir / "user1_111").exists()
        assert (inbox_dir / "user2_222").exists()
        assert (inbox_dir / "user3_333").exists()


class TestInstagramMessagesEdgeCases:
    """Tests for edge cases in Instagram Messages processing."""

    def test_empty_conversation(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle conversation with no messages."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "empty_conv_123": {"title": "Empty Conv", "messages": []},
            },
            new_format=True,
        )

        # Conversation directory might still exist but be empty
        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "empty_conv_123"
        )
        # Generator creates minimal HTML even for empty messages
        assert conv_dir.exists()

    def test_special_characters_in_content(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle messages with special characters."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "user_123": {
                    "title": "Test User",
                    "messages": [
                        {
                            "sender": "Test User",
                            "timestamp": "2021-01-01T12:00:00",
                            "content": "Hello! <script>alert('test')</script> & more",
                        },
                    ],
                }
            },
            new_format=True,
        )

        conv_dir = (
            temp_export_dir
            / "your_instagram_activity"
            / "messages"
            / "inbox"
            / "user_123"
        )
        assert (conv_dir / "message_1.html").exists()


class TestInstagramMessagesRealisticFixtures:
    """Processing tests using committed schema-faithful Instagram Messages fixtures."""

    def test_current_format_html_structure(
        self, instagram_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse current format HTML with <h2> sender."""
        from tests.fixtures.export_tree import copy_export_fixture

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
        from tests.fixtures.export_tree import copy_export_fixture

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
        from tests.fixtures.export_tree import copy_export_fixture

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
        from tests.fixtures.export_tree import copy_export_fixture

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
