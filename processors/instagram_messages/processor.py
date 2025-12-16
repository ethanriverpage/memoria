"""
Instagram Messages Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Instagram message media files, embedding metadata, and updating filesystem timestamps.
"""
import json
import logging
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from common.progress import PHASE_PROCESS, progress_bar
from processors.instagram_messages.preprocess import InstagramMessagesPreprocessor
from processors.base import ProcessorBase
from common.dependency_checker import check_exiftool, print_exiftool_error
from common.utils import extract_username_from_export_dir, sanitize_filename, should_cleanup_temp
from common.exiftool_batch import (
    batch_validate_exif,
    batch_rebuild_exif,
    batch_read_existing_metadata,
    batch_write_metadata_instagram_messages,
)

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
    def process(input_dir: str, output_dir: str = None, **kwargs) -> bool:
        """Process Instagram Messages export

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
            )
            return True

        except Exception as e:
            logger.error(f"Error in InstagramMessagesProcessor: {e}")
            return False


def get_processor():
    """Return processor class for auto-discovery

    This function is called by the unified memoria.py during
    automatic processor discovery.

    Returns:
        InstagramMessagesProcessor class (not instance, as it uses static methods)
    """
    return InstagramMessagesProcessor


# ============================================================================
# Helper Functions
# ============================================================================


def generate_unique_filename(
    message_data, conversation_title, export_username, extension, filename_counter
):
    """Generate a unique filename for a processed media file

    Format: instagram-messages-{exportUsername}-{conversationTitle}-YYYYMMDD_N.extension

    Args:
        message_data: Dict containing message metadata
        conversation_title: Title of the conversation (will be sanitized)
        export_username: Username extracted from input directory
        extension: File extension (including the dot)
        filename_counter: Dict tracking sequence numbers per date

    Returns:
        str: Generated filename
    """
    # Sanitize conversation title for filename
    sanitized_title = sanitize_filename(conversation_title)

    # Parse date from message_data
    # Format: "2022-01-06 06:47:00"
    date_str = message_data["timestamp"]

    # Handle None timestamp
    if date_str is None:
        # Use a fallback date for messages without timestamps
        date_key = "00000000"
    else:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        date_key = date_obj.strftime("%Y%m%d")

    # Track sequence number for this date
    if date_key not in filename_counter:
        filename_counter[date_key] = 1
    else:
        filename_counter[date_key] += 1

    sequence = filename_counter[date_key]

    # Generate filename
    filename = f"instagram-messages-{export_username}-{sanitized_title}-{date_key}_{sequence}{extension}"
    return filename


def update_filesystem_timestamps(file_path, message_data):
    """Update filesystem creation and modification timestamps to match message date

    Args:
        file_path: Path to the file
        message_data: Dict containing message metadata with 'timestamp' field

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Parse date from message_data
        # Format: "2022-01-06 06:47:00"
        date_str = message_data["timestamp"]

        # Skip if no timestamp
        if date_str is None:
            return False

        date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

        # Convert to Unix timestamp
        timestamp = date_obj.timestamp()

        # Update both access time and modification time
        os.utime(file_path, (timestamp, timestamp))
        return True
    except Exception as e:
        logger.warning(f"Failed to update timestamps for {file_path}: {e}")
        return False


