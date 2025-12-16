"""
Detection tests for Discord processor.

Tests verify that the processor correctly identifies valid export
structures and rejects invalid ones.
"""

import json


from tests.fixtures.generators import create_discord_export


class TestDiscordDetection:
    """Tests for Discord processor detection."""

    def test_detect_valid_export(self, discord_processor, temp_export_dir):
        """Should detect a valid Discord export."""
        create_discord_export(temp_export_dir)
        assert discord_processor.detect(temp_export_dir) is True

    def test_detect_with_server_channels_only(self, discord_processor, temp_export_dir):
        """Should detect export with only server channels."""
        create_discord_export(
            temp_export_dir,
            channels={
                "c123456789": {
                    "name": "general",
                    "type": "server",
                    "guild_name": "Test Server",
                    "messages": [
                        {"ID": "msg001", "Timestamp": "2021-01-01 12:00:00", "Contents": "Hello!", "Attachments": ""},
                    ],
                },
            }
        )
        assert discord_processor.detect(temp_export_dir) is True

    def test_detect_with_dm_only(self, discord_processor, temp_export_dir):
        """Should detect export with only DM channels."""
        create_discord_export(
            temp_export_dir,
            channels={
                "c987654321": {
                    "name": "DM with User",
                    "type": "dm",
                    "messages": [
                        {"ID": "msg001", "Timestamp": "2021-01-01 12:00:00", "Contents": "Hi!", "Attachments": ""},
                    ],
                },
            }
        )
        assert discord_processor.detect(temp_export_dir) is True

    def test_detect_with_multiple_channels(self, discord_processor, temp_export_dir):
        """Should detect export with multiple channels of different types."""
        create_discord_export(
            temp_export_dir,
            channels={
                "c111111111": {"name": "general", "type": "server", "guild_name": "Server 1", "messages": []},
                "c222222222": {"name": "random", "type": "server", "guild_name": "Server 1", "messages": []},
                "c333333333": {"name": "DM with Alice", "type": "dm", "messages": []},
                "c444444444": {"name": "DM with Bob", "type": "dm", "messages": []},
            }
        )
        assert discord_processor.detect(temp_export_dir) is True

    def test_reject_missing_messages_dir(self, discord_processor, temp_export_dir):
        """Should reject export without Messages directory."""
        assert discord_processor.detect(temp_export_dir) is False

    def test_reject_empty_messages_dir(self, discord_processor, temp_export_dir):
        """Should reject export with empty Messages directory."""
        messages_dir = temp_export_dir / "Messages"
        messages_dir.mkdir(parents=True)
        assert discord_processor.detect(temp_export_dir) is False

    def test_reject_missing_index_json(self, discord_processor, temp_export_dir):
        """Should reject export without index.json."""
        messages_dir = temp_export_dir / "Messages"
        channel_dir = messages_dir / "c123456789"
        channel_dir.mkdir(parents=True)
        (channel_dir / "messages.json").write_text("[]")
        assert discord_processor.detect(temp_export_dir) is False

    def test_reject_no_channel_folders(self, discord_processor, temp_export_dir):
        """Should reject export with index.json but no channel folders."""
        messages_dir = temp_export_dir / "Messages"
        messages_dir.mkdir(parents=True)
        (messages_dir / "index.json").write_text('{"c123": "general"}')
        assert discord_processor.detect(temp_export_dir) is False

    def test_reject_channel_folder_wrong_prefix(self, discord_processor, temp_export_dir):
        """Should reject channel folders that don't start with 'c'."""
        messages_dir = temp_export_dir / "Messages"
        messages_dir.mkdir(parents=True)
        (messages_dir / "index.json").write_text('{"123": "general"}')
        wrong_folder = messages_dir / "123"  # Should start with 'c'
        wrong_folder.mkdir()
        (wrong_folder / "messages.json").write_text("[]")
        assert discord_processor.detect(temp_export_dir) is False


class TestDiscordPreprocessedDetection:
    """Tests for detecting preprocessed Discord exports."""

    def test_detect_preprocessed_export(self, discord_processor, temp_export_dir):
        """Should detect a preprocessed Discord export."""
        media_dir = temp_export_dir / "media"
        media_dir.mkdir(parents=True)

        metadata = {
            "export_info": {
                "export_username": "testuser",
                "downloads_successful": 5,
                "downloads_failed": 0,
            },
            "conversations": {
                "c123456789": {
                    "name": "general",
                    "guild_name": "Test Server",
                    "messages": [],
                }
            },
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert discord_processor.detect(temp_export_dir) is True

    def test_reject_preprocessed_without_discord_markers(self, discord_processor, temp_export_dir):
        """Should reject preprocessed export without Discord-specific markers."""
        media_dir = temp_export_dir / "media"
        media_dir.mkdir(parents=True)

        # Generic metadata without Discord markers
        metadata = {
            "export_info": {
                "export_username": "testuser",
            },
            "conversations": {
                "some_conv": {"messages": []},
            },
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert discord_processor.detect(temp_export_dir) is False

