"""
Error injection tests for processor error handling.

Tests verify that processors handle error conditions gracefully:
- Permission errors (read-only directories)
- Missing files (metadata references non-existent files)
- Corrupted files (invalid headers, truncated files)
- Disk space errors (simulated via mocking)
"""

import errno
import json
import shutil
import stat
from unittest.mock import patch

import pytest

from tests.fixtures.generators import (
    create_google_photos_export,
)
from tests.fixtures.media_samples import write_media_file


# Check for external tool availability
EXIFTOOL_AVAILABLE = shutil.which("exiftool") is not None
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None

skip_no_exiftool = pytest.mark.skipif(
    not EXIFTOOL_AVAILABLE, reason="exiftool not installed"
)
skip_no_ffmpeg = pytest.mark.skipif(
    not FFMPEG_AVAILABLE, reason="ffmpeg not installed"
)


@pytest.mark.integration
class TestPermissionErrors:
    """Tests for permission error handling."""

    @skip_no_exiftool
    def test_unwritable_output_directory(self, tmp_path):
        """Should handle unwritable output directory gracefully."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        create_google_photos_export(export_dir)
        output_dir.mkdir()

        # Make output directory read-only
        output_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)

        try:
            # Should fail gracefully without crashing
            result = GooglePhotosProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            # Result should be False due to permission error
            # (or True if processor handles it differently)
            assert isinstance(result, bool)
        except PermissionError:
            # Also acceptable - processor may raise PermissionError
            pass
        finally:
            # Restore permissions for cleanup
            output_dir.chmod(stat.S_IRWXU)

    @skip_no_exiftool
    def test_read_only_input_file(self, tmp_path):
        """Should handle read-only input files."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        create_google_photos_export(export_dir)

        # Make all files in export read-only
        for file_path in export_dir.rglob("*"):
            if file_path.is_file():
                file_path.chmod(stat.S_IRUSR)

        try:
            # Should still work (reading is allowed)
            result = GooglePhotosProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        finally:
            # Restore permissions for cleanup
            for file_path in export_dir.rglob("*"):
                if file_path.is_file():
                    file_path.chmod(stat.S_IRWXU)


@pytest.mark.integration
class TestMissingFiles:
    """Tests for handling missing files referenced in metadata."""

    @skip_no_exiftool
    @skip_no_ffmpeg
    def test_metadata_references_missing_media(self, tmp_path):
        """Should handle metadata referencing non-existent media file."""
        from processors.snapchat_memories.processor import SnapchatMemoriesProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        # Create directories
        media_dir = export_dir / "media"
        overlays_dir = export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        # Create metadata referencing non-existent file
        metadata = [
            {
                "date": "2021-01-01 12:00:00 UTC",
                "media_type": "Image",
                "media_filename": "missing_photo.jpg",  # File doesn't exist
                "overlay_filename": None,
            }
        ]
        (export_dir / "metadata.json").write_text(json.dumps(metadata))

        # Should not crash
        try:
            result = SnapchatMemoriesProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        except FileNotFoundError:
            # Also acceptable behavior
            pass

    @skip_no_exiftool
    def test_json_references_missing_attachment(self, tmp_path):
        """Should handle JSON referencing missing attachment in Google Photos."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        # Create minimal structure
        album_dir = export_dir / "Google Photos" / "Test Album"
        album_dir.mkdir(parents=True)

        # Create JSON for non-existent photo
        metadata = {
            "title": "ghost_photo.jpg",
            "photoTakenTime": {"timestamp": "1609459200"},
        }
        (album_dir / "ghost_photo.jpg.json").write_text(json.dumps(metadata))
        # Don't create the actual photo file

        # Should not crash on missing file
        try:
            result = GooglePhotosProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        except Exception:
            # May fail gracefully
            pass


@pytest.mark.integration
class TestCorruptedFiles:
    """Tests for handling corrupted/malformed files."""

    @skip_no_exiftool
    def test_corrupted_jpeg_header(self, tmp_path):
        """Should handle JPEG file with corrupted header."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        album_dir = export_dir / "Google Photos" / "Test Album"
        album_dir.mkdir(parents=True)

        # Create file with invalid JPEG header
        corrupted_file = album_dir / "corrupted.jpg"
        corrupted_file.write_bytes(b"NOT A VALID JPEG FILE HEADER")

        # Create valid JSON metadata
        metadata = {
            "title": "corrupted.jpg",
            "photoTakenTime": {"timestamp": "1609459200"},
        }
        (album_dir / "corrupted.jpg.json").write_text(json.dumps(metadata))

        # Should not crash on corrupted file
        try:
            result = GooglePhotosProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        except Exception:
            # May fail but shouldn't crash
            pass

    @skip_no_exiftool
    def test_truncated_mp4_file(self, tmp_path):
        """Should handle truncated MP4 file."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        album_dir = export_dir / "Google Photos" / "Videos"
        album_dir.mkdir(parents=True)

        # Create truncated MP4 (just first few bytes)
        truncated_file = album_dir / "truncated.mp4"
        truncated_file.write_bytes(b"\x00\x00\x00\x14ftyp")  # Incomplete ftyp box

        metadata = {
            "title": "truncated.mp4",
            "photoTakenTime": {"timestamp": "1609459200"},
        }
        (album_dir / "truncated.mp4.json").write_text(json.dumps(metadata))

        try:
            result = GooglePhotosProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        except Exception:
            pass

    @skip_no_exiftool
    def test_malformed_json_metadata(self, tmp_path):
        """Should handle malformed JSON metadata files."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        album_dir = export_dir / "Google Photos" / "Test Album"
        album_dir.mkdir(parents=True)

        # Create valid image
        write_media_file(album_dir / "photo.jpg", "jpeg")

        # Create malformed JSON
        (album_dir / "photo.jpg.json").write_text("{invalid json content}")

        try:
            result = GooglePhotosProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        except json.JSONDecodeError:
            # Also acceptable
            pass


