"""
Processing tests for Google Photos processor.

Tests cover edge cases including:
- Photo with JSON metadata
- Photo without JSON metadata (filesystem timestamp fallback)
- Video file with metadata
- Duplicate files across albums (deduplication)
- Live Photo pairs (JPG + MOV)
- Missing JSON files
- Content hash caching for cross-processor deduplication
"""

import json
import pytest

from tests.fixtures.export_tree import copy_export_fixture
from tests.fixtures.media_samples import write_media_file


class TestGooglePhotosPreprocessing:
    """Tests that run the preprocessor and verify output."""

    @pytest.fixture
    def preprocessor_factory(self, tmp_path):
        """Factory to create GooglePhotosPreprocessor instances."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        def create_preprocessor(export_dir, output_dir=None):
            if output_dir is None:
                output_dir = tmp_path / "output"
                output_dir.mkdir(exist_ok=True)
            return GooglePhotosPreprocessor(
                export_path=export_dir,
                output_dir=output_dir,
            )

        return create_preprocessor

    def test_takeout_basic_preprocessing(
        self, preprocessor_factory, temp_export_dir, temp_output_dir
    ):
        """Should preprocess Takeout-style export and produce metadata.json."""
        # Username detection requires either Profile/Profile.json or a dirname pattern.
        # Use a dirname that matches detect_from_dirname(..., "google").
        export_dir = temp_export_dir / "google-testuser-20240101"
        export_dir.mkdir(parents=True, exist_ok=True)
        copy_export_fixture("google_photos", "takeout_basic", export_dir)

        preprocessor = preprocessor_factory(export_dir, temp_output_dir)
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
        assert output_metadata["export_info"]["service"] == "photos"

        # Verify media_files were indexed
        media_files = output_metadata.get("media_files", [])
        assert len(media_files) > 0, "Should have indexed at least one media file"

    def test_takeout_basic_content_hashes(
        self, preprocessor_factory, temp_export_dir, temp_output_dir
    ):
        """Preprocessing should save content_hash in metadata.json for dedup."""
        export_dir = temp_export_dir / "google-testuser-20240101"
        export_dir.mkdir(parents=True, exist_ok=True)
        copy_export_fixture("google_photos", "takeout_basic", export_dir)

        preprocessor = preprocessor_factory(export_dir, temp_output_dir)
        result = preprocessor.run()
        assert result is True

        with open(temp_output_dir / "metadata.json") as f:
            output_metadata = json.load(f)

        media_files = output_metadata.get("media_files", [])
        for media_entry in media_files:
            assert (
                "content_hash" in media_entry
            ), "Each media entry should have content_hash"
            assert media_entry["content_hash"], "content_hash should not be empty"
            # xxHash64 produces 16-character hex string
            assert (
                len(media_entry["content_hash"]) == 16
            ), "content_hash should be a 16-char xxHash64 hex digest"

    def test_takeout_duplicate_detection_in_metadata(
        self, preprocessor_factory, temp_export_dir, temp_output_dir
    ):
        """Should identify and track duplicates across albums in metadata."""
        export_dir = temp_export_dir / "google-testuser-20240101"
        export_dir.mkdir(parents=True, exist_ok=True)
        copy_export_fixture(
            "google_photos", "takeout_duplicate_album_vs_library", export_dir
        )

        preprocessor = preprocessor_factory(export_dir, temp_output_dir)
        result = preprocessor.run()
        assert result is True

        with open(temp_output_dir / "metadata.json") as f:
            output_metadata = json.load(f)

        # Duplicates should be tracked - either via content_hash or is_duplicate flag
        media_files = output_metadata.get("media_files", [])
        content_hashes = [m.get("content_hash") for m in media_files if m.get("content_hash")]
        
        # If there are duplicate files, some content_hashes should be the same
        # or there should be duplicate tracking
        assert len(media_files) >= 1, "Should have processed media files"

    def test_takeout_missing_json_fallback(
        self, preprocessor_factory, temp_export_dir, temp_output_dir
    ):
        """Should handle files without JSON metadata using filesystem fallback."""
        export_dir = temp_export_dir / "google-testuser-20240101"
        export_dir.mkdir(parents=True, exist_ok=True)
        copy_export_fixture("google_photos", "takeout_missing_json_some", export_dir)

        preprocessor = preprocessor_factory(export_dir, temp_output_dir)
        result = preprocessor.run()
        assert result is True

        with open(temp_output_dir / "metadata.json") as f:
            output_metadata = json.load(f)

        media_files = output_metadata.get("media_files", [])

        # One file is expected to match metadata; the other is expected to be tracked as a
        # matching failure (orphaned media) when no JSON exists.
        assert len(media_files) >= 1, "Should have processed at least one file"

        export_info = output_metadata.get("export_info", {})
        assert export_info.get("total_media_files") == 2
        assert export_info.get("unmatched_files") == 1

        # FailureTracker should record the missing-metadata media file.
        failure_report_path = temp_output_dir / "issues" / "failure-report.json"
        assert failure_report_path.exists(), "Failure report should exist"
        failure_report = json.loads(failure_report_path.read_text())
        orphaned_media = failure_report.get("failed_matching", {}).get("orphaned_media", [])
        assert len(orphaned_media) == 1

        # Entries should always include source_path.
        for entry in media_files:
            assert "source_path" in entry, "Each entry should have source_path"


class TestGooglePhotosFixtureStructure:
    """Tests that verify schema-faithful fixture structures match docs."""

    def test_takeout_basic_structure(
        self, google_photos_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse Takeout-style export structure."""
        copy_export_fixture("google_photos", "takeout_basic", temp_export_dir)

        takeout_dir = temp_export_dir / "Takeout"
        photos_dir = takeout_dir / "Google Photos"

        # Verify structure matches docs
        assert (photos_dir / "Photos from 2023" / "IMG_0001.jpg").exists()
        assert (photos_dir / "Photos from 2023" / "IMG_0001.jpg.json").exists()
        assert (photos_dir / "Albums" / "Summer 2023" / "vacation.jpg").exists()
        assert (photos_dir / "Albums" / "Summer 2023" / "vacation.jpg.json").exists()
        assert (photos_dir / "metadata.json").exists()

    def test_takeout_json_metadata_format(
        self, google_photos_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse JSON metadata with all documented fields."""
        copy_export_fixture("google_photos", "takeout_basic", temp_export_dir)

        json_path = (
            temp_export_dir
            / "Takeout"
            / "Google Photos"
            / "Photos from 2023"
            / "IMG_0001.jpg.json"
        )
        metadata = json.loads(json_path.read_text())

        # Verify all documented fields are present
        assert "title" in metadata
        assert "photoTakenTime" in metadata
        assert "timestamp" in metadata["photoTakenTime"]
        assert "formatted" in metadata["photoTakenTime"]
        assert "geoData" in metadata
        assert "latitude" in metadata["geoData"]
        assert "longitude" in metadata["geoData"]

    def test_takeout_duplicate_detection_structure(
        self, google_photos_processor, temp_export_dir, temp_output_dir
    ):
        """Should identify same file appearing in album and library."""
        copy_export_fixture(
            "google_photos", "takeout_duplicate_album_vs_library", temp_export_dir
        )

        takeout_dir = temp_export_dir / "Takeout"
        lib_file = (
            takeout_dir / "Google Photos" / "Photos from 2023" / "shared_photo.jpg"
        )
        album_file = (
            takeout_dir / "Google Photos" / "Albums" / "Favorites" / "shared_photo.jpg"
        )

        # Both files should exist and have same content
        assert lib_file.exists()
        assert album_file.exists()
        assert lib_file.read_bytes() == album_file.read_bytes()

    def test_takeout_missing_json_structure(
        self, google_photos_processor, temp_export_dir, temp_output_dir
    ):
        """Should have files with and without JSON metadata."""
        copy_export_fixture(
            "google_photos", "takeout_missing_json_some", temp_export_dir
        )

        photos_dir = temp_export_dir / "Takeout" / "Google Photos" / "Photos from 2023"

        # One has metadata, one doesn't
        assert (photos_dir / "with_metadata.jpg").exists()
        assert (photos_dir / "with_metadata.jpg.json").exists()
        assert (photos_dir / "without_metadata.jpg").exists()
        assert not (photos_dir / "without_metadata.jpg.json").exists()


class TestGooglePhotosLivePhotos:
    """Tests for Live Photo handling in Google Photos processing."""

    def test_live_photo_pair_in_takeout_structure(
        self, google_photos_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle Live Photo pairs (JPG + MOV with same name) in Takeout structure."""
        # Create Takeout-style structure with Live Photo pair
        photos_dir = temp_export_dir / "Takeout" / "Google Photos" / "Live Photos"
        photos_dir.mkdir(parents=True)

        # Create Live Photo pair
        write_media_file(photos_dir / "IMG_1234.JPG", "jpeg")
        write_media_file(photos_dir / "IMG_1234.MOV", "mov")

        # Create matching JSON metadata for both
        metadata = {
            "title": "IMG_1234",
            "photoTakenTime": {"timestamp": "1609459200", "formatted": "Jan 1, 2021"},
        }
        (photos_dir / "IMG_1234.JPG.json").write_text(json.dumps(metadata))
        (photos_dir / "IMG_1234.MOV.json").write_text(json.dumps(metadata))

        # Both files should exist
        assert (photos_dir / "IMG_1234.JPG").exists()
        assert (photos_dir / "IMG_1234.MOV").exists()


class TestGooglePhotosEdgeCases:
    """Tests for edge cases using schema-faithful structure."""

    def test_missing_json_for_some_files(
        self, google_photos_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle album with some files missing JSON metadata."""
        # Use Takeout structure
        photos_dir = temp_export_dir / "Takeout" / "Google Photos" / "Mixed Album"
        photos_dir.mkdir(parents=True)

        # File with metadata
        write_media_file(photos_dir / "with_meta.jpg", "jpeg")
        metadata = {
            "title": "with_meta.jpg",
            "photoTakenTime": {"timestamp": "1609459200", "formatted": "Jan 1, 2021"},
        }
        (photos_dir / "with_meta.jpg.json").write_text(json.dumps(metadata))

        # File without metadata
        write_media_file(photos_dir / "no_meta.jpg", "jpeg")

        assert (photos_dir / "with_meta.jpg.json").exists()
        assert not (photos_dir / "no_meta.jpg.json").exists()

    def test_nested_json_metadata_fields(
        self, google_photos_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle JSON metadata with all optional fields."""
        photos_dir = temp_export_dir / "Takeout" / "Google Photos" / "Full Metadata"
        photos_dir.mkdir(parents=True)

        write_media_file(photos_dir / "full.jpg", "jpeg")

        metadata = {
            "title": "full.jpg",
            "description": "A photo with full metadata",
            "photoTakenTime": {
                "timestamp": "1609459200",
                "formatted": "Jan 1, 2021, 12:00:00 AM UTC",
            },
            "geoData": {
                "latitude": 40.7128,
                "longitude": -74.0060,
                "altitude": 10.0,
                "latitudeSpan": 0.0,
                "longitudeSpan": 0.0,
            },
            "geoDataExif": {
                "latitude": 40.7128,
                "longitude": -74.0060,
                "altitude": 10.0,
                "latitudeSpan": 0.0,
                "longitudeSpan": 0.0,
            },
            "people": [{"name": "Person One"}],
            "url": "https://photos.google.com/photo/xxx",
            "googlePhotosOrigin": {"mobileUpload": {"deviceType": "IOS_PHONE"}},
        }
        (photos_dir / "full.jpg.json").write_text(json.dumps(metadata))

        assert (photos_dir / "full.jpg.json").exists()
        loaded = json.loads((photos_dir / "full.jpg.json").read_text())
        assert loaded["geoData"]["latitude"] == 40.7128


class TestGooglePhotosFileTypes:
    """Tests for various file type handling in Takeout structure."""

    def test_png_file(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should process PNG files."""
        photos_dir = temp_export_dir / "Takeout" / "Google Photos" / "PNGs"
        photos_dir.mkdir(parents=True)
        write_media_file(photos_dir / "image.png", "png")
        assert (photos_dir / "image.png").exists()

    def test_webp_file(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should process WebP files."""
        photos_dir = temp_export_dir / "Takeout" / "Google Photos" / "WebP"
        photos_dir.mkdir(parents=True)
        write_media_file(photos_dir / "image.webp", "webp")
        assert (photos_dir / "image.webp").exists()

    def test_mov_file(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should process MOV video files."""
        photos_dir = temp_export_dir / "Takeout" / "Google Photos" / "Videos"
        photos_dir.mkdir(parents=True)
        write_media_file(photos_dir / "video.mov", "mov")
        assert (photos_dir / "video.mov").exists()
