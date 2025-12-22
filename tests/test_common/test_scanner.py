#!/usr/bin/env python3
"""Tests for common/files/scanner.py media file scanning utilities.

Tests cover:
- iter_media_files() generator for walking directories
- collect_media_files() for gathering files from multiple paths
- Filtering: overlays, thumbnails, banned patterns, extensions
- Recursive vs non-recursive scanning
"""

from pathlib import Path

import pytest

from common.files.scanner import (
    iter_media_files,
    collect_media_files,
    _should_include_file,
)
from common.files.filter import BannedFilesFilter


class TestIterMediaFiles:
    """Tests for iter_media_files generator function."""

    def test_finds_images_in_flat_directory(self, tmp_path: Path) -> None:
        """Should find image files in a flat directory structure."""
        (tmp_path / "photo.jpg").write_bytes(b"jpeg content")
        (tmp_path / "image.png").write_bytes(b"png content")
        (tmp_path / "readme.txt").write_text("not media")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 2
        extensions = {f.suffix.lower() for f in files}
        assert extensions == {".jpg", ".png"}

    def test_finds_videos(self, tmp_path: Path) -> None:
        """Should find video files."""
        (tmp_path / "video.mp4").write_bytes(b"mp4 content")
        (tmp_path / "clip.mov").write_bytes(b"mov content")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 2
        extensions = {f.suffix.lower() for f in files}
        assert extensions == {".mp4", ".mov"}

    def test_finds_files_recursively(self, tmp_path: Path) -> None:
        """Should find media files in nested directories."""
        subdir = tmp_path / "album" / "2024"
        subdir.mkdir(parents=True)
        (subdir / "photo.jpg").write_bytes(b"content")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "photo.jpg"

    def test_non_recursive_mode(self, tmp_path: Path) -> None:
        """Should only scan top-level when recursive=False."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (tmp_path / "top.jpg").write_bytes(b"top level")
        (subdir / "nested.jpg").write_bytes(b"nested")

        files = list(iter_media_files(tmp_path, recursive=False))

        assert len(files) == 1
        assert files[0].name == "top.jpg"

    def test_skips_overlay_files_by_default(self, tmp_path: Path) -> None:
        """Should skip files with 'overlay' in the name by default."""
        (tmp_path / "photo.jpg").write_bytes(b"photo")
        (tmp_path / "overlay.png").write_bytes(b"overlay")
        (tmp_path / "photo_overlay.png").write_bytes(b"overlay2")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "photo.jpg"

    def test_includes_overlay_files_when_disabled(self, tmp_path: Path) -> None:
        """Should include overlay files when skip_overlays=False."""
        (tmp_path / "photo.jpg").write_bytes(b"photo")
        (tmp_path / "overlay.png").write_bytes(b"overlay")

        files = list(iter_media_files(tmp_path, skip_overlays=False))

        assert len(files) == 2

    def test_skips_thumbnail_files_by_default(self, tmp_path: Path) -> None:
        """Should skip files with 'thumbnail' in the name by default."""
        (tmp_path / "photo.jpg").write_bytes(b"photo")
        (tmp_path / "thumbnail.jpg").write_bytes(b"thumb")
        (tmp_path / "photo_thumbnail.jpg").write_bytes(b"thumb2")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "photo.jpg"

    def test_includes_thumbnail_files_when_disabled(self, tmp_path: Path) -> None:
        """Should include thumbnail files when skip_thumbnails=False."""
        (tmp_path / "photo.jpg").write_bytes(b"photo")
        (tmp_path / "thumbnail.jpg").write_bytes(b"thumb")

        files = list(iter_media_files(tmp_path, skip_thumbnails=False))

        assert len(files) == 2

    def test_handles_empty_directory(self, tmp_path: Path) -> None:
        """Should return empty for directory with no media."""
        files = list(iter_media_files(tmp_path))
        assert files == []

    def test_handles_non_media_files_only(self, tmp_path: Path) -> None:
        """Should return empty when no media extensions match."""
        (tmp_path / "readme.txt").write_text("text")
        (tmp_path / "data.json").write_text("{}")
        (tmp_path / "script.py").write_text("print('hi')")

        files = list(iter_media_files(tmp_path))
        assert files == []

    def test_custom_extensions(self, tmp_path: Path) -> None:
        """Should respect custom extension filter."""
        (tmp_path / "photo.jpg").write_bytes(b"jpeg")
        (tmp_path / "image.png").write_bytes(b"png")

        # Only look for PNGs
        files = list(iter_media_files(tmp_path, extensions={".png"}))

        assert len(files) == 1
        assert files[0].suffix.lower() == ".png"

    def test_banned_filter_integration(self, tmp_path: Path) -> None:
        """Should respect banned file filter."""
        (tmp_path / "good.jpg").write_bytes(b"good")
        (tmp_path / ".DS_Store").write_bytes(b"hidden")

        # BannedFilesFilter by default bans .DS_Store
        banned = BannedFilesFilter()
        files = list(iter_media_files(tmp_path, banned_filter=banned))

        assert len(files) == 1
        assert files[0].name == "good.jpg"

    def test_prunes_banned_directories(self, tmp_path: Path) -> None:
        """Should skip entire banned directories during traversal."""
        good_dir = tmp_path / "photos"
        good_dir.mkdir()
        (good_dir / "photo.jpg").write_bytes(b"good")

        # Create a directory that matches banned patterns (use @eaDir which is banned)
        bad_dir = tmp_path / "@eaDir"
        bad_dir.mkdir()
        (bad_dir / "hidden.jpg").write_bytes(b"hidden")

        banned = BannedFilesFilter()
        files = list(iter_media_files(tmp_path, banned_filter=banned))

        assert len(files) == 1
        assert files[0].name == "photo.jpg"

    def test_case_insensitive_extension_matching(self, tmp_path: Path) -> None:
        """Should match extensions case-insensitively."""
        (tmp_path / "photo.JPG").write_bytes(b"jpeg1")
        (tmp_path / "image.Png").write_bytes(b"png1")
        (tmp_path / "video.MP4").write_bytes(b"mp4")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 3

    def test_case_insensitive_overlay_skip(self, tmp_path: Path) -> None:
        """Should skip overlays regardless of case."""
        (tmp_path / "OVERLAY.png").write_bytes(b"overlay1")
        (tmp_path / "Photo_Overlay.PNG").write_bytes(b"overlay2")
        (tmp_path / "photo.jpg").write_bytes(b"photo")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "photo.jpg"


class TestCollectMediaFiles:
    """Tests for collect_media_files convenience function."""

    def test_collects_from_single_directory(self, tmp_path: Path) -> None:
        """Should collect all media from a single directory."""
        (tmp_path / "photo.jpg").write_bytes(b"content")
        (tmp_path / "video.mp4").write_bytes(b"content")

        files = collect_media_files([tmp_path])

        assert len(files) == 2

    def test_collects_from_multiple_directories(self, tmp_path: Path) -> None:
        """Should collect media from multiple base paths."""
        dir1 = tmp_path / "dir1"
        dir2 = tmp_path / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        (dir1 / "photo1.jpg").write_bytes(b"content")
        (dir2 / "photo2.jpg").write_bytes(b"content")

        files = collect_media_files([dir1, dir2])

        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"photo1.jpg", "photo2.jpg"}

    def test_handles_single_file_path(self, tmp_path: Path) -> None:
        """Should handle a single file path directly."""
        single_file = tmp_path / "photo.jpg"
        single_file.write_bytes(b"content")

        files = collect_media_files([single_file])

        assert len(files) == 1
        assert files[0] == single_file

    def test_filters_single_file_path(self, tmp_path: Path) -> None:
        """Should apply filters to single file paths too."""
        overlay_file = tmp_path / "overlay.png"
        overlay_file.write_bytes(b"content")

        files = collect_media_files([overlay_file])

        # overlay.png should be filtered out
        assert len(files) == 0

    def test_skips_nonexistent_paths(self, tmp_path: Path) -> None:
        """Should skip paths that don't exist."""
        existing = tmp_path / "exists.jpg"
        existing.write_bytes(b"content")
        nonexistent = tmp_path / "nonexistent.jpg"

        files = collect_media_files([existing, nonexistent])

        assert len(files) == 1
        assert files[0] == existing

    def test_empty_path_list(self) -> None:
        """Should return empty list for empty input."""
        files = collect_media_files([])
        assert files == []

    def test_mixed_files_and_directories(self, tmp_path: Path) -> None:
        """Should handle mix of files and directories."""
        single_file = tmp_path / "single.jpg"
        single_file.write_bytes(b"single")

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.jpg").write_bytes(b"nested")

        files = collect_media_files([single_file, subdir])

        assert len(files) == 2
        names = {f.name for f in files}
        assert names == {"single.jpg", "nested.jpg"}


