"""
Username detection tests for all processors.

Tests verify that each processor correctly detects usernames from:
- Platform-specific metadata files
- Fallback directory name patterns
"""

import json
import lzma
from pathlib import Path

from common.detection import resolve_username, detect_from_dirname


class TestDetectionModule:
    """Tests for the common detection module."""

    def test_resolve_username_first_match(self, tmp_path):
        """Should return the first successful detector result."""

        def detector_fail(_path: Path) -> str:
            return None

        def detector_success(_path: Path) -> str:
            return "found_user"

        result = resolve_username(
            tmp_path,
            [
                ("fail", detector_fail),
                ("success", detector_success),
            ],
        )

        assert result.username == "found_user"
        assert result.source == "success"

    def test_resolve_username_all_fail(self, tmp_path):
        """Should return None when all detectors fail."""

        def detector_fail(_path: Path) -> str:
            return None

        result = resolve_username(
            tmp_path,
            [
                ("fail1", detector_fail),
                ("fail2", detector_fail),
            ],
        )

        assert result.username is None
        assert result.source == "none"

    def test_resolve_username_exception_handling(self, tmp_path):
        """Should catch exceptions and continue to next detector."""

        def detector_raises(_path: Path) -> str:
            raise ValueError("Test error")

        def detector_success(_path: Path) -> str:
            return "recovered"

        result = resolve_username(
            tmp_path,
            [
                ("raises", detector_raises),
                ("success", detector_success),
            ],
        )

        assert result.username == "recovered"
        assert result.source == "success"

    def test_detect_from_dirname_standard(self, tmp_path):
        """Should extract username from standard pattern."""
        export_dir = tmp_path / "discord-testuser-20251219"
        export_dir.mkdir()

        result = detect_from_dirname(export_dir, "discord")

        assert result == "testuser"

    def test_detect_from_dirname_instagram_underscore(self, tmp_path):
        """Should handle Instagram usernames with trailing underscore."""
        export_dir = tmp_path / "instagram-myuser_-20251219"
        export_dir.mkdir()

        result = detect_from_dirname(export_dir, "instagram")

        assert result == "myuser_"

    def test_detect_from_dirname_no_match(self, tmp_path):
        """Should return None for non-matching directories."""
        export_dir = tmp_path / "random_folder"
        export_dir.mkdir()

        result = detect_from_dirname(export_dir, "discord")

        assert result is None


class TestDiscordUsernameDetection:
    """Tests for Discord username detection."""

    def test_detect_from_user_json(self, tmp_path):
        """Should detect username from Account/user.json."""
        from processors.discord.preprocess import DiscordPreprocessor

        # Create export structure
        export_dir = tmp_path / "discord-unknown-20251219"
        export_dir.mkdir()

        # Create Account/user.json
        account_dir = export_dir / "Account"
        account_dir.mkdir()
        user_data = {"username": "realuser", "id": "123"}
        (account_dir / "user.json").write_text(json.dumps(user_data))

        # Create minimal Messages structure for validation
        messages_dir = export_dir / "Messages"
        messages_dir.mkdir()
        (messages_dir / "index.json").write_text("{}")
        channel_dir = messages_dir / "c123"
        channel_dir.mkdir()
        (channel_dir / "messages.json").write_text("[]")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = DiscordPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "realuser"
        assert result.source == "user.json"

    def test_fallback_to_dirname(self, tmp_path):
        """Should fall back to directory name when user.json missing."""
        from processors.discord.preprocess import DiscordPreprocessor

        # Create export structure without Account/user.json
        export_dir = tmp_path / "discord-diruser-20251219"
        export_dir.mkdir()

        messages_dir = export_dir / "Messages"
        messages_dir.mkdir()
        (messages_dir / "index.json").write_text("{}")
        channel_dir = messages_dir / "c123"
        channel_dir.mkdir()
        (channel_dir / "messages.json").write_text("[]")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = DiscordPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "diruser"
        assert result.source == "dirname"


