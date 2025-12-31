"""
Fixture fidelity tests.

These tests validate that committed export fixtures under tests/fixtures/exports/
match the documented schemas and are correctly detected by their processors.

This prevents fixture drift from docs and ensures future additions are honest.
"""

import json
from pathlib import Path

import pytest

# Fixture directory structure
FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "exports"


# ============================================================================
# Detection Tests - Each fixture should be detected by exactly one processor
# ============================================================================


class TestGooglePhotosFixtureFidelity:
    """Fidelity tests for Google Photos fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.google_photos.processor import GooglePhotosProcessor
        return GooglePhotosProcessor

    def test_takeout_basic_detected(self, processor, tmp_path):
        """takeout_basic fixture should be detected as Google Photos."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_takeout_basic_has_required_structure(self, tmp_path):
        """takeout_basic should have Takeout/Google Photos structure."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_photos", "takeout_basic", tmp_path)

        # Required per docs/Google-Export.md
        assert (tmp_path / "Takeout" / "Google Photos").is_dir()
        assert (tmp_path / "Takeout" / "Google Photos" / "metadata.json").exists()

    def test_takeout_basic_json_metadata_schema(self, tmp_path):
        """JSON sidecar should have photoTakenTime with timestamp."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_photos", "takeout_basic", tmp_path)

        # Find a JSON sidecar
        json_files = list(
            (tmp_path / "Takeout" / "Google Photos").rglob("*.jpg.json")
        )
        assert len(json_files) > 0, "Should have at least one JSON sidecar"

        metadata = json.loads(json_files[0].read_text())
        assert "photoTakenTime" in metadata
        assert "timestamp" in metadata["photoTakenTime"]

    def test_takeout_duplicate_album_vs_library_detected(self, processor, tmp_path):
        """takeout_duplicate_album_vs_library should be detected."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "google_photos", "takeout_duplicate_album_vs_library", tmp_path
        )
        assert processor.detect(tmp_path) is True

    def test_takeout_missing_json_some_detected(self, processor, tmp_path):
        """takeout_missing_json_some should be detected."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_photos", "takeout_missing_json_some", tmp_path)
        assert processor.detect(tmp_path) is True


class TestGoogleChatFixtureFidelity:
    """Fidelity tests for Google Chat fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.google_chat.processor import GoogleChatProcessor
        return GoogleChatProcessor

    def test_groups_and_users_detected(self, processor, tmp_path):
        """groups_and_users fixture should be detected as Google Chat."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_chat", "groups_and_users", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_groups_and_users_has_required_structure(self, tmp_path):
        """groups_and_users should have Google Chat structure."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_chat", "groups_and_users", tmp_path)

        # Required per docs/Google-Export.md
        assert (tmp_path / "Google Chat").is_dir()
        # Should have Groups or Users subdirectory
        has_groups = (tmp_path / "Google Chat" / "Groups").is_dir()
        has_users = (tmp_path / "Google Chat" / "Users").is_dir()
        assert has_groups or has_users


class TestGoogleVoiceFixtureFidelity:
    """Fidelity tests for Google Voice fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.google_voice.processor import GoogleVoiceProcessor
        return GoogleVoiceProcessor

    def test_takeout_calls_basic_detected(self, processor, tmp_path):
        """takeout_calls_basic fixture should be detected as Google Voice."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_voice", "takeout_calls_basic", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_takeout_calls_basic_has_required_structure(self, tmp_path):
        """takeout_calls_basic should have Voice/Calls structure."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_voice", "takeout_calls_basic", tmp_path)

        # Required per docs/Google-Export.md
        assert (tmp_path / "Takeout" / "Voice" / "Calls").is_dir()


