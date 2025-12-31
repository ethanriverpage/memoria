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
    _parse_exiftool_failed_files,
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


class TestUnsupportedFormatsSkipped:
    """Test that ExifTool-unsupported write formats are skipped (not failed)."""

    def test_mp3_is_skipped_without_invoking_exiftool(self, tmp_path: Path) -> None:
        """MP3 files should be skipped since ExifTool cannot write metadata to them."""
        test_file = tmp_path / "audio.mp3"
        test_file.touch()

        request = MetadataWriteRequest(
            file_path=str(test_file),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[("DateTimeOriginal", "2024:01:15 14:30:00")],
            gps=None,
            description=DescriptionWriteSpec(default_description="Audio description"),
            processor_name="test",
        )
        existing_metadata_map = {str(test_file): {}}

        with patch("common.media.exiftool.subprocess.run") as mock_run:
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        mock_run.assert_not_called()
        assert str(test_file) in outcome.skipped
        assert str(test_file) not in outcome.successes
        assert outcome.failures == []


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
        """BMP/WebM should skip metadata writing entirely (ExifTool limitation)."""
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
        with patch("common.media.exiftool.subprocess.run") as mock_run, patch(
            "common.media.exiftool.get_media_type", return_value="image"
        ):
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map,
                dedup_manifest=None,
            )

        mock_run.assert_not_called()
        assert str(test_file) in outcome.skipped

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
                stderr=f"Error: Not a valid MOV (looks more like a TXT) - {test_file}",
            ),
        ):
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map={str(test_file): {}},
                dedup_manifest=None,
            )

        assert outcome.failures
        assert outcome.failures[0].reason_code == "format_error"
        assert str(test_file) not in outcome.successes

    def test_mixed_success_failure_only_marks_failed_files(
        self, tmp_path: Path
    ) -> None:
        """When some files fail and others succeed, only failed files are marked.

        This is the fix for the bug where a non-zero exiftool return code
        caused ALL files in a batch to be marked as failed.
        """
        good1 = tmp_path / "good1.jpg"
        good2 = tmp_path / "good2.jpg"
        bad = tmp_path / "bad.jpg"
        good1.touch()
        good2.touch()
        bad.touch()

        requests = [
            MetadataWriteRequest(
                file_path=str(good1),
                arg_preamble=DEFAULT_PREAMBLE,
                processor_name="test",
            ),
            MetadataWriteRequest(
                file_path=str(bad), arg_preamble=DEFAULT_PREAMBLE, processor_name="test"
            ),
            MetadataWriteRequest(
                file_path=str(good2),
                arg_preamble=DEFAULT_PREAMBLE,
                processor_name="test",
            ),
        ]

        # Simulate exiftool output where only bad.jpg fails
        stderr = f"Error: Not a valid JPG (looks more like a TXT) - {bad}"
        with patch(
            "common.media.exiftool.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr=stderr),
        ):
            outcome = batch_write_metadata_requests(
                requests,
                existing_metadata_map={
                    str(good1): {},
                    str(bad): {},
                    str(good2): {},
                },
                dedup_manifest=None,
            )

        # Only bad.jpg should be marked as failed
        assert len(outcome.failures) == 1
        assert outcome.failures[0].file_path == str(bad)
        assert outcome.failures[0].reason_code == "format_error"

        # good1.jpg and good2.jpg should be in successes
        assert str(good1) in outcome.successes
        assert str(good2) in outcome.successes
        assert str(bad) not in outcome.successes

    def test_multiple_file_errors_in_batch(self, tmp_path: Path) -> None:
        """Multiple files failing should each be tracked individually."""
        good = tmp_path / "good.jpg"
        bad1 = tmp_path / "bad1.jpg"
        bad2 = tmp_path / "bad2.jpg"
        good.touch()
        bad1.touch()
        bad2.touch()

        requests = [
            MetadataWriteRequest(
                file_path=str(good),
                arg_preamble=DEFAULT_PREAMBLE,
                processor_name="test",
            ),
            MetadataWriteRequest(
                file_path=str(bad1),
                arg_preamble=DEFAULT_PREAMBLE,
                processor_name="test",
            ),
            MetadataWriteRequest(
                file_path=str(bad2),
                arg_preamble=DEFAULT_PREAMBLE,
                processor_name="test",
            ),
        ]

        # Simulate exiftool output where both bad files fail
        stderr = (
            f"Error: Not a valid JPG (looks more like a TXT) - {bad1}\n"
            f"Error: File not found - {bad2}"
        )
        with patch(
            "common.media.exiftool.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr=stderr),
        ):
            outcome = batch_write_metadata_requests(
                requests,
                existing_metadata_map={
                    str(good): {},
                    str(bad1): {},
                    str(bad2): {},
                },
                dedup_manifest=None,
            )

        # Both bad files should be marked as failed with correct reason codes
        assert len(outcome.failures) == 2
        failure_map = {f.file_path: f for f in outcome.failures}

        assert str(bad1) in failure_map
        assert failure_map[str(bad1)].reason_code == "format_error"

        assert str(bad2) in failure_map
        assert failure_map[str(bad2)].reason_code == "file_missing"

        # Only good.jpg should be in successes
        assert str(good) in outcome.successes
        assert len(outcome.successes) == 1

    def test_warning_messages_also_tracked(self, tmp_path: Path) -> None:
        """Warning messages should NOT mark files as failed (avoid false positives)."""
        good = tmp_path / "good.jpg"
        warned = tmp_path / "warned.jpg"
        good.touch()
        warned.touch()

        requests = [
            MetadataWriteRequest(
                file_path=str(good),
                arg_preamble=DEFAULT_PREAMBLE,
                processor_name="test",
            ),
            MetadataWriteRequest(
                file_path=str(warned),
                arg_preamble=DEFAULT_PREAMBLE,
                processor_name="test",
            ),
        ]

        stderr = f"Warning: Bad EXIF data - {warned}"
        with patch(
            "common.media.exiftool.subprocess.run",
            return_value=MagicMock(returncode=1, stdout="", stderr=stderr),
        ):
            outcome = batch_write_metadata_requests(
                requests,
                existing_metadata_map={str(good): {}, str(warned): {}},
                dedup_manifest=None,
            )

        # No failures - warnings are ignored for per-file failure attribution
        assert outcome.failures == []
        # Both files should be considered successful
        assert str(good) in outcome.successes
        assert str(warned) in outcome.successes


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


