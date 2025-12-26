"""
Tests for OutputInfo and build_processor_output_path functionality.

Tests the Platform/User/Service output path building mechanism.
"""

import pytest

from common.output.paths import OutputInfo, build_processor_output_path


class TestOutputInfo:
    """Tests for the OutputInfo dataclass."""

    def test_output_info_creation(self):
        """Should create OutputInfo with all fields."""
        info = OutputInfo(
            platform="Google",
            service="photos",
            username_prefix="google",
        )
        assert info.platform == "Google"
        assert info.service == "photos"
        assert info.username_prefix == "google"

    def test_output_info_with_none_service(self):
        """Should allow None service for single-service platforms."""
        info = OutputInfo(
            platform="iMessage",
            service=None,
            username_prefix="imessage",
        )
        assert info.platform == "iMessage"
        assert info.service is None
        assert info.username_prefix == "imessage"

    def test_output_info_is_frozen(self):
        """Should be immutable (frozen dataclass)."""
        info = OutputInfo(
            platform="Google",
            service="photos",
            username_prefix="google",
        )
        with pytest.raises(AttributeError):
            info.platform = "Instagram"


class TestBuildProcessorOutputPath:
    """Tests for the build_processor_output_path function."""

    def test_google_photos_path(self, tmp_path):
        """Should build correct path for Google Photos."""
        info = OutputInfo(
            platform="Google",
            service="photos",
            username_prefix="google",
        )
        result = build_processor_output_path(
            info,
            "google-johndoe-20250526",
            str(tmp_path),
        )
        assert result == str(tmp_path / "Google" / "johndoe")

    def test_instagram_messages_path(self, tmp_path):
        """Should build correct path for Instagram Messages."""
        info = OutputInfo(
            platform="Instagram",
            service="messages",
            username_prefix="instagram",
        )
        result = build_processor_output_path(
            info,
            "instagram-johndoe-20251007",
            str(tmp_path),
        )
        assert result == str(tmp_path / "Instagram" / "johndoe")

    def test_imessage_path_with_device(self, tmp_path):
        """Should build correct path for iMessage with device identifier."""
        info = OutputInfo(
            platform="iMessage",
            service=None,
            username_prefix="imessage",
        )
        # Note: imessage prefix extraction uses the same pattern
        # For iMessage, the directory name typically is like "iph13p-messages-20220426"
        # which doesn't match the "imessage-" prefix pattern, so it returns "unknown"
        # This is handled by custom extraction in the processor
        result = build_processor_output_path(
            info,
            "imessage-mac-20220426",
            str(tmp_path),
        )
        assert result == str(tmp_path / "iMessage" / "mac")

    def test_snapchat_path(self, tmp_path):
        """Should build correct path for Snapchat."""
        info = OutputInfo(
            platform="Snapchat",
            service="memories",
            username_prefix="snapchat",
        )
        result = build_processor_output_path(
            info,
            "snapchat-johndoe-20251017",
            str(tmp_path),
        )
        assert result == str(tmp_path / "Snapchat" / "johndoe")

    def test_discord_path(self, tmp_path):
        """Should build correct path for Discord."""
        info = OutputInfo(
            platform="Discord",
            service=None,
            username_prefix="discord",
        )
        result = build_processor_output_path(
            info,
            "discord-johndoe-20251017",
            str(tmp_path),
        )
        assert result == str(tmp_path / "Discord" / "johndoe")

    def test_username_with_underscore(self, tmp_path):
        """Should handle usernames with trailing underscore (Instagram)."""
        info = OutputInfo(
            platform="Instagram",
            service="posts",
            username_prefix="instagram",
        )
        result = build_processor_output_path(
            info,
            "instagram-johndoe_-20251007",
            str(tmp_path),
        )
        assert result == str(tmp_path / "Instagram" / "johndoe_")

    def test_username_with_email(self, tmp_path):
        """Should handle email-style usernames."""
        info = OutputInfo(
            platform="Google",
            service="photos",
            username_prefix="google",
        )
        result = build_processor_output_path(
            info,
            "google-john.doe@gmail.com-20250526",
            str(tmp_path),
        )
        assert result == str(tmp_path / "Google" / "john.doe@gmail.com")


class TestProcessorGetOutputInfo:
    """Tests that processors correctly implement get_output_info."""

    def test_google_photos_output_info(self, google_photos_processor):
        """Google Photos should return correct OutputInfo."""
        info = google_photos_processor.get_output_info()
        assert info.platform == "Google"
        assert info.service == "photos"
        assert info.username_prefix == "google"

    def test_google_chat_output_info(self, google_chat_processor):
        """Google Chat should return correct OutputInfo."""
        info = google_chat_processor.get_output_info()
        assert info.platform == "Google"
        assert info.service == "chat"
        assert info.username_prefix == "google"

    def test_google_voice_output_info(self, google_voice_processor):
        """Google Voice should return correct OutputInfo."""
        info = google_voice_processor.get_output_info()
        assert info.platform == "Google"
        assert info.service == "voice"
        assert info.username_prefix == "google"

    def test_instagram_messages_output_info(self, instagram_messages_processor):
        """Instagram Messages should return correct OutputInfo."""
        info = instagram_messages_processor.get_output_info()
        assert info.platform == "Instagram"
        assert info.service == "messages"
        assert info.username_prefix == "instagram"

    def test_instagram_public_output_info(self, instagram_public_processor):
        """Instagram Public Media should return correct OutputInfo."""
        info = instagram_public_processor.get_output_info()
        assert info.platform == "Instagram"
        # Note: service="public-media" matches the subdirectory created by process()
        # The actual service subdirs (posts, stories, reels, etc.) are inside public-media
        assert info.service == "public-media"
        assert info.username_prefix == "instagram"

    def test_snapchat_memories_output_info(self, snapchat_memories_processor):
        """Snapchat Memories should return correct OutputInfo."""
        info = snapchat_memories_processor.get_output_info()
        assert info.platform == "Snapchat"
        assert info.service == "memories"
        assert info.username_prefix == "snapchat"

    def test_snapchat_messages_output_info(self, snapchat_messages_processor):
        """Snapchat Messages should return correct OutputInfo."""
        info = snapchat_messages_processor.get_output_info()
        assert info.platform == "Snapchat"
        assert info.service == "messages"
        assert info.username_prefix == "snapchat"

    def test_imessage_output_info(self, imessage_processor):
        """iMessage should return correct OutputInfo with no service."""
        info = imessage_processor.get_output_info()
        assert info.platform == "iMessage"
        assert info.service is None
        assert info.username_prefix == "imessage"

    def test_discord_output_info(self, discord_processor):
        """Discord should return correct OutputInfo with no service."""
        info = discord_processor.get_output_info()
        assert info.platform == "Discord"
        assert info.service is None
        assert info.username_prefix == "discord"
