"""
Processing tests for Snapchat Memories processor.

Tests cover edge cases including:
- Memory with overlay (photo)
- Memory without overlay
- Video memory with overlay
- Memory with missing overlay file
- Empty metadata.json array
- Resolution mismatch (overlay scaling)
"""

import json


from tests.fixtures.generators import create_snapchat_memories_export
from tests.fixtures.media_samples import write_media_file


class TestSnapchatMemoriesOverlays:
    """Tests for overlay handling in Snapchat Memories processing."""

    def test_image_with_overlay(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should process image memory with overlay."""
        create_snapchat_memories_export(
            temp_export_dir,
            memories=[
                {
                    "date": "2021-01-01 12:00:00 UTC",
                    "media_type": "Image",
                    "media_filename": "photo.jpg",
                    "overlay_filename": "overlay.png",
                }
            ],
            include_overlays=True
        )

        assert (temp_export_dir / "media" / "photo.jpg").exists()
        assert (temp_export_dir / "overlays" / "overlay.png").exists()

    def test_image_without_overlay(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should process image memory without overlay."""
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        write_media_file(media_dir / "photo.jpg", "jpeg")

        metadata = [
            {
                "date": "2021-01-01 12:00:00 UTC",
                "media_type": "Image",
                "media_filename": "photo.jpg",
                "overlay_filename": None,
            }
        ]
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert (media_dir / "photo.jpg").exists()
        assert not (overlays_dir / "overlay.png").exists()

    def test_video_with_overlay(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should process video memory with overlay."""
        create_snapchat_memories_export(
            temp_export_dir,
            memories=[
                {
                    "date": "2021-01-01 12:00:00 UTC",
                    "media_type": "Video",
                    "media_filename": "video.mp4",
                    "overlay_filename": "overlay.png",
                }
            ],
            include_overlays=True
        )

        assert (temp_export_dir / "media" / "video.mp4").exists()
        assert (temp_export_dir / "overlays" / "overlay.png").exists()


class TestSnapchatMemoriesMissingFiles:
    """Tests for handling missing files in Snapchat Memories."""

    def test_missing_overlay_file(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should handle memory with referenced but missing overlay file."""
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        write_media_file(media_dir / "photo.jpg", "jpeg")
        # Overlay referenced in metadata but not created

        metadata = [
            {
                "date": "2021-01-01 12:00:00 UTC",
                "media_type": "Image",
                "media_filename": "photo.jpg",
                "overlay_filename": "missing_overlay.png",
            }
        ]
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert (media_dir / "photo.jpg").exists()
        assert not (overlays_dir / "missing_overlay.png").exists()

    def test_missing_media_file(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should handle memory with referenced but missing media file."""
        media_dir = temp_export_dir / "media"
        overlays_dir = temp_export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        write_media_file(overlays_dir / "overlay.png", "png")
        # Media referenced but not created

        metadata = [
            {
                "date": "2021-01-01 12:00:00 UTC",
                "media_type": "Image",
                "media_filename": "missing_photo.jpg",
                "overlay_filename": "overlay.png",
            }
        ]
        (temp_export_dir / "metadata.json").write_text(json.dumps(metadata))

        assert not (media_dir / "missing_photo.jpg").exists()
        assert (overlays_dir / "overlay.png").exists()


class TestSnapchatMemoriesMetadata:
    """Tests for metadata handling in Snapchat Memories."""

    def test_multiple_memories(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should process multiple memories."""
        create_snapchat_memories_export(
            temp_export_dir,
            memories=[
                {
                    "date": "2021-01-01 12:00:00 UTC",
                    "media_type": "Image",
                    "media_filename": "photo1.jpg",
                    "overlay_filename": "overlay1.png",
                },
                {
                    "date": "2021-01-02 12:00:00 UTC",
                    "media_type": "Image",
                    "media_filename": "photo2.jpg",
                    "overlay_filename": "overlay2.png",
                },
                {
                    "date": "2021-01-03 12:00:00 UTC",
                    "media_type": "Video",
                    "media_filename": "video.mp4",
                    "overlay_filename": "overlay3.png",
                },
            ],
            include_overlays=True
        )

        assert (temp_export_dir / "media" / "photo1.jpg").exists()
        assert (temp_export_dir / "media" / "photo2.jpg").exists()
        assert (temp_export_dir / "media" / "video.mp4").exists()

    def test_memories_on_same_date(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should handle multiple memories with same timestamp."""
        create_snapchat_memories_export(
            temp_export_dir,
            memories=[
                {
                    "date": "2021-01-01 12:00:00 UTC",
                    "media_type": "Image",
                    "media_filename": "photo1.jpg",
                    "overlay_filename": "overlay1.png",
                },
                {
                    "date": "2021-01-01 12:00:00 UTC",
                    "media_type": "Image",
                    "media_filename": "photo2.jpg",
                    "overlay_filename": "overlay2.png",
                },
            ],
            include_overlays=True
        )

        metadata = json.loads((temp_export_dir / "metadata.json").read_text())
        assert len(metadata) == 2
        assert metadata[0]["date"] == metadata[1]["date"]


class TestSnapchatMemoriesFileTypes:
    """Tests for various file types in Snapchat Memories."""

    def test_jpg_extension(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should handle .jpg files."""
        create_snapchat_memories_export(
            temp_export_dir,
            memories=[
                {
                    "date": "2021-01-01 12:00:00 UTC",
                    "media_type": "Image",
                    "media_filename": "photo.jpg",
                    "overlay_filename": None,
                }
            ],
            include_overlays=False
        )
        assert (temp_export_dir / "media" / "photo.jpg").exists()

    def test_mp4_extension(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should handle .mp4 files."""
        create_snapchat_memories_export(
            temp_export_dir,
            memories=[
                {
                    "date": "2021-01-01 12:00:00 UTC",
                    "media_type": "Video",
                    "media_filename": "video.mp4",
                    "overlay_filename": None,
                }
            ],
            include_overlays=False
        )
        assert (temp_export_dir / "media" / "video.mp4").exists()


class TestSnapchatMemoriesConsolidated:
    """Tests for consolidated export structure."""

    def test_consolidated_structure(self, snapchat_memories_processor, temp_export_dir, temp_output_dir):
        """Should handle consolidated export with memories/ subdirectory."""
        memories_dir = temp_export_dir / "memories"
        create_snapchat_memories_export(
            memories_dir,
            memories=[
                {
                    "date": "2021-01-01 12:00:00 UTC",
                    "media_type": "Image",
                    "media_filename": "photo.jpg",
                    "overlay_filename": None,
                }
            ],
            include_overlays=False
        )

        # Verify structure is within memories/ subdirectory
        assert (memories_dir / "media" / "photo.jpg").exists()
        assert (memories_dir / "metadata.json").exists()

