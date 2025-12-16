"""
Google Photos Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Google Photos media files, embedding metadata, and updating filesystem timestamps.
"""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from common.dependency_checker import check_exiftool, print_exiftool_error
from common.exiftool_batch import (
    batch_validate_exif,
    batch_rebuild_exif,
    batch_read_existing_metadata,
    batch_write_metadata_google_photos,
)
from common.processing import (
    process_batches_parallel,
    print_processing_summary,
    temp_processing_directory,
)
from common.utils import (
    default_worker_count,
    extract_username_from_export_dir,
    is_preprocessed_directory,
    update_file_timestamps,
)
from processors.base import ProcessorBase
from processors.google_photos.preprocess import GooglePhotosPreprocessor

# Set up logging
logger = logging.getLogger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory

    Detection criteria for Google Photos:
    - Directory contains 'Google Photos/' subdirectory
    - 'Google Photos/' contains album folders (named directories)

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is a Google Photos export, False otherwise
    """
    try:
        photos_dir = input_path / "Google Photos"

        # Check if Google Photos directory exists
        if not photos_dir.exists() or not photos_dir.is_dir():
            return False

        # Check for album folders within Google Photos
        # Need at least one subdirectory (album)
        albums = [d for d in photos_dir.iterdir() if d.is_dir()]

        return len(albums) > 0

    except Exception as e:
        logger.debug(f"Detection failed for Google Photos: {e}")
        return False


class GooglePhotosProcessor(ProcessorBase):
    """Processor for Google Photos exports"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input"""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name"""
        return "Google Photos"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first)"""
        return 50  # Medium priority

    @staticmethod
    def process(input_dir: str, output_dir: Optional[str] = None, **kwargs) -> bool:
        """Process Google Photos export

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            **kwargs: Additional arguments (verbose, workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Check for required dependencies
        if not check_exiftool():
            print_exiftool_error()
            return False

        try:
            # Choose content-type subdirectory under provided base output
            if output_dir:
                processor_output = str(Path(output_dir) / "photos")
            else:
                processor_output = kwargs.get("output", "final_googlephotos/photos")

            # Call processing logic directly
            process_logic(
                input_dir=input_dir,
                output_dir=processor_output,
                temp_dir=kwargs.get("temp_dir", "../pre"),
                verbose=kwargs.get("verbose", False),
                workers=kwargs.get("workers"),
            )
            return True

        except Exception as e:
            logger.error(f"Error in GooglePhotosProcessor: {e}")
            return False


def get_processor():
    """Return processor class for auto-discovery

    This function is called by the unified memoria.py during
    automatic processor discovery.

    Returns:
        GooglePhotosProcessor class (not instance, as it uses static methods)
    """
    return GooglePhotosProcessor


# ============================================================================
# Helper Functions
# ============================================================================


def generate_base_filename(media_data, export_username):
    """Generate base filename without extension or sequence

    Format: gphotos-{exportUsername}-YYYYMMDD

    Args:
        media_data: Dict containing media metadata
        export_username: Username extracted from metadata

    Returns:
        str: Base filename without extension or sequence
    """
    # Parse date from media_data
    # Format: "2018-01-10T00:30:37Z" (ISO 8601)
    timestamp_str = media_data.get("capture_timestamp")

    # Handle None timestamp
    if timestamp_str is None:
        # Use a fallback date for media without timestamps
        date_key = "00000000"
    else:
        # Parse ISO 8601 format
        date_obj = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        date_key = date_obj.strftime("%Y%m%d")

    # Generate base filename (without extension or sequence)
    base_filename = f"gphotos-{export_username}-{date_key}"
    return base_filename


def get_live_photo_group_key(media_data):
    """Generate a key to identify files that are part of the same live photo

    Live photos have the same timestamp and original filename

    Args:
        media_data: Dict containing media metadata

    Returns:
        tuple: Key identifying the live photo group (timestamp, original_filename)
    """
    timestamp = media_data.get("capture_timestamp", "")
    original_filename = media_data.get("original_filename", "")
    # Use the filename without extension as the group identifier
    if original_filename:
        base_name = os.path.splitext(original_filename)[0]
    else:
        base_name = ""
    return (timestamp, base_name)


def process_media_batch(batch_args):
    """Process a batch of media files (worker function for multiprocessing)

    Args:
        batch_args: List of tuples, each containing (media_file, media_data,
                    album_name, output_filename, media_dir, output_dir, export_username)

    Returns:
        List of (success, failed, exif_rebuilt) tuples
    """
    # Phase 1: Copy all files
    file_paths = []
    file_info = []

    for args_tuple in batch_args:
        (
            media_file,
            media_data,
            album_name,
            output_filename,
            media_dir,
            output_dir,
            export_username,
        ) = args_tuple

        media_path = os.path.join(media_dir, media_file)
        output_path = os.path.join(output_dir, output_filename)

        if not os.path.exists(media_path):
            logger.warning(f"Media file not found: {media_path}")
            continue

        try:
            shutil.copy2(media_path, output_path)
            file_paths.append(output_path)
            file_info.append((output_path, media_data, album_name, export_username))
        except Exception as e:
            logger.error(f"Failed to copy {media_file}: {e}")

    if not file_paths:
        return [(False, True, False)] * len(batch_args)

    # Phase 2: Batch validate and rebuild
    corrupted_files = batch_validate_exif(file_paths)
    if corrupted_files:
        batch_rebuild_exif(list(corrupted_files))

    # Phase 3: Batch read metadata, then batch write
    existing_metadata_map = batch_read_existing_metadata(file_paths)
    batch_write_metadata_google_photos(file_info, existing_metadata_map)

    # Phase 4: Update timestamps and compile results
    results = []
    for output_path, media_data, _, _ in file_info:
        # Google Photos uses ISO 8601 format: "2018-01-10T00:30:37Z"
        timestamp_str = media_data.get("capture_timestamp")
        if timestamp_str:
            update_file_timestamps(output_path, timestamp_str, "%Y-%m-%dT%H:%M:%S")
        exif_rebuilt = output_path in corrupted_files
        results.append((True, False, exif_rebuilt))

    # Add failed results for files that didn't get copied
    while len(results) < len(batch_args):
        results.append((False, True, False))

    return results


def process_logic(
    input_dir,
    output_dir="final_googlephotos",
    temp_dir="../pre",
    verbose=False,
    workers=None,
):
    """Core processing logic for Google Photos exports

    Args:
        input_dir: Input directory (raw export or preprocessed)
        output_dir: Output directory for processed files
        temp_dir: Directory for temporary preprocessing files
        verbose: Enable verbose logging
        workers: Number of parallel workers (None = auto-detect)
    """
    # Logging is configured by the main process
    # Verbose mode enables detailed logging to file

    # Check for exiftool
    if not check_exiftool():
        print_exiftool_error()
        return

    logger.info("Google Photos Media Processor")
    logger.info("=" * 50)

    # Check if input is already preprocessed
    if is_preprocessed_directory(input_dir):
        logger.info(f"Input directory is already preprocessed: {input_dir}")
        _process_working_directory(input_dir, output_dir, workers)
    else:
        logger.info(f"Input directory is raw export: {input_dir}")
        logger.info("Running preprocessing...")

        # Use context manager for temp directory with automatic cleanup
        with temp_processing_directory(temp_dir, "gphotos") as temp_dir_path:
            logger.info(f"Preprocessing to: {temp_dir_path}")

            # Run preprocessing with final output directory for failure tracking
            final_output_path = Path(output_dir)
            preprocessor = GooglePhotosPreprocessor(
                export_path=Path(input_dir),
                output_dir=temp_dir_path,
                workers=workers,
                final_output_dir=final_output_path,
            )
            preprocessor.process()

            logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
            _process_working_directory(str(temp_dir_path), output_dir, workers)


def _process_working_directory(working_dir, output_dir, workers):
    """Process a working directory (preprocessed export)

    Args:
        working_dir: Path to preprocessed directory with metadata.json and media/
        output_dir: Output directory for processed files
        workers: Number of parallel workers
    """
    # Configuration
    metadata_file = os.path.join(working_dir, "metadata.json")
    media_dir = os.path.join(working_dir, "media")

    # Check for metadata file (should exist after preprocessing or if already preprocessed)
    if not os.path.exists(metadata_file):
        logger.error(f"Metadata file not found: {metadata_file}")
        return

    # Check for media directory
    if not os.path.exists(media_dir):
        logger.error(f"Media directory not found: {media_dir}")
        return

    # Load metadata
    logger.info(f"Loading metadata from {metadata_file}...")
    with open(metadata_file, "r", encoding="utf-8") as f:
        metadata_json = json.load(f)

    export_info = metadata_json.get("export_info", {})
    media_files = metadata_json.get("media_files", [])

    # Count total media files
    total_media_files = len(media_files)

    print(f"Found {total_media_files} media files to process")
    logger.info(f"Found {total_media_files} media files to process")

    # Extract export username from export_name
    # Pattern: "google-{username}-{date}" (date can be YYYYMMDD or YYYY-MM-DD)
    export_name = export_info.get("export_name", "")
    export_username = extract_username_from_export_dir(export_name, "google")

    logger.info(f"Export username: {export_username}")

    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    logger.debug(f"Using {num_workers} parallel workers")

    # Pre-generate all output filenames to avoid race conditions
    logger.debug("Pre-generating filenames...")

    # First pass: Analyze all files to determine duplicates and group live photos
    # Map: base_filename -> list of (media_data, file_ext)
    base_filename_map = {}
    # Map: live_photo_group_key -> list of (media_data, file_ext, base_filename)
    live_photo_groups = {}

    for media_data in media_files:
        media_file = media_data.get("filename")

        if not media_file:
            logger.warning("Skipping media file without filename")
            continue

        # Get file extension
        file_ext = os.path.splitext(media_file)[1].lower()

        # Generate base filename
        base_filename = generate_base_filename(media_data, export_username)

        # Track all files with this base filename
        if base_filename not in base_filename_map:
            base_filename_map[base_filename] = []
        base_filename_map[base_filename].append((media_data, file_ext))

        # Group live photos together
        live_photo_key = get_live_photo_group_key(media_data)
        if live_photo_key not in live_photo_groups:
            live_photo_groups[live_photo_key] = []
        live_photo_groups[live_photo_key].append((media_data, file_ext, base_filename))

    # Second pass: Generate filenames with sequences only where needed
    # Map: base_filename -> current sequence number for that base
    sequence_counters = {}
    # Track which live photo groups have been assigned sequences
    live_photo_sequences = {}

    processing_tasks = []

    for media_data in media_files:
        media_file = media_data.get("filename")

        if not media_file:
            continue

        # Get file extension
        file_ext = os.path.splitext(media_file)[1].lower()

        # Generate base filename
        base_filename = generate_base_filename(media_data, export_username)

        # Check if this file is part of a live photo group
        live_photo_key = get_live_photo_group_key(media_data)

        # Determine if we need a sequence number
        files_with_same_base = base_filename_map[base_filename]
        needs_sequence = len(files_with_same_base) > 1

        # For live photos, all files in the group should get the same sequence
        if live_photo_key in live_photo_sequences:
            # This live photo group already has a sequence assigned
            sequence = live_photo_sequences[live_photo_key]
        elif needs_sequence:
            # Initialize or increment sequence for this base filename
            if base_filename not in sequence_counters:
                sequence_counters[base_filename] = 0
            sequence_counters[base_filename] += 1
            sequence = sequence_counters[base_filename]

            # If this is a live photo, record the sequence for all files in the group
            live_photo_group = live_photo_groups[live_photo_key]
            if len(live_photo_group) > 1:
                live_photo_sequences[live_photo_key] = sequence
        else:
            sequence = None

        # Generate final filename
        if sequence is not None:
            output_filename = f"{base_filename}_{sequence}{file_ext}"
        else:
            output_filename = f"{base_filename}{file_ext}"

        # Get primary album name for metadata (use first album in list)
        albums_list = media_data.get("albums", [])
        album_name = albums_list[0] if albums_list else "unknown"

        # Create task tuple for worker
        processing_tasks.append(
            (
                media_file,
                media_data,
                album_name,
                output_filename,
                media_dir,
                output_dir,
                export_username,
            )
        )

    # Process media files in parallel
    print(f"\nProcessing media files to {output_dir}/")
    logger.info("=" * 50)

    # Use shared batch processing utility
    results = process_batches_parallel(
        tasks=processing_tasks,
        worker_fn=process_media_batch,
        num_workers=num_workers,
        batch_size=100,
        description="Creating files",
    )

    # Aggregate results
    success_count = 0
    failed_count = 0
    exif_rebuilt_count = 0

    for success, failed, exif_rebuilt in results:
        if success:
            success_count += 1
        if failed:
            failed_count += 1
        if exif_rebuilt:
            exif_rebuilt_count += 1

    # Print summary using shared utility
    print_processing_summary(
        success=success_count,
        failed=failed_count,
        total=len(processing_tasks),
        output_dir=output_dir,
        extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
    )
