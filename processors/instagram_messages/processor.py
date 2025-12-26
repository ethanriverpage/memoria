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

from common.dedup.catalog import catalog_media_files
from common.dedup.service import is_file_primary_by_hash
from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_INSTAGRAM,
    SERVICE_MESSAGES,
)
from common.dependency_checker import check_exiftool, print_exiftool_error
from common.files.utils import batch_correct_extensions, copy_file
from common.media.exiftool import (
    DescriptionWriteSpec,
    MetadataWriteRequest,
    PREAMBLE_WITH_IGNORE_ERRORS,
)
from common.processing import (
    process_batches_parallel,
    print_processing_summary,
)
from common.utils import (
    ALL_MEDIA_EXTENSIONS,
    default_worker_count,
    extract_username_from_export_dir,
    FilenameGenerator,
    is_preprocessed_directory,
    sanitize_filename,
    update_file_timestamps,
)
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.instagram_messages.preprocess import InstagramMessagesPreprocessor

# Set up logging
logger = logging.getLogger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory

    Detection criteria for Instagram Messages:
    - Directory contains messages inbox (new or legacy location)
    - Inbox contains conversation folders with message_N.html files

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is an Instagram Messages export, False otherwise
    """
    try:
        # Check both new format and legacy format paths
        new_format_inbox = input_path / "your_instagram_activity" / "messages" / "inbox"
        legacy_format_inbox = input_path / "messages" / "inbox"

        inbox_dir = None
        if new_format_inbox.exists() and new_format_inbox.is_dir():
            inbox_dir = new_format_inbox
        elif legacy_format_inbox.exists() and legacy_format_inbox.is_dir():
            inbox_dir = legacy_format_inbox

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
        logger.debug(f"Detection failed for Instagram Messages: {e}")
        return False


class InstagramMessagesProcessor(ProcessorBase):
    """Processor for Instagram Messages exports"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input"""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name"""
        return "Instagram Messages"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first)"""
        return 70  # High priority - specific path

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Instagram",
            service="messages",
            username_prefix="instagram",
        )

    @staticmethod
    def supports_dedup() -> bool:
        """Return True - Instagram Messages supports cross-processor deduplication."""
        return True

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata.

        Scans all media files in the Instagram Messages export, computes hashes,
        and returns a catalog structure without copying any files.

        Args:
            input_dir: Path to input directory
            **kwargs: Additional arguments (verbose, workers)

        Returns:
            Catalog dictionary with processor name and file entries.
        """
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        # Find inbox directory (new or legacy format)
        inbox_dir = None
        new_format = input_path / "your_instagram_activity" / "messages" / "inbox"
        legacy_format = input_path / "messages" / "inbox"
        media_dir = input_path / "media"  # Preprocessed

        if new_format.exists():
            inbox_dir = new_format
        elif legacy_format.exists():
            inbox_dir = legacy_format
        elif media_dir.exists():
            inbox_dir = media_dir

        if not inbox_dir or not inbox_dir.exists():
            logger.warning(f"No inbox directory found in: {input_path}")
            return

        media_files = []
        for file_path in inbox_dir.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in ALL_MEDIA_EXTENSIONS:
                media_files.append(file_path)

        yield from catalog_media_files(
            processor_name="Instagram Messages",
            input_path=input_path,
            media_files=media_files,
            workers=workers,
        )

    @staticmethod
    def process(
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
            **kwargs: Additional arguments (verbose, workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Check for required dependencies
        if not check_exiftool():
            print_exiftool_error()
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
                temp_dir=kwargs.get("temp_dir", "../pre"),
                verbose=kwargs.get("verbose", False),
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.error(f"Error in InstagramMessagesProcessor: {e}")
            return False


# ============================================================================
# Helper Functions
# ============================================================================


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str, str]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Instagram Messages files.

    Args:
        file_info: List of (file_path, message_data, conversation_title, export_username) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for file_path, message_data, conversation_title, export_username in file_info:
        # Build datetime tags
        datetime_tags: List[Tuple[str, str]] = []
        date_str = message_data.get("timestamp")
        if date_str:
            exif_date = date_str.replace("-", ":")
            datetime_tags = [
                ("DateTimeOriginal", exif_date),
                ("CreateDate", exif_date),
                ("ModifyDate", exif_date),
            ]

        # Build description using unified format
        entry = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username=export_username,
            service=SERVICE_MESSAGES,
            sender=message_data["sender"],
            conversation=conversation_title,
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
            )
        )

    return requests


def process_media_batch(batch_args):
    """Process a batch of media files (worker function for multiprocessing)

    Copies files, corrects extensions, and updates timestamps.
    EXIF validation/rebuild and metadata writing happen in the main process.

    Args:
        batch_args: List of tuples, each containing (media_file, message_data,
                    conversation_title, output_filename, media_dir, output_dir, export_username)

    Returns:
        List of (success, output_path, file_info_tuple) tuples where file_info_tuple is
        (output_path, message_data, conversation_title, export_username) or None on failure
    """
    results = []

    # Phase 1: Copy all files
    file_paths = []
    file_info = []

    for args_tuple in batch_args:
        (
            media_file,
            message_data,
            conversation_title,
            output_filename,
            media_dir,
            output_dir,
            export_username,
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
                (output_path, message_data, conversation_title, export_username)
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
            (corrections.get(fp, fp), md, ct, eu) for fp, md, ct, eu in file_info
        ]

    # Phase 3: Update timestamps and compile results
    for output_path, message_data, conversation_title, export_username in file_info:
        # Instagram Messages uses format: "2022-01-06 06:47:00"
        timestamp_str = message_data.get("timestamp")
        if timestamp_str:
            update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")
        results.append(
            (
                True,
                output_path,
                (output_path, message_data, conversation_title, export_username),
            )
        )

    return results


def process_logic(
    input_dir,
    output_dir="final_messages",
    temp_dir="../pre",
    verbose=False,
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Instagram Messages exports

    Args:
        input_dir: Input directory (raw export or preprocessed)
        output_dir: Output directory for processed files
        temp_dir: Directory for temporary preprocessing files
        verbose: Enable verbose logging
        workers: Number of parallel workers (None = auto-detect)
        dedup_manifest: Optional deduplication manifest for filtering secondary files
    """
    # Logging is configured by the main process
    # Verbose mode enables detailed logging to file

    # Check for exiftool
    if not check_exiftool():
        print_exiftool_error()
        return

    logger.info("Instagram Messages Media Processor")
    logger.info("=" * 50)

    # Check if input is already preprocessed
    if is_preprocessed_directory(input_dir):
        logger.info(f"Input directory is already preprocessed: {input_dir}")
        _process_working_directory(input_dir, output_dir, workers, dedup_manifest)
    else:
        logger.info(f"Input directory is raw export: {input_dir}")
        logger.info("Running preprocessing...")

        # Use context manager for temp directory with automatic cleanup
        with InstagramMessagesPreprocessor.temp_output_dir(temp_dir, "instagram_msgs") as temp_dir_path:
            logger.info(f"Preprocessing to: {temp_dir_path}")

            # Run preprocessing with final output directory for failure tracking
            final_output_path = Path(output_dir)
            preprocessor = InstagramMessagesPreprocessor(
                export_path=Path(input_dir),
                output_dir=temp_dir_path,
                workers=workers,
                final_output_dir=final_output_path,
            )
            if not preprocessor.run():
                logger.warning(
                    "Preprocessing failed - export may not have required structure"
                )
                return False

            logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
            _process_working_directory(
                str(temp_dir_path), output_dir, workers, dedup_manifest
            )


