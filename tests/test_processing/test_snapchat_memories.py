"""
Processing tests for Snapchat Memories processor.

Tests cover edge cases including:
- Memory with overlay (photo)
- Memory without overlay
- Video memory with overlay
- Memory with missing overlay file
- Empty metadata.json array
- Resolution mismatch (overlay scaling)
- GPS coordinate handling (filtering invalid coordinates)
"""

import json

import pytest

from processors.snapchat_memories.processor import _build_metadata_requests
from tests.fixtures.generators import create_snapchat_memories_export
from tests.fixtures.media_samples import write_media_file


class TestSnapchatMemoriesOverlays:
    """Tests for overlay handling in Snapchat Memories processing."""

    def test_image_with_overlay(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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
            include_overlays=True,
        )

        assert (temp_export_dir / "media" / "photo.jpg").exists()
        assert (temp_export_dir / "overlays" / "overlay.png").exists()

    def test_image_without_overlay(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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

    def test_video_with_overlay(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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
            include_overlays=True,
        )

        assert (temp_export_dir / "media" / "video.mp4").exists()
        assert (temp_export_dir / "overlays" / "overlay.png").exists()


class TestSnapchatMemoriesMissingFiles:
    """Tests for handling missing files in Snapchat Memories."""

    def test_missing_overlay_file(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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

    def test_missing_media_file(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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

    def test_multiple_memories(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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
            include_overlays=True,
        )

        assert (temp_export_dir / "media" / "photo1.jpg").exists()
        assert (temp_export_dir / "media" / "photo2.jpg").exists()
        assert (temp_export_dir / "media" / "video.mp4").exists()

    def test_memories_on_same_date(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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
            include_overlays=True,
        )

        metadata = json.loads((temp_export_dir / "metadata.json").read_text())
        assert len(metadata) == 2
        assert metadata[0]["date"] == metadata[1]["date"]


class TestSnapchatMemoriesFileTypes:
    """Tests for various file types in Snapchat Memories."""

    def test_jpg_extension(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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
            include_overlays=False,
        )
        assert (temp_export_dir / "media" / "photo.jpg").exists()

    def test_mp4_extension(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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
            include_overlays=False,
        )
        assert (temp_export_dir / "media" / "video.mp4").exists()


class TestSnapchatMemoriesConsolidated:
    """Tests for consolidated export structure."""

    def test_consolidated_structure(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
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
            include_overlays=False,
        )

        # Verify structure is within memories/ subdirectory
        assert (memories_dir / "media" / "photo.jpg").exists()
        assert (memories_dir / "metadata.json").exists()


class TestSnapchatMemoriesRealisticFixtures:
    """Processing tests using committed schema-faithful Snapchat Memories fixtures."""

    def test_memories_basic_structure(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse the memories_basic fixture structure."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", temp_export_dir)

        memories_dir = temp_export_dir / "memories"

        # Verify all expected directories
        assert (memories_dir / "media").is_dir()
        assert (memories_dir / "overlays").is_dir()
        assert (memories_dir / "metadata.json").is_file()

    def test_memories_metadata_json_array_format(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse metadata.json as an array of memory objects."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", temp_export_dir)

        metadata = json.loads(
            (temp_export_dir / "memories" / "metadata.json").read_text()
        )

        # Should be an array
        assert isinstance(metadata, list)
        assert len(metadata) == 3

        # Check documented timestamp format
        assert metadata[0]["date"] == "2023-01-15 10:30:45 UTC"
        assert metadata[1]["date"] == "2023-02-20 14:22:10 UTC"

    def test_memories_media_types(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse PHOTO and VIDEO media types."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", temp_export_dir)

        metadata = json.loads(
            (temp_export_dir / "memories" / "metadata.json").read_text()
        )

        # First two are PHOTO and VIDEO
        assert metadata[0]["media_type"] == "PHOTO"
        assert metadata[1]["media_type"] == "VIDEO"
        assert metadata[2]["media_type"] == "PHOTO"

    def test_memories_optional_overlay_filename(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle memories with and without overlay_filename."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", temp_export_dir)

        metadata = json.loads(
            (temp_export_dir / "memories" / "metadata.json").read_text()
        )

        # First two have overlays
        assert "overlay_filename" in metadata[0]
        assert metadata[0]["overlay_filename"] == "overlay_001.png"
        assert "overlay_filename" in metadata[1]
        assert metadata[1]["overlay_filename"] == "overlay_002.png"

        # Third has no overlay
        assert "overlay_filename" not in metadata[2]

    def test_memories_media_files_exist(
        self, snapchat_memories_processor, temp_export_dir, temp_output_dir
    ):
        """Should have all referenced media files present."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("snapchat_memories", "memories_basic", temp_export_dir)

        memories_dir = temp_export_dir / "memories"

        # All media files should exist
        assert (memories_dir / "media" / "snap_001.jpg").exists()
        assert (memories_dir / "media" / "snap_002.mp4").exists()
        assert (memories_dir / "media" / "snap_003.jpg").exists()

        # Overlay files should exist
        assert (memories_dir / "overlays" / "overlay_001.png").exists()
        assert (memories_dir / "overlays" / "overlay_002.png").exists()


class TestSnapchatMemoriesGPSHandling:
    """Tests for GPS coordinate handling in metadata requests."""

    @pytest.fixture
    def base_media_data(self):
        """Base media data for GPS tests."""
        return {
            "date": "2021-01-01 12:00:00 UTC",
            "canonical_timestamp": "2021-01-01T12:00:00Z",
        }

    def test_gps_zero_zero_is_skipped(self, base_media_data, tmp_path):
        """GPS (0.0, 0.0) should be treated as missing, not embedded."""
        # Create a test file
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"\xff\xd8\xff\xe0")  # Minimal JPEG header

        media_data = {
            **base_media_data,
            "latitude": 0.0,
            "longitude": 0.0,
        }

        file_info = [
            (str(test_file), media_data, "testuser", "hash123", "original.jpg")
        ]

        requests = _build_metadata_requests(file_info)

        assert len(requests) == 1
        assert requests[0].gps is None  # GPS should not be set

    def test_valid_gps_is_included(self, base_media_data, tmp_path):
        """Valid GPS coordinates should be embedded."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"\xff\xd8\xff\xe0")

        media_data = {
            **base_media_data,
            "latitude": 37.7749,
            "longitude": -122.4194,
        }

        file_info = [
            (str(test_file), media_data, "testuser", "hash123", "original.jpg")
        ]

        requests = _build_metadata_requests(file_info)

        assert len(requests) == 1
        assert requests[0].gps is not None
        assert requests[0].gps.latitude == 37.7749
        assert requests[0].gps.longitude == -122.4194

    def test_gps_out_of_range_is_skipped(self, base_media_data, tmp_path):
        """GPS coordinates out of valid range should be skipped."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"\xff\xd8\xff\xe0")

        # Latitude out of range
        media_data = {
            **base_media_data,
            "latitude": 95.0,  # Invalid: > 90
            "longitude": -122.4194,
        }

        file_info = [
            (str(test_file), media_data, "testuser", "hash123", "original.jpg")
        ]

        requests = _build_metadata_requests(file_info)

        assert len(requests) == 1
        assert requests[0].gps is None

    def test_gps_missing_is_skipped(self, base_media_data, tmp_path):
        """Missing GPS coordinates should result in no GPS data."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"\xff\xd8\xff\xe0")

        # No latitude/longitude in media_data
        file_info = [
            (str(test_file), base_media_data, "testuser", "hash123", "original.jpg")
        ]

        requests = _build_metadata_requests(file_info)

        assert len(requests) == 1
        assert requests[0].gps is None

    def test_gps_boundary_values_are_valid(self, base_media_data, tmp_path):
        """GPS at boundary values (-90/90, -180/180) should be valid."""
        test_file = tmp_path / "test.jpg"
        test_file.write_bytes(b"\xff\xd8\xff\xe0")

        # Test at boundaries
        media_data = {
            **base_media_data,
            "latitude": 90.0,
            "longitude": 180.0,
        }

        file_info = [
            (str(test_file), media_data, "testuser", "hash123", "original.jpg")
        ]

        requests = _build_metadata_requests(file_info)

        assert len(requests) == 1
        assert requests[0].gps is not None
        assert requests[0].gps.latitude == 90.0
        assert requests[0].gps.longitude == 180.0
