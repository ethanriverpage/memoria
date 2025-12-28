"""
Google Chat Messages Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Google Chat message media files, embedding metadata, and updating filesystem timestamps.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.service import is_file_primary_by_hash
from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_GOOGLE,
    SERVICE_CHAT,
)
from common.files.utils import batch_correct_extensions, copy_file
from common.media.exiftool import (
    DescriptionWriteSpec,
    MetadataWriteRequest,
    PREAMBLE_WITH_IGNORE_ERRORS,
    build_datetime_tags,
)
from common.output.logging import get_processor_logger
from common.processing import (
    process_batches_parallel,
    print_processing_summary,
)
from common.utils import (
    default_worker_count,
    FilenameGenerator,
    is_preprocessed_directory,
    sanitize_filename,
    update_file_timestamps,
)
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.google_chat.preprocess import GoogleChatPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


class GoogleChatProcessor(ProcessorBase):
    """Processor for Google Chat exports"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory.

        Detection criteria for Google Chat:
        - Directory contains 'Google Chat/Groups/' subdirectory
        - Directory contains 'Google Chat/Users/' subdirectory
        - At least one Group folder with group_info.json and messages.json
        """
        try:
            chat_dir = input_path / "Google Chat"
            groups_dir = chat_dir / "Groups"
            users_dir = chat_dir / "Users"

            # Check if Google Chat structure exists
            if not chat_dir.exists() or not chat_dir.is_dir():
                return False

            if not groups_dir.exists() or not groups_dir.is_dir():
                return False

            if not users_dir.exists() or not users_dir.is_dir():
                return False

            # Check for at least one valid group with required files
            for group_folder in groups_dir.iterdir():
                if not group_folder.is_dir():
                    continue

                group_info = group_folder / "group_info.json"
                messages = group_folder / "messages.json"

                if group_info.exists() and messages.exists():
                    return True

            return False

        except Exception as e:
            log.detection_failed("Google Chat", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "Google Chat"

    @staticmethod
    def get_priority() -> int:
        return 50

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Google",
            service="chat",
            username_prefix="google",
        )

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if Google Chat export needs preprocessing."""
        input_path = Path(input_dir)

        # Check if already preprocessed (has metadata.json)
        if is_preprocessed_directory(input_dir):
            return False

        # Check for raw export structure (Google Chat/Groups/ or Users/)
        chat_dir = input_path / "Google Chat"
        if chat_dir.exists():
            return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw Google Chat export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        logger.info("Preprocessing Google Chat export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = GoogleChatPreprocessor(
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
            if "conversation_name" in entry:
                result["conversation"] = entry["conversation_name"]
            if "sender" in entry:
                result["sender"] = entry["sender"]
            if "timestamp" in entry:
                result["timestamp"] = entry["timestamp"]
            return result

        catalog_iter = GoogleChatProcessor.catalog_from_preprocessed_if_available(
            processor_name="Google Chat",
            input_path=input_path,
            workers=workers,
            metadata_extractor=extract_preprocessed_metadata,
            container_key="conversations",
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
        """Process Google Chat export

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
            # Choose content-type subdirectory under provided base output
            if output_dir:
                processor_output = str(Path(output_dir) / "chat")
            else:
                processor_output = kwargs.get("output", "final_googlechat/chat")

            # Call processing logic directly
            process_logic(
                input_dir=input_dir,
                output_dir=processor_output,
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.error(f"Error in GoogleChatProcessor: {e}")
            return False


# ============================================================================
# Helper Functions
# ============================================================================


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str, str, Optional[str], Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Google Chat files.

    Args:
        file_info: List of (file_path, message_data, conversation_name, export_username,
                   content_hash, original_filename) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for (
        file_path,
        message_data,
        conversation_name,
        export_username,
        content_hash,
        original_filename,
    ) in file_info:
        # Build datetime tags
        datetime_tags = build_datetime_tags(message_data["timestamp"])

        # Build description using unified format
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username=export_username,
            service=SERVICE_CHAT,
            sender=message_data["sender"],
            conversation=conversation_name,
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
                processor_name="google_chat",
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
        batch_args: List of tuples, each containing (media_file, message_data,
                    conversation_name, output_filename, media_dir, output_dir,
                    export_username, content_hash, original_filename)

    Returns:
        List of (success, output_path, file_info_tuple) tuples where file_info_tuple is
        (output_path, message_data, conversation_name, export_username, content_hash,
        original_filename) or None on failure
    """
    results = []

    # Phase 1: Copy all files
    file_paths = []
    file_info = []

    for args_tuple in batch_args:
        (
            media_file,
            message_data,
            conversation_name,
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
            logger.warning(f"Media file not found: {media_path}")
            results.append((False, None, None))
            continue

        try:
            copy_file(media_path, output_path)
            file_paths.append(output_path)
            file_info.append(
                (
                    output_path,
                    message_data,
                    conversation_name,
                    export_username,
                    content_hash,
                    original_filename,
                )
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
            (corrections.get(fp, fp), md, cn, eu, ch, of)
            for fp, md, cn, eu, ch, of in file_info
        ]

    # Phase 3: Update timestamps and compile results
    for (
        output_path,
        message_data,
        conversation_name,
        export_username,
        content_hash,
        original_filename,
    ) in file_info:
        # Google Chat uses format: "2016-08-23 16:27:32"
        timestamp_str = message_data.get("timestamp")
        if timestamp_str:
            update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")
        results.append(
            (
                True,
                output_path,
                (
                    output_path,
                    message_data,
                    conversation_name,
                    export_username,
                    content_hash,
                    original_filename,
                ),
            )
        )

    return results


def process_logic(
    input_dir,
    output_dir="final_googlechat",
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Google Chat exports.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling GoogleChatProcessor.preprocess() directly).

    Args:
        input_dir: Input directory (must be preprocessed)
        output_dir: Output directory for processed files
        workers: Number of parallel workers (None = auto-detect)
        dedup_manifest: Optional deduplication manifest for filtering secondary files
    """
    # Check for exiftool
    if not GoogleChatProcessor.check_dependencies():
        return

    logger.info("Google Chat Messages Media Processor")
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
    metadata_json = GoogleChatProcessor.load_metadata(
        Path(metadata_file), validate=True, require_source_paths=True
    )

    export_info = metadata_json.get("export_info", {})
    conversations = metadata_json.get("conversations", [])

    # Count total messages with media
    total_media_files = sum(
        len(message.get("media_files", []))
        for conversation in conversations
        for message in conversation.get("messages", [])
    )

    logger.info(f"Found {len(conversations)} conversations")
    logger.info(f"Found {total_media_files} media files to process")

    # Extract export username from export_info
    export_username = export_info.get("export_username", "unknown")
    logger.info(f"Export username: {export_username}")

    # Create output directory (including parents)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    logger.debug(f"Using {num_workers} parallel workers")

    # Pre-generate all output filenames to avoid race conditions
    logger.debug("Pre-generating filenames...")
    filename_gen = FilenameGenerator("gchat")
    processing_tasks = []

    # Get file_hashes lookup table for dedup optimization
    file_hashes = metadata_json.get("file_hashes", {})

    skipped_dedup = 0
    for conversation in conversations:
        conversation_name = conversation.get("conversation_name", "unknown")
        sanitized_name = sanitize_filename(conversation_name)
        messages = conversation.get("messages", [])

        for message in messages:
            media_files = message.get("media_files", [])

            for media_file in media_files:
                # Skip secondary files based on dedup manifest (using hash-based lookup)
                # Use cached file_hashes from preprocessing when available
                content_hash = file_hashes.get(media_file)

                # Get source path from metadata
                source_paths = message.get("source_paths", {})
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
                            dedup_manifest,
                            content_hash,
                            "Google Chat",
                            full_source_path,
                        ):
                            skipped_dedup += 1
                            continue
                    else:
                        # Fallback for files without cached hash
                        if GoogleChatProcessor.should_skip_for_dedup(
                            full_source_path, dedup_manifest, "Google Chat"
                        ):
                            skipped_dedup += 1
                            continue

                # Get file extension
                file_ext = os.path.splitext(media_file)[1].lower()

                # Generate output filename using shared FilenameGenerator
                identifier = f"{export_username}-{sanitized_name}"
                output_filename = filename_gen.generate(
                    message.get("timestamp"),
                    identifier,
                    file_ext,
                )

                # Get original filename for XMP:PreservedFileName embedding
                original_filename = source_file_name

                # Create task tuple for worker (include content_hash and original_filename)
                processing_tasks.append(
                    (
                        source_file_name,
                        message,
                        conversation_name,
                        output_filename,
                        source_file_path,
                        output_dir,
                        export_username,
                        content_hash,
                        original_filename,
                    )
                )

    if skipped_dedup > 0:
        logger.info(f"Skipped {skipped_dedup} secondary files (dedup)")

    # Process media files in parallel
    logger.info("Processing media files to %s/", output_dir)
    logger.info("=" * 50)

    # Use shared batch processing utility (copy files only)
    results = process_batches_parallel(
        tasks=processing_tasks,
        worker_fn=process_media_batch,
        num_workers=num_workers,
        batch_size=100,
        description="Copying files",
    )

    # Collect successful files for EXIF processing
    collected = GoogleChatProcessor.collect_processing_results(results)

    logger.info(
        f"Copied {collected.success_count} files ({collected.failed_count} failed)"
    )

    # Consolidated EXIF processing
    exif_rebuilt_count = 0
    if collected.file_paths:
        logger.info("Batch processing EXIF metadata...")
        exif_rebuilt_count = GoogleChatProcessor.batch_process_exif(
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
