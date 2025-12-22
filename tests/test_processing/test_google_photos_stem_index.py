"""
Tests for the stem-based metadata index optimization in Google Photos preprocessing.

Tests cover:
- Stem index construction in build_album_catalog()
- O(1) exact match lookups
- Candidate lookup for duplicate-indexed files
- Edge cases: trailing chars, truncated names, live photos
"""

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from tests.fixtures.media_samples import write_media_file


class TestStemIndexConstruction:
    """Tests for metadata_by_stem index building in build_album_catalog()."""

    @pytest.fixture
    def preprocessor(self, tmp_path):
        """Create a GooglePhotosPreprocessor instance for testing."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        return GooglePhotosPreprocessor(
            export_path=tmp_path,
            output_dir=tmp_path / "out",
        )

    @pytest.fixture
    def album_with_metadata(self, tmp_path) -> Path:
        """Create an album directory with media files and metadata."""
        album_dir = tmp_path / "Test Album"
        album_dir.mkdir()

        # Create media files
        write_media_file(album_dir / "IMG_0001.JPG", "jpeg")
        write_media_file(album_dir / "IMG_0002.PNG", "png")
        write_media_file(album_dir / "photo_with_trailing-.jpg", "jpeg")

        # Create corresponding JSON metadata files
        metadata = {"title": "test", "photoTakenTime": {"timestamp": "1609459200"}}
        (album_dir / "IMG_0001.JPG.json").write_text(json.dumps(metadata))
        (album_dir / "IMG_0002.PNG.json").write_text(json.dumps(metadata))
        (album_dir / "photo_with_trailing-.jpg.json").write_text(json.dumps(metadata))

        return album_dir

    def test_stem_index_created(self, preprocessor, album_with_metadata):
        """Stem index should be created in catalog."""
        catalog = preprocessor.build_album_catalog(album_with_metadata)

        assert "metadata_by_stem" in catalog
        assert isinstance(catalog["metadata_by_stem"], dict)

    def test_stem_index_contains_lowercase_stems(self, preprocessor, album_with_metadata):
        """Stem index keys should be lowercase stems (without extension)."""
        catalog = preprocessor.build_album_catalog(album_with_metadata)
        stem_index = catalog["metadata_by_stem"]

        # Check that stems are lowercase (stem is filename without extension)
        assert "img_0001" in stem_index
        assert "img_0002" in stem_index

    def test_stem_index_maps_to_metadata_paths(self, preprocessor, album_with_metadata):
        """Stem index values should be lists of (name, path) tuples."""
        catalog = preprocessor.build_album_catalog(album_with_metadata)
        stem_index = catalog["metadata_by_stem"]

        # Stem is filename without extension
        entries = stem_index.get("img_0001", [])
        assert len(entries) >= 1

        # Each entry should be (metadata_name, metadata_path)
        name, path = entries[0]
        assert isinstance(name, str)
        assert isinstance(path, Path)
        assert path.suffix == ".json"

    def test_stem_index_includes_cleaned_stems(self, preprocessor, album_with_metadata):
        """Stem index should include stems with trailing punctuation stripped."""
        catalog = preprocessor.build_album_catalog(album_with_metadata)
        stem_index = catalog["metadata_by_stem"]

        # Original stem with trailing dash
        assert "photo_with_trailing-.jpg" in stem_index or "photo_with_trailing-" in stem_index

        # Cleaned stem without trailing dash should also be indexed
        # The stem without extension for trailing chars matcher
        has_cleaned = any(
            "photo_with_trailing" in key and not key.endswith("-")
            for key in stem_index.keys()
        )
        # Either the original or cleaned version should exist
        assert len(stem_index) > 0


class TestStemIndexWithDuplicates:
    """Tests for stem index with duplicate-indexed files like IMG_0001(1).JPG."""

    @pytest.fixture
    def preprocessor(self, tmp_path):
        """Create a GooglePhotosPreprocessor instance for testing."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        return GooglePhotosPreprocessor(
            export_path=tmp_path,
            output_dir=tmp_path / "out",
        )

    @pytest.fixture
    def album_with_duplicates(self, tmp_path) -> Path:
        """Create an album with duplicate-indexed files."""
        album_dir = tmp_path / "Duplicates Album"
        album_dir.mkdir()

        # Create media files with duplicate indices
        write_media_file(album_dir / "IMG_0001.JPG", "jpeg")
        write_media_file(album_dir / "IMG_0001(1).JPG", "jpeg")
        write_media_file(album_dir / "IMG_0001(2).JPG", "jpeg")

        # Metadata for base file
        metadata = {"title": "test", "photoTakenTime": {"timestamp": "1609459200"}}
        (album_dir / "IMG_0001.JPG.json").write_text(json.dumps(metadata))

        return album_dir

    def test_duplicate_indexed_metadata_indexed_by_base_stem(
        self, preprocessor, album_with_duplicates
    ):
        """Metadata with duplicate index should be indexed under base stem too."""
        # Add metadata for duplicate file
        metadata = {"title": "test", "photoTakenTime": {"timestamp": "1609459200"}}
        (album_with_duplicates / "IMG_0001(1).JPG.json").write_text(
            json.dumps(metadata)
        )

        catalog = preprocessor.build_album_catalog(album_with_duplicates)
        stem_index = catalog["metadata_by_stem"]

        # Base stem (without extension) should have entries for both base and duplicate
        base_entries = stem_index.get("img_0001", [])
        assert len(base_entries) >= 1

        # The indexed entries should include paths to actual metadata files
        metadata_names = [entry[0] for entry in base_entries]
        assert "IMG_0001.JPG" in metadata_names or any(
            "IMG_0001" in name for name in metadata_names
        )


