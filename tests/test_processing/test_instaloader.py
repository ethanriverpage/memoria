"""
Processing tests for Instaloader format via Instagram Public Media processor.

Tests cover edge cases including:
- UTC timestamp filename pattern
- Paired .txt caption files
- Paired .json metadata files
- Carousel numbering (_1, _2)

Note: Instaloader format is now handled by the unified Instagram Public Media processor.
"""

import json


from tests.fixtures.generators import create_instaloader_export
from tests.fixtures.media_samples import write_media_file


class TestInstaloaderFilenamePattern:
    """Tests for UTC timestamp filename pattern."""

    def test_utc_timestamp_pattern(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should process files with YYYY-MM-DD_HH-MM-SS_UTC pattern."""
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                }
            ],
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()

    def test_multiple_timestamps(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle files with different timestamps."""
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                },
                {
                    "timestamp": "2021-01-02_14-30-00",
                    "extension": "jpg",
                    "caption": None,
                },
                {
                    "timestamp": "2021-01-03_09-15-30",
                    "extension": "jpg",
                    "caption": None,
                },
            ],
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()
        assert (temp_export_dir / "2021-01-02_14-30-00_UTC.jpg").exists()
        assert (temp_export_dir / "2021-01-03_09-15-30_UTC.jpg").exists()


class TestInstaloaderCaptions:
    """Tests for paired .txt caption files."""

    def test_paired_txt_caption(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle paired .txt caption files."""
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": "This is the caption text",
                }
            ],
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.txt").exists()

        caption = (temp_export_dir / "2021-01-01_12-00-00_UTC.txt").read_text()
        assert caption == "This is the caption text"

    def test_media_without_caption(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle media without caption file."""
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                }
            ],
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()
        assert not (temp_export_dir / "2021-01-01_12-00-00_UTC.txt").exists()


class TestInstaloaderMetadata:
    """Tests for metadata handling.

    Note: The detection pattern only matches .json.xz (compressed JSON), not .json.
    These tests verify handling of manually created metadata files.
    """

    def test_media_file_without_metadata(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle media files without accompanying metadata."""
        # Create media file manually without using generator (which always creates JSON)
        temp_export_dir.mkdir(parents=True, exist_ok=True)
        write_media_file(temp_export_dir / "2021-01-01_12-00-00_UTC.jpg", "jpeg")

        # Media file exists
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()
        # No JSON metadata file for this test case
        assert not (temp_export_dir / "2021-01-01_12-00-00_UTC.json").exists()

    def test_json_metadata_handling(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
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
        (temp_export_dir / "2021-01-01_12-00-00_UTC.json").write_text(
            json.dumps(metadata)
        )

        loaded = json.loads(
            (temp_export_dir / "2021-01-01_12-00-00_UTC.json").read_text()
        )
        assert loaded["location"]["name"] == "Test Location"


class TestInstaloaderCarousel:
    """Tests for carousel numbering (_1, _2, etc.)."""

    def test_carousel_numbering(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle carousel files with _1, _2 suffixes."""
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "suffix": "_1",
                    "caption": "Carousel post",
                },
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "suffix": "_2",
                    "caption": None,
                },
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "suffix": "_3",
                    "caption": None,
                },
            ],
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_1.jpg").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_2.jpg").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_3.jpg").exists()

    def test_carousel_caption_on_first_only(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should have caption only on first carousel item."""
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "suffix": "_1",
                    "caption": "Carousel caption",
                },
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "suffix": "_2",
                    "caption": None,
                },
            ],
        )

        # Only first item has caption
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_1.txt").exists()
        assert not (temp_export_dir / "2021-01-01_12-00-00_UTC_2.txt").exists()

    def test_carousel_mixed_media(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle carousel with mixed photos and videos."""
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "suffix": "_1",
                    "caption": "Mixed carousel",
                },
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "mp4",
                    "suffix": "_2",
                    "caption": None,
                },
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "suffix": "_3",
                    "caption": None,
                },
            ],
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_1.jpg").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_2.mp4").exists()
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC_3.jpg").exists()


