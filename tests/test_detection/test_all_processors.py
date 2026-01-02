#!/usr/bin/env python3
"""Detection tests for all 9 processors.

Notes:
- iMazing format exports are handled by the unified iMessage processor.
- Instaloader format exports are handled by the unified Instagram Public Media processor.

Tests verify that each processor's detect() function:
- Returns True for valid exports of that processor type
- Returns False for empty directories
- Returns False for exports of other processor types
- Handles edge cases gracefully (missing files, corrupted data)
"""

from pathlib import Path

import pytest

from tests.fixtures.media_samples import write_media_file
from tests.fixtures.export_tree import copy_export_fixture


# Import all processor classes
from processors.google_photos.processor import GooglePhotosProcessor
from processors.google_chat.processor import GoogleChatProcessor
from processors.google_voice.processor import GoogleVoiceProcessor
from processors.snapchat_memories.processor import SnapchatMemoriesProcessor
from processors.snapchat_messages.processor import SnapchatMessagesProcessor
from processors.instagram_messages.processor import InstagramMessagesProcessor
from processors.instagram_public_media.processor import InstagramPublicMediaProcessor
from processors.discord.processor import DiscordProcessor
from processors.imessage.processor import IMessageProcessor


# Use static detect methods from processor classes
def detect_google_photos(path):
    return GooglePhotosProcessor.detect(path)


def detect_google_chat(path):
    return GoogleChatProcessor.detect(path)


def detect_google_voice(path):
    return GoogleVoiceProcessor.detect(path)


def detect_snapchat_memories(path):
    return SnapchatMemoriesProcessor.detect(path)


def detect_snapchat_messages(path):
    return SnapchatMessagesProcessor.detect(path)


def detect_instagram_messages(path):
    return InstagramMessagesProcessor.detect(path)


def detect_instagram_public(path):
    return InstagramPublicMediaProcessor.detect(path)


def detect_discord(path):
    return DiscordProcessor.detect(path)


def detect_imessage(path):
    return IMessageProcessor.detect(path)


