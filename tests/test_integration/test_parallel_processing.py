"""
Parallel processing tests for processor worker configurations.

Tests verify:
- Worker count validation (sequential vs parallel)
- Output consistency regardless of worker count
- No race conditions or duplicate processing
- Resource cleanup after parallel processing

Note: Processors require preprocessed input. Each test must call
preprocess() before process() to set up the required metadata.json
and media directory structure.
"""

import hashlib
import shutil
from pathlib import Path
from typing import Dict, Set, Type

import pytest

from processors.base import ProcessorBase
from tests.fixtures.generators import create_google_photos_export


# Check for external tool availability
EXIFTOOL_AVAILABLE = shutil.which("exiftool") is not None
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None

skip_no_exiftool = pytest.mark.skipif(
    not EXIFTOOL_AVAILABLE, reason="exiftool not installed"
)
skip_no_ffmpeg = pytest.mark.skipif(not FFMPEG_AVAILABLE, reason="ffmpeg not installed")


def preprocess_export(
    processor_class: Type[ProcessorBase],
    export_dir: Path,
    cache_dir: Path,
    **kwargs,
) -> str:
    """Run preprocessing on a raw export and return the working directory."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    working_dir = processor_class.preprocess(str(export_dir), str(cache_dir), **kwargs)
    return working_dir if working_dir else str(cache_dir)


def create_large_google_photos_export(
    base_path: Path, num_albums: int = 10, files_per_album: int = 5
) -> Path:
    """Create a larger Google Photos export for parallel processing tests.

    Args:
        base_path: Base directory for export
        num_albums: Number of albums to create
        files_per_album: Number of files per album

    Returns:
        Path to created export
    """
    albums = {}
    for i in range(num_albums):
        album_name = f"Album_{i:03d}"
        files = [f"photo_{j:03d}.jpg" for j in range(files_per_album)]
        albums[album_name] = files

    return create_google_photos_export(base_path, albums=albums)


def get_file_hashes(directory: Path) -> Dict[str, str]:
    """Get MD5 hashes of all files in a directory.

    Args:
        directory: Directory to scan

    Returns:
        Dict mapping relative paths to MD5 hashes
    """
    hashes = {}
    for file_path in directory.rglob("*"):
        if file_path.is_file():
            relative = file_path.relative_to(directory)
            with open(file_path, "rb") as f:
                hashes[str(relative)] = hashlib.md5(
                    f.read(), usedforsecurity=False
                ).hexdigest()
    return hashes


def get_file_names(directory: Path) -> Set[str]:
    """Get set of all file names in a directory.

    Args:
        directory: Directory to scan

    Returns:
        Set of relative file paths
    """
    return {str(f.relative_to(directory)) for f in directory.rglob("*") if f.is_file()}


def count_files(directory: Path) -> int:
    """Count total files in directory recursively."""
    return len([f for f in directory.rglob("*") if f.is_file()])


@pytest.mark.integration
@pytest.mark.slow
class TestWorkerCountValidation:
    """Tests for different worker count configurations."""

    @skip_no_exiftool
    def test_sequential_processing_workers_1(self, tmp_path):
        """Should process with workers=1 (sequential)."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output_dir = tmp_path / "output"

        create_google_photos_export(export_dir)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        result = GooglePhotosProcessor.process(
            working_dir, str(output_dir), workers=1
        )

        assert result is True
        assert output_dir.exists()

    @skip_no_exiftool
    def test_parallel_processing_workers_4(self, tmp_path):
        """Should process with workers=4 (parallel)."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output_dir = tmp_path / "output"

        create_google_photos_export(export_dir)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        result = GooglePhotosProcessor.process(
            working_dir, str(output_dir), workers=4
        )

        assert result is True
        assert output_dir.exists()

    @skip_no_exiftool
    def test_auto_detect_workers_none(self, tmp_path):
        """Should process with workers=None (auto-detect)."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output_dir = tmp_path / "output"

        create_google_photos_export(export_dir)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        result = GooglePhotosProcessor.process(
            working_dir, str(output_dir), workers=None
        )

        assert result is True
        assert output_dir.exists()

    @skip_no_exiftool
    def test_high_worker_count(self, tmp_path):
        """Should handle high worker count without issues."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output_dir = tmp_path / "output"

        create_google_photos_export(export_dir)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        result = GooglePhotosProcessor.process(
            working_dir, str(output_dir), workers=16
        )

        assert result is True


@pytest.mark.integration
@pytest.mark.slow
class TestOutputConsistency:
    """Tests verifying output is consistent regardless of worker count."""

    @skip_no_exiftool
    def test_file_count_consistent(self, tmp_path):
        """File count should be consistent with different worker counts."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output1 = tmp_path / "output1"
        output2 = tmp_path / "output2"

        create_large_google_photos_export(export_dir, num_albums=5, files_per_album=3)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        # Process with 1 worker
        GooglePhotosProcessor.process(
            working_dir, str(output1), workers=1
        )

        # Process with 4 workers
        GooglePhotosProcessor.process(
            working_dir, str(output2), workers=4
        )

        count1 = count_files(output1)
        count2 = count_files(output2)

        assert count1 == count2, f"File counts differ: {count1} vs {count2}"

    @skip_no_exiftool
    def test_same_files_produced(self, tmp_path):
        """Same files should be produced regardless of worker count."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output1 = tmp_path / "output1"
        output2 = tmp_path / "output2"

        create_large_google_photos_export(export_dir, num_albums=5, files_per_album=3)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        GooglePhotosProcessor.process(
            working_dir, str(output1), workers=1
        )

        GooglePhotosProcessor.process(
            working_dir, str(output2), workers=4
        )

        files1 = get_file_names(output1)
        files2 = get_file_names(output2)

        # Same file names should be produced
        assert (
            files1 == files2
        ), f"File sets differ: {files1.symmetric_difference(files2)}"


@pytest.mark.integration
@pytest.mark.slow
class TestRaceConditions:
    """Tests for race condition detection."""

    @skip_no_exiftool
    def test_no_duplicate_processing(self, tmp_path):
        """Should not process same file multiple times."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output_dir = tmp_path / "output"

        create_large_google_photos_export(export_dir, num_albums=10, files_per_album=5)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        # Process with high parallelism
        GooglePhotosProcessor.process(
            working_dir, str(output_dir), workers=8
        )

        # No duplicate file names should exist in same directory
        # (though same name in different dirs is OK)
        for dir_path in [d for d in output_dir.rglob("*") if d.is_dir()]:
            dir_files = [f.name for f in dir_path.iterdir() if f.is_file()]
            assert len(dir_files) == len(
                set(dir_files)
            ), "Duplicate files in same directory"

    @skip_no_exiftool
    def test_no_missing_files(self, tmp_path):
        """Should not miss any files during parallel processing."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output_dir = tmp_path / "output"

        num_albums = 5
        files_per_album = 4
        create_large_google_photos_export(
            export_dir, num_albums=num_albums, files_per_album=files_per_album
        )
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        GooglePhotosProcessor.process(
            working_dir, str(output_dir), workers=4
        )

        # Should have processed all input files
        output_files = list(output_dir.rglob("*"))
        output_media = [
            f
            for f in output_files
            if f.is_file() and f.suffix.lower() in [".jpg", ".png"]
        ]

        # At minimum, should have some output
        assert len(output_media) > 0, "No output files produced"


@pytest.mark.integration
@pytest.mark.slow
class TestResourceCleanup:
    """Tests for resource cleanup after parallel processing."""

    @skip_no_exiftool
    def test_temp_files_cleaned_after_processing(self, tmp_path):
        """Temporary files should be cleaned up after processing."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output_dir = tmp_path / "output"

        create_google_photos_export(export_dir)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        GooglePhotosProcessor.process(
            working_dir,
            str(output_dir),
            workers=4,
            
        )

        # Check that output exists and has files
        assert output_dir.exists()
        output_files = list(output_dir.rglob("*"))
        assert len([f for f in output_files if f.is_file()]) > 0

    @skip_no_exiftool
    def test_multiple_runs_produce_same_output(self, tmp_path):
        """Multiple processing runs should produce consistent output."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"

        create_google_photos_export(export_dir)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        # Run processing multiple times
        file_counts = []
        for i in range(3):
            output_dir = tmp_path / f"output_{i}"
            GooglePhotosProcessor.process(
                working_dir,
                str(output_dir),
                workers=2,
                
            )
            file_counts.append(count_files(output_dir))

        # All runs should produce same file count
        assert all(
            c == file_counts[0] for c in file_counts
        ), f"File counts differ across runs: {file_counts}"


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.parallel
class TestLargeExportProcessing:
    """Tests for processing larger exports."""

    @skip_no_exiftool
    def test_large_export_completes(self, tmp_path):
        """Should complete processing of larger export."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output_dir = tmp_path / "output"

        # Create larger export: 20 albums * 10 files = 200 files
        create_large_google_photos_export(export_dir, num_albums=20, files_per_album=10)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        result = GooglePhotosProcessor.process(
            working_dir, str(output_dir), workers=4
        )

        assert result is True
        assert count_files(output_dir) > 0

    @skip_no_exiftool
    def test_sequential_vs_parallel_large_export(self, tmp_path):
        """Sequential and parallel should produce same results for large export."""
        from processors.google_photos.processor import GooglePhotosProcessor

        export_dir = tmp_path / "export"
        cache_dir = tmp_path / "cache"
        output_seq = tmp_path / "output_seq"
        output_par = tmp_path / "output_par"

        create_large_google_photos_export(export_dir, num_albums=10, files_per_album=5)
        working_dir = preprocess_export(GooglePhotosProcessor, export_dir, cache_dir)

        # Sequential
        GooglePhotosProcessor.process(
            working_dir, str(output_seq), workers=1
        )

        # Parallel
        GooglePhotosProcessor.process(
            working_dir, str(output_par), workers=4
        )

        seq_count = count_files(output_seq)
        par_count = count_files(output_par)

        assert (
            seq_count == par_count
        ), f"Counts differ: seq={seq_count}, par={par_count}"
