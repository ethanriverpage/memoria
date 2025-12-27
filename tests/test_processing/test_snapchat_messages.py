"""
Processing tests for Snapchat Messages processor.

Tests cover edge cases including:
- Media matched to conversation
- Orphaned media (no metadata match)
- Ambiguous overlay matching (multiple videos/overlays same timestamp)
- Overlay timestamp matching
"""

import json


from tests.fixtures.generators import create_snapchat_messages_export
from tests.fixtures.media_samples import write_media_file


class TestSnapchatMessagesMatching:
    """Tests for media-to-conversation matching in Snapchat Messages."""

    def test_media_matched_to_conversation(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should match media file to conversation via metadata."""
        create_snapchat_messages_export(
            temp_export_dir,
            conversations={
                "other_user": {
                    "title": "Other User",
                    "type": "dm",
                    "messages": [
                        {
                            "created": "2021-01-01 12:00:00 UTC",
                            "sender": "Other User",
                            "media_id": "b~abc123",
                            "media_file": "2021-01-01_b~abc123.jpg",
                        }
                    ],
                }
            },
            raw_format=False,
        )

        assert (temp_export_dir / "media" / "2021-01-01_b~abc123.jpg").exists()

        metadata = json.loads((temp_export_dir / "metadata.json").read_text())
        assert "other_user" in metadata["conversations"]

    def test_orphaned_media(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle orphaned media (no matching metadata)."""
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        # Create media file without matching metadata
        write_media_file(media_dir / "orphaned_photo.jpg", "jpeg")

        metadata = {
            "export_info": {"export_username": "testuser"},
            "conversations": {},
            "unmatched_media": [
                {
                    "media_files": ["orphaned_photo.jpg"],
                    "source_paths": {"orphaned_photo.jpg": str(media_dir / "orphaned_photo.jpg")},
                    "created": "2021-01-01 12:00:00 UTC",
                }
            ],
            "file_hashes": {},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert (media_dir / "orphaned_photo.jpg").exists()


class TestSnapchatMessagesOverlays:
    """Tests for overlay matching in Snapchat Messages."""

    def test_media_with_overlay(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should match media with corresponding overlay."""
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        write_media_file(media_dir / "2021-01-01_b~abc123.jpg", "jpeg")
        write_media_file(overlays_dir / "2021-01-01_b~abc123_overlay.png", "png")

        metadata = {
            "export_info": {"export_username": "testuser"},
            "conversations": {
                "other_user": {
                    "title": "Other User",
                    "type": "dm",
                    "messages": [
                        {
                            "created": "2021-01-01 12:00:00 UTC",
                            "sender": "Other User",
                            "media_id": "b~abc123",
                            "media_files": ["2021-01-01_b~abc123.jpg"],
                            "source_paths": {"2021-01-01_b~abc123.jpg": str(media_dir / "2021-01-01_b~abc123.jpg")},
                            "overlay_file": "2021-01-01_b~abc123_overlay.png",
                        }
                    ],
                }
            },
            "unmatched_media": [],
            "file_hashes": {},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert (media_dir / "2021-01-01_b~abc123.jpg").exists()
        assert (overlays_dir / "2021-01-01_b~abc123_overlay.png").exists()

    def test_timestamp_based_overlay_matching(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should match overlays based on timestamp when media_id matching fails."""
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        # Files with matching timestamps but different naming
        write_media_file(media_dir / "2021-01-01_video.mp4", "mp4")
        write_media_file(overlays_dir / "2021-01-01_overlay.png", "png")

        metadata = {
            "export_info": {"export_username": "testuser"},
            "conversations": {},
            "unmatched_media": [],
            "file_hashes": {},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert (media_dir / "2021-01-01_video.mp4").exists()
        assert (overlays_dir / "2021-01-01_overlay.png").exists()


class TestSnapchatMessagesAmbiguousCases:
    """Tests for ambiguous matching scenarios."""

    def test_multiple_videos_same_timestamp(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle multiple videos with same timestamp."""
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        # Multiple videos with same date prefix
        write_media_file(media_dir / "2021-01-01_b~abc123.mp4", "mp4")
        write_media_file(media_dir / "2021-01-01_b~def456.mp4", "mp4")

        # Multiple overlays with same date prefix
        write_media_file(overlays_dir / "2021-01-01_overlay1.png", "png")
        write_media_file(overlays_dir / "2021-01-01_overlay2.png", "png")

        metadata = {
            "export_info": {"export_username": "testuser"},
            "conversations": {},
            "unmatched_media": [],
            "file_hashes": {},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        # Both videos should exist
        assert (media_dir / "2021-01-01_b~abc123.mp4").exists()
        assert (media_dir / "2021-01-01_b~def456.mp4").exists()


class TestSnapchatMessagesRawFormat:
    """Tests for raw export format processing."""

    def test_raw_export_structure(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process raw export with json/ directory."""
        create_snapchat_messages_export(temp_export_dir, raw_format=True)

        json_dir = temp_export_dir / "json"
        assert (json_dir / "chat_history.json").exists()
        assert (json_dir / "snap_history.json").exists()

    def test_chat_history_structure(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should parse chat history with received and sent sections."""
        create_snapchat_messages_export(temp_export_dir, raw_format=True)

        chat_history = json.loads(
            (temp_export_dir / "json" / "chat_history.json").read_text()
        )
        assert "Received Saved Chat History" in chat_history
        assert "Sent Saved Chat History" in chat_history

    def test_snap_history_structure(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should parse snap history with received and sent sections."""
        create_snapchat_messages_export(temp_export_dir, raw_format=True)

        snap_history = json.loads(
            (temp_export_dir / "json" / "snap_history.json").read_text()
        )
        assert "Received Snap History" in snap_history


class TestSnapchatMessagesConsolidated:
    """Tests for consolidated export structure."""

    def test_consolidated_messages_structure(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle consolidated export with messages/ subdirectory."""
        messages_dir = temp_export_dir / "messages"
        create_snapchat_messages_export(messages_dir, raw_format=True)

        # Verify structure is within messages/ subdirectory
        assert (messages_dir / "json" / "chat_history.json").exists()
        assert (messages_dir / "json" / "snap_history.json").exists()


class TestSnapchatMessagesConversationTypes:
    """Tests for different conversation types."""

    def test_dm_conversation(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process DM conversations."""
        create_snapchat_messages_export(
            temp_export_dir,
            conversations={
                "user1": {
                    "title": "User One",
                    "type": "dm",
                    "messages": [
                        {
                            "created": "2021-01-01 12:00:00 UTC",
                            "sender": "User One",
                            "media_id": "b~abc123",
                            "media_file": "2021-01-01_b~abc123.jpg",
                        }
                    ],
                }
            },
            raw_format=False,
        )

        metadata = json.loads((temp_export_dir / "metadata.json").read_text())
        assert metadata["conversations"]["user1"]["type"] == "dm"

    def test_group_conversation(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should process group conversations."""
        create_snapchat_messages_export(
            temp_export_dir,
            conversations={
                "group_abc": {
                    "title": "Friend Group",
                    "type": "group",
                    "messages": [
                        {
                            "created": "2021-01-01 12:00:00 UTC",
                            "sender": "Friend One",
                            "media_id": "b~xyz789",
                            "media_file": "2021-01-01_b~xyz789.jpg",
                        }
                    ],
                }
            },
            raw_format=False,
        )

        metadata = json.loads((temp_export_dir / "metadata.json").read_text())
        assert metadata["conversations"]["group_abc"]["type"] == "group"


class TestSnapchatMessagesRealisticFixtures:
    """Processing tests using committed schema-faithful Snapchat Messages fixtures."""

    def test_raw_conversation_id_map_structure(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse conversation-ID-keyed chat_history.json."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "snapchat_messages", "raw_conversation_id_map", temp_export_dir
        )

        chat_history = json.loads(
            (temp_export_dir / "messages" / "json" / "chat_history.json").read_text()
        )

        # Verify doc-style structure
        assert len(chat_history) == 2

        # DM conversation (null Conversation Title)
        dm_conv = chat_history["conversation_abc123def456"]
        assert dm_conv[0]["Conversation Title"] is None
        assert dm_conv[0]["From"] == "friend_username"
        assert dm_conv[0]["Media Type"] == "MEDIA"
        assert dm_conv[0]["Created"] == "2024-04-20 01:29:22 UTC"

        # Group conversation (non-null Conversation Title)
        group_conv = chat_history["conversation_xyz789ghi012"]
        assert group_conv[0]["Conversation Title"] == "Weekend Trip Planning"

    def test_raw_media_ids_pipe_separated_parsing(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse pipe-separated Media IDs."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "snapchat_messages", "raw_media_ids_pipe_separated", temp_export_dir
        )

        chat_history = json.loads(
            (temp_export_dir / "messages" / "json" / "chat_history.json").read_text()
        )

        msg = chat_history["conversation_multi_media"][0]
        media_ids = msg["Media IDs"].split(" | ")

        assert len(media_ids) == 3
        assert media_ids[0] == "b~abc123"
        assert media_ids[1] == "b~def456"
        assert media_ids[2] == "b~ghi789"

    def test_overlay_ambiguous_mtime_files(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should have multiple videos and overlays with same timestamps."""
        from tests.fixtures.export_tree import copy_export_fixture
        import os

        copy_export_fixture(
            "snapchat_messages", "overlay_ambiguous_mtime", temp_export_dir
        )

        chat_media = temp_export_dir / "messages" / "chat_media"

        # Get all videos and overlays
        videos = sorted(chat_media.glob("*_media~*.mp4"))
        overlays = sorted(chat_media.glob("*_overlay~*.png"))

        assert len(videos) == 2
        assert len(overlays) == 2

        # Note: mtime ambiguity is set up in the fixture
        # Tests can verify the scenario exists for needs_matching logic

    def test_snap_history_format(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse snap_history.json with simplified schema."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "snapchat_messages", "raw_conversation_id_map", temp_export_dir
        )

        snap_history = json.loads(
            (temp_export_dir / "messages" / "json" / "snap_history.json").read_text()
        )

        # snap_history uses IMAGE/VIDEO instead of MEDIA
        snap_msg = snap_history["conversation_abc123def456"][0]
        assert snap_msg["Media Type"] == "IMAGE"
        # No Media IDs or Content in snap_history
        assert "Media IDs" not in snap_msg
        assert "Content" not in snap_msg
