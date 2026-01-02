"""
Snapchat Memories Processor

This processor is designed to be used through memoria.py.
It handles adding overlays to videos and embedding metadata into all Snapchat Memories files.

Requires preprocessing via SnapchatMemoriesPreprocessor to convert raw exports
into standardized metadata.json format with source_path entries.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_SNAPCHAT,
    SERVICE_MEMORIES,
)
from common.files.utils import copy_file, short_path
from common.media.exiftool import (
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    GPSData,
    MetadataWriteRequest,
    build_datetime_tags,
)
from common.config.runtime import default_worker_count
from common.dedup.service import get_canonical_timestamp, save_dedup_metadata
from common.detection import detect_from_dirname
from common.failures import FailureTracker
from common.files.filter import BannedFilesFilter
from common.files.naming import FilenameGenerator
from common.files.timestamps import update_file_timestamps
from common.files.types import get_media_type, MediaType
from common.media.overlay import (
    create_image_with_overlay,
    create_video_with_overlay,
    OverlayProcessingError,
)
from common.output.logging import get_processor_logger
from common.output.summary import print_processing_summary
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.snapchat_memories.preprocess import SnapchatMemoriesPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


def _check_memories_structure(base_path: Path) -> bool:
    """Check if a path contains valid Snapchat Memories structure (raw format)."""
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

    # Validate metadata.json structure (raw format is a list)
    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        # Should be a list/array for raw format
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

        return True

    except (json.JSONDecodeError, KeyError, IndexError):
        return False


class SnapchatMemoriesProcessor(ProcessorBase):
    """Processor for Snapchat Memories exports.

    Requires preprocessed input with standardized metadata.json containing
    source_path entries. Raw exports must be preprocessed first via
    SnapchatMemoriesPreprocessor.
    """

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory.

        Detection criteria for Snapchat Memories:
        - Directory contains 'media/' subdirectory
        - Directory contains 'overlays/' subdirectory
        - Directory contains 'metadata.json' file
        - metadata.json contains array of memory objects with required fields
        - Consolidated: Directory contains 'memories/' subdirectory with above
        """
        try:
            # Check for consolidated structure first (memories/ subdirectory)
            memories_subdir = input_path / "memories"
            if memories_subdir.exists() and memories_subdir.is_dir():
                if _check_memories_structure(memories_subdir):
                    return True

            # Check for direct structure
            if _check_memories_structure(input_path):
                return True

            # Also detect preprocessed format using platform/service markers
            if ProcessorBase.is_preprocessed_for_platform(
                input_path, platform="Snapchat", service="memories"
            ):
                return True

            return False

        except Exception as e:
            log.detection_failed("Snapchat Memories", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "Snapchat Memories"

    @staticmethod
    def get_priority() -> int:
        # Slower processor (~tens of minutes) - run after the fastest processors
        return 70

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
        """Check if Snapchat Memories export needs preprocessing.

        Snapchat Memories raw exports have a metadata.json (list format),
        but this is NOT the same as our preprocessed format (dict with
        media_files containing source_path entries).

        Uses strict metadata schema validation to correctly distinguish between
        raw and preprocessed formats.
        """
        input_path = Path(input_dir)

        # Check if already preprocessed (validated metadata schema)
        if SnapchatMemoriesProcessor.is_preprocessed_export_dir(input_path):
            return False

        # Check for raw export structure (supports both direct and consolidated)
        if _check_memories_structure(input_path):
            return True

        # Check for consolidated structure (memories/ subdirectory)
        memories_subdir = input_path / "memories"
        if memories_subdir.exists() and _check_memories_structure(memories_subdir):
            return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw Snapchat Memories export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        # Check for consolidated export structure (memories/ subdirectory)
        # Extract username from parent directory for consolidated exports
        username_override = None
        memories_subdir = input_path / "memories"
        if memories_subdir.exists() and _check_memories_structure(memories_subdir):
            username_override = detect_from_dirname(input_path, "snapchat") or "unknown"
            logger.info(
                "Detected consolidated export structure, preprocessing from: %s",
                memories_subdir,
            )

        logger.info("Preprocessing Snapchat Memories export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = SnapchatMemoriesPreprocessor(
            export_path=input_path,
            output_dir=cache_path,
            workers=workers,
            username_override=username_override,
        )

        if preprocessor.run():
            return str(cache_path)
        return None

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata.

        Requires preprocessed directory with metadata.json and source_path fields.

        Args:
            input_dir: Path to preprocessed input directory
            **kwargs: Additional arguments (workers)

        Yields:
            Catalog entries with processor name and file data.
        """
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        def extract_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
            """Extract metadata from preprocessed entry."""
            result: Dict[str, Any] = {}
            if "date" in entry:
                result["timestamp"] = entry["date"]
            return result

        catalog_iter = SnapchatMemoriesProcessor.catalog_from_preprocessed_if_available(
            processor_name="Snapchat Memories",
            input_path=input_path,
            workers=workers,
            metadata_extractor=extract_metadata,
            media_files_key="media_files",
        )
        if catalog_iter is not None:
            yield from catalog_iter
            return

        logger.warning("Directory not preprocessed, skipping catalog: %s", input_path)

    @classmethod
    def _process_impl(
        cls,
        input_dir: str,
        output_dir: str,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process Snapchat Memories from preprocessed metadata."""
        input_path = Path(input_dir)

        # Verify input is preprocessed format
        media_files = cls.check_preprocessed_source_path(
            input_path, media_files_key="media_files"
        )

        if media_files is None:
            logger.error(
                "Input is not preprocessed. Run preprocessing first: %s",
                input_path,
            )
            return False

        return _process_preprocessed(
            input_path=input_path,
            output_dir=Path(output_dir),
            workers=kwargs.get("workers"),
            dedup_manifest=dedup_manifest,
        )


# ============================================================================
# Helper Functions
# ============================================================================


def _build_metadata_requests(
    file_info: Sequence[Tuple[Optional[str], dict, str, Optional[str], Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Snapchat Memories files.

    Filters out MKV files as they already have metadata embedded during overlay creation.

    Args:
        file_info: List of (file_path, media_data, export_username, content_hash,
                   original_filename) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for (
        file_path,
        media_data,
        export_username,
        content_hash,
        original_filename,
    ) in file_info:
        if file_path is None:
            continue
        # Skip MKV files (they already have metadata from overlay creation)
        if file_path.endswith(".mkv"):
            continue

        # Build datetime tags
        datetime_tags = build_datetime_tags(
            media_data.get("canonical_timestamp"), strip_utc=True
        )

        # Build GPS data (skip invalid coordinates like 0.0, 0.0)
        gps: Optional[GPSData] = None
        if "latitude" in media_data and "longitude" in media_data:
            lat = float(media_data["latitude"])
            lon = float(media_data["longitude"])
            # Skip (0.0, 0.0) as it represents "no GPS data" in Snapchat exports
            # Also skip out-of-range coordinates
            if not (lat == 0.0 and lon == 0.0) and (
                -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0
            ):
                gps = GPSData(latitude=lat, longitude=lon)

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
                original_filename=original_filename,
            )
        )

    return requests


def _update_memory_timestamps(file_path: str, media_data: dict) -> bool:
    """Update filesystem creation and modification timestamps to match memory date.

    Wrapper around shared update_file_timestamps that handles Snapchat Memories
    date format: "2021-01-04 23:08:30 UTC"

    Args:
        file_path: Path to the file
        media_data: Dict containing memory metadata with 'date' field

    Returns:
        bool: True if successful, False otherwise
    """
    date_str = media_data.get("date")
    if not date_str:
        return False

    return update_file_timestamps(file_path, date_str)


def _create_memory_file(args_tuple):
    """Create output file with overlay if needed (worker function).

    Takes source paths directly from preprocessed metadata.

    Args:
        args_tuple: Tuple containing (media_data, output_filename, media_source_path,
                    overlay_source_path, output_dir, export_username, content_hash,
                    original_filename)

    Returns:
        Tuple of (success: bool, output_path: str or None, is_mkv: bool, media_data: dict,
                  export_username: str, content_hash: str or None, failure_reason: str or None,
                  original_filename: str or None)
    """
    (
        media_data,
        output_filename,
        media_source_path,
        overlay_source_path,
        output_dir,
        export_username,
        content_hash,
        original_filename,
    ) = args_tuple

    media_path = Path(media_source_path)
    output_dir_path = Path(output_dir)

    if not media_path.exists():
        logger.warning("Media file not found: %s", short_path(media_source_path))
        return (
            False,
            None,
            False,
            media_data,
            export_username,
            content_hash,
            str(media_source_path),
            original_filename,
        )

    # Determine if this is a video or image
    is_video = get_media_type(media_path) == MediaType.VIDEO
    is_image = get_media_type(media_path) == MediaType.IMAGE

    # Process based on file type and whether overlay exists
    if overlay_source_path:
        overlay_path = Path(overlay_source_path)

        if not overlay_path.exists():
            logger.warning(
                "Overlay file not found: %s", short_path(overlay_source_path)
            )
            # Copy file without overlay
            output_path = output_dir_path / output_filename
            copy_file(media_path, output_path)
            _update_memory_timestamps(str(output_path), media_data)
            return (
                True,
                str(output_path),
                False,
                media_data,
                export_username,
                content_hash,
                None,
                original_filename,
            )

        if is_video:
            # Video with overlay - create multi-track MKV
            output_filename_mkv = Path(output_filename).stem + ".mkv"
            output_path = output_dir_path / output_filename_mkv

            try:
                success = create_video_with_overlay(
                    media_path,
                    overlay_path,
                    output_path,
                    media_data,
                    export_username=export_username,
                    original_filename=original_filename,
                )
                if success:
                    _update_memory_timestamps(str(output_path), media_data)
                    return (
                        True,
                        str(output_path),
                        True,
                        media_data,
                        export_username,
                        content_hash,
                        None,
                        original_filename,
                    )
                else:
                    # Fallback - copy original video
                    fallback_path = output_dir_path / output_filename
                    copy_file(media_path, fallback_path)
                    _update_memory_timestamps(str(fallback_path), media_data)
                    return (
                        True,
                        str(fallback_path),
                        False,
                        media_data,
                        export_username,
                        content_hash,
                        None,
                        original_filename,
                    )
            except OverlayProcessingError as e:
                logger.warning(
                    "Overlay processing failed for %s: %s - copying base video",
                    short_path(media_path),
                    e,
                )
                fallback_path = output_dir_path / output_filename
                copy_file(media_path, fallback_path)
                _update_memory_timestamps(str(fallback_path), media_data)
                return (
                    True,
                    str(fallback_path),
                    False,
                    media_data,
                    export_username,
                    content_hash,
                    f"overlay_corrupt: {e}",
                    original_filename,
                )

        elif is_image:
            # Image with overlay - composite
            output_path = output_dir_path / output_filename

            try:
                create_image_with_overlay(media_path, overlay_path, output_path)
                _update_memory_timestamps(str(output_path), media_data)
                return (
                    True,
                    str(output_path),
                    False,
                    media_data,
                    export_username,
                    content_hash,
                    None,
                    original_filename,
                )
            except OverlayProcessingError as e:
                logger.warning(
                    "Overlay processing failed for %s: %s - copying base image",
                    short_path(media_path),
                    e,
                )
                copy_file(media_path, output_path)
                _update_memory_timestamps(str(output_path), media_data)
                return (
                    True,
                    str(output_path),
                    False,
                    media_data,
                    export_username,
                    content_hash,
                    f"overlay_corrupt: {e}",
                    original_filename,
                )
        else:
            # Unknown file type with overlay - just copy
            output_path = output_dir_path / output_filename
            copy_file(media_path, output_path)
            _update_memory_timestamps(str(output_path), media_data)
            return (
                True,
                str(output_path),
                False,
                media_data,
                export_username,
                content_hash,
                None,
                original_filename,
            )
    else:
        # No overlay - just copy
        output_path = output_dir_path / output_filename
        copy_file(media_path, output_path)
        _update_memory_timestamps(str(output_path), media_data)
        return (
            True,
            str(output_path),
            False,
            media_data,
            export_username,
            content_hash,
            None,
            original_filename,
        )


# ============================================================================
# Processing Logic (Preprocessed Format Only)
# ============================================================================


def _process_preprocessed(
    input_path: Path,
    output_dir: Path,
    workers: Optional[int] = None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Process Snapchat Memories from preprocessed metadata.

    Args:
        input_path: Path to preprocessed directory with metadata.json
        output_dir: Output directory for processed files
        workers: Number of parallel workers (None = auto-detect)
        dedup_manifest: Optional deduplication manifest

    Returns:
        True if processing succeeded, False otherwise
    """
    log.processor_start("Snapchat Memories - Processing")

    # Load preprocessed metadata
    metadata_path = input_path / "metadata.json"
    if not metadata_path.exists():
        logger.error("Metadata file not found: %s", short_path(metadata_path))
        return False

    try:
        metadata = SnapchatMemoriesProcessor.load_metadata(
            metadata_path, validate=True, processor_name="Snapchat Memories"
        )
    except Exception as e:
        logger.error("Failed to load metadata: %s", e)
        return False

    # Extract export info
    export_info = metadata.get("export_info", {})
    export_username = export_info.get("export_username", "unknown")
    log.export_username(export_username)

    # Get media files list
    media_files = metadata.get("media_files", [])
    log.found_items(len(media_files), "media files")

    if not media_files:
        logger.warning("No media files found in metadata")
        return False

    # Initialize banned files filter
    banned_filter = BannedFilesFilter()

    # Initialize failure tracker
    failure_tracker = FailureTracker(
        processor_name="Snapchat Memories",
        export_directory=str(input_path),
    )

    # Create output directory
    output_dir.mkdir(exist_ok=True, parents=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    log.using_workers(num_workers)

    # Build hash lookup from dedup_manifest
    hash_lookup: Dict[str, str] = {}
    file_hashes = metadata.get("file_hashes", {})
    if dedup_manifest:
        for file_hash, entry in dedup_manifest.get("files", {}).items():
            for source in entry.get("sources", []):
                source_path = source.get("path", "")
                if source_path:
                    hash_lookup[source_path] = file_hash

    # Pre-generate all output filenames
    log.generating_filenames()
    filename_gen = FilenameGenerator(
        "snap",
        export_username,
        service="memories",
    )

    processing_tasks = []
    skipped_count = 0
    skipped_dedup = 0
    skipped_secondary_files: List[str] = []

    for media_data in media_files:
        filename = media_data.get("filename")
        source_path = media_data.get("source_path")

        if not filename or not source_path:
            logger.warning("Skipping entry without filename or source_path")
            continue

        # Check if file should be skipped
        if banned_filter.is_banned(Path(filename)):
            skipped_count += 1
            continue

        # Get content hash
        content_hash = file_hashes.get(filename) or hash_lookup.get(source_path)

        # Skip secondary files based on dedup manifest
        if content_hash and SnapchatMemoriesProcessor.should_skip_for_dedup(
            dedup_manifest, content_hash, "Snapchat Memories", source_path
        ):
            skipped_dedup += 1
            skipped_secondary_files.append(source_path)
            continue

        # Get file extension
        file_ext = Path(filename).suffix.lower()

        # Generate output filename
        canonical_ts = get_canonical_timestamp(
            dedup_manifest, content_hash, media_data.get("date")
        )
        media_data["canonical_timestamp"] = canonical_ts
        output_filename = filename_gen.generate(canonical_ts, file_ext)

        # Get overlay source path if present
        overlay_source_path = media_data.get("overlay_source_path")

        # Create task tuple for worker
        processing_tasks.append(
            (
                media_data,
                output_filename,
                source_path,
                overlay_source_path,
                output_dir,
                export_username,
                content_hash,
                filename,  # original_filename
            )
        )

    if skipped_dedup > 0:
        log.skipped_secondary(skipped_dedup)

    if skipped_count > 0:
        logger.info("Skipped %d banned files", skipped_count)

    # Check for no media files
    if not processing_tasks:
        logger.warning("No media files found to process.")
        failure_tracker.add_export_failure(
            reason="no_media_files",
            error_details="No media files found in export to process",
        )
        failure_tracker.handle_failures(output_dir)
        return False

    # Phase 1: Create all files with overlays in parallel
    log.processing_start(str(output_dir))

    results = SnapchatMemoriesProcessor.process_items_parallel(
        tasks=processing_tasks,
        worker_fn=_create_memory_file,
        num_workers=num_workers,
        description="Creating files",
    )

    # Phase 2: Collect non-MKV files for batch EXIF processing
    def extract_non_mkv_info(r):
        is_mkv = r[2]
        if is_mkv:
            return None
        return (r[1], r[3], r[4], r[5], r[7])

    collected = SnapchatMemoriesProcessor.collect_processing_results(
        results, extract_non_mkv_info
    )

    # Track failures
    for r in results:
        (
            success,
            output_path,
            _is_mkv,
            media_data,
            _export_username,
            _content_hash,
            failure_info,
            _original_filename,
        ) = r
        if not (success and output_path) and failure_info:
            failure_tracker.add_orphaned_metadata(
                metadata_entry=media_data,
                reason="Media file not found in filesystem",
                context={"expected_path": failure_info},
            )
        elif success and output_path and failure_info:
            if str(failure_info).startswith("overlay_corrupt:"):
                failure_tracker.add_processing_failure(
                    media_path=Path(output_path),
                    metadata=media_data,
                    reason="overlay_corrupt",
                    error_details=str(failure_info),
                )

    logger.info(
        "Created %d files (%d need EXIF processing)",
        collected.success_count,
        len(collected.file_info),
    )

    # Phase 3: Correct file extensions and batch process EXIF metadata
    logger.info("Batch processing EXIF metadata...")

    # Build metadata lookup for failure tracking
    file_info_map = {info[0]: info for info in collected.file_info}

    def metadata_for_file(file_path: str) -> Optional[dict]:
        info = file_info_map.get(file_path)
        return info[1] if info else None  # info[1] is media_data

    _, _, exif_rebuilt_count = SnapchatMemoriesProcessor.finalize_files(
        collected.file_paths,
        collected.file_info,
        _build_metadata_requests,
        dedup_manifest,
        path_index=0,
        failure_tracker=failure_tracker,
        metadata_for_file=metadata_for_file,
    )

    # Handle failures
    failure_tracker.handle_failures(output_dir)

    # Save dedup references for skipped secondary files
    if dedup_manifest and skipped_secondary_files:
        save_dedup_metadata(
            manifest=dedup_manifest,
            skipped_files=skipped_secondary_files,
            processor_name="Snapchat Memories",
            output_dir=output_dir,
        )

    # Print summary
    print_processing_summary(
        success=collected.success_count,
        failed=collected.failed_count,
        total=collected.success_count + collected.failed_count,
        output_dir=str(output_dir),
        extra_stats={
            "EXIF structures rebuilt": exif_rebuilt_count,
            "Skipped (banned files)": skipped_count,
            "Total media files": len(media_files),
        },
    )
    logger.info("Note: Videos with overlays are saved as multi-track MKV files:")
    logger.info("  - Track 0 (default): Video with overlay embedded")
    logger.info("  - Track 1: Original video without overlay")
    logger.info("  Switch tracks in VLC: Video > Video Track > Select track")

    return True
