#!/usr/bin/env python3
"""Tests for common/exiftool_batch.py metadata write request functionality.

Focuses on:
- Description always being written (overwriting existing)
- Global dedup description override
- Datetime and GPS tag handling
"""

from pathlib import Path
from unittest.mock import MagicMock, patch


from common.media.exiftool import (
    BatchReadResult,
    BatchWriteOutcome,
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    ExifWriteFailure,
    GPSData,
    MetadataWriteRequest,
    XMP_PRESERVED_FILENAME_EXTENSIONS,
    batch_read_existing_metadata,
    batch_write_metadata_requests,
)


class TestDescriptionWriting:
    """Test description writing behavior in batch_write_metadata_requests."""

    def test_description_always_written_for_images(self, tmp_path: Path) -> None:
        """Description should always be written for images."""
        test_file = tmp_path / "test_image.jpg"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=None,
            description=DescriptionWriteSpec(
                default_description="Test Description",
            ),
            processor_name="test",
        )

        # Simulate no existing metadata
        existing_metadata_map = {str(test_file): {}}

        # Capture the argfile content
        argfile_content = []

        def capture_argfile(*args, **kwargs):
            # Read the argfile that was just written
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        # Verify ImageDescription was included in the argfile
        assert len(argfile_content) == 1
        assert "-ImageDescription=Test Description" in argfile_content[0]
        assert "-IPTC:Caption-Abstract=Test Description" in argfile_content[0]

    def test_description_overwrites_existing(self, tmp_path: Path) -> None:
        """Description should be written even if ImageDescription exists."""
        test_file = tmp_path / "test_image.jpg"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=None,
            description=DescriptionWriteSpec(
                default_description="New Description",
            ),
            processor_name="test",
        )

        # Simulate existing metadata - ImageDescription already exists
        existing_metadata_map = {
            str(test_file): {
                "ImageDescription": True,  # Already has description
                "Comment": True,
                "Description": True,
            }
        }

        # Capture the argfile content
        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        # Verify ImageDescription was included despite existing
        assert len(argfile_content) == 1
        assert "-ImageDescription=New Description" in argfile_content[0]

    def test_video_uses_comment_description_tags(self, tmp_path: Path) -> None:
        """Video files should use Comment and Description tags, not ImageDescription."""
        test_file = tmp_path / "test_video.mp4"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=None,
            description=DescriptionWriteSpec(
                default_description="Video Description",
            ),
            processor_name="test",
        )

        existing_metadata_map = {str(test_file): {}}

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="video"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        assert len(argfile_content) == 1
        assert "-Comment=Video Description" in argfile_content[0]
        assert "-Description=Video Description" in argfile_content[0]
        assert "-ImageDescription" not in argfile_content[0]


class TestDedupOverride:
    """Test global dedup description override behavior."""

    def test_dedup_override_replaces_default_description(self, tmp_path: Path) -> None:
        """When dedup manifest provides a merged description, it should override default."""
        test_file = tmp_path / "test_image.jpg"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=None,
            description=DescriptionWriteSpec(
                default_description="Processor Default Description",
            ),
            processor_name="test",
        )

        existing_metadata_map = {str(test_file): {}}

        # Mock dedup manifest to return a merged description
        merged_description = "Merged: Source1 + Source2 provenance"

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"), patch(
            "common.media.exiftool._get_description_for_file",
            return_value=merged_description,
        ):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest={"some": "manifest"},
            )

        assert len(argfile_content) == 1
        # Should use the merged description, not the default
        assert f"-ImageDescription={merged_description}" in argfile_content[0]
        assert "Processor Default Description" not in argfile_content[0]

    def test_no_dedup_uses_default_description(self, tmp_path: Path) -> None:
        """When no dedup manifest, should use the processor's default description."""
        test_file = tmp_path / "test_image.jpg"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=None,
            description=DescriptionWriteSpec(
                default_description="Processor Default Description",
            ),
            processor_name="test",
        )

        existing_metadata_map = {str(test_file): {}}

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,  # No dedup
            )

        assert len(argfile_content) == 1
        assert "-ImageDescription=Processor Default Description" in argfile_content[0]