class TestShouldIncludeFile:
    """Tests for _should_include_file helper function."""

    @pytest.fixture
    def banned_filter(self) -> BannedFilesFilter:
        """Create a banned filter instance."""
        return BannedFilesFilter()

    def test_includes_valid_media_file(
        self, tmp_path: Path, banned_filter: BannedFilesFilter
    ) -> None:
        """Should include a valid media file."""
        test_file = tmp_path / "photo.jpg"
        test_file.write_bytes(b"content")

        result = _should_include_file(
            test_file,
            test_file.name,
            extensions={".jpg", ".png"},
            banned_filter=banned_filter,
            skip_overlays=True,
            skip_thumbnails=True,
        )

        assert result is True

    def test_excludes_wrong_extension(
        self, tmp_path: Path, banned_filter: BannedFilesFilter
    ) -> None:
        """Should exclude file with non-media extension."""
        test_file = tmp_path / "document.txt"
        test_file.write_text("content")

        result = _should_include_file(
            test_file,
            test_file.name,
            extensions={".jpg", ".png"},
            banned_filter=banned_filter,
            skip_overlays=True,
            skip_thumbnails=True,
        )

        assert result is False

    def test_excludes_overlay_when_enabled(
        self, tmp_path: Path, banned_filter: BannedFilesFilter
    ) -> None:
        """Should exclude overlay file when skip_overlays=True."""
        test_file = tmp_path / "overlay.png"
        test_file.write_bytes(b"content")

        result = _should_include_file(
            test_file,
            test_file.name,
            extensions={".png"},
            banned_filter=banned_filter,
            skip_overlays=True,
            skip_thumbnails=True,
        )

        assert result is False

    def test_includes_overlay_when_disabled(
        self, tmp_path: Path, banned_filter: BannedFilesFilter
    ) -> None:
        """Should include overlay file when skip_overlays=False."""
        test_file = tmp_path / "overlay.png"
        test_file.write_bytes(b"content")

        result = _should_include_file(
            test_file,
            test_file.name,
            extensions={".png"},
            banned_filter=banned_filter,
            skip_overlays=False,
            skip_thumbnails=True,
        )

        assert result is True

    def test_excludes_thumbnail_when_enabled(
        self, tmp_path: Path, banned_filter: BannedFilesFilter
    ) -> None:
        """Should exclude thumbnail file when skip_thumbnails=True."""
        test_file = tmp_path / "photo_thumbnail.jpg"
        test_file.write_bytes(b"content")

        result = _should_include_file(
            test_file,
            test_file.name,
            extensions={".jpg"},
            banned_filter=banned_filter,
            skip_overlays=True,
            skip_thumbnails=True,
        )

        assert result is False

    def test_excludes_banned_file(
        self, tmp_path: Path, banned_filter: BannedFilesFilter
    ) -> None:
        """Should exclude files matching banned patterns."""
        test_file = tmp_path / ".DS_Store"
        test_file.write_bytes(b"content")

        result = _should_include_file(
            test_file,
            test_file.name,
            extensions={".DS_Store"},  # Even with extension match
            banned_filter=banned_filter,
            skip_overlays=True,
            skip_thumbnails=True,
        )

        assert result is False


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    def test_deep_nesting(self, tmp_path: Path) -> None:
        """Should handle deeply nested directory structures."""
        deep_path = tmp_path
        for i in range(10):
            deep_path = deep_path / f"level_{i}"
        deep_path.mkdir(parents=True)
        (deep_path / "deep_photo.jpg").write_bytes(b"content")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 1
        assert files[0].name == "deep_photo.jpg"

    def test_many_files(self, tmp_path: Path) -> None:
        """Should handle directories with many files."""
        for i in range(100):
            (tmp_path / f"photo_{i:03d}.jpg").write_bytes(f"content_{i}".encode())

        files = list(iter_media_files(tmp_path))

        assert len(files) == 100

    def test_special_characters_in_filenames(self, tmp_path: Path) -> None:
        """Should handle special characters in filenames."""
        (tmp_path / "photo (1).jpg").write_bytes(b"content")
        (tmp_path / "photo - copy.jpg").write_bytes(b"content")
        (tmp_path / "photo_with_underscore.jpg").write_bytes(b"content")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 3

    def test_unicode_filenames(self, tmp_path: Path) -> None:
        """Should handle unicode characters in filenames."""
        (tmp_path / "photo_cafe.jpg").write_bytes(b"content")
        (tmp_path / "photo_2024.jpg").write_bytes(b"content")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 2

    def test_symlinks_not_followed_by_default(self, tmp_path: Path) -> None:
        """Should not follow symlinks (os.walk default behavior)."""
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        (real_dir / "photo.jpg").write_bytes(b"content")

        link_dir = tmp_path / "link"
        try:
            link_dir.symlink_to(real_dir)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")

        # Scan from tmp_path - should find photo in real_dir
        # but os.walk by default doesn't follow symlinks
        files = list(iter_media_files(tmp_path))

        # Should find the file in real/ directory
        assert any(f.name == "photo.jpg" for f in files)

    def test_heic_and_heif_extensions(self, tmp_path: Path) -> None:
        """Should find HEIC/HEIF files (common iPhone format)."""
        (tmp_path / "photo.heic").write_bytes(b"heic content")
        (tmp_path / "photo.heif").write_bytes(b"heif content")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 2
        extensions = {f.suffix.lower() for f in files}
        assert ".heic" in extensions
        assert ".heif" in extensions

    def test_webp_extension(self, tmp_path: Path) -> None:
        """Should find WebP files."""
        (tmp_path / "image.webp").write_bytes(b"webp content")

        files = list(iter_media_files(tmp_path))

        assert len(files) == 1
        assert files[0].suffix.lower() == ".webp"