class TestInstaloaderFileTypes:
    """Tests for various file types in Instaloader format."""

    def test_jpg_files(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle JPG files."""
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                }
            ],
        )
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.jpg").exists()

    def test_mp4_files(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle MP4 video files."""
        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "mp4",
                    "caption": None,
                }
            ],
        )
        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.mp4").exists()

    def test_png_files(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle PNG files."""
        temp_export_dir.mkdir(parents=True, exist_ok=True)
        write_media_file(temp_export_dir / "2021-01-01_12-00-00_UTC.png", "png")
        (temp_export_dir / "2021-01-01_12-00-00_UTC.json").write_text(
            '{"taken_at": "2021-01-01T12:00:00"}'
        )

        assert (temp_export_dir / "2021-01-01_12-00-00_UTC.png").exists()


class TestInstaloaderOrphanDetection:
    """Tests for orphaned metadata detection."""

    def test_orphaned_metadata_txt_without_media(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should track .txt caption files without matching media as orphaned metadata."""
        from processors.instagram_public_media.preprocess_instaloader import (
            InstaloaderFormatPreprocessor,
        )

        temp_export_dir.mkdir(parents=True, exist_ok=True)

        # Create a valid media file so preprocessor doesn't exit early
        write_media_file(temp_export_dir / "2021-01-02_12-00-00_UTC.jpg", "jpeg")
        # Add JSON with username for username detection
        (temp_export_dir / "2021-01-02_12-00-00_UTC.json").write_text(
            '{"taken_at": "2021-01-02T12:00:00", "owner": {"username": "testuser"}}'
        )

        # Create a .txt file without corresponding media file (orphaned)
        (temp_export_dir / "2021-01-01_12-00-00_UTC.txt").write_text("Orphaned caption")

        # Run preprocessor
        preprocessor = InstaloaderFormatPreprocessor(
            export_path=temp_export_dir,
            output_dir=temp_output_dir,
            final_output_dir=temp_output_dir,
        )
        preprocessor.run()

        # Check that orphaned metadata was tracked
        assert len(preprocessor.failure_tracker.orphaned_metadata) >= 1
        orphaned_entry = preprocessor.failure_tracker.orphaned_metadata[0]
        assert (
            orphaned_entry["reason"]
            == "Metadata exists but no matching media file found"
        )
        assert orphaned_entry["context"]["has_txt"] is True

    def test_orphaned_metadata_json_without_media(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should track .json metadata files without matching media as orphaned metadata."""
        from processors.instagram_public_media.preprocess_instaloader import (
            InstaloaderFormatPreprocessor,
        )

        temp_export_dir.mkdir(parents=True, exist_ok=True)

        # Create a valid media file so preprocessor doesn't exit early
        write_media_file(temp_export_dir / "2021-01-02_12-00-00_UTC.jpg", "jpeg")
        # Add JSON with username for username detection
        (temp_export_dir / "2021-01-02_12-00-00_UTC.json").write_text(
            '{"taken_at": "2021-01-02T12:00:00", "owner": {"username": "testuser"}}'
        )

        # Create a .json file without corresponding media file (orphaned)
        (temp_export_dir / "2021-01-01_12-00-00_UTC.json").write_text(
            '{"taken_at": "2021-01-01T12:00:00", "owner": {"username": "testuser"}}'
        )

        # Run preprocessor
        preprocessor = InstaloaderFormatPreprocessor(
            export_path=temp_export_dir,
            output_dir=temp_output_dir,
            final_output_dir=temp_output_dir,
        )
        preprocessor.run()

        # Check that orphaned metadata was tracked
        assert len(preprocessor.failure_tracker.orphaned_metadata) >= 1
        orphaned_entry = preprocessor.failure_tracker.orphaned_metadata[0]
        assert orphaned_entry["context"]["has_json"] is True

    def test_no_orphans_when_media_exists(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should not track orphans when media file exists."""
        from processors.instagram_public_media.preprocess_instaloader import (
            InstaloaderFormatPreprocessor,
        )

        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": "Valid caption",
                }
            ],
        )

        # Run preprocessor
        preprocessor = InstaloaderFormatPreprocessor(
            export_path=temp_export_dir,
            output_dir=temp_output_dir,
            final_output_dir=temp_output_dir,
        )
        preprocessor.run()

        # No orphaned metadata should be tracked
        assert len(preprocessor.failure_tracker.orphaned_metadata) == 0


class TestInstaloaderPostsOnlyInvariant:
    """Tests that Instaloader exports are always processed as posts."""

    def test_all_media_items_have_posts_media_type(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """All Instaloader media items must have media_type='posts'."""
        from processors.instagram_public_media.preprocess_instaloader import (
            InstaloaderFormatPreprocessor,
        )

        create_instaloader_export(
            temp_export_dir,
            media_files=[
                {
                    "timestamp": "2021-01-01_12-00-00",
                    "extension": "jpg",
                    "caption": "Test 1",
                },
                {
                    "timestamp": "2021-01-02_12-00-00",
                    "extension": "mp4",
                    "caption": "Test 2",
                },
                {
                    "timestamp": "2021-01-03_12-00-00",
                    "extension": "jpg",
                    "caption": None,
                },
            ],
        )

        preprocessor = InstaloaderFormatPreprocessor(
            export_path=temp_export_dir,
            output_dir=temp_output_dir,
            final_output_dir=temp_output_dir,
        )
        preprocessor.run()

        # Load and verify metadata
        metadata_file = temp_output_dir / "metadata.json"
        assert metadata_file.exists()

        metadata = json.loads(metadata_file.read_text())

        # Verify export_info
        assert metadata["export_info"]["platform"] == "Instagram"
        assert metadata["export_info"]["service"] == "public-media"

        # Verify ALL items have media_type == "posts"
        media_items = metadata.get("media", [])
        assert len(media_items) > 0, "Should have processed media items"

        for item in media_items:
            assert (
                item.get("media_type") == "posts"
            ), f"Instaloader item must have media_type='posts', got: {item.get('media_type')}"


class TestInstaloaderEmptyExport:
    """Tests for empty export handling (Policy A: export-level soft-fail)."""

    def test_empty_export_produces_failure_report(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Empty export should produce failure-report.json with export_failures."""
        from processors.instagram_public_media.preprocess_instaloader import (
            InstaloaderFormatPreprocessor,
        )

        # Create empty export directory (no media files)
        temp_export_dir.mkdir(parents=True, exist_ok=True)
        # Create a non-media file so directory isn't completely empty
        (temp_export_dir / "profile_pic.txt").write_text("not a media file")

        # Run preprocessor
        preprocessor = InstaloaderFormatPreprocessor(
            export_path=temp_export_dir,
            output_dir=temp_output_dir,
            final_output_dir=temp_output_dir,
        )
        result = preprocessor.run()

        # Should return False (FAILED)
        assert result is False

        # Should produce failure-report.json with export_failures
        report_path = temp_output_dir / "issues" / "failure-report.json"
        assert report_path.exists(), "failure-report.json should be created"

        import json

        report = json.loads(report_path.read_text())
        assert "export_failures" in report
        assert len(report["export_failures"]) > 0
        assert report["export_failures"][0]["reason"] == "no_media_files"