def process_media_batch(batch_args):
    """Process a batch of media files (worker function for multiprocessing)
    
    Args:
        batch_args: List of tuples, each containing (media_file, message_data, 
                    conversation_title, output_filename, media_dir, output_dir, export_username)
    
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
            continue
        
        try:
            shutil.copy2(media_path, output_path)
            file_paths.append(output_path)
            file_info.append((output_path, message_data, conversation_title, export_username))
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
    batch_write_metadata_instagram_messages(file_info, existing_metadata_map)
    
    # Phase 4: Update timestamps and compile results
    results = []
    for output_path, message_data, _, _ in file_info:
        update_filesystem_timestamps(output_path, message_data)
        exif_rebuilt = output_path in corrupted_files
        results.append((True, False, exif_rebuilt))
    
    # Add failed results for files that didn't get copied
    while len(results) < len(batch_args):
        results.append((False, True, False))
    
    return results


def is_preprocessed(input_dir):
    """Check if input directory is already preprocessed

    Args:
        input_dir: Path to input directory

    Returns:
        bool: True if already preprocessed, False if raw export
    """
    input_path = Path(input_dir)
    metadata_file = input_path / "metadata.json"
    media_dir = input_path / "media"

    return metadata_file.exists() and media_dir.exists()


def process_logic(
    input_dir,
    output_dir="final_messages",
    temp_dir="../pre",
    verbose=False,
    workers=None,
):
    """Core processing logic for Instagram Messages exports

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

    logger.info("Instagram Messages Media Processor")
    logger.info("=" * 50)

    # Determine if input needs preprocessing
    temp_dir_created = None

    try:
        if is_preprocessed(input_dir):
            logger.info(f"Input directory is already preprocessed: {input_dir}")
            working_dir = input_dir
        else:
            logger.info(f"Input directory is raw export: {input_dir}")
            logger.info("Running preprocessing...")

            # Create temp directory for preprocessing
            temp_base = Path(temp_dir).resolve()
            temp_base.mkdir(parents=True, exist_ok=True)

            # Create unique temp subdirectory
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            temp_dir_created = temp_base / f"temp_{timestamp}_{unique_id}"
            temp_dir_created.mkdir(parents=True, exist_ok=True)

            logger.info(f"Preprocessing to: {temp_dir_created}")

            # Run preprocessing with final output directory for failure tracking
            # output_dir is already the processor-specific path
            final_output_path = Path(output_dir)
            preprocessor = InstagramMessagesPreprocessor(
                export_path=Path(input_dir),
                output_dir=temp_dir_created,
                workers=workers,
                final_output_dir=final_output_path,
            )
            preprocessor.process()

            working_dir = str(temp_dir_created)
            logger.info(f"Preprocessing complete. Using: {working_dir}")

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
        export_name = export_info.get("export_name", "")
        if export_name:
            export_username = extract_username_from_export_dir(export_name, "instagram")
        else:
            export_username = "unknown"

        logger.info(f"Export username: {export_username}")

        # Create output directory (including parents)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Determine number of workers
        from common.utils import default_worker_count

        num_workers = workers if workers is not None else default_worker_count()
        logger.debug(f"Using {num_workers} parallel workers")

        # Pre-generate all output filenames to avoid race conditions
        logger.debug("Pre-generating filenames...")
        filename_counter = {}
        processing_tasks = []

        for conversation in conversations:
            conversation_title = conversation.get("conversation_title", "unknown")
            messages = conversation.get("messages", [])

            for message in messages:
                media_files = message.get("media_files", [])

                for media_file in media_files:
                    # Get file extension
                    file_ext = os.path.splitext(media_file)[1].lower()

                    # Generate output filename
                    output_filename = generate_unique_filename(
                        message,
                        conversation_title,
                        export_username,
                        file_ext,
                        filename_counter,
                    )

                    # Create task tuple for worker
                    processing_tasks.append(
                        (
                            media_file,
                            message,
                            conversation_title,
                            output_filename,
                            media_dir,
                            output_dir,
                            export_username,
                        )
                    )

        # Process media files in parallel
        print(f"\nProcessing media files to {output_dir}/")
        print("=" * 50)

        success_count = 0
        failed_count = 0
        exif_rebuilt_count = 0

        # Group tasks into batches of 100 files each
        batch_size = 100
        batched_tasks = []
        for i in range(0, len(processing_tasks), batch_size):
            batched_tasks.append(processing_tasks[i:i + batch_size])

        # Process batches in parallel
        with multiprocessing.Pool(processes=num_workers) as pool:
            batch_results = list(
                progress_bar(
                    pool.imap(process_media_batch, batched_tasks),
                    PHASE_PROCESS,
                    "Creating files",
                    total=len(batched_tasks),
                )
            )

        # Flatten results
        results = []
        for batch_result in batch_results:
            results.extend(batch_result)

        # Aggregate results
        for success, failed, exif_rebuilt in results:
            if success:
                success_count += 1
            if failed:
                failed_count += 1
            if exif_rebuilt:
                exif_rebuilt_count += 1

        # Summary
        print("\n" + "=" * 50)
        print("Processing complete!")
        print(f"  Successfully processed: {success_count}")
        print(f"  Failed: {failed_count}")
        print(f"  EXIF structures rebuilt: {exif_rebuilt_count}")
        print(f"  Total: {len(processing_tasks)}")
        print(f"\nFinal files saved to: {os.path.abspath(output_dir)}")

    finally:
        # Clean up temp directory if it was created
        if temp_dir_created and temp_dir_created.exists():
            if should_cleanup_temp():
                logger.debug(f"Cleaning up temporary directory: {temp_dir_created}")
                shutil.rmtree(temp_dir_created)
            else:
                logger.info(f"Temp cleanup disabled. Temporary directory preserved at: {temp_dir_created}")
