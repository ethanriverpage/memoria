"""
Detection tests for Google processors (Photos, Chat, Voice).

Tests verify that each processor correctly identifies valid export
structures and rejects invalid ones.
"""



from tests.fixtures.generators import (
    create_google_photos_export,
    create_google_chat_export,
    create_google_voice_export,
)


class TestGooglePhotosDetection:
    """Tests for Google Photos processor detection."""

    def test_detect_valid_export(self, google_photos_processor, temp_export_dir):
        """Should detect a valid Google Photos export."""
        create_google_photos_export(temp_export_dir)
        assert google_photos_processor.detect(temp_export_dir) is True

    def test_detect_with_multiple_albums(
        self, google_photos_processor, temp_export_dir
    ):
        """Should detect export with multiple album folders."""
        create_google_photos_export(
            temp_export_dir,
            albums={
                "Album 1": ["photo1.jpg"],
                "Album 2": ["photo2.png"],
                "Album 3": ["video.mp4"],
            },
        )
        assert google_photos_processor.detect(temp_export_dir) is True

    def test_reject_missing_google_photos_dir(
        self, google_photos_processor, temp_export_dir
    ):
        """Should reject directory without Google Photos subdirectory."""
        # Create empty directory
        assert google_photos_processor.detect(temp_export_dir) is False

    def test_reject_empty_google_photos_dir(
        self, google_photos_processor, temp_export_dir
    ):
        """Should reject empty Google Photos directory (no albums)."""
        photos_dir = temp_export_dir / "Google Photos"
        photos_dir.mkdir(parents=True)
        assert google_photos_processor.detect(temp_export_dir) is False

    def test_reject_google_photos_with_only_files(
        self, google_photos_processor, temp_export_dir
    ):
        """Should reject Google Photos directory with only files (no album subdirs)."""
        photos_dir = temp_export_dir / "Google Photos"
        photos_dir.mkdir(parents=True)
        (photos_dir / "stray_file.jpg").write_bytes(b"test")
        assert google_photos_processor.detect(temp_export_dir) is False

    def test_detect_without_json_metadata(
        self, google_photos_processor, temp_export_dir
    ):
        """Should detect export even without JSON metadata files."""
        create_google_photos_export(temp_export_dir, include_json_metadata=False)
        assert google_photos_processor.detect(temp_export_dir) is True


class TestGoogleChatDetection:
    """Tests for Google Chat processor detection."""

    def test_detect_valid_export(self, google_chat_processor, temp_export_dir):
        """Should detect a valid Google Chat export."""
        create_google_chat_export(temp_export_dir)
        assert google_chat_processor.detect(temp_export_dir) is True

    def test_detect_with_groups_only(self, google_chat_processor, temp_export_dir):
        """Should detect export with only group conversations."""
        create_google_chat_export(
            temp_export_dir,
            conversations={
                "Groups/Test Group": [
                    {
                        "creator": {"name": "User"},
                        "created_date": "2021-01-01T12:00:00Z",
                        "text": "Hi",
                    }
                ]
            },
        )
        assert google_chat_processor.detect(temp_export_dir) is True

    def test_detect_with_users_only(self, google_chat_processor, temp_export_dir):
        """Should reject export with only DM conversations (requires at least one Group)."""
        # The actual detection requires at least one Group with group_info.json
        create_google_chat_export(
            temp_export_dir,
            conversations={
                "Users/Other User": [
                    {
                        "creator": {"name": "User"},
                        "created_date": "2021-01-01T12:00:00Z",
                        "text": "Hi",
                    }
                ]
            },
        )
        # Google Chat detection requires at least one Group folder with group_info.json
        assert google_chat_processor.detect(temp_export_dir) is False

    def test_reject_missing_google_chat_dir(
        self, google_chat_processor, temp_export_dir
    ):
        """Should reject directory without Google Chat subdirectory."""
        assert google_chat_processor.detect(temp_export_dir) is False

    def test_reject_empty_google_chat_dir(self, google_chat_processor, temp_export_dir):
        """Should reject empty Google Chat directory."""
        chat_dir = temp_export_dir / "Google Chat"
        chat_dir.mkdir(parents=True)
        assert google_chat_processor.detect(temp_export_dir) is False

    def test_reject_google_chat_without_messages(
        self, google_chat_processor, temp_export_dir
    ):
        """Should reject Google Chat with empty conversation folders."""
        chat_dir = temp_export_dir / "Google Chat"
        groups_dir = chat_dir / "Groups" / "Test Group"
        groups_dir.mkdir(parents=True)
        # No messages.json file
        assert google_chat_processor.detect(temp_export_dir) is False