class TestGooglePhotosDetection:
    """Detection tests for Google Photos processor."""

    def test_detects_valid_export(self, tmp_path: Path) -> None:
        """Should detect valid Google Photos export."""
        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert detect_google_photos(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_google_photos(tmp_path) is False

    def test_rejects_no_photos_folder(self, tmp_path: Path) -> None:
        """Should reject directory without Google Photos folder."""
        (tmp_path / "random").mkdir()
        write_media_file(tmp_path / "random" / "photo.jpg", "jpeg")
        assert detect_google_photos(tmp_path) is False

    def test_rejects_other_processor_exports(self, tmp_path: Path) -> None:
        """Should reject exports from other processors."""
        copy_export_fixture("discord", "raw_minimal", tmp_path)
        assert detect_google_photos(tmp_path) is False


class TestGoogleChatDetection:
    """Detection tests for Google Chat processor."""

    def test_detects_valid_export(self, tmp_path: Path) -> None:
        """Should detect valid Google Chat export."""
        copy_export_fixture("google_chat", "groups_and_users", tmp_path)
        assert detect_google_chat(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_google_chat(tmp_path) is False

    def test_rejects_no_chat_folder(self, tmp_path: Path) -> None:
        """Should reject directory without Google Chat structure."""
        (tmp_path / "random").mkdir()
        assert detect_google_chat(tmp_path) is False

    def test_rejects_other_processor_exports(self, tmp_path: Path) -> None:
        """Should reject exports from other processors."""
        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert detect_google_chat(tmp_path) is False


class TestGoogleVoiceDetection:
    """Detection tests for Google Voice processor."""

    def test_detects_valid_export(self, tmp_path: Path) -> None:
        """Should detect valid Google Voice export."""
        copy_export_fixture("google_voice", "takeout_calls_basic", tmp_path)
        assert detect_google_voice(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_google_voice(tmp_path) is False

    def test_rejects_no_voice_folder(self, tmp_path: Path) -> None:
        """Should reject directory without Voice folder."""
        (tmp_path / "random").mkdir()
        assert detect_google_voice(tmp_path) is False

    def test_rejects_other_processor_exports(self, tmp_path: Path) -> None:
        """Should reject exports from other processors."""
        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert detect_google_voice(tmp_path) is False


class TestSnapchatMemoriesDetection:
    """Detection tests for Snapchat Memories processor."""

    def test_detects_valid_export(self, tmp_path: Path) -> None:
        """Should detect valid Snapchat Memories export."""
        copy_export_fixture("snapchat_memories", "memories_basic", tmp_path)
        assert detect_snapchat_memories(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_snapchat_memories(tmp_path) is False

    def test_rejects_missing_metadata(self, tmp_path: Path) -> None:
        """Should reject export without metadata.json."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        write_media_file(media_dir / "photo.jpg", "jpeg")
        assert detect_snapchat_memories(tmp_path) is False

    def test_rejects_other_processor_exports(self, tmp_path: Path) -> None:
        """Should reject exports from other processors."""
        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert detect_snapchat_memories(tmp_path) is False


class TestSnapchatMessagesDetection:
    """Detection tests for Snapchat Messages processor."""

    def test_detects_valid_raw_export(self, tmp_path: Path) -> None:
        """Should detect valid raw Snapchat Messages export."""
        copy_export_fixture("snapchat_messages", "raw_conversation_id_map", tmp_path)
        assert detect_snapchat_messages(tmp_path) is True

    def test_detects_valid_preprocessed_export(self, tmp_path: Path) -> None:
        """Should detect valid preprocessed Snapchat Messages export."""
        # Keep generator for preprocessed-format detection (preprocessed is internal, not doc schema)
        from tests.fixtures.generators import create_snapchat_messages_export

        create_snapchat_messages_export(tmp_path, raw_format=False)
        assert detect_snapchat_messages(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_snapchat_messages(tmp_path) is False

    def test_rejects_other_processor_exports(self, tmp_path: Path) -> None:
        """Should reject exports from other processors."""
        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert detect_snapchat_messages(tmp_path) is False


class TestInstagramMessagesDetection:
    """Detection tests for Instagram Messages processor."""

    def test_detects_valid_new_format_export(self, tmp_path: Path) -> None:
        """Should detect valid new format Instagram Messages export."""
        copy_export_fixture("instagram_messages", "current_format_doc_html", tmp_path)
        assert detect_instagram_messages(tmp_path) is True

    def test_detects_valid_legacy_format_export(self, tmp_path: Path) -> None:
        """Should detect valid legacy format Instagram Messages export."""
        copy_export_fixture("instagram_messages", "legacy_format_doc_html", tmp_path)
        assert detect_instagram_messages(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_instagram_messages(tmp_path) is False

    def test_rejects_other_processor_exports(self, tmp_path: Path) -> None:
        """Should reject exports from other processors."""
        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert detect_instagram_messages(tmp_path) is False


class TestInstagramPublicDetection:
    """Detection tests for Instagram Public Media processor."""

    def test_detects_valid_export(self, tmp_path: Path) -> None:
        """Should detect valid Instagram Public Media export."""
        copy_export_fixture("instagram_public", "posts_and_archived_posts", tmp_path)
        assert detect_instagram_public(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_instagram_public(tmp_path) is False

    def test_rejects_other_processor_exports(self, tmp_path: Path) -> None:
        """Should reject exports from other processors."""
        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert detect_instagram_public(tmp_path) is False


class TestInstaloaderFormatDetection:
    """Detection tests for Instaloader format via Instagram Public Media processor.

    Note: Instaloader format is now handled by the unified Instagram Public Media processor.
    """

    def test_detects_valid_export(self, tmp_path: Path) -> None:
        """Should detect valid Instaloader scrape data via Instagram Public Media."""
        copy_export_fixture("instaloader", "basic_with_json_xz", tmp_path)
        # Instaloader format is now detected by Instagram Public Media processor
        assert detect_instagram_public(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_instagram_public(tmp_path) is False

    def test_rejects_no_instaloader_pattern(self, tmp_path: Path) -> None:
        """Should reject directory without Instaloader filename pattern."""
        write_media_file(tmp_path / "random_photo.jpg", "jpeg")
        assert detect_instagram_public(tmp_path) is False


class TestDiscordDetection:
    """Detection tests for Discord processor."""

    def test_detects_valid_export(self, tmp_path: Path) -> None:
        """Should detect valid Discord export."""
        copy_export_fixture("discord", "raw_minimal", tmp_path)
        assert detect_discord(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_discord(tmp_path) is False

    def test_rejects_no_messages_folder(self, tmp_path: Path) -> None:
        """Should reject directory without Messages folder."""
        (tmp_path / "random").mkdir()
        assert detect_discord(tmp_path) is False

    def test_rejects_other_processor_exports(self, tmp_path: Path) -> None:
        """Should reject exports from other processors."""
        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert detect_discord(tmp_path) is False


class TestIMessageDetection:
    """Detection tests for iMessage processor."""

    def test_detects_valid_mac_export(self, tmp_path: Path) -> None:
        """Should detect valid iMessage Mac export."""
        copy_export_fixture("imessage", "mac_basic", tmp_path)
        assert detect_imessage(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_imessage(tmp_path) is False

    def test_rejects_no_chat_db(self, tmp_path: Path) -> None:
        """Should reject directory without chat.db."""
        (tmp_path / "Attachments").mkdir()
        assert detect_imessage(tmp_path) is False

    def test_rejects_other_processor_exports(self, tmp_path: Path) -> None:
        """Should reject exports from other processors."""
        copy_export_fixture("google_photos", "takeout_basic", tmp_path)
        assert detect_imessage(tmp_path) is False


class TestIMessageIMazingDetection:
    """Detection tests for iMazing format exports via unified iMessage processor."""

    def test_detects_valid_export(self, tmp_path: Path) -> None:
        """Should detect valid iMazing iMessage export via unified processor."""
        copy_export_fixture("imessage_imazing", "basic_device_info", tmp_path)
        # iMazing exports are now detected by the unified iMessage processor
        assert detect_imessage(tmp_path) is True

    def test_rejects_empty_directory(self, tmp_path: Path) -> None:
        """Should reject empty directory."""
        assert detect_imessage(tmp_path) is False

    def test_rejects_no_device_info(self, tmp_path: Path) -> None:
        """Should reject directory without Device-Info.txt and no SQLite DB."""
        (tmp_path / "Messages - Contact.csv").write_text("header\n")
        # Just a CSV with no iMazing markers won't be detected
        assert detect_imessage(tmp_path) is False


class TestCrossProcessorDetection:
    """Cross-processor detection tests to ensure no false positives."""

    @pytest.fixture
    def all_exports(self, tmp_path: Path) -> dict:
        """Create one export of each type in separate directories."""
        exports = {}
        exports["google_photos"] = tmp_path / "google_photos"
        exports["google_chat"] = tmp_path / "google_chat"
        exports["google_voice"] = tmp_path / "google_voice"
        exports["snapchat_memories"] = tmp_path / "snapchat_memories"
        exports["snapchat_messages"] = tmp_path / "snapchat_messages"
        exports["instagram_messages"] = tmp_path / "instagram_messages"
        exports["instagram_public"] = tmp_path / "instagram_public"
        exports["instaloader"] = tmp_path / "instaloader"
        exports["discord"] = tmp_path / "discord"
        exports["imessage"] = tmp_path / "imessage"
        exports["imessage_imazing"] = tmp_path / "imessage_imazing"

        for name, path in exports.items():
            path.mkdir()

        copy_export_fixture("google_photos", "takeout_basic", exports["google_photos"])
        copy_export_fixture("google_chat", "groups_and_users", exports["google_chat"])
        copy_export_fixture(
            "google_voice", "takeout_calls_basic", exports["google_voice"]
        )
        copy_export_fixture(
            "snapchat_memories", "memories_basic", exports["snapchat_memories"]
        )
        copy_export_fixture(
            "snapchat_messages", "raw_conversation_id_map", exports["snapchat_messages"]
        )
        copy_export_fixture(
            "instagram_messages",
            "current_format_doc_html",
            exports["instagram_messages"],
        )
        copy_export_fixture(
            "instagram_public",
            "posts_and_archived_posts",
            exports["instagram_public"],
        )
        copy_export_fixture("instaloader", "basic_with_json_xz", exports["instaloader"])
        copy_export_fixture("discord", "raw_minimal", exports["discord"])
        copy_export_fixture("imessage", "mac_basic", exports["imessage"])
        # iMazing format is now handled by unified iMessage processor
        copy_export_fixture(
            "imessage_imazing", "basic_device_info", exports["imessage_imazing"]
        )

        return exports

    def test_google_photos_only_detects_own_exports(self, all_exports: dict) -> None:
        """Google Photos should only detect its own exports."""
        for name, path in all_exports.items():
            result = detect_google_photos(path)
            if name == "google_photos":
                assert result is True, f"Should detect {name}"
            else:
                assert result is False, f"Should not detect {name}"

    def test_google_chat_only_detects_own_exports(self, all_exports: dict) -> None:
        """Google Chat should only detect its own exports."""
        for name, path in all_exports.items():
            result = detect_google_chat(path)
            if name == "google_chat":
                assert result is True, f"Should detect {name}"
            else:
                assert result is False, f"Should not detect {name}"

    def test_discord_only_detects_own_exports(self, all_exports: dict) -> None:
        """Discord should only detect its own exports."""
        for name, path in all_exports.items():
            result = detect_discord(path)
            if name == "discord":
                assert result is True, f"Should detect {name}"
            else:
                assert result is False, f"Should not detect {name}"


class TestDetectionEdgeCases:
    """Edge case tests for processor detection."""

    def test_handles_permission_error(self, tmp_path: Path) -> None:
        """Should handle permission errors gracefully."""
        # This test is platform-specific and may not work on all systems
        # Skip if we can't change permissions
        protected = tmp_path / "protected"
        protected.mkdir()

        try:
            protected.chmod(0o000)
            # Should return False, not raise exception
            assert detect_google_photos(protected) is False
        except PermissionError:
            pytest.skip("Cannot change permissions on this system")
        finally:
            try:
                protected.chmod(0o755)
            except Exception:
                pass

    def test_handles_corrupted_json(self, tmp_path: Path) -> None:
        """Should handle corrupted JSON files gracefully."""
        # Create a Snapchat Memories structure with corrupted JSON
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        write_media_file(media_dir / "photo.jpg", "jpeg")
        (tmp_path / "metadata.json").write_text("{ invalid json }")

        # Should return False, not raise exception
        assert detect_snapchat_memories(tmp_path) is False

    def test_handles_empty_json(self, tmp_path: Path) -> None:
        """Should handle empty JSON files gracefully."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()
        (tmp_path / "metadata.json").write_text("")

        assert detect_snapchat_memories(tmp_path) is False

    def test_handles_symlinks(self, tmp_path: Path) -> None:
        """Should handle symlinks in export directories."""
        real_dir = tmp_path / "real"
        copy_export_fixture("google_photos", "takeout_basic", real_dir)

        symlink_dir = tmp_path / "link"
        try:
            symlink_dir.symlink_to(real_dir)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")

        assert detect_google_photos(symlink_dir) is True

    def test_handles_deep_nesting(self, tmp_path: Path) -> None:
        """Should handle deeply nested directory structures."""
        # Create export in a deeply nested path
        deep_path = tmp_path
        for i in range(5):
            deep_path = deep_path / f"level{i}"
        deep_path.mkdir(parents=True)

        copy_export_fixture("google_photos", "takeout_basic", deep_path)
        assert detect_google_photos(deep_path) is True
