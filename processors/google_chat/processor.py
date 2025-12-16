"""
Google Chat Messages Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Google Chat message media files, embedding metadata, and updating filesystem timestamps.
"""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from common.dependency_checker import check_exiftool, print_exiftool_error
from common.exiftool_batch import (
    batch_validate_exif,
    batch_rebuild_exif,
    batch_read_existing_metadata,
    batch_write_metadata_google_chat,
)
from common.processing import (
    process_batches_parallel,
    print_processing_summary,
    temp_processing_directory,
)
from common.utils import (
    default_worker_count,
    is_preprocessed_directory,
    sanitize_filename,
    update_file_timestamps,
)
from processors.base import ProcessorBase
from processors.google_chat.preprocess import GoogleChatPreprocessor

# Set up logging
logger = logging.getLogger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory

    Detection criteria for Google Chat:
    - Directory contains 'Google Chat/Groups/' subdirectory
    - Directory contains 'Google Chat/Users/' subdirectory
    - At least one Group folder with group_info.json and messages.json

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is a Google Chat export, False otherwise
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
        logger.debug(f"Detection failed for Google Chat: {e}")
        return False


class GoogleChatProcessor(ProcessorBase):
    """Processor for Google Chat exports"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input"""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name"""
        return "Google Chat"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first)"""
        return 50  # Medium priority

    @staticmethod
    def process(input_dir: str, output_dir: Optional[str] = None, **kwargs) -> bool:
        """Process Google Chat export

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            **kwargs: Additional arguments (verbose, workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Check for required dependencies
        if not check_exiftool():
            print_exiftool_error()
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
                temp_dir=kwargs.get("temp_dir", "../pre"),
                verbose=kwargs.get("verbose", False),
                workers=kwargs.get("workers"),
            )
            return True

        except Exception as e:
            logger.error(f"Error in GoogleChatProcessor: {e}")
            return False


def get_processor():
    """Return processor class for auto-discovery

    This function is called by the unified memoria.py during
    automatic processor discovery.

    Returns:
        GoogleChatProcessor class (not instance, as it uses static methods)
    """
    return GoogleChatProcessor


# ============================================================================
# Helper Functions
# ============================================================================


def generate_unique_filename(
    message_data, conversation_name, export_username, extension, used_filenames
):
    """Generate a unique filename for a processed media file

    Format: googlechat-{exportUsername}-{conversationName}-YYYYMMDD.extension
    If duplicate, adds _N suffix: googlechat-{exportUsername}-{conversationName}-YYYYMMDD_N.extension

    Args:
        message_data: Dict containing message metadata
        conversation_name: Name of the conversation (original with spaces)
        export_username: Username extracted from metadata
        extension: File extension (including the dot)
        used_filenames: Set tracking already-used filenames

    Returns:
        str: Generated filename
    """
    # Sanitize conversation name for use in filename
    sanitized_name = sanitize_filename(conversation_name)

    # Parse date from message_data
    # Format: "2016-08-23 16:27:32"
    date_str = message_data["timestamp"]

    # Handle None timestamp
    if date_str is None:
        # Use a fallback date for messages without timestamps
        date_key = "00000000"
    else:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        date_key = date_obj.strftime("%Y%m%d")

    # Generate base filename without sequence
    base_filename = f"gchat-{export_username}-{sanitized_name}-{date_key}{extension}"

    # Check if filename is unique
    if base_filename not in used_filenames:
        used_filenames.add(base_filename)
        return base_filename

    # If not unique, try adding sequence numbers
    sequence = 1
    while True:
        filename = (
            f"gchat-{export_username}-{sanitized_name}-{date_key}_{sequence}{extension}"
        )
        if filename not in used_filenames:
            used_filenames.add(filename)
            return filename
        sequence += 1


def process_media_batch(batch_args):
    """Process a batch of media files (worker function for multiprocessing)

    Args:
        batch_args: List of tuples, each containing (media_file, message_data,
                    conversation_name, output_filename, media_dir, output_dir, export_username)

    Returns:
        List of (success, failed, exif_rebuilt) tuples
    """
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
        ) = args_tuple

        media_path = os.path.join(media_dir, media_file)
        output_path = os.path.join(output_dir, output_filename)

        if not os.path.exists(media_path):
            logger.warning(f"Media file not found: {media_path}")
            continue

        try:
            shutil.copy2(media_path, output_path)
            file_paths.append(output_path)
            file_info.append(
                (output_path, message_data, conversation_name, export_username)
            )
        except Exception as e:
            logger.error(f"Failed to copy {media_file}: {e}")

    if not file_paths:
        return [(False, True, False)] * len(batch_args)

    # Phase 2: Batch validate and rebuild
    corrupted_files = batch_validate_exif(file_paths)
    if corrupted_files:
        batch_rebuild_exif(list(corrupted_files))

    # Phase 3: Batch read metadata, then batch write
    existing_metadata_map = batch_read_existing_metadata(file_paths)
    batch_write_metadata_google_chat(file_info, existing_metadata_map)

    # Phase 4: Update timestamps and compile results
    results = []
    for output_path, message_data, _, _ in file_info:
        # Google Chat uses format: "2016-08-23 16:27:32"
        timestamp_str = message_data.get("timestamp")
        if timestamp_str:
            update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")
        exif_rebuilt = output_path in corrupted_files
        results.append((True, False, exif_rebuilt))

    # Add failed results for files that didn't get copied
    while len(results) < len(batch_args):
        results.append((False, True, False))

    return results


def process_logic(
    input_dir,
    output_dir="final_googlechat",
    temp_dir="../pre",
    verbose=False,
    workers=None,
):
    """Core processing logic for Google Chat exports

    Args:
        input_dir: Input directory (raw export or preprocessed)
        output_dir: Output directory for processed files
        temp_dir: Directory for temporary preprocessing files
        verbose: Enable verbose logging
        workers: Number of parallel workers (None = auto-detect)
    """
    # Logging is configured by the main process
    # Verbose mode enables detailed logging to file

    # Check for exiftool
    if not check_exiftool():
        print_exiftool_error()
        return

    logger.info("Google Chat Messages Media Processor")
    logger.info("=" * 50)

    # Check if input is already preprocessed
    if is_preprocessed_directory(input_dir):
        logger.info(f"Input directory is already preprocessed: {input_dir}")
        _process_working_directory(input_dir, output_dir, workers)
    else:
        logger.info(f"Input directory is raw export: {input_dir}")
        logger.info("Running preprocessing...")

        # Use context manager for temp directory with automatic cleanup
        with temp_processing_directory(temp_dir, "gchat") as temp_dir_path:
            logger.info(f"Preprocessing to: {temp_dir_path}")

            # Run preprocessing with final output directory for failure tracking
            final_output_path = Path(output_dir)
            preprocessor = GoogleChatPreprocessor(
                export_path=Path(input_dir),
                output_dir=temp_dir_path,
                workers=workers,
                final_output_dir=final_output_path,
            )
            preprocessor.process()

            logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
            _process_working_directory(str(temp_dir_path), output_dir, workers)


def _process_working_directory(working_dir, output_dir, workers):
    """Process a working directory (preprocessed export)

    Args:
        working_dir: Path to preprocessed directory with metadata.json and media/
        output_dir: Output directory for processed files
        workers: Number of parallel workers
    """
    # Configuration
    metadata_file = os.path.join(working_dir, "metadata.json")
    media_dir = os.path.join(working_dir, "media")

    # Check for metadata file (should exist after preprocessing or if already preprocessed)
    if not os.path.exists(metadata_file):
        logger.error(f"Metadata file not found: {metadata_file}")
        return

    # Check for media directory
    if not os.path.exists(media_dir):
        logger.error(f"Media directory not found: {media_dir}")
        return

    # Load metadata
    logger.info(f"Loading metadata from {metadata_file}...")
    with open(metadata_file, "r", encoding="utf-8") as f:
        metadata_json = json.load(f)

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
    used_filenames = set()
    processing_tasks = []

    for conversation in conversations:
        conversation_name = conversation.get("conversation_name", "unknown")
        messages = conversation.get("messages", [])

        for message in messages:
            media_files = message.get("media_files", [])

            for media_file in media_files:
                # Get file extension
                file_ext = os.path.splitext(media_file)[1].lower()

                # Generate output filename
                output_filename = generate_unique_filename(
                    message,
                    conversation_name,
                    export_username,
                    file_ext,
                    used_filenames,
                )

                # Create task tuple for worker
                processing_tasks.append(
                    (
                        media_file,
                        message,
                        conversation_name,
                        output_filename,
                        media_dir,
                        output_dir,
                        export_username,
                    )
                )

    # Process media files in parallel
    print(f"\nProcessing media files to {output_dir}/")
    logger.info("=" * 50)

    # Use shared batch processing utility
    results = process_batches_parallel(
        tasks=processing_tasks,
        worker_fn=process_media_batch,
        num_workers=num_workers,
        batch_size=100,
        description="Creating files",
    )

    # Aggregate results
    success_count = 0
    failed_count = 0
    exif_rebuilt_count = 0

    for success, failed, exif_rebuilt in results:
        if success:
            success_count += 1
        if failed:
            failed_count += 1
        if exif_rebuilt:
            exif_rebuilt_count += 1

    # Print summary using shared utility
    print_processing_summary(
        success=success_count,
        failed=failed_count,
        total=len(processing_tasks),
        output_dir=output_dir,
        extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
    )