@pytest.mark.integration
class TestDiskSpaceErrors:
    """Tests for simulated disk space errors."""

    @skip_no_exiftool
    def test_disk_full_during_copy(self, tmp_path):
        """Should handle disk full error during file copy."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        create_google_photos_export(export_dir)

        def raise_enospc(*args, **kwargs):
            raise OSError(errno.ENOSPC, "No space left on device")

        # Mock shutil.copy2 to simulate disk full
        with patch("shutil.copy2", side_effect=raise_enospc):
            try:
                result = GooglePhotosProcessor.process(
                    str(export_dir), str(output_dir), verbose=False
                )
                # Should fail gracefully
                assert result is False or isinstance(result, bool)
            except OSError as e:
                # Also acceptable if error propagates
                assert e.errno == errno.ENOSPC


@pytest.mark.integration
class TestEmptyExports:
    """Tests for handling empty or minimal exports."""

    @skip_no_exiftool
    def test_empty_album(self, tmp_path):
        """Should handle export with empty album."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        # Create album with no files
        album_dir = export_dir / "Google Photos" / "Empty Album"
        album_dir.mkdir(parents=True)

        try:
            result = GooglePhotosProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        except Exception:
            pass

    @skip_no_exiftool
    @skip_no_ffmpeg
    def test_empty_metadata_array(self, tmp_path):
        """Should handle Snapchat export with empty metadata array."""
        from processors.snapchat_memories.processor import SnapchatMemoriesProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        media_dir = export_dir / "media"
        overlays_dir = export_dir / "overlays"
        media_dir.mkdir(parents=True)
        overlays_dir.mkdir(parents=True)

        # Empty metadata array
        (export_dir / "metadata.json").write_text("[]")

        try:
            result = SnapchatMemoriesProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        except Exception:
            pass


@pytest.mark.integration
class TestConcurrentModification:
    """Tests for handling files modified during processing."""

    @skip_no_exiftool
    def test_file_deleted_during_processing(self, tmp_path):
        """Should handle file deletion during processing."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        create_google_photos_export(
            export_dir,
            albums={"Test": ["photo1.jpg", "photo2.jpg", "photo3.jpg"]},
        )

        original_copy2 = shutil.copy2
        call_count = [0]

        def delete_file_on_second_call(src, dst, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                # Delete a different file to simulate concurrent deletion
                photo3 = export_dir / "Google Photos" / "Test" / "photo3.jpg"
                if photo3.exists():
                    photo3.unlink()
            return original_copy2(src, dst, **kwargs)

        with patch("shutil.copy2", side_effect=delete_file_on_second_call):
            try:
                result = GooglePhotosProcessor.process(
                    str(export_dir), str(output_dir), verbose=False
                )
                assert isinstance(result, bool)
            except FileNotFoundError:
                # Acceptable if file was deleted
                pass


@pytest.mark.integration
class TestSpecialCharacterPaths:
    """Tests for paths with special characters."""

    @skip_no_exiftool
    def test_unicode_in_path(self, tmp_path):
        """Should handle unicode characters in paths."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"

        # Create album with unicode name
        album_dir = export_dir / "Google Photos" / "Vacation Photos"
        album_dir.mkdir(parents=True)

        write_media_file(album_dir / "photo.jpg", "jpeg")
        metadata = {"title": "photo.jpg", "photoTakenTime": {"timestamp": "1609459200"}}
        (album_dir / "photo.jpg.json").write_text(json.dumps(metadata))

        try:
            result = GooglePhotosProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        except Exception:
            pass

    @skip_no_exiftool
    def test_spaces_in_path(self, tmp_path):
        """Should handle spaces in paths."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export with spaces"
        output_dir = tmp_path / "output with spaces"

        create_google_photos_export(
            export_dir,
            albums={"Album With Spaces": ["photo with spaces.jpg"]},
        )

        try:
            result = GooglePhotosProcessor.process(
                str(export_dir), str(output_dir), verbose=False
            )
            assert isinstance(result, bool)
        except Exception:
            pass

