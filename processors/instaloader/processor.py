"""
Instaloader Media Processor

This processor handles media scraped using Instaloader (https://instaloader.github.io/).
It is NOT an official Instagram export format - it processes scrape data from the Instaloader tool.
Handles renaming media files, embedding metadata, and updating filesystem timestamps.
"""

import logging
import multiprocessing
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.service import (
    get_canonical_timestamp,
    save_dedup_metadata,
)
from common.failure_tracker import FailureTracker
from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_INSTAGRAM,
    SERVICE_STORIES,
    SERVICE_POSTS,
    SERVICE_REELS,
)
from common.output.console import console
from common.files.utils import batch_correct_extensions, copy_file, short_path
from common.processing import print_processing_summary
from common.output.progress import PHASE_PROCESS, progress_bar
from processors.instaloader.preprocess import InstaloaderPreprocessor
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from common.detection import detect_from_dirname
from common.utils import (
    FilenameGenerator,
    is_preprocessed_directory,
    update_file_timestamps,
)
from common.media.exiftool import (
    DescriptionWriteSpec,
    MetadataWriteRequest,
    PREAMBLE_WITH_IGNORE_ERRORS,
    build_datetime_tags,
)
from common.output.logging import get_processor_logger

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


class InstaloaderProcessor(ProcessorBase):
    """Processor for Instaloader scrape data (not official Instagram export)"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory.

        Detection criteria for Instaloader scrape data:
        - Files directly in root directory
        - Multiple files matching pattern: YYYY-MM-DD_HH-MM-SS_UTC.*
        - Typically has .jpg/.mp4 and .txt pairs
        """
        try:
            # Pattern for Instaloader files: YYYY-MM-DD_HH-MM-SS_UTC[_N].*
            # The optional _N suffix is for carousel posts (e.g., _1, _2)
            instaloader_pattern = re.compile(
                r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_UTC(_\d+)?\.(jpg|jpeg|mp4|txt|json(\.xz)?)$"
            )

            # Count matching files in root directory
            matching_files = 0

            for file_path in input_path.iterdir():
                if file_path.is_file() and instaloader_pattern.match(file_path.name):
                    matching_files += 1
                    # If we find at least 3 matching files, it's probably Instaloader
                    if matching_files >= 3:
                        return True

            return False

        except Exception as e:
            log.detection_failed("Instaloader", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "Instaloader"

    @staticmethod
    def get_priority() -> int:
        return 30

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Instagram",
            service="posts",
            username_prefix="instagram",
        )

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if Instaloader export needs preprocessing."""
        # Check if already preprocessed
        if is_preprocessed_directory(input_dir):
            return False

        input_path = Path(input_dir)
        # Check for raw Instaloader format (files with timestamp pattern in root)
        instaloader_pattern = re.compile(
            r"^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_UTC\.(jpg|jpeg|mp4)$"
        )
        for f in input_path.iterdir():
            if f.is_file() and instaloader_pattern.match(f.name):
                return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw Instaloader export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        logger.info("Preprocessing Instaloader export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = InstaloaderPreprocessor(
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

        def extract_preprocessed_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
            """Extract metadata from preprocessed entry."""
            result: Dict[str, Any] = {}
            if "caption" in entry:
                result["caption"] = entry["caption"]
            if "timestamp" in entry:
                result["timestamp"] = entry["timestamp"]
            return result

        catalog_iter = InstaloaderProcessor.catalog_from_preprocessed_if_available(
            processor_name="Instaloader",
            input_path=input_path,
            workers=workers,
            metadata_extractor=extract_preprocessed_metadata,
            media_files_key="media",  # Flat structure with posts
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
        """Process Instaloader scrape data

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            dedup_manifest: Optional deduplication manifest
            **kwargs: Additional arguments (workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Check for required dependencies
        if not cls.check_dependencies():
            return False

        try:
            # Choose subdirectory under provided base output
            if output_dir:
                processor_output = str(Path(output_dir) / "public-media")
            else:
                processor_output = kwargs.get("output", "final_media")

            # Call processing logic directly
            process_logic(
                input_dir=input_dir,
                output_dir=processor_output,
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.error("Error in InstaloaderProcessor: %s", e)
            return False


# ============================================================================
# Helper Functions
# ============================================================================


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str, str, Optional[str], Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Instaloader files.

    Args:
        file_info: List of (file_path, post_data, export_username, media_type,
                   content_hash, original_filename) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for (
        file_path,
        post_data,
        export_username,
        media_type,
        content_hash,
        original_filename,
    ) in file_info:
        # Build datetime tags (ISO format)
        datetime_tags = build_datetime_tags(post_data.get("canonical_timestamp"))

        # Build description using unified format
        # Map media_type to service name (story->stories, post->posts, reel->reels)
        service_map = {
            "story": SERVICE_STORIES,
            "stories": SERVICE_STORIES,
            "post": SERVICE_POSTS,
            "posts": SERVICE_POSTS,
            "reel": SERVICE_REELS,
            "reels": SERVICE_REELS,
        }
        service = service_map.get(media_type.lower(), media_type)
        entry = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username=export_username,
            service=service,
            content=post_data.get("caption", ""),
        )
        default_description = build_description(entry)

        requests.append(
            MetadataWriteRequest(
                file_path=file_path,
                arg_preamble=PREAMBLE_WITH_IGNORE_ERRORS,
                datetime_tags=datetime_tags,
                gps=None,
                description=DescriptionWriteSpec(
                    default_description=default_description,
                ),
                processor_name="instaloader",
                content_hash=content_hash,
                original_filename=original_filename,
            )
        )

    return requests


def process_media_batch(batch_args):
    """Process a batch of media files (worker function for multiprocessing)

    Copies files, corrects extensions, and updates timestamps.
    EXIF validation/rebuild and metadata writing happen in the main process.

    Args:
        batch_args: List of tuples, each containing (media_file, post_data,
                    output_filename, media_dir, output_dir, export_username,
                    media_type, content_hash, original_filename)

    Returns:
        List of (success, output_path, file_info_tuple) tuples where file_info_tuple is
        (output_path, post_data, export_username, media_type, content_hash, original_filename)
        or None on failure
    """
    results = []

    # Phase 1: Copy all files
    file_paths = []
    file_info = []

    for args_tuple in batch_args:
        (
            media_file,
            post_data,
            output_filename,
            media_dir,
            output_dir,
            export_username,
            media_type,
            content_hash,
            original_filename,
        ) = args_tuple

        media_path = os.path.join(media_dir, media_file)
        media_type_dir = os.path.join(output_dir, media_type)
        output_path = os.path.join(media_type_dir, output_filename)

        if not os.path.exists(media_path):
            logger.warning("Media file not found: %s", short_path(media_path))
            results.append((False, None, None))
            continue

        try:
            copy_file(media_path, output_path)
            file_paths.append(output_path)
            file_info.append(
                (output_path, post_data, export_username, media_type, content_hash, original_filename)
            )
        except Exception as e:  # noqa: BLE001
            logger.error("Failed to copy %s: %s", media_file, e)
            results.append((False, None, None))

    if not file_paths:
        return results if results else [(False, None, None)] * len(batch_args)

    # Phase 2: Correct file extensions based on actual content
    corrections = batch_correct_extensions([Path(p) for p in file_paths])
    if corrections:
        # Update file_paths and file_info with corrected paths
        file_paths = [corrections.get(p, p) for p in file_paths]
        file_info = [
            (corrections.get(fp, fp), pd, eu, mt, ch, of)
            for fp, pd, eu, mt, ch, of in file_info
        ]

    # Phase 3: Update timestamps and compile results
    for (
        output_path,
        post_data,
        export_username,
        media_type,
        content_hash,
        original_filename,
    ) in file_info:
        update_file_timestamps(output_path, post_data.get("timestamp"))
        logger.debug(
            "proc=instaloader src=%s dest=%s type=%s",
            Path(output_path).name,
            output_filename,
            media_type,
        )
        results.append(
            (
                True,
                output_path,
                (output_path, post_data, export_username, media_type, content_hash, original_filename),
            )
        )

    return results


def process_logic(
    input_dir,
    output_dir="final_media",
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Instaloader scrape data.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling InstaloaderProcessor.preprocess() directly).

    Args:
        input_dir: Input directory (must be preprocessed)
        output_dir: Output directory for processed files
        workers: Number of parallel workers (None = auto-detect)
        dedup_manifest: Optional deduplication manifest for filtering secondary files
    """
    # Check for exiftool
    if not InstaloaderProcessor.check_dependencies():
        return

    log.processor_start("Instaloader Media Processor")

    def _process_working_directory(working_dir: str) -> None:
        # Configuration
        metadata_file = os.path.join(working_dir, "metadata.json")

        # Check for metadata file (should exist after preprocessing or if already preprocessed)
        if not os.path.exists(metadata_file):
            logger.error("Metadata file not found: %s", short_path(metadata_file))
            return

        # Load metadata
        log.loading_metadata(short_path(metadata_file))
        metadata_json = InstaloaderProcessor.load_metadata(
            Path(metadata_file), validate=True, require_source_paths=True
        )

        export_info = metadata_json.get("export_info", {})
        media_posts = metadata_json.get("media", [])
        file_hashes = metadata_json.get("file_hashes", {})

        log.found_items(len(media_posts), "posts")

        # Extract export username from validated export_info
        export_username = export_info.get("export_username")
        log.export_username(export_username)

        # Initialize failure tracker for this export
        failure_tracker = FailureTracker(
            processor_name="Instaloader",
            export_directory=str(working_dir),
        )

        # Create output directory
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Determine number of workers
        from common.utils import default_worker_count

        num_workers = workers if workers is not None else default_worker_count()
        log.using_workers(num_workers)

        # Pre-generate all output filenames to avoid race conditions
        log.generating_filenames()
        generators: Dict[str, FilenameGenerator] = {}

        def get_filename_generator(media_type: str) -> FilenameGenerator:
            if media_type not in generators:
                generators[media_type] = FilenameGenerator(
                    "insta", export_username, service=media_type
                )
            return generators[media_type]

        processing_tasks = []
        media_types_set = set()
        skipped_dedup = 0
        skipped_secondary_files: list[str] = []
        missing_source_path = 0
        missing_examples: list[str] = []
        for post in media_posts:
            media_type = post.get("media_type", "posts")
            media_files = post.get("media_files", [])
            media_types_set.add(media_type)

            for media_file in media_files:
                # Skip secondary files based on dedup manifest (using hash-based lookup)
                # Use cached file_hashes from preprocessing when available
                content_hash = file_hashes.get(media_file)

                # Get source path from metadata
                source_paths = post.get("source_paths", {})
                full_source_path = source_paths.get(media_file)
                if not full_source_path:
                    missing_source_path += 1
                    if len(missing_examples) < 5:
                        missing_examples.append(media_file)
                    continue

                source_file_path = os.path.dirname(full_source_path)
                source_file_name = os.path.basename(full_source_path)

                if InstaloaderProcessor.should_skip_for_dedup(
                    dedup_manifest, content_hash, "Instaloader", full_source_path
                ):
                    skipped_dedup += 1
                    skipped_secondary_files.append(full_source_path)
                    continue

                # Get file extension
                file_ext = os.path.splitext(media_file)[1].lower()

                canonical_ts = get_canonical_timestamp(
                    dedup_manifest, content_hash, post.get("timestamp")
                )
                post["canonical_timestamp"] = canonical_ts

                # Generate output filename using shared FilenameGenerator (per media type)
                output_filename = get_filename_generator(media_type).generate(
                    canonical_ts,
                    file_ext,
                )

                # Get original filename for XMP:PreservedFileName embedding
                original_filename = source_file_name

                # Create task tuple for worker (include content_hash and original_filename)
                processing_tasks.append(
                    (
                        source_file_name,
                        post,
                        output_filename,
                        source_file_path,
                        output_dir,
                        export_username,
                        media_type,
                        content_hash,
                        original_filename,
                    )
                )

        if skipped_dedup > 0:
            log.skipped_secondary(skipped_dedup)
        if missing_source_path > 0:
            logger.info(
                "Missing source_path for %d files (e.g. %s)",
                missing_source_path,
                ", ".join(missing_examples),
            )

        # Ensure subdirectories exist (no per-dir logging)
        for media_type in media_types_set:
            media_type_dir = os.path.join(output_dir, media_type)
            Path(media_type_dir).mkdir(parents=True, exist_ok=True)

        # Process media files in parallel
        log.processing_start(str(output_dir))

        import time

        # Check for no media files (export-level soft-fail)
        if not processing_tasks:
            log.logger.warning("No media files found to process.")
            failure_tracker.add_export_failure(
                reason="no_media_files",
                error_details="No media files found in export to process",
            )
            failure_tracker.handle_failures(Path(output_dir))
            return False

        start_time = time.time()
        logger.debug(
            "Starting parallel processing of %d files with %d workers",
            len(processing_tasks),
            num_workers,
        )

        # Group tasks into batches of 100 files each
        batch_size = 100
        batched_tasks = []
        for i in range(0, len(processing_tasks), batch_size):
            batched_tasks.append(processing_tasks[i : i + batch_size])

        # Process batches in parallel (copy files only)
        with multiprocessing.Pool(processes=num_workers) as pool:
            batch_results = list(
                progress_bar(
                    pool.imap(process_media_batch, batched_tasks),
                    PHASE_PROCESS,
                    "Copying files",
                    total=len(batched_tasks),
                )
            )

        # Flatten results and collect successful files for EXIF processing
        collected = InstaloaderProcessor.collect_batch_processing_results(batch_results)

        processing_time = time.time() - start_time
        logger.debug(
            "Processing completed in %.2f seconds (%.2f files/sec)",
            processing_time,
            len(processing_tasks) / processing_time if processing_time else 0,
        )
        log.copied_files(collected.success_count, collected.failed_count)

        # Consolidated EXIF processing
        exif_rebuilt_count = 0
        if collected.file_paths:
            log.exif_batch_start()

            # Build metadata lookup for failure tracking
            file_info_map = {info[0]: info for info in collected.file_info}

            def metadata_for_file(file_path: str) -> Optional[dict]:
                info = file_info_map.get(file_path)
                return info[1] if info else None  # info[1] is post_data

            exif_rebuilt_count, write_outcome, missing_files = (
                InstaloaderProcessor.batch_process_exif(
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
                processor_name="Instaloader",
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
        console.print(
            f"Files organized into {len(media_types_set)} media type subfolders:"
        )
        for media_type in sorted(media_types_set):
            console.print(f"  - {media_type}/")

    # Require preprocessed input
    if not is_preprocessed_directory(input_dir):
        logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            input_dir,
        )
        return False

    logger.info("Processing preprocessed directory: %s", short_path(input_dir))
    _process_working_directory(input_dir)