class TestInstagramMessagesUsernameDetection:
    """Tests for Instagram Messages username detection."""

    def test_detect_from_personal_info_new_format(self, tmp_path):
        """Should detect username from new format personal_information.html."""
        from processors.instagram_messages.preprocess import (
            InstagramMessagesPreprocessor,
        )

        # Create export structure
        export_dir = tmp_path / "instagram-unknown-20251219"
        export_dir.mkdir()

        # Create new format personal_information.html
        personal_info_dir = export_dir / "personal_information" / "personal_information"
        personal_info_dir.mkdir(parents=True)
        html = """<html><body>
<td class="_2pin _a6_q">Username<div><div>htmluser</div></div></td>
</body></html>"""
        (personal_info_dir / "personal_information.html").write_text(html)

        # Create inbox structure
        inbox_dir = export_dir / "your_instagram_activity" / "messages" / "inbox"
        inbox_dir.mkdir(parents=True)
        conv_dir = inbox_dir / "test_123"
        conv_dir.mkdir()
        (conv_dir / "message_1.html").write_text("<html></html>")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = InstagramMessagesPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "htmluser"
        assert result.source == "personal_information.html"

    def test_detect_from_personal_info_legacy_format(self, tmp_path):
        """Should detect username from legacy format personal_information.html."""
        from processors.instagram_messages.preprocess import (
            InstagramMessagesPreprocessor,
        )

        # Create export structure
        export_dir = tmp_path / "instagram-unknown-20251219"
        export_dir.mkdir()

        # Create legacy format personal_information.html
        personal_info_dir = export_dir / "personal_information"
        personal_info_dir.mkdir()
        html = """<html><body>
<td class="_2pin _a6_q">Username<div><div>legacyuser</div></div></td>
</body></html>"""
        (personal_info_dir / "personal_information.html").write_text(html)

        # Create legacy inbox structure
        inbox_dir = export_dir / "messages" / "inbox"
        inbox_dir.mkdir(parents=True)
        conv_dir = inbox_dir / "test_123"
        conv_dir.mkdir()
        (conv_dir / "message_1.html").write_text("<html></html>")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = InstagramMessagesPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "legacyuser"
        assert result.source == "personal_information.html"


class TestInstaloaderUsernameDetection:
    """Tests for Instaloader username detection."""

    def test_detect_from_json_metadata(self, tmp_path):
        """Should detect username from JSON metadata files."""
        from processors.instaloader.preprocess import InstaloaderPreprocessor

        # Create export structure
        export_dir = tmp_path / "instagram-unknown-20251219"
        export_dir.mkdir()

        # Create a media file (needed for validation)
        (export_dir / "2021-01-01_12-00-00_UTC.jpg").write_bytes(b"\xff\xd8\xff\xe0")

        # Create JSON metadata with owner.username
        json_data = {
            "node": {
                "owner": {
                    "username": "jsonuser",
                    "id": "123456",
                }
            }
        }
        (export_dir / "2021-01-01_12-00-00_UTC.json").write_text(json.dumps(json_data))

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = InstaloaderPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "jsonuser"
        assert result.source == "json_metadata"

    def test_detect_from_compressed_json(self, tmp_path):
        """Should detect username from .json.xz compressed metadata."""
        from processors.instaloader.preprocess import InstaloaderPreprocessor

        # Create export structure
        export_dir = tmp_path / "instagram-unknown-20251219"
        export_dir.mkdir()

        # Create a media file
        (export_dir / "2021-01-01_12-00-00_UTC.jpg").write_bytes(b"\xff\xd8\xff\xe0")

        # Create compressed JSON metadata
        json_data = {
            "node": {
                "owner": {
                    "username": "compresseduser",
                    "id": "123456",
                }
            }
        }
        json_bytes = json.dumps(json_data).encode("utf-8")
        compressed = lzma.compress(json_bytes)
        (export_dir / "2021-01-01_12-00-00_UTC.json.xz").write_bytes(compressed)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = InstaloaderPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "compresseduser"
        assert result.source == "json_metadata"


class TestIMessageUsernameDetection:
    """Tests for iMessage username (device) detection."""

    def test_detect_from_dirname(self, tmp_path):
        """Should detect device from imessage-{device}-YYYYMMDD pattern."""
        from processors.imessage.preprocess import IMessagePreprocessor

        # Create export structure
        export_dir = tmp_path / "imessage-iph17pm-20251219"
        export_dir.mkdir()

        # Create chat.db (minimal)
        from tests.fixtures.media_samples import create_imessage_chat_db

        create_imessage_chat_db(export_dir / "chat.db")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = IMessagePreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "iph17pm"
        assert result.source == "dirname"


