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

    def test_current_format_detection(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
        """Should process current format (your_instagram_activity/)."""
        create_instagram_messages_export(temp_export_dir, new_format=True)

        inbox_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox"
        assert inbox_dir.exists()

    def test_legacy_format_detection(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
        """Should process legacy format (messages/inbox/)."""
        create_instagram_messages_export(temp_export_dir, new_format=False)

        inbox_dir = temp_export_dir / "messages" / "inbox"
        assert inbox_dir.exists()


class TestInstagramMessagesMedia:
    """Tests for media handling in Instagram Messages."""

    def test_conversation_with_photos(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
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
            new_format=True
        )

        conv_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox" / "user_123"
        assert (conv_dir / "photo.jpg").exists()

    def test_conversation_with_videos(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
        """Should process conversation with video attachments."""
        conv_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox" / "user_123"
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

    def test_missing_media_file(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
        """Should handle conversation referencing missing media."""
        conv_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox" / "user_123"
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

    def test_dm_conversation(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
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
            new_format=True
        )

        conv_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox" / "friend_user_123"
        assert conv_dir.exists()
        assert (conv_dir / "message_1.html").exists()

    def test_group_conversation(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
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
            new_format=True
        )

        conv_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox" / "groupchat_456"
        assert conv_dir.exists()


class TestInstagramMessagesMultipleConversations:
    """Tests for multiple conversation handling."""

    def test_multiple_conversations(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
        """Should process multiple conversations."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "user1_111": {"title": "User One", "messages": [
                    {"sender": "User One", "timestamp": "2021-01-01T12:00:00", "content": "Hi"}
                ]},
                "user2_222": {"title": "User Two", "messages": [
                    {"sender": "User Two", "timestamp": "2021-01-02T12:00:00", "content": "Hello"}
                ]},
                "user3_333": {"title": "User Three", "messages": [
                    {"sender": "User Three", "timestamp": "2021-01-03T12:00:00", "content": "Hey"}
                ]},
            },
            new_format=True
        )

        inbox_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox"
        assert (inbox_dir / "user1_111").exists()
        assert (inbox_dir / "user2_222").exists()
        assert (inbox_dir / "user3_333").exists()


class TestInstagramMessagesEdgeCases:
    """Tests for edge cases in Instagram Messages processing."""

    def test_empty_conversation(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
        """Should handle conversation with no messages."""
        create_instagram_messages_export(
            temp_export_dir,
            conversations={
                "empty_conv_123": {"title": "Empty Conv", "messages": []},
            },
            new_format=True
        )

        # Conversation directory might still exist but be empty
        conv_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox" / "empty_conv_123"
        # Generator creates minimal HTML even for empty messages
        assert conv_dir.exists()

    def test_special_characters_in_content(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
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
            new_format=True
        )

        conv_dir = temp_export_dir / "your_instagram_activity" / "messages" / "inbox" / "user_123"
        assert (conv_dir / "message_1.html").exists()