def _process_working_directory(
    working_dir,
    output_dir,
    workers,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Process a working directory (preprocessed export)

    Args:
        working_dir: Path to preprocessed directory with metadata.json and media/
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
    metadata_json = InstagramMessagesProcessor.load_metadata(Path(metadata_file))

    export_info = metadata_json.get("export_info", {})
    conversations = metadata_json.get("conversations", [])
    file_hashes = metadata_json.get("file_hashes", {})

    # Count total messages with media
    total_media_files = sum(
        len(message.get("media_files", []))
        for conversation in conversations
        for message in conversation.get("messages", [])
    )

    logger.info(f"Found {len(conversations)} conversations")
    logger.info(f"Found {total_media_files} media files to process")

    # Check if this is new format (source_paths in messages) or legacy format
    sample_message = None
    for conv in conversations:
        for msg in conv.get("messages", []):
            if msg.get("media_files"):
                sample_message = msg
                break
        if sample_message:
            break
    use_legacy_format = sample_message is None or "source_paths" not in sample_message

    if use_legacy_format:
        media_dir = os.path.join(working_dir, "media")
        if not os.path.exists(media_dir):
            logger.error(f"Media directory not found: {media_dir}")
            return
        logger.info("Using legacy format (media/ directory)")
    else:
        media_dir = None
        logger.info("Using new format (source_path in metadata)")

    # Extract export username from export_info
    export_name = export_info.get("export_name", "")
    if export_name:
        export_username = extract_username_from_export_dir(export_name, "instagram")
    else:
        export_username = "unknown"

    logger.info(f"Export username: {export_username}")

    # Create output directory (including parents)
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    logger.debug(f"Using {num_workers} parallel workers")

    # Pre-generate all output filenames to avoid race conditions
    logger.debug("Pre-generating filenames...")
    filename_gen = FilenameGenerator("insta-messages")
    processing_tasks = []

    skipped_dedup = 0
    for conversation in conversations:
        conversation_title = conversation.get("conversation_title", "unknown")
        sanitized_title = sanitize_filename(conversation_title)
        messages = conversation.get("messages", [])

        for message in messages:
            media_files = message.get("media_files", [])

            for media_file in media_files:
                # Skip secondary files based on dedup manifest (using hash-based lookup)
                # Use cached file_hashes from preprocessing when available
                content_hash = file_hashes.get(media_file)

                # Determine source path based on format
                if use_legacy_format:
                    source_file_path = media_dir
                    source_file_name = media_file
                else:
                    source_paths = message.get("source_paths", {})
                    source_file_path = os.path.dirname(source_paths.get(media_file, ""))
                    source_file_name = os.path.basename(source_paths.get(media_file, media_file))

                if dedup_manifest:
                    # Get full source path for within-processor dedup
                    full_source_path = source_paths.get(media_file) if not use_legacy_format else None
                    if content_hash:
                        # Use cached hash from preprocessing (avoids re-hashing)
                        if not is_file_primary_by_hash(
                            dedup_manifest, content_hash, "Instagram Messages", full_source_path
                        ):
                            skipped_dedup += 1
                            continue
                    else:
                        # Fallback for files without cached hash
                        full_path = os.path.join(source_file_path, source_file_name) if source_file_path else source_file_name
                        if InstagramMessagesProcessor.should_skip_for_dedup(
                            full_path, dedup_manifest, "Instagram Messages"
                        ):
                            skipped_dedup += 1
                            continue

                # Get file extension
                file_ext = os.path.splitext(media_file)[1].lower()

                # Generate output filename using shared FilenameGenerator
                identifier = f"{export_username}-{sanitized_title}"
                output_filename = filename_gen.generate(
                    message.get("timestamp"),
                    identifier,
                    file_ext,
                )

                # Create task tuple for worker
                processing_tasks.append(
                    (
                        source_file_name,
                        message,
                        conversation_title,
                        output_filename,
                        source_file_path,
                        output_dir,
                        export_username,
                    )
                )

    if skipped_dedup > 0:
        logger.info(f"Skipped {skipped_dedup} secondary files (dedup)")

    # Process media files in parallel
    logger.info("Processing media files to %s/", output_dir)
    logger.info("%s", "=" * 50)

    # Use shared batch processing utility (copy files only)
    results = process_batches_parallel(
        tasks=processing_tasks,
        worker_fn=process_media_batch,
        num_workers=num_workers,
        batch_size=100,
        description="Copying files",
    )

    # Collect successful files for EXIF processing
    success_count = 0
    failed_count = 0
    file_paths = []
    file_info = []

    for success, output_path, info_tuple in results:
        if success and output_path:
            success_count += 1
            file_paths.append(output_path)
            file_info.append(info_tuple)
        else:
            failed_count += 1

    logger.info(f"Copied {success_count} files ({failed_count} failed)")

    # Consolidated EXIF processing
    exif_rebuilt_count = 0
    if file_paths:
        logger.info("Batch processing EXIF metadata...")
        exif_rebuilt_count = InstagramMessagesProcessor.batch_process_exif(
            file_paths, file_info, _build_metadata_requests, dedup_manifest
        )
        logger.info(f"EXIF metadata written for {len(file_info)} files")

    # Print summary using shared utility
    print_processing_summary(
        success=success_count,
        failed=failed_count,
        total=len(processing_tasks),
        output_dir=output_dir,
        extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
    )


def get_processor():
    """Return processor class for auto-discovery."""
    return InstagramMessagesProcessor
