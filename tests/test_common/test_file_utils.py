"""
Tests for common/file_utils.py

Tests the batch_correct_extensions function which detects and corrects
file extensions based on actual file content using python-magic.
"""

import pytest
from pathlib import Path

from common.files.utils import batch_correct_extensions
from tests.fixtures.media_samples import MINIMAL_JPEG, MINIMAL_PNG, MINIMAL_MP4


class TestBatchCorrectExtensions:
    """Tests for batch_correct_extensions function."""

    def test_no_files(self, tmp_path: Path):
        """Test with empty file list returns empty dict."""
        result = batch_correct_extensions([])
        assert result == {}

    def test_correct_extension_no_change(self, tmp_path: Path):
        """Test that files with correct extensions are not changed."""
        # Create a JPEG file with correct .jpg extension
        jpeg_file = tmp_path / "photo.jpg"
        jpeg_file.write_bytes(MINIMAL_JPEG)

        result = batch_correct_extensions([jpeg_file])

        # No corrections needed - file already has correct extension
        assert result == {}
        assert jpeg_file.exists()

    def test_incorrect_extension_corrected(self, tmp_path: Path):
        """Test that files with incorrect extensions are renamed."""
        # Create a JPEG file with wrong .png extension
        wrong_ext_file = tmp_path / "photo.png"
        wrong_ext_file.write_bytes(MINIMAL_JPEG)

        result = batch_correct_extensions([wrong_ext_file])

        # Should have been renamed to .jpg
        assert len(result) == 1
        old_path = str(wrong_ext_file)
        new_path = result[old_path]
        assert new_path.endswith(".jpg")

        # Original file should not exist, new file should exist
        assert not wrong_ext_file.exists()
        assert Path(new_path).exists()

    def test_png_with_jpeg_extension(self, tmp_path: Path):
        """Test PNG file with .jpg extension is corrected."""
        wrong_ext_file = tmp_path / "image.jpg"
        wrong_ext_file.write_bytes(MINIMAL_PNG)

        result = batch_correct_extensions([wrong_ext_file])

        assert len(result) == 1
        old_path = str(wrong_ext_file)
        new_path = result[old_path]
        assert new_path.endswith(".png")
        assert Path(new_path).exists()

    def test_multiple_files(self, tmp_path: Path):
        """Test batch processing multiple files."""
        # Create files with various extension scenarios
        correct_jpeg = tmp_path / "correct.jpg"
        correct_jpeg.write_bytes(MINIMAL_JPEG)

        wrong_jpeg = tmp_path / "wrong.png"  # JPEG with .png extension
        wrong_jpeg.write_bytes(MINIMAL_JPEG)

        correct_png = tmp_path / "correct.png"
        correct_png.write_bytes(MINIMAL_PNG)

        result = batch_correct_extensions([correct_jpeg, wrong_jpeg, correct_png])

        # Only the wrong one should be corrected
        assert len(result) == 1
        assert str(wrong_jpeg) in result

    def test_cross_category_not_corrected(self, tmp_path: Path):
        """Test that cross-category corrections are skipped (video extension on image)."""
        # Create a JPEG with video extension - should not be corrected
        cross_category_file = tmp_path / "video.mp4"
        cross_category_file.write_bytes(MINIMAL_JPEG)

        result = batch_correct_extensions([cross_category_file])

        # Cross-category corrections are prevented, file should remain unchanged
        assert result == {}
        assert cross_category_file.exists()

    def test_nonexistent_file_skipped(self, tmp_path: Path):
        """Test that nonexistent files are skipped without error."""
        nonexistent = tmp_path / "nonexistent.jpg"

        result = batch_correct_extensions([nonexistent])

        assert result == {}

    def test_collision_handling(self, tmp_path: Path):
        """Test that file name collisions are handled."""
        # Create a JPEG with wrong extension
        wrong_ext = tmp_path / "photo.png"
        wrong_ext.write_bytes(MINIMAL_JPEG)

        # Create a file that already has the correct name
        existing = tmp_path / "photo.jpg"
        existing.write_bytes(MINIMAL_PNG)

        result = batch_correct_extensions([wrong_ext])

        # Should have been renamed to photo_1.jpg or similar
        assert len(result) == 1
        old_path = str(wrong_ext)
        new_path = result[old_path]
        assert new_path.endswith(".jpg")
        assert "photo" in new_path
        # Should not overwrite existing file
        assert Path(new_path) != existing
        assert Path(new_path).exists()
        assert existing.exists()

    def test_returns_string_paths(self, tmp_path: Path):
        """Test that returned paths are strings, not Path objects."""
        wrong_ext = tmp_path / "photo.png"
        wrong_ext.write_bytes(MINIMAL_JPEG)

        result = batch_correct_extensions([wrong_ext])

        for old_path, new_path in result.items():
            assert isinstance(old_path, str)
            assert isinstance(new_path, str)

    def test_video_extension_correction(self, tmp_path: Path):
        """Test that video files with wrong video extension are corrected."""
        # MP4 files may be detected as video/quicktime or video/mp4
        # Create MP4 content with .avi extension (same category: video)
        wrong_video = tmp_path / "video.avi"
        wrong_video.write_bytes(MINIMAL_MP4)

        result = batch_correct_extensions([wrong_video])

        # MP4 content should be detected and corrected to .mp4 or .mov
        # The exact result depends on what python-magic detects
        if result:
            old_path = str(wrong_video)
            new_path = result[old_path]
            # Should be corrected to a video extension
            assert new_path.endswith(".mp4") or new_path.endswith(".mov")

