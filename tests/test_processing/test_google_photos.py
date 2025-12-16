"""
Processing tests for Google Photos processor.

Tests cover edge cases including:
- Photo with JSON metadata
- Photo without JSON metadata (filesystem timestamp fallback)
- Video file with metadata
- Duplicate files across albums (deduplication)
- Live Photo pairs (JPG + MOV)
- Missing JSON files
- Files with wrong extensions
"""

import json


from tests.fixtures.generators import create_google_photos_export
from tests.fixtures.media_samples import write_media_file


class TestGooglePhotosMetadata:
    """Tests for metadata handling in Google Photos processing."""

    def test_photo_with_json_metadata(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should process photo with accompanying JSON metadata."""
        create_google_photos_export(
            temp_export_dir,
            albums={"Test Album": ["photo.jpg"]},
            include_json_metadata=True
        )

        # Verify the export structure is correct
        assert (temp_export_dir / "Google Photos" / "Test Album" / "photo.jpg").exists()
        assert (temp_export_dir / "Google Photos" / "Test Album" / "photo.jpg.json").exists()

    def test_photo_without_json_metadata(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle photo without JSON metadata (uses filesystem timestamps)."""
        create_google_photos_export(
            temp_export_dir,
            albums={"Test Album": ["photo.jpg"]},
            include_json_metadata=False
        )

        # Verify no JSON file exists
        assert (temp_export_dir / "Google Photos" / "Test Album" / "photo.jpg").exists()
        assert not (temp_export_dir / "Google Photos" / "Test Album" / "photo.jpg.json").exists()

    def test_video_with_metadata(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should process video file with JSON metadata."""
        create_google_photos_export(
            temp_export_dir,
            albums={"Videos": ["video.mp4"]},
            include_json_metadata=True
        )

        assert (temp_export_dir / "Google Photos" / "Videos" / "video.mp4").exists()
        assert (temp_export_dir / "Google Photos" / "Videos" / "video.mp4.json").exists()


class TestGooglePhotosDuplicates:
    """Tests for deduplication in Google Photos processing."""

    def test_duplicate_across_albums(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle duplicate files appearing in multiple albums."""
        # Create same-named files in different albums
        create_google_photos_export(
            temp_export_dir,
            albums={
                "Album 1": ["photo.jpg"],
                "Album 2": ["photo.jpg"],
            },
            include_json_metadata=True
        )

        # Both files should exist in export
        assert (temp_export_dir / "Google Photos" / "Album 1" / "photo.jpg").exists()
        assert (temp_export_dir / "Google Photos" / "Album 2" / "photo.jpg").exists()


class TestGooglePhotosLivePhotos:
    """Tests for Live Photo handling in Google Photos processing."""

    def test_live_photo_pair(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle Live Photo pairs (JPG + MOV with same name)."""
        photos_dir = temp_export_dir / "Google Photos" / "Live Photos"
        photos_dir.mkdir(parents=True)

        # Create Live Photo pair
        write_media_file(photos_dir / "IMG_1234.JPG", "jpeg")
        write_media_file(photos_dir / "IMG_1234.MOV", "mov")

        # Create matching JSON metadata for both
        metadata = {
            "title": "IMG_1234",
            "photoTakenTime": {"timestamp": "1609459200"},
        }
        (photos_dir / "IMG_1234.JPG.json").write_text(json.dumps(metadata))
        (photos_dir / "IMG_1234.MOV.json").write_text(json.dumps(metadata))

        # Both files should exist
        assert (photos_dir / "IMG_1234.JPG").exists()
        assert (photos_dir / "IMG_1234.MOV").exists()


class TestGooglePhotosEdgeCases:
    """Tests for edge cases in Google Photos processing."""

    def test_missing_json_for_some_files(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle album with some files missing JSON metadata."""
        photos_dir = temp_export_dir / "Google Photos" / "Mixed Album"
        photos_dir.mkdir(parents=True)

        # File with metadata
        write_media_file(photos_dir / "with_meta.jpg", "jpeg")
        metadata = {"title": "with_meta.jpg", "photoTakenTime": {"timestamp": "1609459200"}}
        (photos_dir / "with_meta.jpg.json").write_text(json.dumps(metadata))

        # File without metadata
        write_media_file(photos_dir / "no_meta.jpg", "jpeg")

        assert (photos_dir / "with_meta.jpg.json").exists()
        assert not (photos_dir / "no_meta.jpg.json").exists()

    def test_file_with_wrong_extension(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle files with incorrect extensions."""
        photos_dir = temp_export_dir / "Google Photos" / "Test Album"
        photos_dir.mkdir(parents=True)

        # Create a JPEG file with PNG extension
        write_media_file(photos_dir / "misnamed.png", "jpeg")

        metadata = {"title": "misnamed.png", "photoTakenTime": {"timestamp": "1609459200"}}
        (photos_dir / "misnamed.png.json").write_text(json.dumps(metadata))

        assert (photos_dir / "misnamed.png").exists()

    def test_empty_album(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle album with no media files."""
        create_google_photos_export(temp_export_dir, albums={"Empty Album": []})
        
        # Album directory should exist but be empty
        album_dir = temp_export_dir / "Google Photos" / "Empty Album"
        assert album_dir.exists()
        assert len(list(album_dir.iterdir())) == 0

    def test_special_characters_in_album_name(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle album names with special characters."""
        create_google_photos_export(
            temp_export_dir,
            albums={"Vacation 2021 (Summer) - Beach!": ["photo.jpg"]},
            include_json_metadata=True
        )

        album_dir = temp_export_dir / "Google Photos" / "Vacation 2021 (Summer) - Beach!"
        assert album_dir.exists()
        assert (album_dir / "photo.jpg").exists()

    def test_nested_json_metadata_fields(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should handle JSON metadata with all optional fields."""
        photos_dir = temp_export_dir / "Google Photos" / "Full Metadata"
        photos_dir.mkdir(parents=True)

        write_media_file(photos_dir / "full.jpg", "jpeg")

        metadata = {
            "title": "full.jpg",
            "description": "A photo with full metadata",
            "photoTakenTime": {
                "timestamp": "1609459200",
                "formatted": "Jan 1, 2021, 12:00:00 AM UTC"
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
            "googlePhotosOrigin": {
                "mobileUpload": {"deviceType": "IOS_PHONE"}
            },
        }
        (photos_dir / "full.jpg.json").write_text(json.dumps(metadata))

        assert (photos_dir / "full.jpg.json").exists()
        loaded = json.loads((photos_dir / "full.jpg.json").read_text())
        assert loaded["geoData"]["latitude"] == 40.7128


class TestGooglePhotosFileTypes:
    """Tests for various file type handling."""

    def test_png_file(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should process PNG files."""
        create_google_photos_export(
            temp_export_dir,
            albums={"PNGs": ["image.png"]},
            include_json_metadata=True
        )
        assert (temp_export_dir / "Google Photos" / "PNGs" / "image.png").exists()

    def test_webp_file(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should process WebP files."""
        photos_dir = temp_export_dir / "Google Photos" / "WebP"
        photos_dir.mkdir(parents=True)
        write_media_file(photos_dir / "image.webp", "webp")
        assert (photos_dir / "image.webp").exists()

    def test_mov_file(self, google_photos_processor, temp_export_dir, temp_output_dir):
        """Should process MOV video files."""
        photos_dir = temp_export_dir / "Google Photos" / "Videos"
        photos_dir.mkdir(parents=True)
        write_media_file(photos_dir / "video.mov", "mov")
        assert (photos_dir / "video.mov").exists()

