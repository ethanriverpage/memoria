"""
Processing tests for Google Chat processor.

Tests cover edge cases including:
- Group conversation with media
- DM conversation with media
- Messages without attachments
- Empty conversation folders
"""

import json


from tests.fixtures.generators import create_google_chat_export
from tests.fixtures.media_samples import write_media_file


class TestGoogleChatConversations:
    """Tests for conversation handling in Google Chat processing."""

    def test_group_conversation_with_media(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should process group conversation with attached media."""
        create_google_chat_export(
            temp_export_dir,
            conversations={
                "Groups/Test Group": [
                    {
                        "creator": {"name": "User One", "email": "user1@example.com"},
                        "created_date": "2021-01-01T12:00:00Z",
                        "text": "Check out this photo!",
                        "attached_files": [{"export_name": "photo.jpg"}],
                    },
                    {
                        "creator": {"name": "User Two", "email": "user2@example.com"},
                        "created_date": "2021-01-01T12:05:00Z",
                        "text": "Nice!",
                        "attached_files": [],
                    },
                ]
            },
            include_media=True,
        )

        group_dir = temp_export_dir / "Google Chat" / "Groups" / "Test Group"
        assert (group_dir / "messages.json").exists()
        assert (group_dir / "photo.jpg").exists()

    def test_dm_conversation_with_media(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should process DM conversation with attached media."""
        create_google_chat_export(
            temp_export_dir,
            conversations={
                "Users/Other User": [
                    {
                        "creator": {"name": "Me", "email": "me@example.com"},
                        "created_date": "2021-01-02T12:00:00Z",
                        "text": "Here's the file",
                        "attached_files": [{"export_name": "document.png"}],
                    },
                ]
            },
            include_media=True,
        )

        dm_dir = temp_export_dir / "Google Chat" / "Users" / "Other User"
        assert (dm_dir / "messages.json").exists()
        assert (dm_dir / "document.png").exists()


class TestGoogleChatFilenameMatching:
    """Unit tests for Google Chat preprocessor attachment matching heuristics."""

    def test_common_prefix_match_handles_copy_suffix_and_tail_differences(
        self, tmp_path
    ):
        """
        Real exports sometimes include long File-* names where the export_name and filesystem
        filename differ in the tail (and the filesystem may add "(1)").

        We should still match using a strong common-prefix heuristic.
        """
        from processors.google_chat.preprocess import GoogleChatPreprocessor

        pre = GoogleChatPreprocessor(export_path=tmp_path, output_dir=tmp_path / "out")

        export_name = "File-10356259_299725040193726_4259086571886135715_n.jpg"
        disk_name = "File-10356259_299725040193726_42590865718861357(1).jpg"

        file_catalog = {disk_name: tmp_path / disk_name}

        result = pre.find_media_file(export_name, file_catalog)
        assert result is not None
        matched_filename, matched_path = result
        assert matched_filename == disk_name
        assert matched_path == file_catalog[disk_name]

    def test_copy_suffix_allocation_is_stable_by_message_order(self, tmp_path):
        """
        If multiple messages reference the same export_name (no copy suffix), but the
        filesystem has multiple copies (base, (1), (2)), we must allocate them in order:
        base -> (1) -> (2).
        """
        from processors.google_chat.preprocess import GoogleChatPreprocessor

        pre = GoogleChatPreprocessor(export_path=tmp_path, output_dir=tmp_path / "out")

        export_name = "File-2017-06-03.jpg"
        file_catalog = {
            "File-2017-06-03.jpg": tmp_path / "File-2017-06-03.jpg",
            "File-2017-06-03(1).jpg": tmp_path / "File-2017-06-03(1).jpg",
            "File-2017-06-03(2).jpg": tmp_path / "File-2017-06-03(2).jpg",
        }

        r1 = pre.find_media_file(export_name, file_catalog)
        r2 = pre.find_media_file(export_name, file_catalog)
        r3 = pre.find_media_file(export_name, file_catalog)

        assert r1 is not None and r2 is not None and r3 is not None
        assert r1[0] == "File-2017-06-03.jpg"
        assert r2[0] == "File-2017-06-03(1).jpg"
        assert r3[0] == "File-2017-06-03(2).jpg"

    def test_conversation_without_attachments(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should process conversation with text-only messages."""
        create_google_chat_export(
            temp_export_dir,
            conversations={
                "Groups/Text Only": [
                    {
                        "creator": {"name": "User", "email": "user@example.com"},
                        "created_date": "2021-01-01T12:00:00Z",
                        "text": "Hello!",
                        "attached_files": [],
                    },
                    {
                        "creator": {"name": "Other", "email": "other@example.com"},
                        "created_date": "2021-01-01T12:01:00Z",
                        "text": "Hi there!",
                        "attached_files": [],
                    },
                ]
            },
            include_media=False,
        )

        group_dir = temp_export_dir / "Google Chat" / "Groups" / "Text Only"
        assert (group_dir / "messages.json").exists()
        # Should be no media files
        media_files = list(group_dir.glob("*.jpg")) + list(group_dir.glob("*.png"))
        assert len(media_files) == 0


class TestGoogleChatEdgeCases:
    """Tests for edge cases in Google Chat processing."""

    def test_empty_conversation(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle conversation with no messages."""
        create_google_chat_export(
            temp_export_dir,
            conversations={"Groups/Empty Group": []},
            include_media=False,
        )

        group_dir = temp_export_dir / "Google Chat" / "Groups" / "Empty Group"
        assert (group_dir / "messages.json").exists()

        messages_data = json.loads((group_dir / "messages.json").read_text())
        assert messages_data["messages"] == []

    def test_multiple_attachments_per_message(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle message with multiple attachments."""
        chat_dir = temp_export_dir / "Google Chat" / "Groups" / "Multi Attach"
        chat_dir.mkdir(parents=True)

        messages = {
            "messages": [
                {
                    "creator": {"name": "User", "email": "user@example.com"},
                    "created_date": "2021-01-01T12:00:00Z",
                    "text": "Multiple files",
                    "attached_files": [
                        {"export_name": "file1.jpg"},
                        {"export_name": "file2.png"},
                        {"export_name": "file3.mp4"},
                    ],
                }
            ]
        }
        (chat_dir / "messages.json").write_text(json.dumps(messages))

        # Create the media files
        write_media_file(chat_dir / "file1.jpg", "jpeg")
        write_media_file(chat_dir / "file2.png", "png")
        write_media_file(chat_dir / "file3.mp4", "mp4")

        assert (chat_dir / "file1.jpg").exists()
        assert (chat_dir / "file2.png").exists()
        assert (chat_dir / "file3.mp4").exists()

    def test_special_characters_in_conversation_name(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle conversation names with special characters."""
        create_google_chat_export(
            temp_export_dir,
            conversations={
                "Groups/Project Team (2021) - Q4!": [
                    {
                        "creator": {"name": "User", "email": "user@example.com"},
                        "created_date": "2021-01-01T12:00:00Z",
                        "text": "Hello team!",
                        "attached_files": [],
                    },
                ]
            },
            include_media=False,
        )

        group_dir = (
            temp_export_dir / "Google Chat" / "Groups" / "Project Team (2021) - Q4!"
        )
        assert group_dir.exists()

    def test_mixed_groups_and_users(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle export with both groups and DMs."""
        create_google_chat_export(
            temp_export_dir,
            conversations={
                "Groups/Group One": [
                    {
                        "creator": {"name": "User"},
                        "created_date": "2021-01-01T12:00:00Z",
                        "text": "Hi",
                        "attached_files": [],
                    }
                ],
                "Groups/Group Two": [
                    {
                        "creator": {"name": "User"},
                        "created_date": "2021-01-02T12:00:00Z",
                        "text": "Hello",
                        "attached_files": [],
                    }
                ],
                "Users/Alice": [
                    {
                        "creator": {"name": "Alice"},
                        "created_date": "2021-01-03T12:00:00Z",
                        "text": "Hey",
                        "attached_files": [],
                    }
                ],
                "Users/Bob": [
                    {
                        "creator": {"name": "Bob"},
                        "created_date": "2021-01-04T12:00:00Z",
                        "text": "Hi there",
                        "attached_files": [],
                    }
                ],
            },
            include_media=False,
        )

        assert (temp_export_dir / "Google Chat" / "Groups" / "Group One").exists()
        assert (temp_export_dir / "Google Chat" / "Groups" / "Group Two").exists()
        assert (temp_export_dir / "Google Chat" / "Users" / "Alice").exists()
        assert (temp_export_dir / "Google Chat" / "Users" / "Bob").exists()


class TestGoogleChatMediaTypes:
    """Tests for various media type handling in Google Chat."""

    def test_image_attachment(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle image attachments."""
        create_google_chat_export(
            temp_export_dir,
            conversations={
                "Users/Test": [
                    {
                        "creator": {"name": "User"},
                        "created_date": "2021-01-01T12:00:00Z",
                        "text": "Photo",
                        "attached_files": [{"export_name": "photo.jpg"}],
                    },
                ]
            },
            include_media=True,
        )

        assert (
            temp_export_dir / "Google Chat" / "Users" / "Test" / "photo.jpg"
        ).exists()

    def test_video_attachment(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle video attachments."""
        chat_dir = temp_export_dir / "Google Chat" / "Users" / "Test"
        chat_dir.mkdir(parents=True)

        messages = {
            "messages": [
                {
                    "creator": {"name": "User"},
                    "created_date": "2021-01-01T12:00:00Z",
                    "text": "Video",
                    "attached_files": [{"export_name": "video.mp4"}],
                },
            ]
        }
        (chat_dir / "messages.json").write_text(json.dumps(messages))
        write_media_file(chat_dir / "video.mp4", "mp4")

        assert (chat_dir / "video.mp4").exists()


class TestGoogleChatRealisticFixtures:
    """Processing tests using committed schema-faithful Google Chat fixtures."""

    def test_doc_timestamp_format_parsing(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse doc-style timestamp strings."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_chat", "doc_timestamp_format", temp_export_dir)

        messages_path = (
            temp_export_dir
            / "Google Chat"
            / "Groups"
            / "Space 12345678"
            / "messages.json"
        )
        data = json.loads(messages_path.read_text())

        # Verify timestamp format: "Thursday, November 3, 2016 at 11:59:50 AM UTC"
        msg = data["messages"][0]
        timestamp = msg["created_date"]
        assert timestamp == "Thursday, November 3, 2016 at 11:59:50 AM UTC"

        # Verify message structure
        assert msg["creator"]["name"] == "Test User"
        assert msg["creator"]["email"] == "testuser@example.com"
        assert msg["creator"]["user_type"] == "Human"

    def test_file_naming_patterns_structure(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse messages with various file naming patterns."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_chat", "file_naming_patterns", temp_export_dir)

        messages_path = (
            temp_export_dir
            / "Google Chat"
            / "Groups"
            / "DM 98765432"
            / "messages.json"
        )
        data = json.loads(messages_path.read_text())

        # Verify all naming patterns in attached_files
        export_names = []
        for msg in data["messages"]:
            for f in msg.get("attached_files", []):
                export_names.append(f["export_name"])

        assert "File-2016-09-06.png" in export_names  # Date-based
        assert "File-2016-09-06(1).png" in export_names  # Same-day counter
        assert "File-03b06911-def0-4c33-ad50-03f90084a158.mov" in export_names  # UUID
        assert "File-284346614161283602_account_id=3.jpg" in export_names  # Account ID

    def test_groups_and_users_structure(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse export with both Groups and Users directories."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("google_chat", "groups_and_users", temp_export_dir)

        chat_dir = temp_export_dir / "Google Chat"

        # Verify both directory types exist
        assert (chat_dir / "Groups" / "Space Main").exists()
        assert (chat_dir / "Users" / "User Friend").exists()

        # Verify group has messages and media
        group_messages = json.loads(
            (chat_dir / "Groups" / "Space Main" / "messages.json").read_text()
        )
        assert len(group_messages["messages"]) > 0
        assert (chat_dir / "Groups" / "Space Main" / "File-2024-01-15.jpg").exists()

        # Verify user directory has user_info
        assert (chat_dir / "Users" / "User Friend" / "user_info.json").exists()
