#!/usr/bin/env python3
"""Unit tests for perceptual hashing."""

from pathlib import Path
from unittest.mock import patch

from common.dedup.hashing import (
    hash_similarity,
    is_perceptual_hash_available,
    compute_image_hash,
    compute_perceptual_hash_with_dims,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    is_live_photo_pair,
    find_live_photo_video,
    LIVE_PHOTO_IMAGE_EXTENSIONS,
    LIVE_PHOTO_VIDEO_EXTENSIONS,
)


class TestHashSimilarity:
    """Tests for hash similarity calculation."""

    def test_identical_hashes_similarity(self):
        """Test similarity of identical hashes is 1.0."""
        hash1 = "ffffffff00000000"
        hash2 = "ffffffff00000000"
        assert hash_similarity(hash1, hash2) == 1.0

    def test_opposite_hashes_similarity(self):
        """Test similarity of opposite hashes is 0.0."""
        hash1 = "0000000000000000"
        hash2 = "ffffffffffffffff"
        assert hash_similarity(hash1, hash2) == 0.0

    def test_partial_similarity(self):
        """Test similarity with some matching bits."""
        hash1 = "ff00ff00"  # 32 bits (8 hex chars)
        hash2 = "ff00ff01"  # 1 bit different
        similarity = hash_similarity(hash1, hash2)
        # 31/32 bits match = 0.96875
        assert abs(similarity - 0.96875) < 0.01

    def test_empty_hashes(self):
        """Test empty hashes return 0.0."""
        assert hash_similarity("", "abc") == 0.0
        assert hash_similarity("abc", "") == 0.0
        assert hash_similarity("", "") == 0.0


class TestSimilarityThreshold:
    """Tests for similarity threshold comparison using hash_similarity."""

    def test_identical_similar(self):
        """Test identical hashes are similar (above threshold)."""
        assert hash_similarity("ffffffff", "ffffffff") >= 0.9

    def test_below_threshold_not_similar(self):
        """Test very different hashes are below threshold."""
        assert hash_similarity("00000000", "ffffffff") < 0.9

    def test_at_threshold(self):
        """Test hashes at various thresholds."""
        hash1 = "ff00ff00"
        hash2 = "ff00ff01"  # 1 bit different out of 32 = 96.875%
        similarity = hash_similarity(hash1, hash2)
        assert similarity >= 0.96
        assert similarity < 0.98


class TestImageExtensions:
    """Tests for supported file extensions."""

    def test_common_image_extensions(self):
        """Test common image formats are supported."""
        assert ".jpg" in IMAGE_EXTENSIONS
        assert ".jpeg" in IMAGE_EXTENSIONS
        assert ".png" in IMAGE_EXTENSIONS
        assert ".gif" in IMAGE_EXTENSIONS
        assert ".heic" in IMAGE_EXTENSIONS

    def test_common_video_extensions(self):
        """Test common video formats are supported."""
        assert ".mp4" in VIDEO_EXTENSIONS
        assert ".mov" in VIDEO_EXTENSIONS
        assert ".avi" in VIDEO_EXTENSIONS


class TestPerceptualHashAvailability:
    """Tests for perceptual hash availability check."""

    def test_returns_bool(self):
        """Test that availability check returns boolean."""
        result = is_perceptual_hash_available()
        assert isinstance(result, bool)


class TestComputePerceptualHashWithDims:
    """Tests for compute_perceptual_hash_with_dims function."""

    def test_unsupported_extension(self):
        """Test unsupported file extensions return (None, None)."""
        result = compute_perceptual_hash_with_dims(Path("/path/file.txt"))
        assert result == (None, None)

    @patch("common.dedup.hashing.PERCEPTION_AVAILABLE", False)
    def test_returns_none_when_unavailable(self):
        """Test returns (None, None) when perception not available."""
        result = compute_perceptual_hash_with_dims(Path("/path/file.jpg"))
        assert result == (None, None)