class TestInstagramPublicUsernameDetection:
    """Tests for Instagram Public Media username detection."""

    def test_detect_from_personal_info(self, tmp_path):
        """Should detect username from personal_information.html."""
        from processors.instagram_public_media.preprocess import InstagramPreprocessor

        # Create export structure
        export_dir = tmp_path / "instagram-unknown-20251219"
        export_dir.mkdir()

        # Create personal_information.html
        personal_info_dir = export_dir / "personal_information" / "personal_information"
        personal_info_dir.mkdir(parents=True)
        html = """<html><body>
<td class="_2pin _a6_q">Username<div><div>publicuser</div></div></td>
</body></html>"""
        (personal_info_dir / "personal_information.html").write_text(html)

        # Create media structure
        media_dir = export_dir / "media" / "posts" / "202101"
        media_dir.mkdir(parents=True)
        (media_dir / "photo.jpg").write_bytes(b"\xff\xd8\xff\xe0")

        # Create HTML metadata dir
        html_dir = export_dir / "your_instagram_activity" / "media"
        html_dir.mkdir(parents=True)
        (html_dir / "posts_1.html").write_text("<html></html>")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = InstagramPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "publicuser"
        assert result.source == "personal_information.html"


class TestGoogleChatUsernameDetection:
    """Tests for Google Chat username detection."""

    def test_detect_from_profile_json(self, tmp_path):
        """Should detect username from Profile/Profile.json."""
        from processors.google_chat.preprocess import GoogleChatPreprocessor

        # Create export structure
        export_dir = tmp_path / "google-unknown-20251219"
        export_dir.mkdir()

        # Create Profile.json
        profile_dir = export_dir / "Profile"
        profile_dir.mkdir()
        (profile_dir / "Profile.json").write_text(
            json.dumps(
                {
                    "name": {"formattedName": "Test User"},
                    "emails": [{"value": "testuser@gmail.com"}],
                }
            )
        )

        # Create Google Chat structure
        chat_dir = export_dir / "Google Chat"
        (chat_dir / "Groups").mkdir(parents=True)
        (chat_dir / "Users").mkdir(parents=True)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GoogleChatPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "testuser"
        assert result.source == "Profile.json"

    def test_detect_from_user_info_json(self, tmp_path):
        """Should detect username from user_info.json when Profile.json absent."""
        from processors.google_chat.preprocess import GoogleChatPreprocessor

        # Create export structure
        export_dir = tmp_path / "google-unknown-20251219"
        export_dir.mkdir()

        # Create Google Chat structure with user_info.json
        chat_dir = export_dir / "Google Chat"
        (chat_dir / "Groups").mkdir(parents=True)
        users_dir = chat_dir / "Users"
        user_subdir = users_dir / "123456"
        user_subdir.mkdir(parents=True)
        (user_subdir / "user_info.json").write_text(
            json.dumps(
                {
                    "user": {
                        "email": "chatuserinfo@gmail.com",
                        "name": "Chat User",
                    }
                }
            )
        )

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GoogleChatPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "chatuserinfo"
        assert result.source == "user_info.json"

    def test_fallback_to_dirname(self, tmp_path):
        """Should fallback to directory name if no JSON files exist."""
        from processors.google_chat.preprocess import GoogleChatPreprocessor

        # Create export structure
        export_dir = tmp_path / "google-dirnameuser-20251219"
        export_dir.mkdir()

        # Create minimal Google Chat structure
        chat_dir = export_dir / "Google Chat"
        (chat_dir / "Groups").mkdir(parents=True)
        (chat_dir / "Users").mkdir(parents=True)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GoogleChatPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "dirnameuser"
        assert result.source == "dirname"


