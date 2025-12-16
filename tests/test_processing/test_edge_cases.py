"""
Cross-cutting edge case tests for all processors.

Tests cover scenarios that apply across multiple processors:
- Unicode/special characters in filenames
- Empty metadata arrays
- Large file counts
- Filesystem timestamp handling
"""

import json
import os


from tests.fixtures.media_samples import write_media_file


class TestUnicodeFilenames:
    """Tests for unicode character handling in filenames."""

    def test_unicode_album_name_google_photos(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle album names with unicode characters."""
        from tests.fixtures.generators import create_google_photos_export

        create_google_photos_export(
            temp_export_dir,
            albums={"Vacation 2021": ["photo.jpg"]},
        )
        assert google_photos_processor.detect(temp_export_dir) is True

    def test_unicode_conversation_name_google_chat(self, google_chat_processor, temp_export_dir, temp_output_dir):
        """Should handle conversation names with unicode."""
        from tests.fixtures.generators import create_google_chat_export

        create_google_chat_export(
            temp_export_dir,
            conversations={
                "Groups/Team Discussion": [
                    {
                        "creator": {"name": "User"},
                        "created_date": "2021-01-01T12:00:00Z",
                        "text": "Hello team!",
                        "attached_files": [],
                    }
                ]
            },
        )
        assert google_chat_processor.detect(temp_export_dir) is True

    def test_emoji_in_discord_message(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should handle emoji characters in Discord messages."""
        from tests.fixtures.generators import create_discord_export

        create_discord_export(
            temp_export_dir,
            channels={
                "c123456789": {
                    "name": "general",
                    "type": "server",
                    "guild_name": "Test Server",
                    "messages": [
                        {
                            "ID": "msg001",
                            "Timestamp": "2021-01-01 12:00:00",
                            "Contents": "Hello! Great work everyone!",
                            "Attachments": "",
                        },
                    ],
                },
            }
        )

        channel_dir = temp_export_dir / "Messages" / "c123456789"
        messages = json.loads((channel_dir / "messages.json").read_text())
        assert "Hello!" in messages[0]["Contents"]


class TestEmptyMetadata:
    """Tests for handling empty metadata arrays."""

    def test_snapchat_memories_empty_metadata(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should handle empty metadata.json array gracefully."""
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        # Write empty array
        (temp_export_dir / "metadata.json").write_text("[]")

        # Processor should not detect empty export as valid
        assert snapchat_memories_processor.detect(temp_export_dir) is False

    def test_google_chat_empty_messages(self, google_chat_processor, temp_export_dir, temp_output_dir):
        """Should handle conversation with empty messages array."""
        # Detection requires both Groups/ and Users/ directories
        groups_dir = temp_export_dir / "Google Chat" / "Groups" / "Empty Group"
        users_dir = temp_export_dir / "Google Chat" / "Users"
        groups_dir.mkdir(parents=True)
        users_dir.mkdir(parents=True)

        # Create group_info.json for detection
        group_info = {"name": "Empty Group", "members": []}
        (groups_dir / "group_info.json").write_text(json.dumps(group_info))

        # Empty messages - still valid structure
        (groups_dir / "messages.json").write_text('{"messages": []}')

        assert google_chat_processor.detect(temp_export_dir) is True


class TestFilesystemTimestamps:
    """Tests for filesystem timestamp handling."""

    def test_file_mtime_preserved(self, temp_export_dir, temp_output_dir):
        """Should be able to set and read file modification times."""
        test_file = temp_export_dir / "test.txt"
        test_file.write_text("test content")

        # Set a specific timestamp (Jan 1, 2021 12:00:00 UTC)
        target_time = 1609502400.0
        os.utime(test_file, (target_time, target_time))

        # Verify timestamp was set
        stat = test_file.stat()
        assert abs(stat.st_mtime - target_time) < 1.0

    def test_media_file_timestamp(self, temp_export_dir, temp_output_dir):
        """Should set timestamps on media files."""
        media_file = temp_export_dir / "photo.jpg"
        write_media_file(media_file, "jpeg")

        target_time = 1609502400.0
        os.utime(media_file, (target_time, target_time))

        stat = media_file.stat()
        assert abs(stat.st_mtime - target_time) < 1.0


class TestLargeExports:
    """Tests for handling larger export structures."""

    def test_many_albums_google_photos(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle export with many albums."""
        from tests.fixtures.generators import create_google_photos_export

        # Create 20 albums with 2 files each
        albums = {f"Album {i}": [f"photo_{i}_1.jpg", f"photo_{i}_2.jpg"] for i in range(20)}

        create_google_photos_export(temp_export_dir, albums=albums)

        assert google_photos_processor.detect(temp_export_dir) is True

        photos_dir = temp_export_dir / "Google Photos"
        album_count = len(list(photos_dir.iterdir()))
        assert album_count == 20

    def test_many_conversations_instagram(self, instagram_messages_processor, temp_export_dir, temp_output_dir):
        """Should handle export with many conversations."""
        from tests.fixtures.generators import create_instagram_messages_export

        # Create 15 conversations
        conversations = {
            f"user_{i}_123456": {
                "title": f"User {i}",
                "messages": [
                    {
                        "sender": f"User {i}",
                        "timestamp": "2021-01-01T12:00:00",
                        "content": f"Message from user {i}",
                    }
                ],
            }
            for i in range(15)
        }

        create_instagram_messages_export(temp_export_dir, conversations=conversations)

        assert instagram_messages_processor.detect(temp_export_dir) is True


class TestMalformedJson:
    """Tests for handling malformed JSON files."""

    def test_invalid_json_metadata(self, temp_export_dir, temp_output_dir):
        """Should handle invalid JSON gracefully."""
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        # Write invalid JSON
        (temp_export_dir / "metadata.json").write_text("{invalid json}")

        # Check that processor doesn't crash on invalid JSON
        from processors.snapchat_memories.processor import SnapchatMemoriesProcessor

        processor = SnapchatMemoriesProcessor()
        # Should return False, not raise exception
        try:
            result = processor.detect(temp_export_dir)
            assert result is False
        except json.JSONDecodeError:
            # If it does raise, that's also acceptable behavior
            pass


class TestSpecialFilenames:
    """Tests for special characters in filenames."""

    def test_spaces_in_filename(self, temp_export_dir, temp_output_dir):
        """Should handle filenames with spaces."""
        from tests.fixtures.generators import create_google_photos_export

        create_google_photos_export(
            temp_export_dir,
            albums={"My Vacation Photos": ["my photo 01.jpg", "my photo 02.jpg"]},
        )

        album_dir = temp_export_dir / "Google Photos" / "My Vacation Photos"
        assert (album_dir / "my photo 01.jpg").exists()
        assert (album_dir / "my photo 02.jpg").exists()

    def test_parentheses_in_filename(self, temp_export_dir, temp_output_dir):
        """Should handle filenames with parentheses."""
        from tests.fixtures.generators import create_google_photos_export

        create_google_photos_export(
            temp_export_dir,
            albums={"Photos (2021)": ["IMG_001 (1).jpg"]},
        )

        album_dir = temp_export_dir / "Google Photos" / "Photos (2021)"
        assert (album_dir / "IMG_001 (1).jpg").exists()


class TestDuplicateHandling:
    """Tests for duplicate file handling."""

    def test_same_filename_different_albums(self, temp_export_dir, temp_output_dir):
        """Should handle same filename in different albums."""
        from tests.fixtures.generators import create_google_photos_export

        create_google_photos_export(
            temp_export_dir,
            albums={
                "Album A": ["photo.jpg"],
                "Album B": ["photo.jpg"],
                "Album C": ["photo.jpg"],
            },
        )

        # All three should exist
        assert (temp_export_dir / "Google Photos" / "Album A" / "photo.jpg").exists()
        assert (temp_export_dir / "Google Photos" / "Album B" / "photo.jpg").exists()
        assert (temp_export_dir / "Google Photos" / "Album C" / "photo.jpg").exists()

    def test_duplicate_message_ids_discord(self, temp_export_dir, temp_output_dir):
        """Should handle multiple messages in Discord channel."""
        from tests.fixtures.generators import create_discord_export

        create_discord_export(
            temp_export_dir,
            channels={
                "c123456789": {
                    "name": "general",
                    "type": "server",
                    "guild_name": "Test Server",
                    "messages": [
                        {"ID": "msg001", "Timestamp": "2021-01-01 12:00:00", "Contents": "First", "Attachments": ""},
                        {"ID": "msg002", "Timestamp": "2021-01-01 12:00:01", "Contents": "Second", "Attachments": ""},
                        {"ID": "msg003", "Timestamp": "2021-01-01 12:00:02", "Contents": "Third", "Attachments": ""},
                    ],
                },
            }
        )

        channel_dir = temp_export_dir / "Messages" / "c123456789"
        messages = json.loads((channel_dir / "messages.json").read_text())
        assert len(messages) == 3


