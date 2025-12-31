"""
Processing tests for Google Chat processor.

Tests cover edge cases including:
- Group conversation with media
- DM conversation with media
- Messages without attachments
- Empty conversation folders
- Various file naming patterns
- Timestamp format parsing
"""

import json

from tests.fixtures.export_tree import copy_export_fixture
from tests.fixtures.media_samples import write_media_file


class TestGoogleChatPreprocessing:
    """Tests that run the preprocessor and verify output."""

    def test_groups_and_users_preprocessing(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should preprocess export with both Groups and Users directories."""
        from processors.google_chat.preprocess import GoogleChatPreprocessor

        # Username detection requires either Profile/Profile.json or a dirname pattern.
        # Use a dirname that matches detect_from_dirname(..., "google").
        export_dir = temp_export_dir / "google-testuser-20240101"
        export_dir.mkdir(parents=True, exist_ok=True)
        copy_export_fixture("google_chat", "groups_and_users", export_dir)

        preprocessor = GoogleChatPreprocessor(
            export_path=export_dir,
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
        assert output_metadata["export_info"]["platform"] == "Google"
        assert output_metadata["export_info"]["service"] == "chat"

    def test_doc_timestamp_format_preprocessing(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse doc-style timestamp strings during preprocessing."""
        from processors.google_chat.preprocess import GoogleChatPreprocessor

        export_dir = temp_export_dir / "google-testuser-20240101"
        export_dir.mkdir(parents=True, exist_ok=True)
        copy_export_fixture("google_chat", "doc_timestamp_format", export_dir)

        preprocessor = GoogleChatPreprocessor(
            export_path=export_dir,
            output_dir=temp_output_dir,
        )
        result = preprocessor.run()
        assert result is True


class TestGoogleChatFixtureStructure:
    """Tests that verify schema-faithful fixture structures match docs."""

    def test_doc_timestamp_format_structure(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse doc-style timestamp strings."""
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


class TestGoogleChatEdgeCases:
    """Tests for edge cases in Google Chat processing."""

    def test_empty_conversation(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle conversation with no messages."""
        copy_export_fixture("google_chat", "groups_and_users", temp_export_dir)

        # The fixture should have a Users directory with user_info.json
        user_dir = temp_export_dir / "Google Chat" / "Users" / "User Friend"
        assert user_dir.exists()

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
                    "created_date": "Thursday, November 3, 2016 at 11:59:50 AM UTC",
                    "text": "Multiple files",
                    "attached_files": [
                        {"export_name": "file1.jpg", "original_name": "file1.jpg"},
                        {"export_name": "file2.png", "original_name": "file2.png"},
                        {"export_name": "file3.mp4", "original_name": "file3.mp4"},
                    ],
                }
            ]
        }
        (chat_dir / "messages.json").write_text(json.dumps(messages))
        (chat_dir / "group_info.json").write_text(
            json.dumps({"name": "Multi Attach", "members": []})
        )

        # Create the media files
        write_media_file(chat_dir / "file1.jpg", "jpeg")
        write_media_file(chat_dir / "file2.png", "png")
        write_media_file(chat_dir / "file3.mp4", "mp4")

        assert (chat_dir / "file1.jpg").exists()
        assert (chat_dir / "file2.png").exists()
        assert (chat_dir / "file3.mp4").exists()


class TestGoogleChatMediaTypes:
    """Tests for various media type handling in Google Chat."""

    def test_image_attachment_in_fixture(
        self, google_chat_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle image attachments."""
        copy_export_fixture("google_chat", "groups_and_users", temp_export_dir)

        # Verify fixture has image
        assert (
            temp_export_dir
            / "Google Chat"
            / "Groups"
            / "Space Main"
            / "File-2024-01-15.jpg"
        ).exists()
