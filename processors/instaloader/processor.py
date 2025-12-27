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

from common.dedup.service import is_file_primary_by_hash
from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_INSTAGRAM,
    SERVICE_STORIES,
    SERVICE_POSTS,
    SERVICE_REELS,
)
from common.output.console import console
from common.files.utils import batch_correct_extensions, copy_file
from common.processing import print_processing_summary
from common.output.progress import PHASE_PROCESS, progress_bar
from processors.instaloader.preprocess import InstaloaderPreprocessor
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from common.utils import (
    extract_username_from_export_dir,
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
            **kwargs: Additional arguments (verbose, workers)

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
            **kwargs: Additional arguments (verbose, workers, temp_dir)

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
                verbose=kwargs.get("verbose", False),
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.error(f"Error in InstaloaderProcessor: {e}")
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
        datetime_tags = build_datetime_tags(post_data.get("timestamp"))

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

        logger.debug(f"Processing: {media_file} -> {output_filename}")

        if not os.path.exists(media_path):
            logger.warning("Media file not found: %s", media_path)
            results.append((False, None, None))
            continue

        try:
            file_size = os.path.getsize(media_path)
            logger.debug(f"Copying {media_file} ({file_size} bytes) to {output_path}")
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
        if post_data.get("timestamp"):
            logger.debug(
                f"Updating timestamps for {output_path} to {post_data['timestamp']}"
            )
        update_file_timestamps(output_path, post_data.get("timestamp"))
        logger.debug(f"Successfully processed {output_path}")
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
    verbose=False,
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Instaloader scrape data.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling InstaloaderProcessor.preprocess() directly).

    Args:
        input_dir: Input directory (must be preprocessed)
        output_dir: Output directory for processed files
        verbose: Enable verbose logging
        workers: Number of parallel workers (None = auto-detect)
        dedup_manifest: Optional deduplication manifest for filtering secondary files
    """
    # Logging is configured by the main process
    # Verbose mode enables detailed logging to file

    # Check for exiftool
    if not InstaloaderProcessor.check_dependencies():
        return

    logger.info("Instaloader Media Processor")
    logger.info("=" * 50)

    def _process_working_directory(working_dir: str) -> None:
        # Configuration
        metadata_file = os.path.join(working_dir, "metadata.json")

        # Check for metadata file (should exist after preprocessing or if already preprocessed)
        if not os.path.exists(metadata_file):
            logger.error(f"Metadata file not found: {metadata_file}")
            return

        # Load metadata
        logger.info(f"Loading metadata from {metadata_file}...")
        metadata_json = InstaloaderProcessor.load_metadata(
            Path(metadata_file), validate=True, require_source_paths=True
        )

        export_info = metadata_json.get("export_info", {})
        media_posts = metadata_json.get("media", [])
        file_hashes = metadata_json.get("file_hashes", {})

        logger.info(f"Found {len(media_posts)} posts to process")

        # Extract export username from export_info
        export_name = export_info.get("export_name", "")
        if export_name:
            export_username = extract_username_from_export_dir(export_name, "instagram")
        else:
            export_username = "unknown"

        logger.info(f"Export username: {export_username}")

        # Create output directory
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Determine number of workers
        from common.utils import default_worker_count

        num_workers = workers if workers is not None else default_worker_count()
        logger.debug(f"Using {num_workers} parallel workers")

        # Pre-generate all output filenames to avoid race conditions
        logger.debug("Pre-generating filenames...")
        filename_gen = FilenameGenerator("insta")
        processing_tasks = []
        media_types_set = set()

        skipped_dedup = 0
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
                    logger.warning(f"No source_path for file: {media_file}")
                    continue

                source_file_path = os.path.dirname(full_source_path)
                source_file_name = os.path.basename(full_source_path)

                if dedup_manifest:
                    if content_hash:
                        # Use cached hash from preprocessing (avoids re-hashing)
                        if not is_file_primary_by_hash(
                            dedup_manifest, content_hash, "Instaloader", full_source_path
                        ):
                            skipped_dedup += 1
                            continue
                    else:
                        # Fallback for files without cached hash
                        if InstaloaderProcessor.should_skip_for_dedup(
                            full_source_path, dedup_manifest, "Instaloader"
                        ):
                            skipped_dedup += 1
                            continue

                # Get file extension
                file_ext = os.path.splitext(media_file)[1].lower()

                # Generate output filename using shared FilenameGenerator
                identifier = f"{media_type}-{export_username}"
                output_filename = filename_gen.generate(
                    post.get("timestamp"),
                    identifier,
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
            logger.info(f"Skipped {skipped_dedup} secondary files (dedup)")

        # Create subdirectories for each media type
        logger.debug(
            f"Creating subdirectories for {len(media_types_set)} media types..."
        )
        for media_type in media_types_set:
            media_type_dir = os.path.join(output_dir, media_type)
            Path(media_type_dir).mkdir(parents=True, exist_ok=True)
            logger.debug("Created directory: %s", media_type_dir)

        # Process media files in parallel
        logger.info("Processing media files to %s/", output_dir)
        logger.info("%s", "=" * 50)

        import time

        start_time = time.time()
        logger.debug(
            f"Starting parallel processing of {len(processing_tasks)} files with {num_workers} workers"
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
            f"Processing completed in {processing_time:.2f} seconds "
            f"({len(processing_tasks)/processing_time:.2f} files/sec)"
        )
        logger.info(
            f"Copied {collected.success_count} files ({collected.failed_count} failed)"
        )

        # Consolidated EXIF processing
        exif_rebuilt_count = 0
        if collected.file_paths:
            logger.info("Batch processing EXIF metadata...")
            exif_rebuilt_count = InstaloaderProcessor.batch_process_exif(
                collected.file_paths,
                collected.file_info,
                _build_metadata_requests,
                dedup_manifest,
            )
            logger.info(f"EXIF metadata written for {len(collected.file_info)} files")

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

    logger.info(f"Processing preprocessed directory: {input_dir}")
    _process_working_directory(input_dir)