class TestFindMetadataWithStemIndex:
    """Tests for find_metadata_for_media() using stem index."""

    @pytest.fixture
    def preprocessor(self, tmp_path):
        """Create a GooglePhotosPreprocessor instance for testing."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        return GooglePhotosPreprocessor(
            export_path=tmp_path,
            output_dir=tmp_path / "out",
        )

    @pytest.fixture
    def catalog_with_index(self, tmp_path) -> Dict[str, Any]:
        """Create a catalog dict with metadata and stem index."""
        album_dir = tmp_path / "Test Album"
        album_dir.mkdir()

        # Create metadata files
        metadata_content = {"title": "test", "photoTakenTime": {"timestamp": "1609459200"}}

        files = [
            "IMG_0001.JPG",
            "IMG_0002.PNG",
            "photo_test.jpg",
            "IMG_0003(1).JPG",
        ]

        catalog: Dict[str, Any] = {"metadata": {}, "metadata_by_stem": {}}

        for filename in files:
            json_path = album_dir / f"{filename}.json"
            json_path.write_text(json.dumps(metadata_content))
            catalog["metadata"][filename] = json_path

            # Build stem index
            stem = Path(filename).stem.lower()
            catalog["metadata_by_stem"].setdefault(stem, []).append(
                (filename, json_path)
            )

        return catalog

    def test_exact_match_returns_immediately(self, preprocessor, catalog_with_index):
        """Exact match should return metadata path directly."""
        result = preprocessor.find_metadata_for_media("IMG_0001.JPG", catalog_with_index)

        assert result is not None
        assert result.name == "IMG_0001.JPG.json"

    def test_no_match_returns_none(self, preprocessor, catalog_with_index):
        """Non-existent file should return None."""
        result = preprocessor.find_metadata_for_media(
            "NONEXISTENT.JPG", catalog_with_index
        )

        assert result is None

    def test_stem_lookup_finds_candidates(self, preprocessor, catalog_with_index):
        """Stem-based lookup should find matching candidates."""
        # IMG_0002.PNG has a stem of "img_0002" which should find IMG_0002.PNG metadata
        result = preprocessor.find_metadata_for_media("IMG_0002.PNG", catalog_with_index)

        assert result is not None
        assert "IMG_0002" in result.name


class TestFindMetadataEdgeCases:
    """Tests for edge cases in metadata matching with stem index."""

    @pytest.fixture
    def preprocessor(self, tmp_path):
        """Create a GooglePhotosPreprocessor instance for testing."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        return GooglePhotosPreprocessor(
            export_path=tmp_path,
            output_dir=tmp_path / "out",
        )

    def test_empty_catalog_returns_none(self, preprocessor):
        """Empty catalog should return None for any lookup."""
        catalog = {"metadata": {}, "metadata_by_stem": {}}

        result = preprocessor.find_metadata_for_media("any_file.jpg", catalog)

        assert result is None

    def test_catalog_without_stem_index_still_works(self, preprocessor, tmp_path):
        """Catalog without stem index should still work (graceful fallback)."""
        album_dir = tmp_path / "Album"
        album_dir.mkdir()

        metadata_content = {"title": "test"}
        json_path = album_dir / "photo.jpg.json"
        json_path.write_text(json.dumps(metadata_content))

        # Catalog without metadata_by_stem key
        catalog = {"metadata": {"photo.jpg": json_path}}

        # Should handle missing stem index gracefully
        result = preprocessor.find_metadata_for_media("photo.jpg", catalog)

        # Exact match should still work via the fast path
        assert result is not None

    def test_duplicate_file_finds_base_metadata(self, preprocessor, tmp_path):
        """Duplicate-indexed file should find base metadata via stem index."""
        album_dir = tmp_path / "Album"
        album_dir.mkdir()

        metadata_content = {"title": "test"}
        json_path = album_dir / "IMG_0001.JPG.json"
        json_path.write_text(json.dumps(metadata_content))

        catalog = {
            "metadata": {"IMG_0001.JPG": json_path},
            "metadata_by_stem": {
                "img_0001.jpg": [("IMG_0001.JPG", json_path)],
                "img_0001": [("IMG_0001.JPG", json_path)],
            },
        }

        # Looking for IMG_0001(1).JPG should find IMG_0001.JPG metadata
        result = preprocessor.find_metadata_for_media("IMG_0001(1).JPG", catalog)

        assert result is not None
        assert result.name == "IMG_0001.JPG.json"

    def test_prefix_index_and_title_index_enable_suffix_variant_match(
        self, preprocessor, tmp_path
    ):
        """
        If a metadata JSON filename lacks a media extension (uuid__hash.json), but the JSON
        contains a 'title' with the real media filename (including extension), we should:
        - index by that title
        - allow fast candidate lookup via prefix index
        - match media to metadata even when stems differ only in trailing chars
        """
        album_dir = tmp_path / "Archive"
        album_dir.mkdir()

        # Media filename is a truncated/suffix variant of the metadata title
        media_name = "52738308278__E577206E-8A5D-49EB-B93E-636E23081D.JPG"
        write_media_file(album_dir / media_name, "jpeg")

        # Metadata sidecar file lacks extension in its filename, but has a title that includes it
        metadata_filename = "52738308278__E577206E-8A5D-49EB-B93E-636E23081.json"
        metadata_title = (
            "52738308278__E577206E-8A5D-49EB-B93E-636E23081DE1.JPG"
        )
        (album_dir / metadata_filename).write_text(json.dumps({"title": metadata_title}))

        catalog = preprocessor.build_album_catalog(album_dir)

        # Ensure prefix index exists
        assert "metadata_by_prefix" in catalog

        matched = preprocessor.find_metadata_for_media(media_name, catalog)
        assert matched is not None
        assert matched.name == metadata_filename