class TestDatetimeAndGPS:
    """Test datetime and GPS tag writing."""

    def test_datetime_tags_written_when_missing(self, tmp_path: Path) -> None:
        """Datetime tags should be written when not present in existing metadata."""
        test_file = tmp_path / "test_image.jpg"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[
                ("DateTimeOriginal", "2024:01:15 14:30:00"),
                ("CreateDate", "2024:01:15 14:30:00"),
                ("ModifyDate", "2024:01:15 14:30:00"),
            ],
            gps=None,
            description=None,
            processor_name="test",
        )

        existing_metadata_map = {
            str(test_file): {
                "DateTimeOriginal": False,
                "CreateDate": False,
                "ModifyDate": False,
            }
        }

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        assert len(argfile_content) == 1
        assert "-DateTimeOriginal=2024:01:15 14:30:00" in argfile_content[0]
        assert "-CreateDate=2024:01:15 14:30:00" in argfile_content[0]
        assert "-ModifyDate=2024:01:15 14:30:00" in argfile_content[0]

    def test_datetime_tags_skipped_when_existing(self, tmp_path: Path) -> None:
        """Datetime tags should NOT be written when already present."""
        test_file = tmp_path / "test_image.jpg"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[
                ("DateTimeOriginal", "2024:01:15 14:30:00"),
                ("CreateDate", "2024:01:15 14:30:00"),
                ("ModifyDate", "2024:01:15 14:30:00"),
            ],
            gps=None,
            description=None,
            processor_name="test",
        )

        # All datetime fields already exist
        existing_metadata_map = {
            str(test_file): {
                "DateTimeOriginal": True,
                "CreateDate": True,
                "ModifyDate": True,
            }
        }

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        assert len(argfile_content) == 1
        # None of the datetime tags should be present
        assert "-DateTimeOriginal" not in argfile_content[0]
        assert "-CreateDate" not in argfile_content[0]
        assert "-ModifyDate" not in argfile_content[0]

    def test_gps_data_written_when_missing(self, tmp_path: Path) -> None:
        """GPS data should be written when not present."""
        test_file = tmp_path / "test_image.jpg"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=GPSData(latitude=37.7749, longitude=-122.4194),
            description=None,
            processor_name="test",
        )

        existing_metadata_map = {str(test_file): {"GPSLatitude": False}}

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"), patch(
            "common.media.exiftool.get_gps_format", return_value="signed"
        ):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        assert len(argfile_content) == 1
        assert "-GPSLatitude=37.7749" in argfile_content[0]
        assert "-GPSLongitude=-122.4194" in argfile_content[0]


class TestMetadataWriteRequestModel:
    """Test the MetadataWriteRequest data model."""

    def test_default_values(self) -> None:
        """MetadataWriteRequest should have sensible defaults."""
        request = MetadataWriteRequest(file_path="/some/path.jpg")

        assert request.file_path == "/some/path.jpg"
        assert request.arg_preamble == []
        assert request.datetime_tags == []
        assert request.gps is None
        assert request.description is None
        assert request.processor_name == "unknown"
        assert request.original_filename is None

    def test_gps_data_with_altitude(self) -> None:
        """GPSData should support optional altitude."""
        gps = GPSData(latitude=37.7749, longitude=-122.4194, altitude=100.5)

        assert gps.latitude == 37.7749
        assert gps.longitude == -122.4194
        assert gps.altitude == 100.5

    def test_description_write_spec_defaults(self) -> None:
        """DescriptionWriteSpec should have proper defaults."""
        spec = DescriptionWriteSpec(default_description="Test")

        assert spec.default_description == "Test"
        assert spec.image_tags == ["ImageDescription", "IPTC:Caption-Abstract"]
        assert spec.video_tags == ["Comment", "Description"]


