"""
Google Chat Messages Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Google Chat message media files, embedding metadata, and updating filesystem timestamps.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.config.runtime import default_worker_count
from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_GOOGLE,
    SERVICE_CHAT,
)
from common.dedup.service import (
    get_canonical_timestamp,
    save_dedup_metadata,
)
from common.failures import FailureTracker
from common.files.naming import FilenameGenerator
from common.files.utils import short_path
from common.media.exiftool import (
    DescriptionWriteSpec,
    MetadataWriteRequest,
    PREAMBLE_WITH_IGNORE_ERRORS,
    build_datetime_tags,
)
from common.output.logging import get_processor_logger
from common.output.paths import OutputInfo
from common.output.summary import print_processing_summary
from common.processing import process_batches_parallel
from processors.base import MediaCopyTask, ProcessorBase
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
        # Fast processor (~minutes) - run early when multiple processors match (e.g., Takeout)
        return 86

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

        # Check if already preprocessed (validated metadata schema)
        if GoogleChatProcessor.is_preprocessed_export_dir(
            input_path, container_key="conversations"
        ):
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
    def _process_impl(
        cls,
        input_dir: str,
        output_dir: str,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process Google Chat export."""
        process_logic(
            input_dir=input_dir,
            output_dir=output_dir,
            workers=kwargs.get("workers"),
            dedup_manifest=dedup_manifest,
        )
        return True


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


def process_logic(
    input_dir,
    output_dir="final_googlechat",
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Core processing logic for Google Chat exports.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling GoogleChatProcessor.preprocess() directly).

    Args:
        input_dir: Input directory (must be preprocessed)
        output_dir: Output directory for processed files
        workers: Number of parallel workers (None = auto-detect)
        dedup_manifest: Optional deduplication manifest for filtering secondary files

    Returns:
        True if processing succeeded, False otherwise.
    """
    # Check for exiftool
    if not GoogleChatProcessor.check_dependencies():
        return False

    log.processor_start("Google Chat Messages Media Processor")

    # Require preprocessed input
    if not GoogleChatProcessor.is_preprocessed_export_dir(
        input_dir, container_key="conversations"
    ):
        logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            input_dir,
        )
        return False

    logger.info("Processing preprocessed directory: %s", short_path(input_dir))
    return _process_working_directory(input_dir, output_dir, workers, dedup_manifest)


def _process_working_directory(
    working_dir,
    output_dir,
    workers,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Process a working directory (preprocessed export)

    Args:
        working_dir: Path to preprocessed directory with metadata.json
        output_dir: Output directory for processed files
        workers: Number of parallel workers
        dedup_manifest: Optional deduplication manifest for filtering secondary files

    Returns:
        True if processing succeeded, False otherwise.
    """
    # Configuration
    metadata_file = os.path.join(working_dir, "metadata.json")

    # Check for metadata file (should exist after preprocessing or if already preprocessed)
    if not os.path.exists(metadata_file):
        logger.error("Metadata file not found: %s", short_path(metadata_file))
        return False

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

                # Create task for shared worker (include content_hash and original_filename)
                processing_tasks.append(
                    MediaCopyTask(
                        source_path=full_source_path,
                        dest_path=os.path.join(output_dir, output_filename),
                        info_rest=(
                            message,
                            conversation_name,
                            export_username,
                            content_hash,
                            original_filename,
                        ),
                        timestamp_str=message.get("timestamp"),
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
        worker_fn=ProcessorBase.process_media_copy_batch,
        num_workers=num_workers,
        batch_size=100,
        description="Copying files",
    )

    # Collect successful files for EXIF processing
    collected = GoogleChatProcessor.collect_processing_results(results)

    log.copied_files(collected.success_count, collected.failed_count)

    # Correct file extensions and batch process EXIF metadata
    exif_rebuilt_count = 0
    if collected.file_paths:
        log.exif_batch_start()

        # Build metadata lookup for failure tracking
        file_info_map = {info[0]: info for info in collected.file_info}

        def metadata_for_file(file_path: str) -> Optional[dict]:
            info = file_info_map.get(file_path)
            return info[1] if info else None  # info[1] is message

        _, _, exif_rebuilt_count = GoogleChatProcessor.finalize_files(
            collected.file_paths,
            collected.file_info,
            _build_metadata_requests,
            dedup_manifest,
            path_index=0,
            failure_tracker=failure_tracker,
            metadata_for_file=metadata_for_file,
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

    return True
