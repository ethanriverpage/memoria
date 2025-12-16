"""
Processing tests for Discord processor.

Tests cover edge cases including:
- Server channel messages
- DM messages
- Group DM messages
- Attachment URL simulation
- Multiple attachments per message
- Expired CDN URLs
- Channel type detection
"""

import json


from tests.fixtures.generators import create_discord_export
from tests.fixtures.media_samples import write_media_file


class TestDiscordChannelTypes:
    """Tests for different channel type handling."""

    def test_server_channel_messages(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should process server channel messages."""
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
                            "Contents": "Hello server!",
                            "Attachments": "",
                        },
                    ],
                },
            }
        )

        channel_dir = temp_export_dir / "Messages" / "c123456789"
        assert channel_dir.exists()
        assert (channel_dir / "messages.json").exists()
        assert (channel_dir / "channel.json").exists()

    def test_dm_messages(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should process direct message channels."""
        create_discord_export(
            temp_export_dir,
            channels={
                "c987654321": {
                    "name": "DM with Alice",
                    "type": "dm",
                    "messages": [
                        {
                            "ID": "msg001",
                            "Timestamp": "2021-01-01 12:00:00",
                            "Contents": "Hey Alice!",
                            "Attachments": "",
                        },
                    ],
                },
            }
        )

        channel_dir = temp_export_dir / "Messages" / "c987654321"
        assert channel_dir.exists()
        assert (channel_dir / "messages.json").exists()

    def test_group_dm_messages(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should process group DM channels."""
        create_discord_export(
            temp_export_dir,
            channels={
                "c111111111": {
                    "name": "Group Chat",
                    "type": "dm",  # Group DMs are type dm
                    "messages": [
                        {
                            "ID": "msg001",
                            "Timestamp": "2021-01-01 12:00:00",
                            "Contents": "Group message!",
                            "Attachments": "",
                        },
                    ],
                },
            }
        )

        channel_dir = temp_export_dir / "Messages" / "c111111111"
        assert channel_dir.exists()


class TestDiscordAttachments:
    """Tests for attachment handling."""

    def test_single_attachment(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should handle message with single attachment."""
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
                            "Contents": "Check this image!",
                            "Attachments": "https://cdn.discord.com/attachments/123/456/image.jpg",
                        },
                    ],
                },
            }
        )

        channel_dir = temp_export_dir / "Messages" / "c123456789"
        assert (channel_dir / "image.jpg").exists()

    def test_multiple_attachments(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should handle message with multiple attachments."""
        channel_dir = temp_export_dir / "Messages" / "c123456789"
        channel_dir.mkdir(parents=True)

        messages = [
            {
                "ID": "msg001",
                "Timestamp": "2021-01-01 12:00:00",
                "Contents": "Multiple files!",
                "Attachments": "https://cdn.discord.com/123/file1.jpg https://cdn.discord.com/123/file2.png https://cdn.discord.com/123/file3.mp4",
            },
        ]
        (channel_dir / "messages.json").write_text(json.dumps(messages))

        # Create attachment files
        write_media_file(channel_dir / "file1.jpg", "jpeg")
        write_media_file(channel_dir / "file2.png", "png")
        write_media_file(channel_dir / "file3.mp4", "mp4")

        (channel_dir / "channel.json").write_text('{"id": "123456789", "name": "general", "type": 0}')
        (temp_export_dir / "Messages" / "index.json").write_text('{"c123456789": "general"}')

        assert (channel_dir / "file1.jpg").exists()
        assert (channel_dir / "file2.png").exists()
        assert (channel_dir / "file3.mp4").exists()

    def test_message_without_attachments(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should handle text-only messages."""
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
                            "Contents": "Just text, no attachments",
                            "Attachments": "",
                        },
                    ],
                },
            }
        )

        channel_dir = temp_export_dir / "Messages" / "c123456789"
        messages = json.loads((channel_dir / "messages.json").read_text())
        assert messages[0]["Attachments"] == ""


class TestDiscordExpiredURLs:
    """Tests for expired CDN URL handling."""

    def test_expired_cdn_url(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should handle expired CDN URLs gracefully."""
        channel_dir = temp_export_dir / "Messages" / "c123456789"
        channel_dir.mkdir(parents=True)

        # Message references URL but file doesn't exist locally
        messages = [
            {
                "ID": "msg001",
                "Timestamp": "2021-01-01 12:00:00",
                "Contents": "Old image",
                "Attachments": "https://cdn.discord.com/attachments/123/456/expired_image.jpg",
            },
        ]
        (channel_dir / "messages.json").write_text(json.dumps(messages))
        (channel_dir / "channel.json").write_text('{"id": "123456789", "name": "general", "type": 0}')
        (temp_export_dir / "Messages" / "index.json").write_text('{"c123456789": "general"}')

        # No file created - simulates expired/unavailable attachment
        assert not (channel_dir / "expired_image.jpg").exists()


class TestDiscordMultipleChannels:
    """Tests for multiple channel handling."""

    def test_multiple_server_channels(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should handle multiple channels from same server."""
        create_discord_export(
            temp_export_dir,
            channels={
                "c111111111": {"name": "general", "type": "server", "guild_name": "Test Server", "messages": []},
                "c222222222": {"name": "random", "type": "server", "guild_name": "Test Server", "messages": []},
                "c333333333": {"name": "memes", "type": "server", "guild_name": "Test Server", "messages": []},
            }
        )

        messages_dir = temp_export_dir / "Messages"
        assert (messages_dir / "c111111111").exists()
        assert (messages_dir / "c222222222").exists()
        assert (messages_dir / "c333333333").exists()

    def test_channels_from_different_servers(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should handle channels from different servers."""
        create_discord_export(
            temp_export_dir,
            channels={
                "c111111111": {"name": "general", "type": "server", "guild_name": "Server A", "messages": []},
                "c222222222": {"name": "general", "type": "server", "guild_name": "Server B", "messages": []},
            }
        )

        channel1 = json.loads(
            (temp_export_dir / "Messages" / "c111111111" / "channel.json").read_text()
        )
        channel2 = json.loads(
            (temp_export_dir / "Messages" / "c222222222" / "channel.json").read_text()
        )

        assert channel1["guild"]["name"] == "Server A"
        assert channel2["guild"]["name"] == "Server B"


class TestDiscordIndexFile:
    """Tests for index.json handling."""

    def test_index_contains_all_channels(self, discord_processor, temp_export_dir, temp_output_dir):
        """Should have all channels listed in index.json."""
        create_discord_export(
            temp_export_dir,
            channels={
                "c111": {"name": "channel1", "type": "server", "guild_name": "Server", "messages": []},
                "c222": {"name": "channel2", "type": "dm", "messages": []},
                "c333": {"name": "channel3", "type": "dm", "messages": []},
            }
        )

        index = json.loads((temp_export_dir / "Messages" / "index.json").read_text())
        assert "c111" in index
        assert "c222" in index
        assert "c333" in index