class TestOriginalFilename:
    """Test XMP:PreservedFileName original filename embedding."""

    def test_original_filename_written_for_supported_formats(
        self, tmp_path: Path
    ) -> None:
        """XMP:PreservedFileName should be written for supported formats."""
        test_file = tmp_path / "test_image.jpg"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=None,
            description=None,
            processor_name="test",
            original_filename="IMG_1234.jpg",
        )

        existing_metadata_map = {str(test_file): {}}

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        assert len(argfile_content) == 1
        assert "-XMP:PreservedFileName=IMG_1234.jpg" in argfile_content[0]

    def test_original_filename_skipped_for_unsupported_formats(
        self, tmp_path: Path
    ) -> None:
        """XMP:PreservedFileName should NOT be written for BMP and WebM."""
        test_file = tmp_path / "test_image.bmp"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=None,
            description=None,
            processor_name="test",
            original_filename="original.bmp",
        )

        existing_metadata_map = {str(test_file): {}}

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        assert len(argfile_content) == 1
        assert "-XMP:PreservedFileName" not in argfile_content[0]

    def test_original_filename_not_written_when_none(self, tmp_path: Path) -> None:
        """XMP:PreservedFileName should not be written when original_filename is None."""
        test_file = tmp_path / "test_image.jpg"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=None,
            description=None,
            processor_name="test",
            original_filename=None,  # Explicitly None
        )

        existing_metadata_map = {str(test_file): {}}

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="image"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        assert len(argfile_content) == 1
        assert "-XMP:PreservedFileName" not in argfile_content[0]

    def test_xmp_preserved_filename_extensions_constant(self) -> None:
        """XMP_PRESERVED_FILENAME_EXTENSIONS should contain expected formats."""
        # Common image formats that should be supported
        assert ".jpg" in XMP_PRESERVED_FILENAME_EXTENSIONS
        assert ".jpeg" in XMP_PRESERVED_FILENAME_EXTENSIONS
        assert ".png" in XMP_PRESERVED_FILENAME_EXTENSIONS
        assert ".heic" in XMP_PRESERVED_FILENAME_EXTENSIONS
        assert ".tiff" in XMP_PRESERVED_FILENAME_EXTENSIONS

        # Video formats that should be supported
        assert ".mp4" in XMP_PRESERVED_FILENAME_EXTENSIONS
        assert ".mov" in XMP_PRESERVED_FILENAME_EXTENSIONS
        assert ".avi" in XMP_PRESERVED_FILENAME_EXTENSIONS

        # Formats that should NOT be supported
        assert ".bmp" not in XMP_PRESERVED_FILENAME_EXTENSIONS
        assert ".webm" not in XMP_PRESERVED_FILENAME_EXTENSIONS
        assert ".mkv" not in XMP_PRESERVED_FILENAME_EXTENSIONS

    def test_original_filename_written_for_mp4_video(self, tmp_path: Path) -> None:
        """XMP:PreservedFileName should be written for MP4 video files."""
        test_file = tmp_path / "test_video.mp4"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[],
            gps=None,
            description=None,
            processor_name="test",
            original_filename="VID_20231215_143000.mp4",
        )

        existing_metadata_map = {str(test_file): {}}

        argfile_content = []

        def capture_argfile(*args, **kwargs):
            argfile_path = args[0][2] if len(args) > 0 and len(args[0]) > 2 else None
            if argfile_path and Path(argfile_path).exists():
                argfile_content.append(Path(argfile_path).read_text())
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=capture_argfile
        ), patch("common.media.exiftool.get_media_type", return_value="video"):
            batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        assert len(argfile_content) == 1
        assert "-XMP:PreservedFileName=VID_20231215_143000.mp4" in argfile_content[0]


class TestBatchWriteOutcomes:
    """Tests for BatchWriteOutcome and failure classification."""

    def test_success_outcome_records_successes(self, tmp_path: Path) -> None:
        """Successful exiftool run should populate successes."""
        test_file = tmp_path / "ok.jpg"
        test_file.touch()
        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            processor_name="test",
        )

        with patch(
            "common.media.exiftool.subprocess.run",
            return_value=MagicMock(returncode=0, stdout="", stderr=""),
        ):
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map={str(test_file): {}},
                dedup_manifest=None,
            )
        assert isinstance(outcome, BatchWriteOutcome)
        assert str(test_file) in outcome.successes
        assert not outcome.failures

    def test_missing_file_emits_failure(self, tmp_path: Path) -> None:
        """Missing files should be reported with reason_code=file_missing."""
        missing_file = tmp_path / "missing.jpg"
        request = MetadataWriteRequest(
            file_path=str(missing_file),
            arg_preamble=DEFAULT_PREAMBLE,
            processor_name="test",
        )

        outcome = batch_write_metadata_requests(
            [request],
            existing_metadata_map={},
            dedup_manifest=None,
        )
        assert len(outcome.failures) == 1
        failure = outcome.failures[0]
        assert isinstance(failure, ExifWriteFailure)
        assert failure.reason_code == "file_missing"
        assert not outcome.successes

    def test_format_error_reason_code(self, tmp_path: Path) -> None:
        """Exiftool format errors should classify as format_error."""
        test_file = tmp_path / "bad.mov"
        test_file.touch()
        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            processor_name="test",
        )

        with patch(
            "common.media.exiftool.subprocess.run",
            return_value=MagicMock(
                returncode=1,
                stdout="",
                stderr="File format error: cannot parse",
            ),
        ):
            outcome = batch_write_metadata_requests(
                [request], existing_metadata_map={str(test_file): {}}, dedup_manifest=None
            )

        assert outcome.failures
        assert outcome.failures[0].reason_code == "format_error"
        assert str(test_file) not in outcome.successes


class TestBatchReadResult:
    """Tests for batch_read_existing_metadata return structure."""

    def test_missing_files_listed(self, tmp_path: Path) -> None:
        """Missing files should be returned in missing_files."""
        existing = tmp_path / "exists.jpg"
        existing.touch()
        missing = tmp_path / "nope.jpg"

        result = batch_read_existing_metadata([str(existing), str(missing)])
        assert isinstance(result, BatchReadResult)
        assert str(missing) in result.missing_files
        assert str(existing) in result.metadata_map
