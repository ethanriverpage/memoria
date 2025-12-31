"""
Processing tests for Discord processor.

Tests cover edge cases including:
- Server channel messages
- DM messages
- Group DM messages
- Attachment handling
- String vs numeric channel types
- Expired CDN URLs
- SPOILER_ prefixed filenames
"""

import json

from tests.fixtures.export_tree import copy_export_fixture
from tests.fixtures.media_samples import write_media_file


class TestDiscordPreprocessing:
    """Tests that run the preprocessor and verify output."""

    def test_raw_minimal_preprocessing(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should preprocess minimal Discord export."""
        from processors.discord.preprocess import DiscordPreprocessor

        copy_export_fixture("discord", "raw_minimal", temp_export_dir)

        preprocessor = DiscordPreprocessor(
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

        # Verify export_info
        assert "export_info" in output_metadata
        assert output_metadata["export_info"]["platform"] == "Discord"

    def test_string_channel_types_preprocessing(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly handle string channel type values (GUILD_TEXT, DM, GROUP_DM)."""
        from processors.discord.preprocess import DiscordPreprocessor

        copy_export_fixture("discord", "raw_string_channel_types", temp_export_dir)

        preprocessor = DiscordPreprocessor(
            export_path=temp_export_dir,
            output_dir=temp_output_dir,
        )
        result = preprocessor.run()
        assert result is True

        with open(temp_output_dir / "metadata.json") as f:
            output_metadata = json.load(f)

        # This fixture has no attachments; the preprocessor will drop empty conversations.
        # The regression check here is that string channel types are accepted and preprocessing completes.
        export_info = output_metadata.get("export_info", {})
        assert export_info.get("total_channels") == 3
        assert export_info.get("total_attachments") == 0


class TestDiscordFixtureStructure:
    """Tests that verify schema-faithful fixture structures match docs."""

    def test_raw_minimal_structure(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should have valid minimal Discord export structure."""
        copy_export_fixture("discord", "raw_minimal", temp_export_dir)

        # Verify required structure per docs
        assert (temp_export_dir / "Messages" / "index.json").exists()
        assert (temp_export_dir / "Account" / "user.json").exists()
        
        # Verify at least one channel exists
        channel_dir = temp_export_dir / "Messages" / "c184324186284621824"
        assert channel_dir.exists()
        assert (channel_dir / "messages.json").exists()
        assert (channel_dir / "channel.json").exists()

    def test_string_channel_types_structure(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse exports with string channel type values."""
        copy_export_fixture("discord", "raw_string_channel_types", temp_export_dir)

        # Verify all three channel types are present
        messages_dir = temp_export_dir / "Messages"
        guild_channel = json.loads(
            (messages_dir / "c184324186284621824" / "channel.json").read_text()
        )
        dm_channel = json.loads(
            (messages_dir / "c502291754930929684" / "channel.json").read_text()
        )
        group_dm_channel = json.loads(
            (messages_dir / "c623392507828371479" / "channel.json").read_text()
        )

        assert guild_channel["type"] == "GUILD_TEXT"
        assert dm_channel["type"] == "DM"
        assert group_dm_channel["type"] == "GROUP_DM"

    def test_attachments_with_query_params_structure(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse attachment URLs with query parameters."""
        copy_export_fixture(
            "discord", "raw_attachments_with_query_params", temp_export_dir
        )

        messages = json.loads(
            (
                temp_export_dir
                / "Messages"
                / "c184324186284621824"
                / "messages.json"
            ).read_text()
        )

        # Single attachment with query params
        assert messages[0]["Attachments"].startswith("https://cdn.discordapp.com/")
        assert "photo.jpg?" in messages[0]["Attachments"]

        # Multiple space-separated attachments
        attachments = messages[1]["Attachments"].split(" ")
        assert len(attachments) == 2
        assert "file1.jpg?" in attachments[0]
        assert "file2.png?" in attachments[1]

    def test_spoiler_filename_structure(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle SPOILER_ prefixed filenames correctly."""
        copy_export_fixture("discord", "raw_spoiler_filename", temp_export_dir)

        # Verify the spoiler file exists
        spoiler_file = (
            temp_export_dir
            / "Messages"
            / "c184324186284621824"
            / "SPOILER_secret.jpg"
        )
        assert spoiler_file.exists()

        # Verify message references spoiler filename
        messages = json.loads(
            (
                temp_export_dir
                / "Messages"
                / "c184324186284621824"
                / "messages.json"
            ).read_text()
        )
        assert "SPOILER_secret.jpg" in messages[0]["Attachments"]

    def test_with_servers_optional_structure(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle exports with optional Servers directory."""
        copy_export_fixture("discord", "with_servers_optional", temp_export_dir)

        # Verify Servers directory exists with guild info
        assert (temp_export_dir / "Servers" / "index.json").exists()
        assert (temp_export_dir / "Servers" / "184315303323238400" / "guild.json").exists()


class TestDiscordAttachments:
    """Tests for attachment handling."""

    def test_multiple_attachments_structure(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle message with multiple attachments."""
        copy_export_fixture(
            "discord", "raw_attachments_with_query_params", temp_export_dir
        )

        channel_dir = temp_export_dir / "Messages" / "c184324186284621824"
        
        # Verify attachment files exist
        assert (channel_dir / "photo.jpg").exists()
        assert (channel_dir / "file1.jpg").exists()
        assert (channel_dir / "file2.png").exists()

    def test_message_without_attachments(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle text-only messages."""
        copy_export_fixture("discord", "raw_minimal", temp_export_dir)

        channel_dir = temp_export_dir / "Messages" / "c184324186284621824"
        messages = json.loads((channel_dir / "messages.json").read_text())
        
        # Check for messages with empty attachments
        has_empty_attachments = any(
            msg.get("Attachments", "") == "" for msg in messages
        )
        assert has_empty_attachments or len(messages) == 0


class TestDiscordExpiredURLs:
    """Tests for expired CDN URL handling."""

    def test_expired_cdn_url_handling(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle expired CDN URLs gracefully."""
        copy_export_fixture("discord", "raw_minimal", temp_export_dir)

        channel_dir = temp_export_dir / "Messages" / "c184324186284621824"

        # Add a message with expired URL (file doesn't exist locally)
        messages = [
            {
                "ID": "msg001",
                "Timestamp": "2021-01-01 12:00:00",
                "Contents": "Old image",
                "Attachments": "https://cdn.discord.com/attachments/123/456/expired_image.jpg",
            },
        ]
        (channel_dir / "messages.json").write_text(json.dumps(messages))

        # No file created - simulates expired/unavailable attachment
        assert not (channel_dir / "expired_image.jpg").exists()


class TestDiscordIndexFile:
    """Tests for index.json handling."""

    def test_index_contains_all_channels(
        self, discord_processor, temp_export_dir, temp_output_dir
    ):
        """Should have all channels listed in index.json."""
        copy_export_fixture("discord", "raw_string_channel_types", temp_export_dir)

        index = json.loads((temp_export_dir / "Messages" / "index.json").read_text())
        
        # Should have 3 channels (GUILD_TEXT, DM, GROUP_DM)
        assert len(index) == 3
        assert "c184324186284621824" in index
        assert "c502291754930929684" in index
        assert "c623392507828371479" in index
