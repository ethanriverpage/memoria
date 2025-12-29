"""
Instagram Messages Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Instagram message media files, embedding metadata, and updating filesystem timestamps.
"""

import logging
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
    SERVICE_MESSAGES,
)
from common.files.utils import short_path
from common.media.exiftool import (
    DescriptionWriteSpec,
    MetadataWriteRequest,
    PREAMBLE_WITH_IGNORE_ERRORS,
    build_datetime_tags,
)
from common.processing import (
    process_batches_parallel,
    print_processing_summary,
)
from common.detection import detect_from_dirname
from common.utils import (
    default_worker_count,
    FilenameGenerator,
)
from common.output.logging import get_processor_logger
from common.output.paths import OutputInfo
from processors.base import MediaCopyTask, ProcessorBase
from processors.instagram_messages.preprocess import InstagramMessagesPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


class InstagramMessagesProcessor(ProcessorBase):
    """Processor for Instagram Messages exports"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory.

        Detection criteria for Instagram Messages:
        - Directory contains messages inbox (new, legacy, or oldest location)
        - Inbox contains conversation folders with message_N.html files
        """
        try:
            # Check all supported inbox locations (must match needs_preprocessing)
            # Current location: your_instagram_activity/messages/inbox/
            new_format_inbox = (
                input_path / "your_instagram_activity" / "messages" / "inbox"
            )
            # Legacy location: messages/inbox/
            legacy_format_inbox = input_path / "messages" / "inbox"
            # Oldest location: inbox/
            oldest_format_inbox = input_path / "inbox"

            inbox_dir = None
            if new_format_inbox.exists() and new_format_inbox.is_dir():
                inbox_dir = new_format_inbox
            elif legacy_format_inbox.exists() and legacy_format_inbox.is_dir():
                inbox_dir = legacy_format_inbox
            elif oldest_format_inbox.exists() and oldest_format_inbox.is_dir():
                inbox_dir = oldest_format_inbox

            if inbox_dir is None:
                return False

            # Check for at least one conversation folder with message_N.html files
            message_pattern = re.compile(r"^message_\d+\.html$")

            for conv_folder in inbox_dir.iterdir():
                if not conv_folder.is_dir():
                    continue

                for file_path in conv_folder.iterdir():
                    if file_path.is_file() and message_pattern.match(file_path.name):
                        return True

            return False

        except Exception as e:
            log.detection_failed("Instagram Messages", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "Instagram Messages"

    @staticmethod
    def get_priority() -> int:
        # Fast processor (~minutes) - run early when multiple processors match
        return 88

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Instagram",
            service="messages",
            username_prefix="instagram",
        )

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if Instagram Messages export needs preprocessing."""
        # Check if already preprocessed
        if InstagramMessagesProcessor.is_preprocessed_export_dir(
            Path(input_dir), container_key="conversations"
        ):
            return False

        # Check for raw export structure
        input_path = Path(input_dir)
        # Current location: your_instagram_activity/messages/inbox/
        new_inbox = input_path / "your_instagram_activity" / "messages" / "inbox"
        if new_inbox.exists():
            return True
        # Legacy location: messages/inbox/
        legacy_inbox = input_path / "messages" / "inbox"
        if legacy_inbox.exists():
            return True
        # Oldest location: inbox/
        oldest_inbox = input_path / "inbox"
        if oldest_inbox.exists():
            return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw Instagram Messages export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        logger.info("Preprocessing Instagram Messages export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = InstagramMessagesPreprocessor(
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
            if "conversation_title" in entry:
                result["conversation"] = entry["conversation_title"]
            if "sender" in entry:
                result["sender"] = entry["sender"]
            if "timestamp" in entry:
                result["timestamp"] = entry["timestamp"]
            return result

        catalog_iter = InstagramMessagesProcessor.catalog_from_preprocessed_if_available(
            processor_name="Instagram Messages",
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
        """Process Instagram Messages export

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
                processor_output = str(Path(output_dir) / "messages")
            else:
                processor_output = kwargs.get("output", "final_instamsgs")

            # Call processing logic directly
            process_logic(
                input_dir=input_dir,
                output_dir=processor_output,
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.error("Error in InstagramMessagesProcessor: %s", e)
            return False


# ============================================================================
# Helper Functions
# ============================================================================


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str, str, Optional[str], Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Instagram Messages files.

    Args:
        file_info: List of (file_path, message_data, conversation_title, export_username,
                   content_hash, original_filename) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for (
        file_path,
        message_data,
        conversation_title,
        export_username,
        content_hash,
        original_filename,
    ) in file_info:
        # Build datetime tags
        datetime_tags = build_datetime_tags(message_data.get("canonical_timestamp"))

        # Build description using unified format
        entry = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username=export_username,
            service=SERVICE_MESSAGES,
            sender=message_data["sender"],
            conversation=conversation_title,
            content=message_data.get("content", ""),
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
                processor_name="instagram_messages",
                content_hash=content_hash,
                original_filename=original_filename,
            )
        )

    return requests


