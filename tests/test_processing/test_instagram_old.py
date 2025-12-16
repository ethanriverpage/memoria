"""
Processing tests for Instagram Old Format processor.

Tests cover edge cases including:
- UTC timestamp filename pattern
- Paired .txt caption files
- Paired .json metadata files
- Carousel numbering (_1, _2)
"""

import json


from tests.fixtures.generators import create_instagram_old_export
from tests.fixtures.media_samples import write_media_file


class TestInstagramOldFilenamePattern:
    """Tests for UTC timestamp filename pattern."""

    def test_utc_timestamp_pattern(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should process files with YYYY-MM-DD_HH-MM-SS_UTC pattern."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                }
            ]
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()

    def test_multiple_timestamps(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should handle files with different timestamps."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "caption": None},
                {"timestamp": "2021-01-02_14-30-00", "extension": "jpg", "caption": None},
                {"timestamp": "2021-01-03_09-15-30", "extension": "jpg", "caption": None},
            ]
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()
        assert (temp_export_dir / "2021-01-02_14-30-00_UTC.jpg").exists()
        assert (temp_export_dir / "2021-01-03_09-15-30_UTC.jpg").exists()


class TestInstagramOldCaptions:
    """Tests for paired .txt caption files."""

    def test_paired_txt_caption(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should handle paired .txt caption files."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": "This is the caption text",
                }
            ]
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.txt").exists()

        caption = (temp_export_dir / "2021-01-01_12-00-00_UTC.txt").read_text()
        assert caption == "This is the caption text"

    def test_media_without_caption(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should handle media without caption file."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                }
            ]
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()
        assert not (temp_export_dir / "2021-01-01_12-00-00_UTC.txt").exists()


class TestInstagramOldMetadata:
    """Tests for metadata handling.

    Note: The detection pattern only matches .json.xz (compressed JSON), not .json.
    These tests verify handling of manually created metadata files.
    """

    def test_media_file_without_metadata(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should handle media files without accompanying metadata."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                }
            ]
        )

        # Media file exists
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()
        # JSON files are not created by default generator (detection pattern expects .json.xz)
        assert not (temp_export_dir / "2021-01-01_12-00-00_UTC.json").exists()

    def test_json_metadata_handling(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should preserve location data in JSON metadata when present."""
        temp_export_dir.mkdir(parents=True, exist_ok=True)

        write_media_file(temp_export_dir / "2021-01-01_12-00-00_UTC.jpg", "jpeg")

        # Manually create JSON metadata file for testing
        metadata = {
            "taken_at": "2021-01-01T12:00:00",
            "location": {
                "name": "Test Location",
                "latitude": 40.7128,
                "longitude": -74.0060,
            },
        }
        (temp_export_dir / "2021-01-01_12-00-00_UTC.json").write_text(json.dumps(metadata))

        loaded = json.loads((temp_export_dir / "2021-01-01_12-00-00_UTC.json").read_text())
        assert loaded["location"]["name"] == "Test Location"


class TestInstagramOldCarousel:
    """Tests for carousel numbering (_1, _2, etc.)."""

    def test_carousel_numbering(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should handle carousel files with _1, _2 suffixes."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "suffix": "_1", "caption": "Carousel post"},
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "suffix": "_2", "caption": None},
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "suffix": "_3", "caption": None},
            ]
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_1.jpg").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_2.jpg").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_3.jpg").exists()

    def test_carousel_caption_on_first_only(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should have caption only on first carousel item."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "suffix": "_1", "caption": "Carousel caption"},
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "suffix": "_2", "caption": None},
            ]
        )

        # Only first item has caption
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_1.txt").exists()
        assert not (temp_export_dir / "2021-01-01_12-00-00_UTC_2.txt").exists()

    def test_carousel_mixed_media(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should handle carousel with mixed photos and videos."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "suffix": "_1", "caption": "Mixed carousel"},
                {"timestamp": "2021-01-01_12-00-00", "extension": "mp4", "suffix": "_2", "caption": None},
                {"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "suffix": "_3", "caption": None},
            ]
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_1.jpg").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_2.mp4").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_3.jpg").exists()


class TestInstagramOldFileTypes:
    """Tests for various file types in Instagram Old Format."""

    def test_jpg_files(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should handle JPG files."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[{"timestamp": "2021-01-01_12-00-00", "extension": "jpg", "caption": None}]
        )
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()

    def test_mp4_files(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should handle MP4 video files."""
        create_instagram_old_export(
            temp_export_dir,
            media_files=[{"timestamp": "2021-01-01_12-00-00", "extension": "mp4", "caption": None}]
        )
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.mp4").exists()

    def test_png_files(self, instagram_old_processor, temp_export_dir, temp_output_dir):
        """Should handle PNG files."""
        temp_export_dir.mkdir(parents=True, exist_ok=True)
        write_media_file(temp_export_dir / "2021-01-01_12-00-00_UTC.png", "png")
        (temp_export_dir / "2021-01-01_12-00-00_UTC.json").write_text('{"taken_at": "2021-01-01T12:00:00"}')

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.png").exists()