class TestDiscordFixtureFidelity:
    """Fidelity tests for Discord fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.discord.processor import DiscordProcessor
        return DiscordProcessor

    def test_raw_minimal_detected(self, processor, tmp_path):
        """raw_minimal fixture should be detected as Discord."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("discord", "raw_minimal", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_raw_minimal_has_required_structure(self, tmp_path):
        """raw_minimal should have Messages/index.json."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("discord", "raw_minimal", tmp_path)

        # Required per docs/Discord-Export.md
        assert (tmp_path / "Messages" / "index.json").exists()
        assert (tmp_path / "Account" / "user.json").exists()

    def test_raw_string_channel_types_detected(self, processor, tmp_path):
        """raw_string_channel_types should be detected."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("discord", "raw_string_channel_types", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_channel_json_has_type_field(self, tmp_path):
        """channel.json should have type field."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("discord", "raw_string_channel_types", tmp_path)

        channel_files = list((tmp_path / "Messages").rglob("channel.json"))
        assert len(channel_files) > 0

        for channel_file in channel_files:
            data = json.loads(channel_file.read_text())
            assert "type" in data


class TestSnapchatMessagesFixtureFidelity:
    """Fidelity tests for Snapchat Messages fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.snapchat_messages.processor import SnapchatMessagesProcessor
        return SnapchatMessagesProcessor

    def test_raw_conversation_id_map_detected(self, processor, tmp_path):
        """raw_conversation_id_map should be detected as Snapchat Messages."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_messages", "raw_conversation_id_map", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_raw_conversation_id_map_has_required_structure(self, tmp_path):
        """raw_conversation_id_map should have messages/json/chat_history.json."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_messages", "raw_conversation_id_map", tmp_path)

        # Required per docs/Snapchat-Export.md
        assert (tmp_path / "messages" / "json" / "chat_history.json").exists()

    def test_chat_history_json_schema(self, tmp_path):
        """chat_history.json should have conversation-keyed structure."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_messages", "raw_conversation_id_map", tmp_path)

        data = json.loads(
            (tmp_path / "messages" / "json" / "chat_history.json").read_text()
        )

        # Should be a dict with conversation IDs as keys
        assert isinstance(data, dict)
        for conv_id, messages in data.items():
            assert isinstance(messages, list)
            if len(messages) > 0:
                # Each message should have required fields
                msg = messages[0]
                assert "From" in msg
                assert "Created" in msg


class TestSnapchatMemoriesFixtureFidelity:
    """Fidelity tests for Snapchat Memories fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.snapchat_memories.processor import SnapchatMemoriesProcessor
        return SnapchatMemoriesProcessor

    def test_memories_basic_detected(self, processor, tmp_path):
        """memories_basic should be detected as Snapchat Memories."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_memories_basic_has_required_structure(self, tmp_path):
        """memories_basic should have memories/metadata.json."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", tmp_path)

        # Required per docs/Snapchat-Export.md
        assert (tmp_path / "memories" / "metadata.json").exists()
        assert (tmp_path / "memories" / "media").is_dir()

    def test_metadata_json_is_array(self, tmp_path):
        """metadata.json should be an array of memory objects."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", tmp_path)

        data = json.loads((tmp_path / "memories" / "metadata.json").read_text())
        assert isinstance(data, list)
        if len(data) > 0:
            assert "date" in data[0]
            assert "media_type" in data[0]


class TestInstagramMessagesFixtureFidelity:
    """Fidelity tests for Instagram Messages fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.instagram_messages.processor import InstagramMessagesProcessor
        return InstagramMessagesProcessor

    def test_current_format_doc_html_detected(self, processor, tmp_path):
        """current_format_doc_html should be detected as Instagram Messages."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("instagram_messages", "current_format_doc_html", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_current_format_has_required_structure(self, tmp_path):
        """current_format should have your_instagram_activity/messages/inbox."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("instagram_messages", "current_format_doc_html", tmp_path)

        # Required per docs/Instagram-Export.md
        inbox = tmp_path / "your_instagram_activity" / "messages" / "inbox"
        assert inbox.is_dir()

    def test_legacy_format_doc_html_detected(self, processor, tmp_path):
        """legacy_format_doc_html should be detected as Instagram Messages."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("instagram_messages", "legacy_format_doc_html", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_legacy_format_has_required_structure(self, tmp_path):
        """legacy_format should have messages/inbox."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("instagram_messages", "legacy_format_doc_html", tmp_path)

        # Required per docs/Instagram-Export.md (legacy format)
        inbox = tmp_path / "messages" / "inbox"
        assert inbox.is_dir()


class TestInstagramPublicFixtureFidelity:
    """Fidelity tests for Instagram Public Media fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.instagram_public_media.processor import (
            InstagramPublicMediaProcessor,
        )
        return InstagramPublicMediaProcessor

    def test_posts_and_archived_posts_detected(self, processor, tmp_path):
        """posts_and_archived_posts should be detected as Instagram Public."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("instagram_public", "posts_and_archived_posts", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_posts_and_archived_posts_has_required_structure(self, tmp_path):
        """posts_and_archived_posts should have media/posts directory."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("instagram_public", "posts_and_archived_posts", tmp_path)

        # Required per docs/Instagram-Export.md
        assert (tmp_path / "media" / "posts").is_dir()


class TestInstaloaderFixtureFidelity:
    """Fidelity tests for Instaloader fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.instaloader.processor import InstaloaderProcessor
        return InstaloaderProcessor

    def test_basic_with_json_xz_detected(self, processor, tmp_path):
        """basic_with_json_xz should be detected as Instaloader."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("instaloader", "basic_with_json_xz", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_basic_with_json_xz_has_utc_pattern(self, tmp_path):
        """basic_with_json_xz should have YYYY-MM-DD_HH-MM-SS_UTC files."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("instaloader", "basic_with_json_xz", tmp_path)

        # Should have UTC-timestamp named files
        utc_files = list(tmp_path.glob("*_UTC*"))
        assert len(utc_files) > 0, "Should have UTC-timestamp named files"


