"""
Google Chat Messages Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Google Chat message media files, embedding metadata, and updating filesystem timestamps.
"""

import logging
import os
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
    PLATFORM_GOOGLE,
    SERVICE_CHAT,
)
from common.files.utils import batch_correct_extensions, copy_file, short_path
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
            **kwargs: Additional arguments (workers)

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
            **kwargs: Additional arguments (workers, temp_dir)

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
            logger.error("Error in GoogleChatProcessor: %s", e)
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
        # Build datetime tags (canonical timestamp already computed)
        datetime_tags = build_datetime_tags(message_data["canonical_timestamp"])

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
            logger.warning("Media file not found: %s", short_path(media_path))
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

    log.processor_start("Google Chat Messages Media Processor")

    # Require preprocessed input
    if not is_preprocessed_directory(input_dir):
        logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            input_dir,
        )
        return False

    logger.info("Processing preprocessed directory: %s", short_path(input_dir))
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
        logger.error("Metadata file not found: %s", short_path(metadata_file))
        return

    # Load metadata
    log.loading_metadata(short_path(metadata_file))
    metadata_json = GoogleChatProcessor.load_metadata(
        Path(metadata_file),
        validate=True,
        require_source_paths=True,
        processor_name="Google Chat",
    )

    export_info = metadata_json.get("export_info", {})
    conversations = metadata_json.get("conversations", [])

    # Count total messages with media
    total_media_files = sum(
        len(message.get("media_files", []))
        for conversation in conversations
        for message in conversation.get("messages", [])
    )

    log.found_items(len(conversations), "conversations")
    log.found_files(total_media_files, "media")

    # Extract export username from export_info (validated in load_metadata)
    export_username = export_info.get("export_username")
    log.export_username(export_username)

    # Initialize failure tracker for this export
    failure_tracker = FailureTracker(
        processor_name="Google Chat",
        export_directory=str(working_dir),
    )

    # Check for no media files (export-level soft-fail)
    if total_media_files == 0:
        log.logger.warning("No media files found to process.")
        failure_tracker.add_export_failure(
            reason="no_media_files",
            error_details="No media files found in export to process",
        )
        failure_tracker.handle_failures(Path(output_dir))
        return False

    # Create output directory (including parents)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    log.using_workers(num_workers)

    # Pre-generate all output filenames to avoid race conditions
    log.generating_filenames()
    filename_gen = FilenameGenerator("google", export_username, service="chat")
    processing_tasks = []

    # Get file_hashes lookup table for dedup optimization
    file_hashes = metadata_json.get("file_hashes", {})

    skipped_dedup = 0
    skipped_secondary_files: list[str] = []
    missing_source_path = 0
    missing_examples: list[str] = []
    for conversation in conversations:
        conversation_name = conversation.get("conversation_name", "unknown")
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
                    missing_source_path += 1
                    if len(missing_examples) < 5:
                        missing_examples.append(media_file)
                    continue

                source_file_path = os.path.dirname(full_source_path)
                source_file_name = os.path.basename(full_source_path)

                if GoogleChatProcessor.should_skip_for_dedup(
                    dedup_manifest, content_hash, "Google Chat", full_source_path
                ):
                    skipped_dedup += 1
                    skipped_secondary_files.append(full_source_path)
                    continue

                # Get file extension
                file_ext = os.path.splitext(media_file)[1].lower()

                # Canonical timestamp (dedup merged earliest preferred)
                canonical_ts = get_canonical_timestamp(
                    dedup_manifest, content_hash, message.get("timestamp")
                )
                message["canonical_timestamp"] = canonical_ts

                # Generate output filename using shared FilenameGenerator
                output_filename = filename_gen.generate(canonical_ts, file_ext)

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
        log.skipped_secondary(skipped_dedup)
    if missing_source_path > 0:
        logger.info(
            "Missing source_path for %d files (e.g. %s)",
            missing_source_path,
            ", ".join(missing_examples),
        )

    # Process media files in parallel
    log.processing_start(str(output_dir))

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

    log.copied_files(collected.success_count, collected.failed_count)

    # Consolidated EXIF processing with failure tracking
    exif_rebuilt_count = 0
    if collected.file_paths:
        log.exif_batch_start()

        # Build metadata lookup for failure tracking
        file_info_map = {info[0]: info for info in collected.file_info}

        def metadata_for_file(file_path: str) -> Optional[dict]:
            info = file_info_map.get(file_path)
            return info[1] if info else None  # info[1] is message

        exif_rebuilt_count, write_outcome, missing_files = (
            GoogleChatProcessor.batch_process_exif(
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
            processor_name="Google Chat",
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