class TestParseExiftoolFailedFiles:
    """Tests for _parse_exiftool_failed_files helper function."""

    def test_parse_error_with_file_path(self) -> None:
        """Parse standard error message with file path."""
        stderr = "Error: File not found - /path/to/missing.jpg"
        result = _parse_exiftool_failed_files(stderr)

        assert "/path/to/missing.jpg" in result
        assert result["/path/to/missing.jpg"] == "File not found"

    def test_parse_format_error(self) -> None:
        """Parse format error message."""
        stderr = "Error: Not a valid JPG (looks more like a TXT) - /tmp/bad.jpg"
        result = _parse_exiftool_failed_files(stderr)

        assert "/tmp/bad.jpg" in result
        assert "Not a valid JPG" in result["/tmp/bad.jpg"]

    def test_parse_warning_with_file_path(self) -> None:
        """Warnings are ignored (only Error: lines are treated as failures)."""
        stderr = "Warning: Bad EXIF data - /path/to/warned.jpg"
        result = _parse_exiftool_failed_files(stderr)
        assert result == {}

    def test_parse_multiple_errors(self) -> None:
        """Parse multiple error messages (warnings ignored)."""
        stderr = (
            "Error: File not found - /path/to/missing.jpg\n"
            "Error: Not a valid JPG - /path/to/corrupt.jpg\n"
            "Warning: Bad data - /path/to/warned.jpg"
        )
        result = _parse_exiftool_failed_files(stderr)

        assert len(result) == 2
        assert "/path/to/missing.jpg" in result
        assert "/path/to/corrupt.jpg" in result
        assert "/path/to/warned.jpg" not in result

    def test_parse_empty_stderr(self) -> None:
        """Empty stderr should return empty dict."""
        result = _parse_exiftool_failed_files("")
        assert result == {}

    def test_parse_no_matching_patterns(self) -> None:
        """Stderr without file paths should return empty dict."""
        stderr = "Warning: Tag 'BadTag' is not defined\nNothing to do."
        result = _parse_exiftool_failed_files(stderr)
        assert result == {}

    def test_multiple_errors_same_file_concatenated(self) -> None:
        """Multiple errors for same file should be concatenated."""
        stderr = (
            "Error: First error - /path/to/file.jpg\n"
            "Error: Second error - /path/to/file.jpg"
        )
        result = _parse_exiftool_failed_files(stderr)

        assert len(result) == 1
        assert "/path/to/file.jpg" in result
        assert "First error" in result["/path/to/file.jpg"]
        assert "Second error" in result["/path/to/file.jpg"]