class TestGooglePhotosUsernameDetection:
    """Tests for Google Photos username detection."""

    def test_detect_from_profile_json(self, tmp_path):
        """Should detect username from Profile/Profile.json."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        # Create export structure
        export_dir = tmp_path / "google-unknown-20251219"
        export_dir.mkdir()

        # Create Profile.json
        profile_dir = export_dir / "Profile"
        profile_dir.mkdir()
        (profile_dir / "Profile.json").write_text(
            json.dumps(
                {
                    "name": {"formattedName": "Test User"},
                    "emails": [{"value": "photosuser@gmail.com"}],
                }
            )
        )

        # Create Google Photos structure
        photos_dir = export_dir / "Google Photos"
        album_dir = photos_dir / "Album1"
        album_dir.mkdir(parents=True)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GooglePhotosPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "photosuser"
        assert result.source == "Profile.json"

    def test_detect_from_google_chat_user_info(self, tmp_path):
        """Should detect from Google Chat user_info.json in same export."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        # Create export structure
        export_dir = tmp_path / "google-unknown-20251219"
        export_dir.mkdir()

        # Create Google Photos structure
        photos_dir = export_dir / "Google Photos"
        album_dir = photos_dir / "Album1"
        album_dir.mkdir(parents=True)

        # Create Google Chat structure with user_info.json (no Profile.json)
        chat_dir = export_dir / "Google Chat"
        users_dir = chat_dir / "Users"
        user_subdir = users_dir / "123456"
        user_subdir.mkdir(parents=True)
        (user_subdir / "user_info.json").write_text(
            json.dumps(
                {
                    "user": {
                        "email": "shareduser@gmail.com",
                        "name": "Shared User",
                    }
                }
            )
        )

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GooglePhotosPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "shareduser"
        assert result.source == "Google Chat user_info.json"

    def test_fallback_to_dirname(self, tmp_path):
        """Should fallback to directory name if no JSON files exist."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        # Create export structure
        export_dir = tmp_path / "google-photodiruser-20251219"
        export_dir.mkdir()

        # Create Google Photos structure
        photos_dir = export_dir / "Google Photos"
        album_dir = photos_dir / "Album1"
        album_dir.mkdir(parents=True)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GooglePhotosPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "photodiruser"
        assert result.source == "dirname"


class TestGoogleVoiceUsernameDetection:
    """Tests for Google Voice username detection."""

    def test_detect_from_profile_json(self, tmp_path):
        """Should detect username from Profile/Profile.json."""
        from processors.google_voice.preprocess import GoogleVoicePreprocessor

        # Create export structure
        export_dir = tmp_path / "google-unknown-20251219"
        export_dir.mkdir()

        # Create Profile.json
        profile_dir = export_dir / "Profile"
        profile_dir.mkdir()
        (profile_dir / "Profile.json").write_text(
            json.dumps(
                {
                    "name": {"formattedName": "Test User"},
                    "emails": [{"value": "voiceuser@gmail.com"}],
                }
            )
        )

        # Create Google Voice structure
        voice_dir = export_dir / "Voice"
        calls_dir = voice_dir / "Calls"
        calls_dir.mkdir(parents=True)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GoogleVoicePreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "voiceuser"
        assert result.source == "Profile.json"

    def test_detect_from_google_chat_user_info(self, tmp_path):
        """Should detect from Google Chat user_info.json in same export."""
        from processors.google_voice.preprocess import GoogleVoicePreprocessor

        # Create export structure
        export_dir = tmp_path / "google-unknown-20251219"
        export_dir.mkdir()

        # Create Google Voice structure
        voice_dir = export_dir / "Voice"
        calls_dir = voice_dir / "Calls"
        calls_dir.mkdir(parents=True)

        # Create Google Chat structure with user_info.json (no Profile.json)
        chat_dir = export_dir / "Google Chat"
        users_dir = chat_dir / "Users"
        user_subdir = users_dir / "123456"
        user_subdir.mkdir(parents=True)
        (user_subdir / "user_info.json").write_text(
            json.dumps(
                {
                    "user": {
                        "email": "voiceshared@gmail.com",
                        "name": "Voice Shared User",
                    }
                }
            )
        )

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GoogleVoicePreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "voiceshared"
        assert result.source == "Google Chat user_info.json"

    def test_fallback_to_dirname(self, tmp_path):
        """Should fallback to directory name if no JSON files exist."""
        from processors.google_voice.preprocess import GoogleVoicePreprocessor

        # Create export structure
        export_dir = tmp_path / "google-voicediruser-20251219"
        export_dir.mkdir()

        # Create Google Voice structure
        voice_dir = export_dir / "Voice"
        calls_dir = voice_dir / "Calls"
        calls_dir.mkdir(parents=True)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GoogleVoicePreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "voicediruser"
        assert result.source == "dirname"


class TestGoogleNonGmailEmail:
    """Tests for Google detection with non-gmail emails."""

    def test_profile_json_non_gmail_email(self, tmp_path):
        """Should extract username from non-gmail email by taking part before @."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        # Create export structure
        export_dir = tmp_path / "google-unknown-20251219"
        export_dir.mkdir()

        # Create Profile.json with non-gmail email
        profile_dir = export_dir / "Profile"
        profile_dir.mkdir()
        (profile_dir / "Profile.json").write_text(
            json.dumps(
                {
                    "name": {"formattedName": "Custom User"},
                    "emails": [{"value": "customuser@example.com"}],
                }
            )
        )

        # Create Google Photos structure
        photos_dir = export_dir / "Google Photos"
        album_dir = photos_dir / "Album1"
        album_dir.mkdir(parents=True)

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        preprocessor = GooglePhotosPreprocessor(export_dir, output_dir)
        result = preprocessor.detect_username()

        assert result.username == "customuser"
        assert result.source == "Profile.json"