def process_logic(
    input_dir,
    output_dir="final_messages",
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Core processing logic for Instagram Messages exports.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling InstagramMessagesProcessor.preprocess() directly).

    Args:
        input_dir: Input directory (must be preprocessed)
        output_dir: Output directory for processed files
        workers: Number of parallel workers (None = auto-detect)
        dedup_manifest: Optional deduplication manifest for filtering secondary files

    Returns:
        True if processing succeeded, False otherwise.
    """
    # Check for exiftool
    if not InstagramMessagesProcessor.check_dependencies():
        return False

    log.processor_start("Instagram Messages Media Processor")

    # Require preprocessed input
    if not InstagramMessagesProcessor.is_preprocessed_export_dir(
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
    logger.info("Loading metadata from %s...", short_path(metadata_file))
    metadata_json = InstagramMessagesProcessor.load_metadata(
        Path(metadata_file), validate=True, require_source_paths=True
    )

    export_info = metadata_json.get("export_info", {})
    conversations = metadata_json.get("conversations", [])
    file_hashes = metadata_json.get("file_hashes", {})

    # Count total messages with media
    total_media_files = sum(
        len(message.get("media_files", []))
        for conversation in conversations
        for message in conversation.get("messages", [])
    )

    logger.info("Found %d conversations", len(conversations))
    logger.info("Found %d media files to process", total_media_files)

    # Extract export username from validated export_info
    export_username = export_info.get("export_username")
    log.export_username(export_username)

    # Initialize failure tracker for this export
    failure_tracker = FailureTracker(
        processor_name="Instagram Messages",
        export_directory=str(working_dir),
    )

    # Create output directory (including parents)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    log.using_workers(num_workers)

    # Pre-generate all output filenames to avoid race conditions
    log.generating_filenames()
    filename_gen = FilenameGenerator("insta", export_username, service="msgs")
    processing_tasks = []

    skipped_dedup = 0
    skipped_secondary_files: list[str] = []
    missing_source_path = 0
    missing_examples: list[str] = []
    for conversation in conversations:
        conversation_title = conversation.get("conversation_title", "unknown")
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

                if InstagramMessagesProcessor.should_skip_for_dedup(
                    dedup_manifest, content_hash, "Instagram Messages", full_source_path
                ):
                    skipped_dedup += 1
                    skipped_secondary_files.append(full_source_path)
                    continue

                # Get file extension
                file_ext = os.path.splitext(media_file)[1].lower()

                canonical_ts = get_canonical_timestamp(
                    dedup_manifest, content_hash, message.get("timestamp")
                )
                message["canonical_timestamp"] = canonical_ts

                # Generate output filename using shared FilenameGenerator
                output_filename = filename_gen.generate(
                    canonical_ts,
                    file_ext,
                )

                # Get original filename for XMP:PreservedFileName embedding
                original_filename = source_file_name

                # Create task for shared worker (include content_hash and original_filename)
                processing_tasks.append(
                    MediaCopyTask(
                        source_path=full_source_path,
                        dest_path=os.path.join(output_dir, output_filename),
                        info_rest=(
                            message,
                            conversation_title,
                            export_username,
                            content_hash,
                            original_filename,
                        ),
                        timestamp_str=message.get("timestamp"),
                        timestamp_format="%Y-%m-%d %H:%M:%S",
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

    # Check for no media files (export-level soft-fail)
    if not processing_tasks:
        log.logger.warning("No media files found to process.")
        failure_tracker.add_export_failure(
            reason="no_media_files",
            error_details="No media files found in export to process",
        )
        failure_tracker.handle_failures(Path(output_dir))
        return False

    # Use shared batch processing utility (copy files only)
    results = process_batches_parallel(
        tasks=processing_tasks,
        worker_fn=ProcessorBase.process_media_copy_batch,
        num_workers=num_workers,
        batch_size=100,
        description="Copying files",
    )

    # Collect successful files for EXIF processing
    collected = InstagramMessagesProcessor.collect_processing_results(results)

    log.copied_files(collected.success_count, collected.failed_count)

    # Correct file extensions and batch process EXIF metadata
    exif_rebuilt_count = 0
    if collected.file_paths:
        log.exif_batch_start()

        # Build metadata lookup for failure tracking
        file_info_map = {info[0]: info for info in collected.file_info}

        def metadata_for_file(file_path: str) -> Optional[dict]:
            info = file_info_map.get(file_path)
            return info[1] if info else None  # info[1] is message_data

        _, _, exif_rebuilt_count = InstagramMessagesProcessor.finalize_files(
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
            processor_name="Instagram Messages",
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
