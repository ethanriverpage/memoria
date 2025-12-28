"""Google Photos Media Processor - handles renaming, metadata embedding, and timestamps."""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_GOOGLE,
    SERVICE_PHOTOS,
)
from common.failure_tracker import FailureTracker
from common.files.utils import batch_correct_extensions, copy_file
from common.dedup.service import (
    get_canonical_timestamp,
    save_dedup_metadata,
)
from common.media.exiftool import (
    DescriptionWriteSpec,
    GPSData,
    MetadataWriteRequest,
    PREAMBLE_WITH_IGNORE_ERRORS,
    build_datetime_tags,
)
from common.output.logging import get_processor_logger
from common.processing import (
    process_batches_parallel,
    print_processing_summary,
)
from common.detection import detect_from_dirname
from common.utils import (
    default_worker_count,
    FilenameGenerator,
    is_preprocessed_directory,
    update_file_timestamps,
)
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.google_photos.preprocess import GooglePhotosPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class GooglePhotosProcessor(ProcessorBase):
    """Processor for Google Photos exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this is a Google Photos export.

        Supported source layouts:
        - Raw (non-Takeout): <input>/Google Photos/...
        - Takeout: <input>/Takeout/Google Photos/...
        """
        try:
            photos_dir = input_path / "Google Photos"
            if not photos_dir.exists() or not photos_dir.is_dir():
                # Google Takeout structure
                photos_dir = input_path / "Takeout" / "Google Photos"

            # Check if Google Photos directory exists
            if not photos_dir.exists() or not photos_dir.is_dir():
                return False

            # Check for album folders within Google Photos
            # Need at least one subdirectory (album)
            albums = [d for d in photos_dir.iterdir() if d.is_dir()]

            return len(albums) > 0

        except Exception as e:
            log.detection_failed("Google Photos", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "Google Photos"

    @staticmethod
    def get_priority() -> int:
        return 50

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Google",
            service="photos",
            username_prefix="google",
        )

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if Google Photos export needs preprocessing."""
        # Google Photos can be processed directly from raw exports
        # (it reads JSON sidecars), but preprocessing is faster for large exports
        # Check if already preprocessed
        if is_preprocessed_directory(input_dir):
            return False

        # Check for raw export structure (Google Photos/ directory)
        input_path = Path(input_dir)
        photos_dir = input_path / "Google Photos"
        takeout_photos_dir = input_path / "Takeout" / "Google Photos"
        if photos_dir.exists() or takeout_photos_dir.exists():
            return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw Google Photos export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        log.logger.info("Preprocessing Google Photos export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = GooglePhotosPreprocessor(
            export_path=input_path,
            output_dir=cache_path,
            workers=workers,
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
            for key in ("albums", "gps", "people", "capture_timestamp"):
                if key in entry:
                    result[key] = entry[key]
            return result

        catalog_iter = GooglePhotosProcessor.catalog_from_preprocessed_if_available(
            processor_name="Google Photos",
            input_path=input_path,
            workers=workers,
            metadata_extractor=extract_metadata,
        )
        if catalog_iter is not None:
            yield from catalog_iter
            return

        logger.warning("Directory not preprocessed, skipping catalog: %s", input_path)

    @classmethod
    def process(
        cls,
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process Google Photos export."""
        # Check for required dependencies
        if not cls.check_dependencies():
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
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            log.logger.error("Error in GooglePhotosProcessor: %s", e)
            return False


def _clean_iso_timestamp(timestamp_str: str) -> str:
    """Remove timezone suffix from ISO 8601 timestamp for parsing."""
    if not timestamp_str:
        return ""
    # Remove Z or timezone offset for parsing
    return timestamp_str.replace("Z", "").replace("+00:00", "")


