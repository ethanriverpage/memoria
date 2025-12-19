#!/usr/bin/env python3
"""Unit tests for perceptual hashing."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from common.dedup.hashing import (
    hamming_distance,
    hash_similarity,
    are_similar,
    is_perceptual_hash_available,
    compute_image_hash,
    compute_perceptual_hash_with_dims,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    is_live_photo_pair,
    find_live_photo_video,
    LivePhotoEntry,
    LIVE_PHOTO_IMAGE_EXTENSIONS,
    LIVE_PHOTO_VIDEO_EXTENSIONS,
)


class TestHammingDistance:
    """Tests for Hamming distance calculation."""

    def test_identical_hashes(self):
        """Test distance between identical hashes is 0."""
        hash1 = "ffffffff"
        hash2 = "ffffffff"
        assert hamming_distance(hash1, hash2) == 0

    def test_completely_different(self):
        """Test distance between opposite hashes."""
        hash1 = "00000000"
        hash2 = "ffffffff"
        # 8 hex chars = 32 bits, all different
        assert hamming_distance(hash1, hash2) == 32

    def test_one_bit_different(self):
        """Test distance with one bit difference."""
        hash1 = "00000000"
        hash2 = "00000001"
        assert hamming_distance(hash1, hash2) == 1

    def test_different_lengths_raises(self):
        """Test that different length hashes raise ValueError."""
        with pytest.raises(ValueError):
            hamming_distance("aabb", "aabbccdd")


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


class TestAreSimilar:
    """Tests for similarity threshold comparison."""

    def test_identical_similar(self):
        """Test identical hashes are similar."""
        assert are_similar("ffffffff", "ffffffff", threshold=0.9) is True

    def test_below_threshold_not_similar(self):
        """Test hashes below threshold are not similar."""
        # Very different hashes
        assert are_similar("00000000", "ffffffff", threshold=0.9) is False

    def test_at_threshold(self):
        """Test hashes exactly at threshold."""
        # Create hashes with exactly threshold similarity
        hash1 = "ff00ff00"
        hash2 = "ff00ff01"  # 1 bit different out of 32 = 96.875%
        assert are_similar(hash1, hash2, threshold=0.96) is True
        assert are_similar(hash1, hash2, threshold=0.98) is False

    def test_empty_hash_not_similar(self):
        """Test empty hashes are not similar."""
        assert are_similar("", "abc", threshold=0.9) is False
        assert are_similar("abc", "", threshold=0.9) is False


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


class TestLivePhotoEntry:
    """Tests for LivePhotoEntry dataclass."""

    def test_create_basic_entry(self):
        """Test creating a basic LivePhotoEntry."""
        entry = LivePhotoEntry(image_path=Path("/photo.heic"))
        assert entry.image_path == Path("/photo.heic")
        assert entry.video_path is None
        assert entry.content_hash == ""
        assert entry.perceptual_hash is None
        assert entry.metadata == {}

    def test_create_full_entry(self):
        """Test creating a LivePhotoEntry with all fields."""
        entry = LivePhotoEntry(
            image_path=Path("/photo.heic"),
            video_path=Path("/photo.mov"),
            content_hash="abc123",
            perceptual_hash="def456",
            image_timestamp="2024-01-01T12:00:00Z",
            metadata={"album": "Vacation"},
        )
        assert entry.video_path == Path("/photo.mov")
        assert entry.content_hash == "abc123"
        assert entry.perceptual_hash == "def456"
        assert entry.image_timestamp == "2024-01-01T12:00:00Z"
        assert entry.metadata == {"album": "Vacation"}


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

    def test_returns_none_when_video_not_exists(self):
        """Test returns None when video file doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            image = Path(tmpdir) / "photo.heic"
            image.touch()  # Create image but no video

            video = find_live_photo_video(image)
            assert video is None

    def test_finds_video_in_same_directory(self):
        """Test finding video in same directory as image."""
        with tempfile.TemporaryDirectory() as tmpdir:
            image = Path(tmpdir) / "photo.heic"
            video = Path(tmpdir) / "photo.mov"
            image.touch()
            video.touch()

            found = find_live_photo_video(image)
            assert found == video

    def test_finds_video_in_specified_directory(self):
        """Test finding video in specified search directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            media_dir = Path(tmpdir) / "media"
            media_dir.mkdir()

            image = Path(tmpdir) / "photo.heic"  # Image in different location
            video = media_dir / "photo.mov"
            image.touch()
            video.touch()

            found = find_live_photo_video(image, search_dir=media_dir)
            assert found == video
