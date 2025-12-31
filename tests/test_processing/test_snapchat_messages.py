"""
Processing tests for Snapchat Messages processor.

Tests cover edge cases including:
- Media matched to conversation
- Orphaned media (no metadata match)
- Ambiguous overlay matching (multiple videos/overlays same timestamp)
- Overlay timestamp matching
- Overlay embedding failure handling
"""

import json
from unittest.mock import patch

from tests.fixtures.export_tree import copy_export_fixture
from tests.fixtures.media_samples import write_media_file


class TestSnapchatMessagesPreprocessing:
    """Tests that run the preprocessor and verify output."""

    def test_raw_format_preprocessing(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should preprocess raw Snapchat Messages export."""
        from processors.snapchat_messages.preprocess import SnapchatMessagesPreprocessor

        copy_export_fixture(
            "snapchat_messages", "raw_conversation_id_map", temp_export_dir
        )

        preprocessor = SnapchatMessagesPreprocessor(
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

        assert "export_info" in output_metadata
        assert output_metadata["export_info"]["platform"] == "Snapchat"


class TestSnapchatMessagesFixtureStructure:
    """Tests that verify schema-faithful fixture structures match docs."""

    def test_raw_conversation_id_map_structure(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse conversation-ID-keyed chat_history.json."""
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
        copy_export_fixture(
            "snapchat_messages", "overlay_ambiguous_mtime", temp_export_dir
        )

        chat_media = temp_export_dir / "messages" / "chat_media"

        # Get all videos and overlays
        videos = sorted(chat_media.glob("*_media~*.mp4"))
        overlays = sorted(chat_media.glob("*_overlay~*.png"))

        assert len(videos) == 2
        assert len(overlays) == 2

    def test_snap_history_format(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse snap_history.json with simplified schema."""
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


class TestSnapchatMessagesOverlays:
    """Tests for overlay matching in Snapchat Messages."""

    def test_media_with_overlay_in_fixture(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """Should have overlay files alongside media in fixtures."""
        copy_export_fixture(
            "snapchat_messages", "overlay_ambiguous_mtime", temp_export_dir
        )

        chat_media = temp_export_dir / "messages" / "chat_media"

        # Verify overlay files exist
        overlays = list(chat_media.glob("*_overlay~*.png"))
        assert len(overlays) > 0, "Should have overlay files"


class TestSnapchatMessagesMatching:
    """Tests for media-to-conversation matching."""

    def test_orphaned_media_structure(
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
            "export_info": {
                "export_path": str(temp_export_dir),
                "export_username": "testuser",
                "processed_date": "2021-01-01T12:00:00",
            },
            "conversations": {},
            "unmatched_media": [
                {
                    "media_files": ["orphaned_photo.jpg"],
                    "source_paths": {
                        "orphaned_photo.jpg": str(media_dir / "orphaned_photo.jpg")
                    },
                    "created": "2021-01-01 12:00:00 UTC",
                }
            ],
            "file_hashes": {},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert (media_dir / "orphaned_photo.jpg").exists()


class TestSnapchatMessagesOverlayFailure:
    """Tests for overlay embedding failure handling.

    Regression tests for the bug where overlay failures were misreported as
    'exif_target_missing' for non-existent .mkv files.
    """

    def test_overlay_failure_produces_fallback_and_tracks_correctly(
        self, snapchat_messages_processor, temp_export_dir, temp_output_dir
    ):
        """When overlay embedding fails, should produce fallback .mp4 and track as overlay_embed_failed.

        This is a regression test for:
        - Bug: OverlayProcessingError was returning wrong path (.mkv instead of fallback .mp4)
        - Result: EXIF batch reported 'exif_target_missing' for non-existent .mkv files
        - Fix: Worker now returns fallback path and failure context; tracked as overlay_embed_failed
        """
        from common.media.overlay import OverlayProcessingError

        # Create preprocessed export with a video that has an overlay
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        # Create media and overlay files
        write_media_file(media_dir / "2021-01-01_b~abc123.mp4", "mp4")
        write_media_file(overlays_dir / "2021-01-01_overlay.png", "png")

        # Build a valid preprocessed metadata.json
        metadata = {
            "export_info": {
                "export_path": str(temp_export_dir),
                "export_username": "testuser",
                "processed_date": "2021-01-01T12:00:00",
            },
            "conversations": {
                "other_user": {
                    "title": "Other User",
                    "type": "dm",
                    "messages": [
                        {
                            "created": "2021-01-01 12:00:00 UTC",
                            "sender": "Other User",
                            "media_id": "b~abc123",
                            "media_files": ["2021-01-01_b~abc123.mp4"],
                            "source_paths": {
                                "2021-01-01_b~abc123.mp4": str(
                                    media_dir / "2021-01-01_b~abc123.mp4"
                                )
                            },
                            "overlay_file": "2021-01-01_overlay.png",
                            "overlay_source_path": str(
                                overlays_dir / "2021-01-01_overlay.png"
                            ),
                        }
                    ],
                }
            },
            "unmatched_media": [],
            "file_hashes": {"2021-01-01_b~abc123.mp4": "abc123hash"},
        }
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        # Mock create_video_with_overlay to raise OverlayProcessingError
        with patch(
            "processors.snapchat_messages.processor.create_video_with_overlay"
        ) as mock_overlay:
            mock_overlay.side_effect = OverlayProcessingError(
                "FFmpeg overlay embedding failed: test error"
            )

            # Run the processor
            result = snapchat_messages_processor.process(
                input_dir=str(temp_export_dir),
                output_dir=str(temp_output_dir),
            )

        assert result is True, "Processor should succeed even with overlay failures"

        # Check that a fallback .mp4 was produced (not .mkv)
        messages_dir = temp_output_dir / "messages"
        mp4_files = list(messages_dir.glob("*.mp4")) + list(
            (messages_dir / "issues" / "failed-processing").glob("*.mp4")
            if (messages_dir / "issues" / "failed-processing").exists()
            else []
        )
        mkv_files = list(messages_dir.glob("*.mkv"))

        # There should be no .mkv files (overlay failed)
        assert len(mkv_files) == 0, f"Should have no MKV files, found: {mkv_files}"

        # Check failure report exists and contains overlay_embed_failed
        failure_report_path = messages_dir / "issues" / "failure-report.json"
        assert failure_report_path.exists(), "Failure report should exist"

        failure_report = json.loads(failure_report_path.read_text())

        # Check for overlay_embed_failed entries
        processing_failures = failure_report.get("failed_processing", [])
        overlay_failures = [
            f for f in processing_failures if f.get("reason") == "overlay_embed_failed"
        ]
        assert (
            len(overlay_failures) == 1
        ), f"Should have 1 overlay_embed_failed, found: {processing_failures}"

        # Verify there are NO exif_target_missing failures for .mkv paths
        exif_missing_failures = [
            f
            for f in processing_failures
            if f.get("reason") == "exif_target_missing"
            and ".mkv" in f.get("file_path", "")
        ]
        assert len(exif_missing_failures) == 0, (
            f"Should have no exif_target_missing for .mkv files, found: {exif_missing_failures}"
        )

        # Verify the overlay failure has proper context
        overlay_failure = overlay_failures[0]
        assert "FFmpeg overlay embedding failed" in overlay_failure.get(
            "error_details", ""
        )
        context = overlay_failure.get("context", {})
        assert context.get("intended_mkv_path") is not None
        assert context.get("overlay_source_path") is not None
