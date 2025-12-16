"""
Snapchat Memories Processor

This processor is designed to be used through memoria.py.
It handles adding overlays to videos and embedding metadata into all Snapchat Memories files.
"""

import json
import logging
import multiprocessing
import os
import shutil
from datetime import datetime
from pathlib import Path

from common.dependency_checker import (
    check_exiftool,
    check_ffmpeg,
    print_exiftool_error,
    print_ffmpeg_error,
)
from common.exiftool_batch import (
    batch_validate_exif,
    batch_rebuild_exif,
    batch_read_existing_metadata,
    batch_write_metadata_snapchat_memories,
)
from common.failure_tracker import FailureTracker
from common.filter_banned_files import BannedFilesFilter
from common.overlay import (
    create_image_with_overlay,
    create_video_with_overlay,
)
from common.processing import print_processing_summary
from common.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    default_worker_count,
    extract_username_from_export_dir,
    get_media_type,
    update_file_timestamps,
)
from processors.base import ProcessorBase

# Set up logging
logger = logging.getLogger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory

    Detection criteria for Snapchat Memories:
    - Directory contains 'media/' subdirectory
    - Directory contains 'overlays/' subdirectory
    - Directory contains 'metadata.json' file
    - metadata.json contains array of memory objects with required fields
    - Consolidated: Directory contains 'memories/' subdirectory with above structures

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is a Snapchat Memories export, False otherwise
    """
    try:
        # Helper function to check if a path contains valid memories structure
        def check_memories_structure(base_path: Path) -> bool:
            media_dir = base_path / "media"
            overlays_dir = base_path / "overlays"
            metadata_file = base_path / "metadata.json"

            # Check if required directories and file exist
            if not media_dir.exists() or not media_dir.is_dir():
                return False

            if not overlays_dir.exists() or not overlays_dir.is_dir():
                return False

            if not metadata_file.exists() or not metadata_file.is_file():
                return False

            # Validate metadata.json structure
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                # Should be a list/array
                if not isinstance(metadata, list):
                    return False

                # Should have at least one memory
                if len(metadata) == 0:
                    return False

                # Check first memory has expected fields
                first_memory = metadata[0]
                required_fields = ["date", "media_type", "media_filename"]

                for field in required_fields:
                    if field not in first_memory:
                        return False

                # This looks like a Snapchat Memories export
                return True

            except (json.JSONDecodeError, KeyError, IndexError):
                return False

        # Check for consolidated structure first (memories/ subdirectory)
        memories_subdir = input_path / "memories"
        if memories_subdir.exists() and memories_subdir.is_dir():
            if check_memories_structure(memories_subdir):
                return True

        # Check for direct structure (old format)
        if check_memories_structure(input_path):
            return True

        return False

    except Exception as e:
        logger.debug(f"Detection failed for Snapchat Memories: {e}")
        return False


class SnapchatMemoriesProcessor(ProcessorBase):
    """Processor for Snapchat Memories exports"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input"""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name"""
        return "Snapchat Memories"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first)"""
        return 80  # High priority - specific structure

    @staticmethod
    def process(input_dir: str, output_dir: str = None, **kwargs) -> bool:
        """Process Snapchat Memories export

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            **kwargs: Additional arguments (verbose, workers)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Check for required dependencies
        if not check_exiftool():
            print_exiftool_error()
            return False

        if not check_ffmpeg():
            print_ffmpeg_error()
            return False

        try:
            # Check if this is a consolidated export with memories/ subdirectory
            input_path = Path(input_dir)
            memories_subdir = input_path / "memories"

            # Use memories/ subdirectory if it exists, otherwise use root
            export_username_override = None
            if memories_subdir.exists() and memories_subdir.is_dir():
                # Verify it's actually a memories export in the subdirectory
                media_dir = memories_subdir / "media"
                overlays_dir = memories_subdir / "overlays"
                metadata_file = memories_subdir / "metadata.json"
                if (
                    media_dir.exists()
                    and overlays_dir.exists()
                    and metadata_file.exists()
                ):
                    actual_input_dir = str(memories_subdir)
                    logger.info(
                        f"Detected consolidated export structure, using: {actual_input_dir}"
                    )
                    # Extract username from parent directory for consolidated exports
                    export_username_override = extract_username_from_export_dir(
                        input_dir, "snapchat"
                    )
                else:
                    actual_input_dir = input_dir
            else:
                actual_input_dir = input_dir

            # Create memories subdirectory under output
            if output_dir:
                processor_output = str(Path(output_dir) / "memories")
            else:
                processor_output = kwargs.get("output", "final_snapmemories/memories")

            # Call processing logic directly
            process_logic(
                input_dir=actual_input_dir,
                output_dir=processor_output,
                verbose=kwargs.get("verbose", False),
                workers=kwargs.get("workers"),
                export_username_override=export_username_override,
            )
            return True

        except Exception as e:
            logger.error(f"Error in SnapchatMemoriesProcessor: {e}")
            return False


def get_processor():
    """Return processor class for auto-discovery

    This function is called by the unified memoria.py during
    automatic processor discovery.

    Returns:
        SnapchatMemoriesProcessor class (not instance, as it uses static methods)
    """
    return SnapchatMemoriesProcessor


# ============================================================================
# Helper Functions
# ============================================================================


def check_pillow():
    """Check if PIL/Pillow is installed"""
    try:
        __import__("PIL")
        return True
    except ImportError:
        return False


def generate_unique_filename(memory_data, export_username, extension, used_filenames):
    """Generate a unique filename for a processed memory

    Format: snap-memories-{exportUsername}-YYYYMMDD.extension
    If duplicate: snap-memories-{exportUsername}-YYYYMMDD_N.extension

    Args:
        memory_data: Dict containing memory metadata
        export_username: Username extracted from input directory
        extension: File extension (including the dot)
        used_filenames: Set tracking already-used filenames

    Returns:
        str: Generated filename
    """
    # Parse date from memory_data
    # Format: "2021-01-04 23:08:30 UTC"
    date_str = memory_data["date"]
    date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S UTC")
    date_key = date_obj.strftime("%Y%m%d")

    # Generate base filename without sequence
    base_filename = f"snap-memories-{export_username}-{date_key}{extension}"

    # Check if base filename is already used
    if base_filename not in used_filenames:
        used_filenames.add(base_filename)
        return base_filename

    # If duplicate, find next available sequence number
    sequence = 2
    while True:
        filename = f"snap-memories-{export_username}-{date_key}_{sequence}{extension}"
        if filename not in used_filenames:
            used_filenames.add(filename)
            return filename
        sequence += 1


def _update_memory_timestamps(file_path, memory_data):
    """Update filesystem creation and modification timestamps to match memory date

    Wrapper around shared update_file_timestamps that handles Snapchat Memories
    date format: "2021-01-04 23:08:30 UTC"

    Args:
        file_path: Path to the file
        memory_data: Dict containing memory metadata with 'date' field

    Returns:
        bool: True if successful, False otherwise
    """
    date_str = memory_data.get("date")
    if not date_str:
        return False

    # Snapchat Memories uses format: "2021-01-04 23:08:30 UTC"
    return update_file_timestamps(file_path, date_str, "%Y-%m-%d %H:%M:%S")


def create_memory_file(args_tuple):
    """Create output file with overlay if needed (Phase 1 worker function)

    Args:
        args_tuple: Tuple containing (memory, output_filename, raw_dir, output_dir, export_username)

    Returns:
        Tuple of (success: bool, output_path: str or None, is_mkv: bool, memory: dict,
                  export_username: str, failure_reason: str or None)
    """
    memory, output_filename, raw_dir, output_dir, export_username = args_tuple

    media_filename = memory["media_filename"]
    overlay_filename = memory.get("overlay_filename")

    media_dir = os.path.join(raw_dir, "media")
    overlays_dir = os.path.join(raw_dir, "overlays")
    media_path = os.path.join(media_dir, media_filename)

    # Convert to Path objects once to avoid repeated conversions
    media_path_obj = Path(media_path)

    if not os.path.exists(media_path):
        logger.warning(f"Media file not found: {media_path}")
        return (False, None, False, memory, export_username, media_path)

    # Determine if this is a video or image
    is_video = get_media_type(media_filename) == "video"
    is_image = get_media_type(media_filename) == "image"

    # Process based on file type and whether overlay exists
    if overlay_filename:
        overlay_path = os.path.join(overlays_dir, overlay_filename)
        overlay_path_obj = Path(overlay_path)

        if not os.path.exists(overlay_path):
            logger.warning(f"Overlay file not found: {overlay_path}")
            # Copy file without overlay
            output_path = os.path.join(output_dir, output_filename)
            shutil.copy2(media_path, output_path)
            _update_memory_timestamps(output_path, memory)
            return (True, output_path, False, memory, export_username, None)

        if is_video:
            # Video with overlay - create multi-track MKV
            # Change extension to .mkv in output filename
            output_filename_mkv = os.path.splitext(output_filename)[0] + ".mkv"
            output_path = os.path.join(output_dir, output_filename_mkv)
            output_path_obj = Path(output_path)

            # Use the imported function with metadata (embeds metadata during creation)
            if create_video_with_overlay(
                media_path_obj,
                overlay_path_obj,
                output_path_obj,
                memory,
                export_username=export_username,
            ):
                # Update filesystem timestamps
                _update_memory_timestamps(output_path, memory)
                # MKV files already have metadata embedded, mark as such
                return (True, output_path, True, memory, export_username, None)
            else:
                # Multi-track creation failed, copy original video with original extension
                fallback_path = os.path.join(output_dir, output_filename)
                shutil.copy2(media_path, fallback_path)
                _update_memory_timestamps(fallback_path, memory)
                # Fallback file needs batch EXIF processing
                return (True, fallback_path, False, memory, export_username, None)

        elif is_image:
            # Image with overlay - composite
            output_path = os.path.join(output_dir, output_filename)
            output_path_obj = Path(output_path)

            # Use the imported function
            if create_image_with_overlay(
                media_path_obj, overlay_path_obj, output_path_obj
            ):
                _update_memory_timestamps(output_path, memory)
                # Image needs batch EXIF processing
                return (True, output_path, False, memory, export_username, None)
            else:
                # Image processing failed, copy original
                shutil.copy2(media_path, output_path)
                _update_memory_timestamps(output_path, memory)
                return (True, output_path, False, memory, export_username, None)
        else:
            # Unknown file type with overlay - just copy
            output_path = os.path.join(output_dir, output_filename)
            shutil.copy2(media_path, output_path)
            _update_memory_timestamps(output_path, memory)
            return (True, output_path, False, memory, export_username, None)
    else:
        # No overlay - just copy
        output_path = os.path.join(output_dir, output_filename)
        shutil.copy2(media_path, output_path)
        _update_memory_timestamps(output_path, memory)
        # File needs batch EXIF processing
        return (True, output_path, False, memory, export_username, None)


def process_logic(
    input_dir="raw_downloads",
    output_dir="downloaded_memories",
    verbose=False,
    workers=None,
    export_username_override=None,
):
    """Core processing logic for Snapchat Memories exports

    Args:
        input_dir: Input directory containing media/, overlays/, and metadata.json
        output_dir: Output directory for processed files
        verbose: Enable verbose logging
        workers: Number of parallel workers (None = auto-detect)
        export_username_override: Optional override for export username (used for consolidated exports)
    """
    # Logging is configured by the main process
    # Verbose mode enables detailed logging to file

    # Configuration
    raw_dir = input_dir
    metadata_file = os.path.join(raw_dir, "metadata.json")

    # Check for exiftool
    if not check_exiftool():
        print_exiftool_error()
        return

    # Check for ffmpeg
    if not check_ffmpeg():
        print_ffmpeg_error()
        return

    # Check for Pillow (for image overlay processing)
    if not check_pillow():
        logger.error("PIL/Pillow is not installed")
        logger.error("Please install Pillow:")
        logger.error("  pip install Pillow")
        return

    logger.info("Snapchat Memories Downloader - Step 2: Process")
    logger.info("=" * 50)

    # Extract export username from input directory (or use override for consolidated exports)
    if export_username_override:
        export_username = export_username_override
        logger.info(f"Export username (from parent): {export_username}")
    else:
        export_username = extract_username_from_export_dir(raw_dir, "snapchat")
        logger.info(f"Export username: {export_username}")

    # Check for metadata file
    if not os.path.exists(metadata_file):
        logger.error(f"Metadata file not found: {metadata_file}")
        logger.error("Please run download_memories.py first")
        return

    # Load metadata
    logger.info(f"Loading metadata from {metadata_file}...")
    with open(metadata_file, "r", encoding="utf-8") as f:
        memories = json.load(f)
    logger.info(f"Found {len(memories)} memories to process")

    # Initialize banned files filter
    banned_filter = BannedFilesFilter()
    logger.debug(f"Banned file patterns: {', '.join(banned_filter.get_patterns())}")

    # Initialize failure tracker
    failure_tracker = FailureTracker(
        processor_name="Snapchat Memories",
        export_directory=raw_dir,
    )

    # Track orphaned media files (files in media/ not referenced in metadata)
    media_dir = Path(raw_dir) / "media"
    if media_dir.exists():
        # Build set of all media filenames referenced in metadata
        referenced_files = {memory["media_filename"] for memory in memories}

        # Scan media directory for all files
        for media_file in media_dir.iterdir():
            if media_file.is_file():
                # Skip banned files - they're intentionally excluded
                if banned_filter.is_banned(media_file):
                    continue
                if media_file.name not in referenced_files:
                    logger.debug(f"Orphaned media file (no metadata): {media_file.name}")
                    failure_tracker.add_orphaned_media(
                        media_path=media_file,
                        reason="No matching metadata found",
                        context={"original_location": str(media_file)},
                    )

    # Create output directory (subdirectory already set by caller)
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True, parents=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    logger.debug(f"Using {num_workers} parallel workers")

    # Pre-generate all output filenames to avoid race conditions
    logger.debug("Pre-generating filenames...")
    used_filenames = set()
    processing_tasks = []
    skipped_count = 0

    for memory in memories:
        media_filename = memory["media_filename"]
        overlay_filename = memory.get("overlay_filename")

        # Check if media file should be skipped
        media_path = Path(media_filename)
        if banned_filter.is_banned(media_path):
            logger.debug(f"Skipping banned media file: {media_filename}")
            skipped_count += 1
            continue

        # Check if overlay file should be skipped (if it exists)
        if overlay_filename:
            overlay_path = Path(overlay_filename)
            if banned_filter.is_banned(overlay_path):
                logger.debug(f"Skipping banned overlay file: {overlay_filename}")
                skipped_count += 1
                continue

        file_ext = os.path.splitext(media_filename)[1].lower()

        # Generate output filename
        output_filename = generate_unique_filename(
            memory, export_username, file_ext, used_filenames
        )

        # Create task tuple for worker
        processing_tasks.append(
            (memory, output_filename, raw_dir, output_path, export_username)
        )

    # Report on filtered files
    if skipped_count > 0:
        logger.info(f"Skipped {skipped_count} banned files")

    # Phase 1: Create all files with overlays in parallel
    print(f"\nProcessing {len(processing_tasks)} memories to {output_path}/")
    logger.info("=" * 50)

    success_count = 0
    failed_count = 0

    # Use multiprocessing pool with progress bar
    with multiprocessing.Pool(processes=num_workers) as pool:
        # Create files and show progress
        results = list(
            progress_bar(
                pool.imap(create_memory_file, processing_tasks),
                PHASE_PROCESS,
                "Creating files",
                total=len(processing_tasks),
            )
        )

    # Phase 2: Collect non-MKV files for batch EXIF processing
    file_paths = []
    file_info = []

    for success, output_path, is_mkv, memory, export_username, failure_info in results:
        if success and output_path:
            success_count += 1
            # Only add non-MKV files to batch processing list
            # (MKV files already have metadata from overlay creation)
            if not is_mkv:
                file_paths.append(output_path)
                file_info.append((output_path, memory, export_username))
        else:
            failed_count += 1
            # Track orphaned metadata (metadata without corresponding media file)
            if failure_info:
                failure_tracker.add_orphaned_metadata(
                    metadata_entry=memory,
                    reason="Media file not found in filesystem",
                    context={"expected_path": failure_info},
                )

    logger.info(
        f"\nCreated {success_count} files ({len(file_info)} need EXIF processing)"
    )

    # Phase 3: Batch process EXIF operations on non-MKV files
    exif_rebuilt_count = 0
    if file_paths:
        logger.info("Batch processing EXIF metadata...")

        # Batch validate and rebuild
        corrupted_files = batch_validate_exif(file_paths)
        if corrupted_files:
            logger.info(
                f"Rebuilding {len(corrupted_files)} corrupted EXIF structures..."
            )
            batch_rebuild_exif(list(corrupted_files))
            exif_rebuilt_count = len(corrupted_files)

        # Batch read and write metadata
        existing_metadata_map = batch_read_existing_metadata(file_paths)
        batch_write_metadata_snapchat_memories(file_info, existing_metadata_map)

    # Handle failures - copy orphaned files and generate report
    failure_tracker.handle_failures(output_path)

    # Print summary using shared utility
    print_processing_summary(
        success=success_count,
        failed=failed_count,
        total=success_count + failed_count,
        output_dir=str(output_path),
        extra_stats={
            "EXIF structures rebuilt": exif_rebuilt_count,
            "Skipped (banned files)": skipped_count,
            "Total memories": len(memories),
        },
    )
    print("\nNote: Videos with overlays are saved as multi-track MKV files:")
    print("  - Track 0 (default): Video with overlay embedded")
    print("  - Track 1: Original video without overlay")
    print("  Switch tracks in VLC: Video > Video Track > Select track")