def get_live_photo_group_key(media_data):
    """Generate a key to identify files that are part of the same live photo."""
    timestamp = media_data.get("capture_timestamp", "")
    original_filename = media_data.get("original_filename", "")
    # Use the filename without extension as the group identifier
    if original_filename:
        base_name = os.path.splitext(original_filename)[0]
    else:
        base_name = ""
    return (timestamp, base_name)


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str, str, Optional[str], Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Google Photos files.

    Args:
        file_info: List of (file_path, media_data, album_name, export_username,
                   content_hash, original_filename) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for (
        file_path,
        media_data,
        _album_name,
        export_username,
        content_hash,
        original_filename,
    ) in file_info:
        # Build datetime tags (ISO format) using canonical timestamp
        datetime_tags = build_datetime_tags(media_data.get("canonical_timestamp"))

        # Build GPS data
        gps: Optional[GPSData] = None
        gps_data = media_data.get("gps", {})
        latitude = gps_data.get("latitude", 0.0)
        longitude = gps_data.get("longitude", 0.0)
        altitude = gps_data.get("altitude", 0.0)

        if latitude != 0.0 or longitude != 0.0:
            gps = GPSData(
                latitude=float(latitude),
                longitude=float(longitude),
                altitude=float(altitude) if altitude != 0.0 else None,
            )

        # Build description using unified format
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username=export_username,
            service=SERVICE_PHOTOS,
        )
        default_description = build_description(entry)

        requests.append(
            MetadataWriteRequest(
                file_path=file_path,
                arg_preamble=PREAMBLE_WITH_IGNORE_ERRORS,
                datetime_tags=datetime_tags,
                gps=gps,
                description=DescriptionWriteSpec(
                    default_description=default_description,
                ),
                processor_name="google_photos",
                content_hash=content_hash,
                original_filename=original_filename,
            )
        )

    return requests


def process_media_batch(batch_args):
    """Process a batch of media files (copy, correct extensions, update timestamps).

    Args:
        batch_args: List of tuples, each containing (media_file, media_data,
                    album_name, output_filename, media_dir, output_dir, export_username,
                    content_hash, original_filename)

    Returns:
        List of (success, output_path, file_info_tuple) tuples where file_info_tuple is
        (output_path, media_data, album_name, export_username, content_hash, original_filename)
        or None on failure
    """
    results = []

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
            content_hash,
            original_filename,
        ) = args_tuple

        media_path = os.path.join(media_dir, media_file)
        output_path = os.path.join(output_dir, output_filename)

        if not os.path.exists(media_path):
            log.missing_file(media_path)
            results.append((False, None, None))
            continue

        try:
            copy_file(media_path, output_path)
            file_paths.append(output_path)
            file_info.append(
                (
                    output_path,
                    media_data,
                    album_name,
                    export_username,
                    content_hash,
                    original_filename,
                )
            )
        except Exception as e:
            log.copy_error(media_file, e)
            results.append((False, None, None))

    if not file_paths:
        return results if results else [(False, None, None)] * len(batch_args)

    # Phase 2: Correct file extensions based on actual content
    corrections = batch_correct_extensions([Path(p) for p in file_paths])
    if corrections:
        # Update file_paths and file_info with corrected paths
        file_paths = [corrections.get(p, p) for p in file_paths]
        file_info = [
            (corrections.get(fp, fp), md, an, eu, ch, of)
            for fp, md, an, eu, ch, of in file_info
        ]

    # Phase 3: Update timestamps and compile results
    for (
        output_path,
        media_data,
        album_name,
        export_username,
        content_hash,
        original_filename,
    ) in file_info:
        # Google Photos uses ISO 8601 format: "2018-01-10T00:30:37Z"
        timestamp_str = media_data.get("capture_timestamp")
        if timestamp_str:
            update_file_timestamps(output_path, timestamp_str, "%Y-%m-%dT%H:%M:%S")
        results.append(
            (
                True,
                output_path,
                (
                    output_path,
                    media_data,
                    album_name,
                    export_username,
                    content_hash,
                    original_filename,
                ),
            )
        )

    return results


