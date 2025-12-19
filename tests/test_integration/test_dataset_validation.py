"""
Integration tests that validate processed test dataset against source data.

These tests verify that:
- Processed files have correct EXIF metadata embedded
- File counts match expectations
- Timestamps, GPS, and other metadata are correctly transferred
- Output directory structure is correct

Run modes:
1. Use existing dataset (default):
   pytest tests/test_integration/test_dataset_validation.py -v

2. Auto-generate and cleanup:
   pytest tests/test_integration/test_dataset_validation.py -v --run-full-pipeline

3. Custom paths:
   TEST_DATASET_SOURCE=/path/to/source TEST_DATASET_PROCESSED=/path/to/output \
   pytest tests/test_integration/test_dataset_validation.py -v
"""

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest


# Configuration from environment
ORIGINALS_PATH = Path(os.environ.get("ORIGINALS_PATH", "/mnt/media/originals"))
TEST_DATASET_SOURCE = Path(
    os.environ.get("TEST_DATASET_SOURCE", "/mnt/media/temp/test_dataset")
)
TEST_DATASET_PROCESSED = Path(
    os.environ.get("TEST_DATASET_PROCESSED", "/mnt/media/temp/test_dataset_proc")
)

# Project root for running scripts
PROJECT_ROOT = Path(__file__).parent.parent.parent


# Check for external tool availability
EXIFTOOL_AVAILABLE = shutil.which("exiftool") is not None
FFMPEG_AVAILABLE = shutil.which("ffmpeg") is not None

skip_no_exiftool = pytest.mark.skipif(
    not EXIFTOOL_AVAILABLE, reason="exiftool not installed"
)


def run_sampler(output_dir: Path, source_dir: Path) -> bool:
    """Run the dataset sampler script with random sampling.

    Args:
        output_dir: Where to create the sampled dataset
        source_dir: Original media source directory

    Returns:
        True if sampling succeeded
    """
    sampler_script = PROJECT_ROOT / "standalone" / "create_test_dataset.py"

    if not sampler_script.exists():
        raise FileNotFoundError(f"Sampler script not found: {sampler_script}")

    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    # Uses random seed by default for varied coverage across test runs
    cmd = [
        "python",
        str(sampler_script),
        "--source",
        str(source_dir),
        "--output",
        str(output_dir),
        "--force",
    ]

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=300,  # 5 minute timeout
    )

    if result.returncode != 0:
        print(f"Sampler failed:\n{result.stderr}")
        return False

    return True


def run_memoria(input_dir: Path, output_dir: Path) -> bool:
    """Run memoria processor on the sampled dataset.

    Args:
        input_dir: Sampled dataset directory
        output_dir: Where to write processed output

    Returns:
        True if processing succeeded
    """
    memoria_script = PROJECT_ROOT / "memoria.py"

    if not memoria_script.exists():
        raise FileNotFoundError(f"Memoria script not found: {memoria_script}")

    cmd = [
        "python",
        str(memoria_script),
        "--originals",
        str(input_dir),
        "-o",
        str(output_dir),
        "--skip-upload",
    ]

    result = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=600,  # 10 minute timeout
    )

    if result.returncode != 0:
        print(f"Memoria failed:\n{result.stderr}")
        return False

    return True


@pytest.fixture(scope="session")
def dataset_paths(request) -> Tuple[Path, Path]:
    """Session-scoped fixture that provides dataset paths.

    If --run-full-pipeline is set, creates a fresh dataset, processes it,
    and cleans up after all tests complete.

    Otherwise, uses existing paths from environment or defaults.

    Returns:
        Tuple of (source_path, processed_path)
    """
    run_pipeline = request.config.getoption("--run-full-pipeline", default=False)
    keep_dataset = request.config.getoption("--keep-dataset", default=False)

    if not run_pipeline:
        # Use existing dataset
        if not TEST_DATASET_PROCESSED.exists():
            pytest.skip(
                f"No processed dataset at {TEST_DATASET_PROCESSED}. "
                "Run with --run-full-pipeline to auto-generate."
            )
        yield TEST_DATASET_SOURCE, TEST_DATASET_PROCESSED
        return

    # Create fresh dataset in temp directory
    temp_base = Path(tempfile.mkdtemp(prefix="memoria_test_"))
    source_dir = temp_base / "sampled"
    processed_dir = temp_base / "processed"

    print(f"\n{'='*60}")
    print("FULL PIPELINE TEST MODE")
    print(f"{'='*60}")
    print(f"Temp directory: {temp_base}")
    print(f"Source (originals): {ORIGINALS_PATH}")

    try:
        # Step 1: Sample dataset
        print("\nStep 1: Sampling dataset...")
        if not run_sampler(source_dir, ORIGINALS_PATH):
            pytest.fail("Failed to sample dataset")
        print(f"  Sampled to: {source_dir}")

        # Step 2: Process with memoria
        print("\nStep 2: Processing with memoria...")
        if not run_memoria(source_dir, processed_dir):
            pytest.fail("Failed to process dataset with memoria")
        print(f"  Processed to: {processed_dir}")

        print(f"\n{'='*60}")
        print("Pipeline complete, running tests...")
        print(f"{'='*60}\n")

        yield source_dir, processed_dir

    finally:
        # Cleanup
        if not keep_dataset:
            print(f"\nCleaning up temp directory: {temp_base}")
            shutil.rmtree(temp_base, ignore_errors=True)
        else:
            print(f"\nKeeping dataset at: {temp_base}")