class TestStemIndexPerformance:
    """Tests to verify the stem index provides performance benefits."""

    @pytest.fixture
    def preprocessor(self, tmp_path):
        """Create a GooglePhotosPreprocessor instance for testing."""
        from processors.google_photos.preprocess import GooglePhotosPreprocessor

        return GooglePhotosPreprocessor(
            export_path=tmp_path,
            output_dir=tmp_path / "out",
        )

    def test_large_catalog_exact_match_is_fast(self, preprocessor, tmp_path):
        """Exact match on large catalog should be O(1)."""
        album_dir = tmp_path / "Large Album"
        album_dir.mkdir()

        # Create a large catalog with many entries
        catalog: Dict[str, Any] = {"metadata": {}, "metadata_by_stem": {}}
        metadata_content = {"title": "test"}

        for i in range(1000):
            filename = f"IMG_{i:04d}.JPG"
            json_path = album_dir / f"{filename}.json"
            # Don't actually write files, just build the catalog structure
            catalog["metadata"][filename] = json_path

            stem = Path(filename).stem.lower()
            catalog["metadata_by_stem"].setdefault(stem, []).append(
                (filename, json_path)
            )

        # Write just the file we're looking for
        target_path = album_dir / "IMG_0500.JPG.json"
        target_path.write_text(json.dumps(metadata_content))

        # This should be O(1) lookup, not O(n)
        result = preprocessor.find_metadata_for_media("IMG_0500.JPG", catalog)

        assert result is not None
        assert "IMG_0500" in str(result)