def process_logic(
    input_dir,
    output_dir="final_googlephotos",
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Google Photos exports.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling GooglePhotosProcessor.preprocess() directly).
    """

    # Check for exiftool
    if not GooglePhotosProcessor.check_dependencies():
        return

    log.processor_start("Google Photos Media Processor")

    # Require preprocessed input
    # Google Photos uses new format (source_path in metadata), so only check for metadata.json
    input_path = Path(input_dir)
    metadata_file = input_path / "metadata.json"
    if not metadata_file.exists():
        logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            input_dir,
        )
        return False

    log.already_preprocessed(input_dir)
    _process_working_directory(input_dir, output_dir, workers, dedup_manifest)


def _process_working_directory(
    working_dir,
    output_dir,
    workers,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Process a working directory (preprocessed export)."""
    # Configuration
    metadata_file = os.path.join(working_dir, "metadata.json")

    # Check for metadata file (should exist after preprocessing or if already preprocessed)
    if not os.path.exists(metadata_file):
        log.file_not_found(metadata_file, "Metadata file")
        return

    # Load metadata
    log.loading_metadata(metadata_file)
    metadata_json = GooglePhotosProcessor.load_metadata(
        Path(metadata_file), validate=True, require_source_paths=True
    )

    export_info = metadata_json.get("export_info", {})
    media_files = metadata_json.get("media_files", [])

    # Count total media files
    total_media_files = len(media_files)

    log.found_files(total_media_files, "media")

    # Filter out secondary files based on dedup manifest (using hash-based lookup)
    skipped_secondary_files = []
    if dedup_manifest:
        original_count = len(media_files)
        filtered_media_files = []
        for media_data in media_files:
            media_file = media_data.get("filename")
            if media_file:
                source_path = media_data.get("source_path", "")
                content_hash = media_data.get("content_hash")
                if GooglePhotosProcessor.should_skip_for_dedup(
                    dedup_manifest, content_hash, "Google Photos", source_path
                ):
                    skipped_secondary_files.append(source_path)
                else:
                    filtered_media_files.append(media_data)
        media_files = filtered_media_files
        skipped_count = original_count - len(media_files)
        log.skipped_secondary(skipped_count)

    # Extract export username from validated export_info
    export_username = export_info.get("export_username")
    log.export_username(export_username)

    # Initialize failure tracker for this export
    failure_tracker = FailureTracker(
        processor_name="Google Photos",
        export_directory=str(working_dir),
    )

    # Check for no media files (export-level soft-fail)
    if not media_files:
        log.logger.warning("No media files found to process.")
        failure_tracker.add_export_failure(
            reason="no_media_files",
            error_details="No media files found in export to process",
        )
        failure_tracker.handle_failures(Path(output_dir))
        return False

    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    log.using_workers(num_workers)

    # Pre-generate all output filenames to avoid race conditions
    log.generating_filenames()

    # First pass: Group live photos together
    # Live photo pairs (image + video with same timestamp/filename) need the same sequence
    # Map: live_photo_group_key -> list of (media_data, file_ext)
    live_photo_groups = {}

    for media_data in media_files:
        media_file = media_data.get("filename")
        if not media_file:
            continue
        file_ext = os.path.splitext(media_file)[1].lower()
        live_photo_key = get_live_photo_group_key(media_data)
        if live_photo_key not in live_photo_groups:
            live_photo_groups[live_photo_key] = []
        live_photo_groups[live_photo_key].append((media_data, file_ext))

    # Second pass: Generate filenames using FilenameGenerator
    # For live photo pairs, reuse base filename (with same sequence) for both files
    filename_gen = FilenameGenerator(
        "google",
        export_username,
        service="photos",
        timestamp_format="%Y-%m-%dT%H:%M:%S",
    )
    live_photo_bases = {}  # Map: live_photo_key -> base filename (without extension)

    processing_tasks = []
    missing_source_path = 0
    missing_examples: list[str] = []

    for media_data in media_files:
        media_file = media_data.get("filename")

        if not media_file:
            logger.warning("Skipping media file without filename")
            continue

        file_ext = os.path.splitext(media_file)[1].lower()
        live_photo_key = get_live_photo_group_key(media_data)
        is_live_photo_pair = len(live_photo_groups.get(live_photo_key, [])) > 1

        if is_live_photo_pair and live_photo_key in live_photo_bases:
            # Second file in live photo pair - reuse base from first file
            output_filename = f"{live_photo_bases[live_photo_key]}{file_ext}"
        else:
            # Generate filename using FilenameGenerator (mechanism handles uniqueness)
            timestamp_str = media_data.get("capture_timestamp")
            timestamp_clean = _clean_iso_timestamp(timestamp_str)
            canonical_ts = get_canonical_timestamp(
                dedup_manifest, media_data.get("content_hash"), timestamp_clean
            )
            media_data["canonical_timestamp"] = canonical_ts
            output_filename = filename_gen.generate(canonical_ts, file_ext)

            # If this is first file in a live photo pair, record base for second file
            if is_live_photo_pair:
                # Extract base without extension for reuse
                base = (
                    output_filename[: -len(file_ext)] if file_ext else output_filename
                )
                live_photo_bases[live_photo_key] = base

        # Get primary album name for metadata (use first album in list)
        albums_list = media_data.get("albums", [])
        album_name = albums_list[0] if albums_list else "unknown"

        # Get source path from metadata
        full_source_path = media_data.get("source_path")
        if not full_source_path:
            missing_source_path += 1
            if len(missing_examples) < 5:
                missing_examples.append(media_file or "unknown")
            continue
        source_path = os.path.dirname(full_source_path)
        source_file = os.path.basename(full_source_path)

        # Get content_hash from metadata (if available from preprocessing)
        content_hash = media_data.get("content_hash")

        # Get original filename for XMP:PreservedFileName embedding
        # Prefer original_filename from metadata, fall back to source file basename
        original_filename = media_data.get("original_filename") or source_file

        # Create task tuple for worker (include content_hash and original_filename for EXIF)
        processing_tasks.append(
            (
                source_file,
                media_data,
                album_name,
                output_filename,
                source_path,
                output_dir,
                export_username,
                content_hash,
                original_filename,
            )
        )

    if missing_source_path > 0:
        logger.info(
            "Missing source_path for %d files (e.g. %s)",
            missing_source_path,
            ", ".join(missing_examples),
        )

    # Process media files in parallel
    log.processing_start(output_dir)

    # Use shared batch processing utility (copy files only)
    results = process_batches_parallel(
        tasks=processing_tasks,
        worker_fn=process_media_batch,
        num_workers=num_workers,
        batch_size=100,
        description="Copying files",
    )

    # Collect successful files for EXIF processing
    collected = GooglePhotosProcessor.collect_processing_results(results)

    log.copied_files(collected.success_count, collected.failed_count)

    # Consolidated EXIF processing
    exif_rebuilt_count = 0
    if collected.file_paths:
        log.exif_batch_start()

        # Build metadata lookup for failure tracking
        file_info_map = {info[0]: info for info in collected.file_info}

        def metadata_for_file(file_path: str) -> Optional[dict]:
            info = file_info_map.get(file_path)
            return info[1] if info else None  # info[1] is media_data

        exif_rebuilt_count, write_outcome, missing_files = (
            GooglePhotosProcessor.batch_process_exif(
                collected.file_paths,
                collected.file_info,
                _build_metadata_requests,
                dedup_manifest,
            )
        )

        # Track EXIF failures
        for missing_path in missing_files:
            failure_tracker.add_processing_failure(
                media_path=Path(missing_path),
                metadata=metadata_for_file(missing_path),
                reason="exif_target_missing",
                error_details=f"Output file missing at EXIF write time: {missing_path}",
            )
        for failure in write_outcome.failures:
            failure_tracker.add_processing_failure(
                media_path=Path(failure.file_path),
                metadata=metadata_for_file(failure.file_path),
                reason=failure.reason_code,
                error_details=failure.stderr_excerpt or "",
            )

        log.exif_written(len(collected.file_info))

    # Handle failures - move failed outputs and generate report
    failure_tracker.handle_failures(Path(output_dir))

    # Save dedup references for skipped secondary files
    if dedup_manifest and skipped_secondary_files:
        save_dedup_metadata(
            manifest=dedup_manifest,
            skipped_files=skipped_secondary_files,
            processor_name="Google Photos",
            output_dir=Path(output_dir),
        )

    # Print summary using shared utility
    print_processing_summary(
        success=collected.success_count,
        failed=collected.failed_count,
        total=len(processing_tasks),
        output_dir=output_dir,
        extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
    )
