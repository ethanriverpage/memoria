"""
Detection tests for Snapchat processors (Memories, Messages).

Tests verify that each processor correctly identifies valid export
structures and rejects invalid ones.
"""

import json


from tests.fixtures.generators import (
    create_snapchat_memories_export,
    create_snapchat_messages_export,
)


class TestSnapchatMemoriesDetection:
    """Tests for Snapchat Memories processor detection."""

    def test_detect_valid_export(self, snapchat_memories_processor, temp_export_dir):
        """Should detect a valid Snapchat Memories export."""
        create_snapchat_memories_export(temp_export_dir)
        assert snapchat_memories_processor.detect(temp_export_dir) is True

    def test_detect_consolidated_structure(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should detect consolidated export with memories/ subdirectory."""
        memories_dir = temp_export_dir / "memories"
        create_snapchat_memories_export(memories_dir)
        assert snapchat_memories_processor.detect(temp_export_dir) is True

    def test_detect_without_overlays(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should detect export even if overlays directory is empty."""
        create_snapchat_memories_export(temp_export_dir, include_overlays=False)
        # Still need the overlays directory to exist
        assert snapchat_memories_processor.detect(temp_export_dir) is True

    def test_reject_missing_media_dir(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should reject export without media directory."""
        overlays_dir = temp_export_dir / "overlays"
        overlays_dir.mkdir(parents=True)
        metadata = [
            {
                "date": "2021-01-01 12:00:00 UTC",
                "media_type": "Image",
                "media_filename": "test.jpg",
            }
        ]
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))
        assert snapchat_memories_processor.detect(temp_export_dir) is False

    def test_reject_missing_overlays_dir(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should reject export without overlays directory."""
        media_dir = temp_export_dir / "media"
        media_dir.mkdir(parents=True)
        metadata = [
            {
                "date": "2021-01-01 12:00:00 UTC",
                "media_type": "Image",
                "media_filename": "test.jpg",
            }
        ]
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))
        assert snapchat_memories_processor.detect(temp_export_dir) is False

    def test_reject_missing_metadata(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should reject export without metadata.json."""
        (temp_export_dir / "media").mkdir(parents=True)
        (temp_export_dir / "overlays").mkdir(parents=True)
        assert snapchat_memories_processor.detect(temp_export_dir) is False

    def test_reject_empty_metadata_array(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should reject export with empty metadata array."""
        (temp_export_dir / "media").mkdir(parents=True)
        (temp_export_dir / "overlays").mkdir(parents=True)
        (temp_export_dir / "metadata.json").write_text("[]")
        assert snapchat_memories_processor.detect(temp_export_dir) is False

    def test_reject_invalid_metadata_structure(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should reject export with invalid metadata structure."""
        (temp_export_dir / "media").mkdir(parents=True)
        (temp_export_dir / "overlays").mkdir(parents=True)
        # Missing required fields
        (temp_export_dir / "metadata.json").write_text('[{"invalid": "data"}]')
        assert snapchat_memories_processor.detect(temp_export_dir) is False

    def test_reject_non_array_metadata(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should reject export with non-array metadata."""
        (temp_export_dir / "media").mkdir(parents=True)
        (temp_export_dir / "overlays").mkdir(parents=True)
        (temp_export_dir / "metadata.json").write_text('{"not": "array"}')
        assert snapchat_memories_processor.detect(temp_export_dir) is False


class TestSnapchatMessagesDetection:
    """Tests for Snapchat Messages processor detection."""

    def test_detect_valid_raw_export(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Should detect a valid raw Snapchat Messages export."""
        create_snapchat_messages_export(temp_export_dir, raw_format=True)
        assert snapchat_messages_processor.detect(temp_export_dir) is True

    def test_detect_valid_preprocessed_export(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Should detect a valid preprocessed Snapchat Messages export."""
        create_snapchat_messages_export(temp_export_dir, raw_format=False)
        assert snapchat_messages_processor.detect(temp_export_dir) is True

    def test_detect_consolidated_structure(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Should detect consolidated export with messages/ subdirectory."""
        messages_dir = temp_export_dir / "messages"
        create_snapchat_messages_export(messages_dir, raw_format=True)
        assert snapchat_messages_processor.detect(temp_export_dir) is True

    def test_reject_missing_json_dir(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Should reject export without json directory."""
        assert snapchat_messages_processor.detect(temp_export_dir) is False

    def test_reject_missing_chat_history(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Should reject export without chat_history.json."""
        json_dir = temp_export_dir / "json"
        json_dir.mkdir(parents=True)
        (json_dir / "snap_history.json").write_text("{}")
        assert snapchat_messages_processor.detect(temp_export_dir) is False

    def test_reject_missing_snap_history(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Should reject export without snap_history.json."""
        json_dir = temp_export_dir / "json"
        json_dir.mkdir(parents=True)
        (json_dir / "chat_history.json").write_text("{}")
        assert snapchat_messages_processor.detect(temp_export_dir) is False


class TestSnapchatCombinedDetection:
    """Tests for detecting both Snapchat processors in consolidated exports."""

    def test_detect_both_memories_and_messages(
        self, snapchat_memories_processor, snapchat_messages_processor, temp_export_dir
    ):
        """Should detect both Memories and Messages in consolidated export."""
        # Create consolidated structure
        memories_dir = temp_export_dir / "memories"
        messages_dir = temp_export_dir / "messages"

        create_snapchat_memories_export(memories_dir)
        create_snapchat_messages_export(messages_dir, raw_format=True)

        assert snapchat_memories_processor.detect(temp_export_dir) is True
        assert snapchat_messages_processor.detect(temp_export_dir) is True

    def test_memories_does_not_match_messages_export(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Memories processor should not match a Messages-only export."""
        create_snapchat_messages_export(temp_export_dir, raw_format=True)
        assert snapchat_memories_processor.detect(temp_export_dir) is False

    def test_messages_does_not_match_memories_export(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Messages processor should not match a Memories-only export."""
        create_snapchat_memories_export(temp_export_dir)
        assert snapchat_messages_processor.detect(temp_export_dir) is False


class TestSnapchatMessagesRealisticFixtures:
    """Tests using committed schema-faithful Snapchat Messages fixtures."""

    def test_detect_raw_conversation_id_map_fixture(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Should detect fixture with conversation-ID-keyed chat_history.json."""
        from tests.fixtures.export_tree import copy_export_fixture
        import json

        copy_export_fixture(
            "snapchat_messages", "raw_conversation_id_map", temp_export_dir
        )
        assert snapchat_messages_processor.detect(temp_export_dir) is True

        # Verify chat_history.json structure matches docs
        chat_history = temp_export_dir / "messages" / "json" / "chat_history.json"
        data = json.loads(chat_history.read_text())
        # Keys should be conversation IDs
        assert "conversation_abc123def456" in data
        assert "conversation_xyz789ghi012" in data
        # Messages should have documented fields
        msg = data["conversation_abc123def456"][0]
        assert "From" in msg
        assert "Media Type" in msg
        assert "Created" in msg
        assert "Media IDs" in msg

    def test_detect_raw_media_ids_pipe_separated_fixture(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Should detect fixture with pipe-separated Media IDs."""
        from tests.fixtures.export_tree import copy_export_fixture
        import json

        copy_export_fixture(
            "snapchat_messages", "raw_media_ids_pipe_separated", temp_export_dir
        )
        assert snapchat_messages_processor.detect(temp_export_dir) is True

        # Verify pipe-separated Media IDs
        chat_history = temp_export_dir / "messages" / "json" / "chat_history.json"
        data = json.loads(chat_history.read_text())
        msg = data["conversation_multi_media"][0]
        assert " | " in msg["Media IDs"]
        media_ids = msg["Media IDs"].split(" | ")
        assert len(media_ids) == 3

    def test_detect_overlay_ambiguous_mtime_fixture(
        self, snapchat_messages_processor, temp_export_dir
    ):
        """Should detect fixture with ambiguous overlay timestamps."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "snapchat_messages", "overlay_ambiguous_mtime", temp_export_dir
        )
        assert snapchat_messages_processor.detect(temp_export_dir) is True

        # Verify multiple videos and overlays exist
        chat_media = temp_export_dir / "messages" / "chat_media"
        videos = list(chat_media.glob("*_media~*.mp4"))
        overlays = list(chat_media.glob("*_overlay~*.png"))
        assert len(videos) == 2
        assert len(overlays) == 2


class TestSnapchatMemoriesRealisticFixtures:
    """Tests using committed schema-faithful Snapchat Memories fixtures."""

    def test_detect_memories_basic_fixture(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should detect the memories_basic fixture."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", temp_export_dir)
        assert snapchat_memories_processor.detect(temp_export_dir) is True

        # Verify structure matches docs
        memories_dir = temp_export_dir / "memories"
        assert (memories_dir / "metadata.json").exists()
        assert (memories_dir / "media").is_dir()
        assert (memories_dir / "overlays").is_dir()

    def test_memories_metadata_json_format(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should have metadata.json with documented fields."""
        from tests.fixtures.export_tree import copy_export_fixture
        import json

        copy_export_fixture("snapchat_memories", "memories_basic", temp_export_dir)

        metadata_path = temp_export_dir / "memories" / "metadata.json"
        data = json.loads(metadata_path.read_text())

        # Should be an array
        assert isinstance(data, list)
        assert len(data) == 3

        # Check documented fields
        entry = data[0]
        assert "date" in entry
        assert "media_type" in entry
        assert "media_filename" in entry
        # Optional overlay_filename
        assert "overlay_filename" in entry

        # Third entry has no overlay
        assert "overlay_filename" not in data[2]

    def test_memories_media_and_overlays_structure(
        self, snapchat_memories_processor, temp_export_dir
    ):
        """Should have media/ and overlays/ directories with files."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", temp_export_dir)

        memories_dir = temp_export_dir / "memories"

        # Media files
        assert (memories_dir / "media" / "snap_001.jpg").exists()
        assert (memories_dir / "media" / "snap_002.mp4").exists()
        assert (memories_dir / "media" / "snap_003.jpg").exists()

        # Overlay files
        assert (memories_dir / "overlays" / "overlay_001.png").exists()
        assert (memories_dir / "overlays" / "overlay_002.png").exists()
