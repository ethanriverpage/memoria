"""
Instagram Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Instagram media files, embedding metadata, and updating filesystem timestamps.
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
from common.files.utils import batch_correct_extensions, copy_file
from common.media.exiftool import (
    DescriptionWriteSpec,
    MetadataWriteRequest,
    PREAMBLE_WITH_IGNORE_ERRORS,
    build_datetime_tags,
)
from common.processing import (
    print_processing_summary,
)
from common.output.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    extract_username_from_export_dir,
    FilenameGenerator,
    is_preprocessed_directory,
    update_file_timestamps,
)
from common.output.logging import get_processor_logger
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.instagram_public_media.preprocess import InstagramPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


class InstagramPublicMediaProcessor(ProcessorBase):
    """Processor for Instagram public media exports (new format)"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory.

        Detection criteria for Instagram Public Media (New Format):
        - Directory contains 'media/posts/' or 'media/archived_posts/'
        - These directories contain YYYYMM date-organized folders
        """
        try:
            media_dir = input_path / "media"
            posts_dir = media_dir / "posts"
            archived_posts_dir = media_dir / "archived_posts"

            # Check if media directory exists
            if not media_dir.exists() or not media_dir.is_dir():
                return False

            # Check for posts or archived_posts directories
            has_posts = posts_dir.exists() and posts_dir.is_dir()
            has_archived = archived_posts_dir.exists() and archived_posts_dir.is_dir()

            if not (has_posts or has_archived):
                return False

            # Verify date-organized structure (YYYYMM folders)
            date_folder_pattern = re.compile(r"^\d{6}$")  # YYYYMM format

            # Check posts directory for date folders
            if has_posts:
                date_folders = [
                    d
                    for d in posts_dir.iterdir()
                    if d.is_dir() and date_folder_pattern.match(d.name)
                ]
                if len(date_folders) > 0:
                    return True

            # Check archived_posts directory for date folders
            if has_archived:
                date_folders = [
                    d
                    for d in archived_posts_dir.iterdir()
                    if d.is_dir() and date_folder_pattern.match(d.name)
                ]
                if len(date_folders) > 0:
                    return True

            return False

        except Exception as e:
            log.detection_failed("Instagram Public Media", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "Instagram Public Media"

    @staticmethod
    def get_priority() -> int:
        return 60

    @staticmethod
    def get_output_info() -> OutputInfo:
        # Note: service="public-media" matches the subdirectory created by process()
        # The actual service subdirs (posts, stories, reels, etc.) are inside public-media
        return OutputInfo(
            platform="Instagram",
            service="public-media",
            username_prefix="instagram",
        )

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if Instagram Public Media export needs preprocessing."""
        # Check if already preprocessed
        if is_preprocessed_directory(input_dir):
            return False

        input_path = Path(input_dir)
        # Check for raw export structure (media/posts or media/stories)
        media_dir = input_path / "media"
        if media_dir.exists():
            posts_dir = media_dir / "posts"
            stories_dir = media_dir / "stories"
            if posts_dir.exists() or stories_dir.exists():
                return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw Instagram Public Media export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        logger.info("Preprocessing Instagram Public Media export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = InstagramPreprocessor(
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

        # Instagram Public uses "media" key with posts list
        def extract_preprocessed_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
            """Extract metadata from preprocessed entry."""
            result: Dict[str, Any] = {}
            if "caption" in entry:
                result["caption"] = entry["caption"]
            if "timestamp" in entry:
                result["timestamp"] = entry["timestamp"]
            return result

        catalog_iter = (
            InstagramPublicMediaProcessor.catalog_from_preprocessed_if_available(
                processor_name="Instagram Public Media",
                input_path=input_path,
                workers=workers,
                metadata_extractor=extract_preprocessed_metadata,
                media_files_key="media",  # Flat structure with posts
            )
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
        """Process Instagram public media export

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
                processor_output = kwargs.get("output", "final_instagram")

            # Call processing logic directly
            process_logic(
                input_dir=input_dir,
                output_dir=processor_output,
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.error(f"Error in InstagramPublicMediaProcessor: {e}")
            return False


# ============================================================================
# Helper Functions
# ============================================================================


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str, str, Optional[str], Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Instagram Public Media files.

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
                processor_name="instagram_public",
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
            logger.warning(f"Media file not found: {media_path}")
            results.append((False, None, None))
            continue

        try:
            copy_file(media_path, output_path)
            file_paths.append(output_path)
            file_info.append(
                (output_path, post_data, export_username, media_type, content_hash, original_filename)
            )
        except Exception as e:
            logger.error(f"Failed to copy {media_file}: {e}")
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
    """Core processing logic for Instagram Public Media exports.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling InstagramPublicProcessor.preprocess() directly).

    Args:
        input_dir: Input directory (must be preprocessed)
        output_dir: Output directory for processed files
        workers: Number of parallel workers (None = auto-detect)
        dedup_manifest: Optional deduplication manifest for filtering secondary files
    """
    # Check for exiftool
    if not InstagramPublicMediaProcessor.check_dependencies():
        return

    logger.info("Instagram Media Processor")
    logger.info("=" * 50)

    # Require preprocessed input
    if not is_preprocessed_directory(input_dir):
        logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            input_dir,
        )
        return False

    logger.info(f"Processing preprocessed directory: {input_dir}")
    _process_working_directory(input_dir, output_dir, workers, dedup_manifest)


def _process_working_directory(
    working_dir,
    output_dir,
    workers,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Process a working directory (preprocessed export)

    Args:
        working_dir: Path to preprocessed directory with metadata.json
        output_dir: Output directory for processed files
        workers: Number of parallel workers
        dedup_manifest: Optional deduplication manifest for filtering secondary files
    """
    # Configuration
    metadata_file = os.path.join(working_dir, "metadata.json")

    # Check for metadata file (should exist after preprocessing or if already preprocessed)
    if not os.path.exists(metadata_file):
        logger.error(f"Metadata file not found: {metadata_file}")
        return

    # Load metadata
    logger.info(f"Loading metadata from {metadata_file}...")
    metadata_json = InstagramPublicMediaProcessor.load_metadata(
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
        media_type = post.get("media_type", "unknown")
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
                        dedup_manifest, content_hash, "Instagram Public Media", full_source_path
                    ):
                        skipped_dedup += 1
                        continue
                else:
                    # Fallback for files without cached hash
                    if InstagramPublicMediaProcessor.should_skip_for_dedup(
                        full_source_path, dedup_manifest, "Instagram Public Media"
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
    logger.debug(f"Creating subdirectories for {len(media_types_set)} media types...")
    for media_type in media_types_set:
        media_type_dir = os.path.join(output_dir, media_type)
        Path(media_type_dir).mkdir(parents=True, exist_ok=True)
        logger.debug(f"Created directory: {media_type_dir}")

    # Process media files in parallel
    logger.info("Processing media files to %s/", output_dir)
    logger.info("%s", "=" * 50)

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
    collected = InstagramPublicMediaProcessor.collect_batch_processing_results(
        batch_results
    )

    logger.info(
        f"Copied {collected.success_count} files ({collected.failed_count} failed)"
    )

    # Consolidated EXIF processing
    exif_rebuilt_count = 0
    if collected.file_paths:
        logger.info("Batch processing EXIF metadata...")
        exif_rebuilt_count = InstagramPublicMediaProcessor.batch_process_exif(
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
        extra_stats={
            "EXIF structures rebuilt": exif_rebuilt_count,
            "Media type subfolders": len(media_types_set),
        },
    )