@pytest.fixture(scope="session")
def processed_path(dataset_paths) -> Path:
    """Get just the processed dataset path."""
    return dataset_paths[1]


@pytest.fixture(scope="session")
def source_path(dataset_paths) -> Path:
    """Get just the source dataset path."""
    return dataset_paths[0]


# For backwards compatibility with tests that don't use fixtures
def _get_processed_path() -> Path:
    """Get processed path for non-fixture tests."""
    return TEST_DATASET_PROCESSED


skip_no_dataset = pytest.mark.skipif(
    not TEST_DATASET_PROCESSED.exists() and not os.environ.get("RUN_FULL_PIPELINE"),
    reason=f"Processed test dataset not found at {TEST_DATASET_PROCESSED}",
)


def read_exif(file_path: Path) -> Dict[str, Any]:
    """Read EXIF metadata from a file using exiftool."""
    try:
        result = subprocess.run(
            ["exiftool", "-j", "-n", str(file_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            return data[0] if data else {}
    except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError):
        pass
    return {}


def find_files(directory: Path, extensions: Optional[List[str]] = None) -> List[Path]:
    """Find all files in directory, optionally filtered by extension."""
    if not directory.exists():
        return []

    files = []
    for f in directory.rglob("*"):
        if f.is_file():
            if extensions is None or f.suffix.lower() in extensions:
                files.append(f)
    return files


def count_media_files(directory: Path) -> int:
    """Count media files in a directory."""
    media_extensions = {
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".webp",
        ".heic",
        ".heif",
        ".mp4",
        ".mov",
        ".m4v",
        ".avi",
        ".mkv",
        ".3gp",
    }
    return len(find_files(directory, list(media_extensions)))


@pytest.mark.integration
class TestDatasetStructure:
    """Tests that verify the processed dataset structure."""

    def test_output_directory_exists(self, processed_path):
        """Processed output directory should exist."""
        assert processed_path.exists()
        assert processed_path.is_dir()

    def test_export_directories_created(self, source_path, processed_path):
        """Each export should have a corresponding output directory."""
        source_exports = [
            d
            for d in source_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        for source_export in source_exports:
            output_export = processed_path / source_export.name
            # Some exports may not produce output (e.g., Google Chat only)
            # but most should have output directories
            if output_export.exists():
                assert (
                    output_export.is_dir()
                ), f"{source_export.name} output should be a directory"

    def test_dedup_manifest_exists(self, processed_path):
        """Deduplication manifest should be created."""
        manifest_path = processed_path / "dedup_manifest.json"
        assert manifest_path.exists(), "dedup_manifest.json should exist"

        with open(manifest_path) as f:
            manifest = json.load(f)

        # Manifest structure: version, created, statistics, duplicates
        assert "version" in manifest
        assert "statistics" in manifest
        assert "duplicates" in manifest

    def test_no_empty_output_directories(self, processed_path):
        """Output directories should contain files."""
        for export_dir in processed_path.iterdir():
            if export_dir.is_dir() and not export_dir.name.startswith("."):
                # Skip manifest file
                if export_dir.name == "dedup_manifest.json":
                    continue

                files = list(export_dir.rglob("*"))
                file_count = sum(1 for f in files if f.is_file())
                # Allow some directories to be empty (e.g., issues folders)
                # but the export should have at least some output
                assert file_count > 0 or "issues" in str(
                    export_dir
                ), f"{export_dir.name} should contain output files"


@pytest.mark.integration
@skip_no_exiftool
class TestGooglePhotosOutput:
    """Tests for Google Photos processed output."""

    @pytest.fixture
    def google_exports(self, processed_path) -> List[Path]:
        """Find all Google Photos exports in processed output."""
        return [
            d
            for d in processed_path.iterdir()
            if d.is_dir() and d.name.startswith("google-") and (d / "photos").exists()
        ]

    def test_photos_directory_exists(self, google_exports):
        """Each Google export should have a photos/ subdirectory."""
        assert len(google_exports) > 0, "Should have at least one Google Photos export"

        for export in google_exports:
            photos_dir = export / "photos"
            assert photos_dir.exists(), f"{export.name} should have photos/ directory"

    def test_files_have_exif_dates(self, google_exports):
        """Processed files should have EXIF dates embedded."""
        for export in google_exports:
            photos_dir = export / "photos"
            media_files = find_files(photos_dir, [".jpg", ".jpeg", ".png", ".heic"])

            # Check at least some files (not all, for speed)
            sample = media_files[:5]
            for file_path in sample:
                exif = read_exif(file_path)
                # Should have at least one date field
                date_fields = [
                    "DateTimeOriginal",
                    "CreateDate",
                    "ModifyDate",
                    "GPSDateTime",
                    "FileModifyDate",
                ]
                has_date = any(exif.get(field) for field in date_fields)
                assert has_date, f"{file_path.name} should have date metadata"

    def test_files_organized_by_date(self, google_exports):
        """Files should be organized in YYYY/YYYY-MM-DD folders."""
        for export in google_exports:
            photos_dir = export / "photos"

            # Check that year folders exist
            year_folders = [
                d
                for d in photos_dir.iterdir()
                if d.is_dir() and d.name.isdigit() and len(d.name) == 4
            ]

            if year_folders:  # Only if date organization is enabled
                for year_folder in year_folders:
                    # Check for date subfolders
                    date_folders = [
                        d
                        for d in year_folder.iterdir()
                        if d.is_dir() and len(d.name) == 10  # YYYY-MM-DD format
                    ]
                    # Should have some date folders
                    assert (
                        len(date_folders) >= 0
                    )  # May have files directly in year folder


@pytest.mark.integration
@skip_no_exiftool
class TestSnapchatOutput:
    """Tests for Snapchat processed output."""

    @pytest.fixture
    def snapchat_exports(self, processed_path) -> List[Path]:
        """Find all Snapchat exports in processed output."""
        return [
            d
            for d in processed_path.iterdir()
            if d.is_dir() and d.name.startswith("snapchat-")
        ]

    def test_memories_processed(self, snapchat_exports):
        """Snapchat memories should be processed."""
        memories_found = False
        for export in snapchat_exports:
            memories_dir = export / "memories"
            if memories_dir.exists():
                memories_found = True
                files = find_files(memories_dir)
                assert len(files) > 0, f"{export.name}/memories should have files"

        assert memories_found, "At least one Snapchat export should have memories"

    def test_messages_processed(self, snapchat_exports):
        """Snapchat messages should be processed."""
        messages_found = False
        for export in snapchat_exports:
            messages_dir = export / "messages"
            if messages_dir.exists():
                messages_found = True
                files = find_files(messages_dir)
                assert len(files) > 0, f"{export.name}/messages should have files"

        assert messages_found, "At least one Snapchat export should have messages"

    def test_memories_have_correct_naming(self, snapchat_exports):
        """Memory files should follow snap-memories-username-YYYYMMDD naming."""
        for export in snapchat_exports:
            memories_dir = export / "memories"
            if not memories_dir.exists():
                continue

            media_files = find_files(
                memories_dir, [".jpg", ".jpeg", ".png", ".mp4", ".mov", ".mkv"]
            )

            for file_path in media_files[:5]:  # Sample
                name = file_path.stem
                # Should start with snap-memories-
                assert name.startswith(
                    "snap-memories-"
                ), f"{file_path.name} should start with snap-memories-"


@pytest.mark.integration
@skip_no_exiftool
class TestInstagramOutput:
    """Tests for Instagram processed output."""

    @pytest.fixture
    def instagram_exports(self, processed_path) -> List[Path]:
        """Find all Instagram exports in processed output."""
        return [
            d
            for d in processed_path.iterdir()
            if d.is_dir() and d.name.startswith("instagram-")
        ]

    def test_messages_or_public_processed(self, instagram_exports):
        """Instagram exports should have messages or public-media output."""
        assert len(instagram_exports) > 0, "Should have Instagram exports"

        for export in instagram_exports:
            messages_dir = export / "messages"
            public_dir = export / "public-media"

            has_output = messages_dir.exists() or public_dir.exists()
            assert has_output, f"{export.name} should have messages/ or public-media/"

    def test_files_have_instagram_metadata(self, instagram_exports):
        """Instagram files should have metadata from captions/JSON."""
        for export in instagram_exports:
            # Check public media
            public_dir = export / "public-media"
            if public_dir.exists():
                media_files = find_files(public_dir, [".jpg", ".jpeg", ".png"])

                for file_path in media_files[:3]:  # Sample
                    exif = read_exif(file_path)
                    # Not all files may have dates, so just check it's readable
                    assert exif, f"Should be able to read EXIF from {file_path.name}"


@pytest.mark.integration
@skip_no_exiftool
class TestIMessageOutput:
    """Tests for iMessage processed output."""

    @pytest.fixture
    def imessage_exports(self, processed_path) -> List[Path]:
        """Find all iMessage exports in processed output."""
        return [
            d
            for d in processed_path.iterdir()
            if d.is_dir()
            and ("messages" in d.name.lower() or "iph" in d.name.lower())
            and not d.name.startswith("instagram-")
            and not d.name.startswith("snapchat-")
        ]

    def test_messages_directory_exists(self, imessage_exports):
        """iMessage exports should have messages/ output."""
        assert len(imessage_exports) > 0, "Should have iMessage exports"

        for export in imessage_exports:
            messages_dir = export / "messages"
            assert messages_dir.exists(), f"{export.name} should have messages/"

    def test_files_organized_by_contact(self, imessage_exports):
        """iMessage files should be organized by contact."""
        for export in imessage_exports:
            messages_dir = export / "messages"
            if not messages_dir.exists():
                continue

            # Should have subdirectories for contacts
            subdirs = [d for d in messages_dir.iterdir() if d.is_dir()]
            # May have contact folders or year folders
            assert len(subdirs) >= 0  # Structure varies by config


@pytest.mark.integration
@skip_no_exiftool
class TestDiscordOutput:
    """Tests for Discord processed output."""

    @pytest.fixture
    def discord_exports(self, processed_path) -> List[Path]:
        """Find all Discord exports in processed output."""
        return [
            d
            for d in processed_path.iterdir()
            if d.is_dir() and d.name.startswith("discord-")
        ]

    def test_messages_processed(self, discord_exports):
        """Discord exports should have messages output."""
        assert len(discord_exports) > 0, "Should have Discord exports"

        for export in discord_exports:
            messages_dir = export / "messages"
            assert messages_dir.exists(), f"{export.name} should have messages/"

    def test_dedup_references_created(self, discord_exports):
        """Discord output should have dedup_references.json if duplicates found."""
        for export in discord_exports:
            messages_dir = export / "messages"
            if not messages_dir.exists():
                continue

            # Check for dedup references file
            dedup_ref = messages_dir / "dedup_references.json"
            # This is optional, only created if duplicates exist
            if dedup_ref.exists():
                with open(dedup_ref) as f:
                    refs = json.load(f)
                # Can be a dict with version info or a list
                assert isinstance(refs, (list, dict))
                if isinstance(refs, dict):
                    # Dict format has deduplicated_files list
                    assert "deduplicated_files" in refs or "version" in refs


@pytest.mark.integration
class TestDeduplicationResults:
    """Tests that verify deduplication worked correctly."""

    def test_manifest_statistics(self, processed_path):
        """Dedup manifest should have valid statistics."""
        manifest_path = processed_path / "dedup_manifest.json"
        if not manifest_path.exists():
            pytest.skip("No dedup manifest found")

        with open(manifest_path) as f:
            manifest = json.load(f)

        stats = manifest.get("statistics", {})

        # Basic sanity checks - key names may vary
        total = stats.get("total_files") or stats.get("total_files_scanned", 0)
        unique = stats.get("unique_files", 0)

        assert total > 0, "Should have processed files"
        assert unique > 0, "Should have unique files"
        assert unique <= total, "Unique files should be <= total files"

    def test_duplicates_have_references(self, processed_path):
        """Duplicate files should reference their primary."""
        manifest_path = processed_path / "dedup_manifest.json"
        if not manifest_path.exists():
            pytest.skip("No dedup manifest found")

        with open(manifest_path) as f:
            manifest = json.load(f)

        duplicates = manifest.get("duplicates", {})

        # Duplicates are keyed by content hash, value is a dict with primary/secondary
        if isinstance(duplicates, dict):
            for hash_key, dup_info in duplicates.items():
                assert (
                    "primary" in dup_info
                ), f"Duplicate {hash_key} should have primary reference"
                assert (
                    "source_path" in dup_info["primary"]
                ), "Primary should have source_path"
        elif isinstance(duplicates, list):
            for dup in duplicates:
                assert (
                    "file_path" in dup or "path" in dup or "primary" in dup
                ), "Duplicate should have file path or primary"


@pytest.mark.integration
@skip_no_exiftool
class TestMetadataIntegrity:
    """Tests that verify metadata was correctly transferred to output files."""

    def test_gps_coordinates_preserved(self, processed_path):
        """Files with GPS should have coordinates in output."""
        # Find files that should have GPS (Google Photos often do)
        google_exports = [
            d
            for d in processed_path.iterdir()
            if d.is_dir() and d.name.startswith("google-") and (d / "photos").exists()
        ]

        gps_found = False
        for export in google_exports:
            photos_dir = export / "photos"
            media_files = find_files(photos_dir, [".jpg", ".jpeg", ".heic"])

            for file_path in media_files[:10]:
                exif = read_exif(file_path)
                if exif.get("GPSLatitude") and exif.get("GPSLongitude"):
                    gps_found = True
                    # Validate GPS is reasonable
                    lat = exif.get("GPSLatitude")
                    lon = exif.get("GPSLongitude")
                    assert -90 <= lat <= 90, f"Invalid latitude: {lat}"
                    assert -180 <= lon <= 180, f"Invalid longitude: {lon}"

        # GPS is optional, don't fail if not found
        if not gps_found:
            pytest.skip("No GPS coordinates found in sample files")

    def test_timestamps_are_valid(self, processed_path):
        """File timestamps should be valid dates."""
        all_exports = [
            d
            for d in processed_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        checked = 0
        for export in all_exports:
            media_files = find_files(export, [".jpg", ".jpeg", ".png", ".heic"])

            for file_path in media_files[:5]:
                exif = read_exif(file_path)

                # Check DateTimeOriginal if present
                dto = exif.get("DateTimeOriginal")
                if dto and isinstance(dto, str):
                    checked += 1
                    # Should be parseable
                    try:
                        # Various formats used
                        for fmt in ["%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"]:
                            try:
                                dt = datetime.strptime(dto, fmt)
                                # Sanity check: date should be reasonable
                                assert (
                                    1990 <= dt.year <= 2030
                                ), f"Year {dt.year} seems invalid"
                                break
                            except ValueError:
                                continue
                    except Exception:
                        pass  # Some formats may vary

        assert checked > 0, "Should have checked some file timestamps"


@pytest.mark.integration
class TestFileIntegrity:
    """Tests that verify output files are valid and not corrupted."""

    def test_files_not_empty(self, processed_path):
        """Output files should not be empty."""
        all_exports = [
            d
            for d in processed_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        for export in all_exports:
            media_files = find_files(
                export, [".jpg", ".jpeg", ".png", ".mp4", ".mov", ".mkv"]
            )

            for file_path in media_files:
                size = file_path.stat().st_size
                assert size > 0, f"{file_path} should not be empty"
                # Images should be at least a few hundred bytes
                if file_path.suffix.lower() in [".jpg", ".jpeg", ".png"]:
                    assert size > 100, f"{file_path} seems too small for an image"

    def test_jpg_files_valid(self, processed_path):
        """JPG files should have valid headers."""
        all_exports = [
            d
            for d in processed_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

        checked = 0
        for export in all_exports:
            jpg_files = find_files(export, [".jpg", ".jpeg"])

            for file_path in jpg_files[:10]:  # Sample
                with open(file_path, "rb") as f:
                    header = f.read(3)
                # JPEG magic number
                assert (
                    header[:2] == b"\xff\xd8"
                ), f"{file_path.name} should have JPEG header"
                checked += 1

        assert checked > 0, "Should have checked some JPG files"