class TestIMessageFixtureFidelity:
    """Fidelity tests for iMessage fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.imessage.processor import IMessageProcessor
        return IMessageProcessor

    def test_mac_basic_detected(self, processor, tmp_path):
        """mac_basic should be detected as iMessage."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage", "mac_basic", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_mac_basic_has_required_structure(self, tmp_path):
        """mac_basic should have chat.db and Attachments."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage", "mac_basic", tmp_path)

        # Required per docs/iMessage-Export.md
        assert (tmp_path / "chat.db").exists()
        assert (tmp_path / "Attachments").is_dir()

    def test_iphone_basic_detected(self, processor, tmp_path):
        """iphone_basic should be detected as iMessage."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage", "iphone_basic", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_iphone_basic_has_required_structure(self, tmp_path):
        """iphone_basic should have SMS/sms.db and SMS/Attachments."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage", "iphone_basic", tmp_path)

        # Required per docs/iMessage-Export.md (iPhone format)
        assert (tmp_path / "SMS" / "sms.db").exists()
        assert (tmp_path / "SMS" / "Attachments").is_dir()


class TestIMessageImazingFixtureFidelity:
    """Fidelity tests for iMessage iMazing fixtures."""

    @pytest.fixture
    def processor(self):
        from processors.imessage_imazing.processor import ImazingProcessor
        return ImazingProcessor

    def test_basic_device_info_detected(self, processor, tmp_path):
        """basic_device_info should be detected as iMazing."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage_imazing", "basic_device_info", tmp_path)
        assert processor.detect(tmp_path) is True

    def test_basic_device_info_has_required_files(self, tmp_path):
        """basic_device_info should have Device-Info.txt and CSV."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage_imazing", "basic_device_info", tmp_path)

        # Required per docs/iMessage-Export.md (iMazing format)
        assert (tmp_path / "Device-Info.txt").exists()
        csv_files = list(tmp_path.glob("Messages - *.csv"))
        assert len(csv_files) > 0, "Should have Messages CSV file"


# ============================================================================
# Cross-processor exclusivity tests
# ============================================================================


class TestFixtureExclusivity:
    """Ensure each fixture is detected by exactly one processor."""

    @pytest.fixture
    def all_processors(self):
        """Get all processor classes."""
        from processors.google_photos.processor import GooglePhotosProcessor
        from processors.google_chat.processor import GoogleChatProcessor
        from processors.google_voice.processor import GoogleVoiceProcessor
        from processors.discord.processor import DiscordProcessor
        from processors.snapchat_memories.processor import SnapchatMemoriesProcessor
        from processors.snapchat_messages.processor import SnapchatMessagesProcessor
        from processors.instagram_messages.processor import InstagramMessagesProcessor
        from processors.instagram_public_media.processor import (
            InstagramPublicMediaProcessor,
        )
        from processors.instaloader.processor import InstaloaderProcessor
        from processors.imessage.processor import IMessageProcessor
        from processors.imessage_imazing.processor import ImazingProcessor

        return {
            "google_photos": GooglePhotosProcessor,
            "google_chat": GoogleChatProcessor,
            "google_voice": GoogleVoiceProcessor,
            "discord": DiscordProcessor,
            "snapchat_memories": SnapchatMemoriesProcessor,
            "snapchat_messages": SnapchatMessagesProcessor,
            "instagram_messages": InstagramMessagesProcessor,
            "instagram_public": InstagramPublicMediaProcessor,
            "instaloader": InstaloaderProcessor,
            "imessage": IMessageProcessor,
            "imessage_imazing": ImazingProcessor,
        }

    def test_google_photos_not_detected_by_others(self, all_processors, tmp_path):
        """Google Photos fixture should only be detected by GooglePhotosProcessor."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_photos", "takeout_basic", tmp_path)

        for name, processor in all_processors.items():
            if name == "google_photos":
                assert processor.detect(tmp_path) is True
            else:
                assert processor.detect(tmp_path) is False, (
                    f"{name} should not detect Google Photos fixture"
                )

    def test_discord_not_detected_by_others(self, all_processors, tmp_path):
        """Discord fixture should only be detected by DiscordProcessor."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("discord", "raw_minimal", tmp_path)

        for name, processor in all_processors.items():
            if name == "discord":
                assert processor.detect(tmp_path) is True
            else:
                assert processor.detect(tmp_path) is False, (
                    f"{name} should not detect Discord fixture"
                )

    def test_snapchat_messages_not_detected_by_others(self, all_processors, tmp_path):
        """Snapchat Messages fixture should only be detected by SnapchatMessagesProcessor."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_messages", "raw_conversation_id_map", tmp_path)

        for name, processor in all_processors.items():
            if name == "snapchat_messages":
                assert processor.detect(tmp_path) is True
            else:
                assert processor.detect(tmp_path) is False, (
                    f"{name} should not detect Snapchat Messages fixture"
                )

