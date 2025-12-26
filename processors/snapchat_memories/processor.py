"""
Snapchat Memories Processor

This processor is designed to be used through memoria.py.
It handles adding overlays to videos and embedding metadata into all Snapchat Memories files.
"""

import json
import logging
import multiprocessing
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.catalog import catalog_media_files
from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_SNAPCHAT,
    SERVICE_MEMORIES,
)
from common.files.utils import copy_file
from common.media.exiftool import (
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    GPSData,
    MetadataWriteRequest,
    build_datetime_tags,
)
from common.failure_tracker import FailureTracker
from common.files.filter import BannedFilesFilter
from common.media.overlay import (
    create_image_with_overlay,
    create_video_with_overlay,
)
from common.processing import print_processing_summary
from common.output.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    default_worker_count,
    extract_username_from_export_dir,
    FilenameGenerator,
    get_media_type,
    update_file_timestamps,
)
from common.output.logging import get_processor_logger
from common.output.paths import OutputInfo
from processors.base import ProcessorBase

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


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
        log.detection_failed("Snapchat Memories", e)
        return False


class SnapchatMemoriesProcessor(ProcessorBase):
    """Processor for Snapchat Memories exports"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        return "Snapchat Memories"

    @staticmethod
    def get_priority() -> int:
        return 80

    @staticmethod
    def get_required_dependencies() -> List[str]:
        return ["exiftool", "ffmpeg", "pillow"]

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Snapchat",
            service="memories",
            username_prefix="snapchat",
        )

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Snapchat Memories exports come pre-organized with media/ and metadata.json.

        The Snapchat export format already provides the required structure,
        so preprocessing is not needed. The processor works directly on the
        export data.
        """
        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Snapchat Memories does not require preprocessing."""
        # This should never be called since needs_preprocessing returns False
        return None

    @staticmethod
    def load_memories_metadata(metadata_path: Path) -> List[Dict[str, Any]]:
        """Load and parse metadata.json for Snapchat Memories.

        Snapchat Memories metadata is a list of memory objects, not a dict.

        Args:
            metadata_path: Path to metadata.json file.

        Returns:
            List of memory dictionaries.
        """
        with open(metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata."""
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        # Find media directory (may be in memories/ subdirectory)
        media_dir = input_path / "media"
        catalog_input_path = input_path
        if not media_dir.exists():
            memories_subdir = input_path / "memories"
            if memories_subdir.exists():
                media_dir = memories_subdir / "media"
                catalog_input_path = memories_subdir

        if not media_dir or not media_dir.exists():
            logger.warning(f"No media directory found in: {input_path}")
            return

        # Load metadata for preprocessed exports
        metadata_index: Dict[str, Dict[str, Any]] = {}
        metadata_file = catalog_input_path / "metadata.json"
        if metadata_file.exists():
            try:
                memories = SnapchatMemoriesProcessor.load_memories_metadata(
                    metadata_file
                )
                metadata_index = build_filename_index(memories)
            except Exception as e:
                logger.warning(f"Could not load metadata.json: {e}")

        media_files = [
            f for f in media_dir.iterdir() if f.is_file() and get_media_type(f)
        ]

        def extract_metadata(file_path: Path) -> Dict[str, Any]:
            """Extract Snapchat Memories metadata for a media file."""
            memory = metadata_index.get(file_path.name, {})
            result: Dict[str, Any] = {}
            if memory:
                # Snapchat Memories has timestamp but no sender/conversation
                if memory.get("date"):
                    result["timestamp"] = memory["date"]
            return result

        yield from catalog_media_files(
            processor_name="Snapchat Memories",
            input_path=input_path,
            media_files=media_files,
            workers=workers,
            metadata_extractor=extract_metadata if metadata_index else None,
        )

    @classmethod
    def process(
        cls,
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process Snapchat Memories export

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            dedup_manifest: Optional deduplication manifest
            **kwargs: Additional arguments (verbose, workers)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Check for required dependencies
        if not cls.check_dependencies():
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
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.error(f"Error in SnapchatMemoriesProcessor: {e}")
            return False


# ============================================================================
# Helper Functions
# ============================================================================


def _build_metadata_requests(
    file_info: List[Tuple[Optional[str], dict, str, Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Snapchat Memories files.

    Filters out MKV files as they already have metadata embedded during overlay creation.

    Args:
        file_info: List of (file_path, memory_data, export_username, content_hash) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for file_path, memory_data, export_username, content_hash in file_info:
        if file_path is None:
            continue
        # Skip MKV files (they already have metadata from overlay creation)
        if file_path.endswith(".mkv"):
            continue

        # Build datetime tags
        datetime_tags = build_datetime_tags(memory_data.get("date"), strip_utc=True)

        # Build GPS data
        gps: Optional[GPSData] = None
        if "latitude" in memory_data and "longitude" in memory_data:
            gps = GPSData(
                latitude=float(memory_data["latitude"]),
                longitude=float(memory_data["longitude"]),
            )

        # Build description using unified format
        entry = SourceEntry(
            processor=PLATFORM_SNAPCHAT,
            username=export_username,
            service=SERVICE_MEMORIES,
        )
        default_description = build_description(entry)

        requests.append(
            MetadataWriteRequest(
                file_path=file_path,
                arg_preamble=DEFAULT_PREAMBLE,
                datetime_tags=datetime_tags,
                gps=gps,
                description=DescriptionWriteSpec(
                    default_description=default_description,
                ),
                processor_name="snapchat_memories",
                content_hash=content_hash,
            )
        )

    return requests


def build_filename_index(memories: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build index mapping media filename to memory metadata.

    Args:
        memories: List of memory objects from metadata.json.

    Returns:
        Dict mapping media_filename -> memory data
    """
    return ProcessorBase.build_flat_filename_index(memories, filename_key="media_filename")


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
        args_tuple: Tuple containing (memory, output_filename, raw_dir, output_dir, export_username, content_hash)

    Returns:
        Tuple of (success: bool, output_path: str or None, is_mkv: bool, memory: dict,
                  export_username: str, content_hash: str or None, failure_reason: str or None)
    """
    memory, output_filename, raw_dir, output_dir, export_username, content_hash = (
        args_tuple
    )

    media_filename = memory["media_filename"]
    overlay_filename = memory.get("overlay_filename")

    media_dir = os.path.join(raw_dir, "media")
    overlays_dir = os.path.join(raw_dir, "overlays")
    media_path = os.path.join(media_dir, media_filename)

    # Convert to Path objects once to avoid repeated conversions
    media_path_obj = Path(media_path)

    if not os.path.exists(media_path):
        logger.warning(f"Media file not found: {media_path}")
        return (False, None, False, memory, export_username, content_hash, media_path)

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
            copy_file(media_path, output_path)
            _update_memory_timestamps(output_path, memory)
            return (
                True,
                output_path,
                False,
                memory,
                export_username,
                content_hash,
                None,
            )

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
                return (
                    True,
                    output_path,
                    True,
                    memory,
                    export_username,
                    content_hash,
                    None,
                )
            else:
                # Multi-track creation failed, copy original video with original extension
                fallback_path = os.path.join(output_dir, output_filename)
                copy_file(media_path, fallback_path)
                _update_memory_timestamps(fallback_path, memory)
                # Fallback file needs batch EXIF processing
                return (
                    True,
                    fallback_path,
                    False,
                    memory,
                    export_username,
                    content_hash,
                    None,
                )

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
                return (
                    True,
                    output_path,
                    False,
                    memory,
                    export_username,
                    content_hash,
                    None,
                )
            else:
                # Image processing failed, copy original
                copy_file(media_path, output_path)
                _update_memory_timestamps(output_path, memory)
                return (
                    True,
                    output_path,
                    False,
                    memory,
                    export_username,
                    content_hash,
                    None,
                )
        else:
            # Unknown file type with overlay - just copy
            output_path = os.path.join(output_dir, output_filename)
            copy_file(media_path, output_path)
            _update_memory_timestamps(output_path, memory)
            return (
                True,
                output_path,
                False,
                memory,
                export_username,
                content_hash,
                None,
            )
    else:
        # No overlay - just copy
        output_path = os.path.join(output_dir, output_filename)
        copy_file(media_path, output_path)
        _update_memory_timestamps(output_path, memory)
        # File needs batch EXIF processing
        return (True, output_path, False, memory, export_username, content_hash, None)


def process_logic(
    input_dir="raw_downloads",
    output_dir="downloaded_memories",
    verbose=False,
    workers=None,
    export_username_override=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Snapchat Memories exports

    Args:
        input_dir: Input directory containing media/, overlays/, and metadata.json
        output_dir: Output directory for processed files
        verbose: Enable verbose logging
        workers: Number of parallel workers (None = auto-detect)
        export_username_override: Optional override for export username (used for consolidated exports)
        dedup_manifest: Optional deduplication manifest for filtering secondary files
    """
    # Logging is configured by the main process
    # Verbose mode enables detailed logging to file

    # Configuration
    raw_dir = input_dir
    metadata_file = os.path.join(raw_dir, "metadata.json")

    # Check for required dependencies
    if not SnapchatMemoriesProcessor.check_dependencies():
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
    memories = SnapchatMemoriesProcessor.load_memories_metadata(Path(metadata_file))
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
                    logger.debug(
                        f"Orphaned media file (no metadata): {media_file.name}"
                    )
                    failure_tracker.add_orphaned_media(
                        media_path=media_file,
                        reason="No matching metadata found",
                        context={"original_location": str(media_file)},
                    )

    # Create output directory (subdirectory already set by caller)
    output_base_path = Path(output_dir)
    output_base_path.mkdir(exist_ok=True, parents=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    logger.debug(f"Using {num_workers} parallel workers")

    # Pre-generate all output filenames to avoid race conditions
    logger.debug("Pre-generating filenames...")
    # Use custom timestamp format for Snapchat memories: "2021-01-04 23:08:30 UTC"
    filename_gen = FilenameGenerator(
        "snap-memories", timestamp_format="%Y-%m-%d %H:%M:%S UTC"
    )
    processing_tasks = []
    skipped_count = 0

    # Build hash lookup from dedup_manifest if available
    hash_lookup: Dict[str, str] = {}
    if dedup_manifest:
        for file_hash, entry in dedup_manifest.get("files", {}).items():
            for source in entry.get("sources", []):
                source_path = source.get("path", "")
                if source_path:
                    # Store both full path and filename as keys
                    hash_lookup[source_path] = file_hash
                    hash_lookup[os.path.basename(source_path)] = file_hash

    skipped_dedup = 0
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

        # Build full path for dedup check
        full_media_path = os.path.join(raw_dir, "media", media_filename)

        # Get content_hash from catalog manifest (computed during catalog phase)
        content_hash = hash_lookup.get(full_media_path) or hash_lookup.get(
            media_filename
        )

        # Skip secondary files based on dedup manifest (using hash-based lookup)
        if SnapchatMemoriesProcessor.should_skip_for_dedup(
            full_media_path, dedup_manifest, "Snapchat Memories"
        ):
            skipped_dedup += 1
            continue

        file_ext = os.path.splitext(media_filename)[1].lower()

        # Generate output filename using shared FilenameGenerator
        # Snapchat uses "date" field instead of "timestamp"
        output_filename = filename_gen.generate(
            memory.get("date"),
            export_username,
            file_ext,
        )

        # Create task tuple for worker
        processing_tasks.append(
            (
                memory,
                output_filename,
                raw_dir,
                output_base_path,
                export_username,
                content_hash,
            )
        )

    if skipped_dedup > 0:
        logger.info(f"Skipped {skipped_dedup} secondary files (dedup)")

    # Report on filtered files
    if skipped_count > 0:
        logger.info(f"Skipped {skipped_count} banned files")

    # Phase 1: Create all files with overlays in parallel
    logger.info(
        "Processing %d memories to %s/", len(processing_tasks), output_base_path
    )
    logger.info("=" * 50)

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
    # Result tuple: (success, output_path, is_mkv, memory, export_username, content_hash, failure_info)
    # Skip MKV files in file_info (they already have metadata from overlay creation)
    def extract_non_mkv_info(r):
        is_mkv = r[2]
        if is_mkv:
            return None  # Skip MKV files from EXIF processing
        return (r[1], r[3], r[4], r[5])  # (output_path, memory, export_username, hash)

    collected = SnapchatMemoriesProcessor.collect_processing_results(
        results, extract_non_mkv_info
    )

    # Track orphaned metadata for failures (side effect not handled by collector)
    for r in results:
        success, output_path, is_mkv, memory, export_username, content_hash, failure_info = r
        if not (success and output_path) and failure_info:
            failure_tracker.add_orphaned_metadata(
                metadata_entry=memory,
                reason="Media file not found in filesystem",
                context={"expected_path": failure_info},
            )

    logger.info(
        f"\nCreated {collected.success_count} files "
        f"({len(collected.file_info)} need EXIF processing)"
    )

    # Phase 3: Correct file extensions based on actual content
    file_paths, file_info = (
        SnapchatMemoriesProcessor.correct_extensions_and_update_info(
            collected.file_paths, collected.file_info, path_index=0
        )
    )

    # Phase 4: Batch process EXIF operations on non-MKV files
    exif_rebuilt_count = 0
    if file_paths:
        logger.info("Batch processing EXIF metadata...")
        exif_rebuilt_count = SnapchatMemoriesProcessor.batch_process_exif(
            file_paths, file_info, _build_metadata_requests, dedup_manifest
        )

    # Handle failures - copy orphaned files and generate report
    failure_tracker.handle_failures(output_base_path)

    # Print summary using shared utility
    print_processing_summary(
        success=collected.success_count,
        failed=collected.failed_count,
        total=collected.success_count + collected.failed_count,
        output_dir=str(output_base_path),
        extra_stats={
            "EXIF structures rebuilt": exif_rebuilt_count,
            "Skipped (banned files)": skipped_count,
            "Total memories": len(memories),
        },
    )
    logger.info("Note: Videos with overlays are saved as multi-track MKV files:")
    logger.info("  - Track 0 (default): Video with overlay embedded")
    logger.info("  - Track 1: Original video without overlay")
    logger.info("  Switch tracks in VLC: Video > Video Track > Select track")


def get_processor():
    return SnapchatMemoriesProcessor