class TestComputeImageHash:
    """Tests for compute_image_hash function."""

    @patch("common.dedup.hashing.PERCEPTION_AVAILABLE", False)
    def test_returns_none_when_unavailable(self):
        """Test returns None when perception not available."""
        result = compute_image_hash(Path("/path/file.jpg"))
        assert result is None

    def test_computes_phash_integration(self):
        """Test that compute_image_hash returns a string or None."""
        # This is an integration test - actual behavior depends on perception availability
        result = compute_image_hash(Path("/nonexistent/file.jpg"))
        assert result is None  # File doesn't exist

    def test_handles_nonexistent_file(self):
        """Test handles non-existent files gracefully."""
        result = compute_image_hash(Path("/path/to/nonexistent/file.jpg"))
        assert result is None


class TestLivePhotoExtensions:
    """Tests for Live Photo extension constants."""

    def test_image_extensions(self):
        """Test Live Photo image extensions."""
        assert ".heic" in LIVE_PHOTO_IMAGE_EXTENSIONS
        assert ".heif" in LIVE_PHOTO_IMAGE_EXTENSIONS
        assert ".jpg" in LIVE_PHOTO_IMAGE_EXTENSIONS
        assert ".jpeg" in LIVE_PHOTO_IMAGE_EXTENSIONS

    def test_video_extensions(self):
        """Test Live Photo video extensions."""
        assert ".mov" in LIVE_PHOTO_VIDEO_EXTENSIONS


class TestIsLivePhotoPair:
    """Tests for is_live_photo_pair function."""

    def test_matching_heic_mov_pair(self):
        """Test matching HEIC/MOV pair is detected."""
        image = Path("/photos/IMG_0001.heic")
        video = Path("/photos/IMG_0001.mov")
        assert is_live_photo_pair(image, video) is True

    def test_matching_jpg_mov_pair(self):
        """Test matching JPG/MOV pair is detected."""
        image = Path("/photos/photo.jpg")
        video = Path("/photos/photo.mov")
        assert is_live_photo_pair(image, video) is True

    def test_case_insensitive_extension(self):
        """Test case-insensitive extension matching."""
        image = Path("/photos/photo.HEIC")
        video = Path("/photos/photo.MOV")
        assert is_live_photo_pair(image, video) is True

    def test_different_basenames_not_pair(self):
        """Test files with different basenames are not a pair."""
        image = Path("/photos/IMG_0001.heic")
        video = Path("/photos/IMG_0002.mov")
        assert is_live_photo_pair(image, video) is False

    def test_wrong_image_extension_not_pair(self):
        """Test wrong image extension is not a pair."""
        image = Path("/photos/photo.png")  # Not a Live Photo image format
        video = Path("/photos/photo.mov")
        assert is_live_photo_pair(image, video) is False

    def test_wrong_video_extension_not_pair(self):
        """Test wrong video extension is not a pair."""
        image = Path("/photos/photo.heic")
        video = Path("/photos/photo.mp4")  # Not .mov
        assert is_live_photo_pair(image, video) is False


class TestFindLivePhotoVideo:
    """Tests for find_live_photo_video function."""

    def test_returns_none_for_non_image(self):
        """Test returns None for non-Live Photo image extension."""
        image = Path("/photos/file.png")
        video = find_live_photo_video(image)
        assert video is None

    def test_returns_none_when_video_not_exists(self, tmp_path):
        """Test returns None when video file doesn't exist."""
        image = tmp_path / "photo.heic"
        image.touch()  # Create image but no video

        video = find_live_photo_video(image)
        assert video is None

    def test_finds_video_in_same_directory(self, tmp_path):
        """Test finding video in same directory as image."""
        image = tmp_path / "photo.heic"
        video = tmp_path / "photo.mov"
        image.touch()
        video.touch()

        found = find_live_photo_video(image)
        assert found == video

    def test_finds_video_in_specified_directory(self, tmp_path):
        """Test finding video in specified search directory."""
        media_dir = tmp_path / "media"
        media_dir.mkdir()

        image = tmp_path / "photo.heic"  # Image in different location
        video = media_dir / "photo.mov"
        image.touch()
        video.touch()

        found = find_live_photo_video(image, search_dir=media_dir)
        assert found == video