class TestGoogleVoiceDetection:
    """Tests for Google Voice processor detection."""

    def test_detect_valid_export(self, google_voice_processor, temp_export_dir):
        """Should detect a valid Google Voice export."""
        create_google_voice_export(temp_export_dir)
        assert google_voice_processor.detect(temp_export_dir) is True

    def test_detect_with_multiple_conversations(
        self, google_voice_processor, temp_export_dir
    ):
        """Should detect export with multiple voice conversations."""
        create_google_voice_export(
            temp_export_dir,
            calls=[
                {
                    "contact": "+1234567890",
                    "type": "Text",
                    "timestamp": "2021-01-01T12:00:00Z",
                    "messages": [{"sender": "Me", "text": "Hi", "time": "12:00 PM"}],
                    "media": [],
                },
                {
                    "contact": "+0987654321",
                    "type": "Text",
                    "timestamp": "2021-01-02T12:00:00Z",
                    "messages": [{"sender": "Me", "text": "Hello", "time": "12:00 PM"}],
                    "media": [],
                },
            ],
        )
        assert google_voice_processor.detect(temp_export_dir) is True

    def test_reject_missing_voice_dir(self, google_voice_processor, temp_export_dir):
        """Should reject directory without Voice subdirectory."""
        assert google_voice_processor.detect(temp_export_dir) is False

    def test_reject_empty_voice_dir(self, google_voice_processor, temp_export_dir):
        """Should reject empty Voice directory."""
        voice_dir = temp_export_dir / "Voice"
        voice_dir.mkdir(parents=True)
        assert google_voice_processor.detect(temp_export_dir) is False

    def test_reject_voice_without_calls_dir(
        self, google_voice_processor, temp_export_dir
    ):
        """Should reject Voice directory without Calls subdirectory."""
        voice_dir = temp_export_dir / "Voice"
        voice_dir.mkdir(parents=True)
        (voice_dir / "some_file.txt").write_text("test")
        assert google_voice_processor.detect(temp_export_dir) is False

    def test_reject_calls_without_html(self, google_voice_processor, temp_export_dir):
        """Should reject Voice/Calls directory without HTML files."""
        calls_dir = temp_export_dir / "Voice" / "Calls"
        calls_dir.mkdir(parents=True)
        (calls_dir / "some_file.txt").write_text("test")
        assert google_voice_processor.detect(temp_export_dir) is False


class TestGoogleMultiProcessorDetection:
    """Tests for detecting multiple Google processors in a single export."""

    def test_detect_photos_and_chat(
        self, google_photos_processor, google_chat_processor, temp_export_dir
    ):
        """Should detect both Photos and Chat in same export."""
        create_google_photos_export(temp_export_dir)
        create_google_chat_export(temp_export_dir)

        assert google_photos_processor.detect(temp_export_dir) is True
        assert google_chat_processor.detect(temp_export_dir) is True

    def test_detect_all_three_google_processors(
        self,
        google_photos_processor,
        google_chat_processor,
        google_voice_processor,
        temp_export_dir,
    ):
        """Should detect Photos, Chat, and Voice in same export."""
        create_google_photos_export(temp_export_dir)
        create_google_chat_export(temp_export_dir)
        create_google_voice_export(temp_export_dir)

        assert google_photos_processor.detect(temp_export_dir) is True
        assert google_chat_processor.detect(temp_export_dir) is True
        assert google_voice_processor.detect(temp_export_dir) is True