class TestLargeVideoFfmpegFallback:
    """Tests for ffmpeg remux fallback when ExifTool fails on large videos."""

    def test_atom_too_large_triggers_ffmpeg_fallback_success(
        self, tmp_path: Path
    ) -> None:
        """ExifTool 'atom is too large' error should trigger ffmpeg fallback."""
        large_video = tmp_path / "large_video.mp4"
        large_video.touch()

        request = MetadataWriteRequest(
            file_path=str(large_video),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[("DateTimeOriginal", "2024:01:15 12:30:00")],
            description=DescriptionWriteSpec(default_description="Test video"),
            processor_name="test",
        )

        # First call is exiftool (fails), second is ffmpeg (succeeds)
        call_count = [0]

        def mock_subprocess_run(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0]
            if cmd[0] == "exiftool":
                # ExifTool fails with large file error
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr=f"Error: '0x6f091e88' atom is too large for rewriting (3541 MB) - {large_video}",
                )
            elif cmd[0] == "ffmpeg":
                # FFmpeg succeeds - create the temp output file with content
                # The temp file path is the last argument
                output_path = Path(cmd[-1])
                output_path.write_bytes(b"fake video content for test")
                return MagicMock(returncode=0, stdout="", stderr="")
            elif cmd[0] == "ffprobe":
                # Return matching duration for both original and remuxed file
                return MagicMock(returncode=0, stdout="10.5\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=mock_subprocess_run
        ):
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map={str(large_video): {}},
                dedup_manifest=None,
            )

        # File should be in successes, not failures
        assert str(large_video) in outcome.successes
        assert len(outcome.failures) == 0
        # Both exiftool and ffmpeg should have been called
        assert call_count[0] >= 2

    def test_sidx_box_error_triggers_ffmpeg_fallback_success(
        self, tmp_path: Path
    ) -> None:
        """ExifTool 'sidx box' error should trigger ffmpeg fallback."""
        video = tmp_path / "video_with_sidx.mp4"
        video.touch()

        request = MetadataWriteRequest(
            file_path=str(video),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[("DateTimeOriginal", "2024:01:15 12:30:00")],
            processor_name="test",
        )

        call_count = [0]

        def mock_subprocess_run(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0]
            if cmd[0] == "exiftool":
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr=f"Error: Can't yet handle sidx box - {video}",
                )
            elif cmd[0] == "ffmpeg":
                output_path = Path(cmd[-1])
                output_path.write_bytes(b"fake video content for test")
                return MagicMock(returncode=0, stdout="", stderr="")
            elif cmd[0] == "ffprobe":
                # Return matching duration for both original and remuxed file
                return MagicMock(returncode=0, stdout="10.5\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=mock_subprocess_run
        ):
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map={str(video): {}},
                dedup_manifest=None,
            )

        assert str(video) in outcome.successes
        assert len(outcome.failures) == 0
        assert call_count[0] >= 2

    def test_ffmpeg_fallback_failure_records_remux_error(
        self, tmp_path: Path
    ) -> None:
        """When ffmpeg remux also fails, record the remux failure."""
        video = tmp_path / "problem_video.mp4"
        video.touch()

        request = MetadataWriteRequest(
            file_path=str(video),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[("DateTimeOriginal", "2024:01:15 12:30:00")],
            processor_name="test",
        )

        def mock_subprocess_run(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "exiftool":
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr=f"Error: atom is too large for rewriting - {video}",
                )
            elif cmd[0] == "ffmpeg":
                # FFmpeg also fails
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr="Error: codec not supported for remux",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=mock_subprocess_run
        ):
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map={str(video): {}},
                dedup_manifest=None,
            )

        # File should be in failures with remux failure reason
        assert str(video) not in outcome.successes
        assert len(outcome.failures) == 1
        assert "video_metadata_remux_failed" in outcome.failures[0].reason_code

    def test_non_video_file_no_ffmpeg_fallback(self, tmp_path: Path) -> None:
        """Large file errors on non-video files should not trigger ffmpeg fallback."""
        image = tmp_path / "big_image.jpg"
        image.touch()

        request = MetadataWriteRequest(
            file_path=str(image),
            arg_preamble=DEFAULT_PREAMBLE,
            processor_name="test",
        )

        call_count = [0]

        def mock_subprocess_run(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0]
            if cmd[0] == "exiftool":
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr=f"Error: atom is too large for rewriting - {image}",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=mock_subprocess_run
        ):
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map={str(image): {}},
                dedup_manifest=None,
            )

        # Should fail without ffmpeg fallback (jpg not eligible)
        assert str(image) not in outcome.successes
        assert len(outcome.failures) == 1
        # Only exiftool should have been called
        assert call_count[0] == 1

    def test_mov_file_eligible_for_ffmpeg_fallback(self, tmp_path: Path) -> None:
        """MOV files should also be eligible for ffmpeg fallback."""
        video = tmp_path / "large_video.mov"
        video.touch()

        request = MetadataWriteRequest(
            file_path=str(video),
            arg_preamble=DEFAULT_PREAMBLE,
            datetime_tags=[("DateTimeOriginal", "2024:01:15 12:30:00")],
            processor_name="test",
        )

        def mock_subprocess_run(*args, **kwargs):
            cmd = args[0]
            if cmd[0] == "exiftool":
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr=f"Error: atom is too large for rewriting - {video}",
                )
            elif cmd[0] == "ffmpeg":
                output_path = Path(cmd[-1])
                output_path.write_bytes(b"fake video content for test")
                return MagicMock(returncode=0, stdout="", stderr="")
            elif cmd[0] == "ffprobe":
                # Return matching duration for both original and remuxed file
                return MagicMock(returncode=0, stdout="10.5\n", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=mock_subprocess_run
        ):
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map={str(video): {}},
                dedup_manifest=None,
            )

        assert str(video) in outcome.successes
        assert len(outcome.failures) == 0

    def test_regular_exiftool_error_no_fallback(self, tmp_path: Path) -> None:
        """Regular ExifTool errors should not trigger ffmpeg fallback."""
        video = tmp_path / "corrupt_video.mp4"
        video.touch()

        request = MetadataWriteRequest(
            file_path=str(video),
            arg_preamble=DEFAULT_PREAMBLE,
            processor_name="test",
        )

        call_count = [0]

        def mock_subprocess_run(*args, **kwargs):
            call_count[0] += 1
            cmd = args[0]
            if cmd[0] == "exiftool":
                return MagicMock(
                    returncode=1,
                    stdout="",
                    stderr=f"Error: Not a valid MP4 - {video}",
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch(
            "common.media.exiftool.subprocess.run", side_effect=mock_subprocess_run
        ):
            outcome = batch_write_metadata_requests(
                [request],
                existing_metadata_map={str(video): {}},
                dedup_manifest=None,
            )

        # Should fail without ffmpeg fallback (not a large file error)
        assert str(video) not in outcome.successes
        assert len(outcome.failures) == 1
        assert outcome.failures[0].reason_code == "format_error"
        # Only exiftool should have been called
        assert call_count[0] == 1
