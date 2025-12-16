#!/usr/bin/env python3
"""
iMessage Processor

This processor handles iMessage exports from Mac and iPhone backups.
It supports cross-export consolidation for deduplication across multiple devices.
"""

import json
import logging
import shutil
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import List, Optional

from common.dependency_checker import check_exiftool, print_exiftool_error
from common.exiftool_batch import (
    batch_read_existing_metadata,
    batch_rebuild_exif,
    batch_validate_exif,
    batch_write_metadata_imessage,
)
from common.processing import (
    print_processing_summary,
    temp_processing_directory,
)
from common.progress import PHASE_PROCESS, progress_bar
from common.utils import default_worker_count, sanitize_filename, update_file_timestamps
from processors.base import ProcessorBase
from processors.imessage.preprocess import IMessagePreprocessor

logger = logging.getLogger(__name__)


# ============================================================================
# Processor Detection and Registration
# ============================================================================


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory.

    Detection criteria for iMessage:
    - Mac export: chat.db + Attachments/ directory
    - iPhone export: SMS/sms.db + SMS/Attachments/ directory
    - Preprocessed: metadata.json with conversations + media/ directory

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is an iMessage export, False otherwise
    """
    try:
        # Check for Mac export: chat.db + Attachments/
        if (input_path / "chat.db").exists() and (input_path / "Attachments").is_dir():
            return True

        # Check for iPhone export: SMS/sms.db + SMS/Attachments/
        sms_dir = input_path / "SMS"
        if sms_dir.exists():
            if (sms_dir / "sms.db").exists() and (sms_dir / "Attachments").is_dir():
                return True

        # Check for preprocessed iMessage export
        metadata_file = input_path / "metadata.json"
        media_dir = input_path / "media"

        if metadata_file.exists() and media_dir.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                # iMessage preprocessed exports have conversations and export_paths
                if isinstance(metadata, dict) and "conversations" in metadata:
                    export_info = metadata.get("export_info", {})
                    # Check for iMessage-specific markers
                    export_paths = export_info.get("export_paths", [])
                    if export_paths:
                        # Check if any path looks like an iMessage export
                        for path in export_paths:
                            if "messages" in path.lower() or "sms" in path.lower():
                                return True
            except (json.JSONDecodeError, KeyError):
                pass

        return False

    except Exception as e:
        logger.debug(f"Detection failed for iMessage: {e}")
        return False


class IMessageProcessor(ProcessorBase):
    """Processor for iMessage exports from Mac and iPhone."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input."""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name."""
        return "iMessage"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first).

        Priority 80: High priority - requires specific SQLite database structure.
        """
        return 80

    @staticmethod
    def supports_consolidation() -> bool:
        """Enable cross-export consolidation for iMessage.

        When True, memoria.py will group all detected iMessage exports
        and call process_consolidated() instead of process() for each.
        """
        return True

    @staticmethod
    def process_consolidated(
        input_dirs: List[str], output_dir: str = None, **kwargs
    ) -> bool:
        """Process multiple iMessage exports as a consolidated unit.

        Called by memoria.py when CONSOLIDATE_EXPORTS=true (default) and
        multiple iMessage exports are detected. Enables cross-export
        deduplication before any files are copied.

        Args:
            input_dirs: List of paths to iMessage export directories
            output_dir: Base output directory
            **kwargs: Additional arguments (verbose, workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Create messages subdirectory under output
        if output_dir:
            processor_output = str(Path(output_dir) / "messages")
        else:
            processor_output = kwargs.get("output", "final_imessage/messages")

        return process_logic(
            input_dirs=input_dirs,
            output_dir=processor_output,
            temp_dir=kwargs.get("temp_dir", "../pre"),
            verbose=kwargs.get("verbose", False),
            workers=kwargs.get("workers"),
        )

    @staticmethod
    def process(input_dir: str, output_dir: Optional[str] = None, **kwargs) -> bool:
        """Process a single iMessage export.

        Delegates to process_consolidated with a single-element list,
        ensuring the same code path handles both modes.

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            **kwargs: Additional arguments (verbose, workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        return IMessageProcessor.process_consolidated([input_dir], output_dir, **kwargs)


def get_processor():
    """Return processor class for auto-discovery.

    This function is called by memoria.py during automatic processor discovery.

    Returns:
        IMessageProcessor class
    """
    return IMessageProcessor


# ============================================================================
# Helper Functions
# ============================================================================


def is_preprocessed(input_path: Path) -> bool:
    """Check if input directory is already preprocessed.

    Args:
        input_path: Path to input directory

    Returns:
        True if already preprocessed, False if raw export
    """
    metadata_file = input_path / "metadata.json"
    media_dir = input_path / "media"

    if not metadata_file.exists() or not media_dir.exists():
        return False

    # Verify it's an iMessage preprocessed export
    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        return "conversations" in metadata and "export_info" in metadata
    except (json.JSONDecodeError, KeyError):
        return False


def load_metadata(metadata_path: Path) -> dict:
    """Load and parse metadata.json.

    Args:
        metadata_path: Path to metadata.json

    Returns:
        Parsed metadata dictionary
    """
    with open(metadata_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_filename_index(metadata: dict) -> dict:
    """Build index mapping media_file to message metadata.

    Args:
        metadata: Parsed metadata.json content

    Returns:
        Dict mapping media_file -> message dict
    """
    filename_index = {}

    for _conv_id, conv_data in metadata.get("conversations", {}).items():
        for message in conv_data.get("messages", []):
            media_file = message.get("media_file")
            if media_file:
                filename_index[media_file] = message

    for message in metadata.get("orphaned_media", []):
        media_file = message.get("media_file")
        if media_file:
            filename_index[media_file] = message

    return filename_index


def generate_imessage_filename(
    message: dict, export_username: str, extension: str, used_filenames: set
) -> str:
    """Generate unique filename for an iMessage attachment.

    Format: imessage-{contact_or_group}-{YYYYMMDD}_{seq}.{ext}

    Args:
        message: Message metadata dict
        export_username: Username/device identifier from export (unused, kept for API compatibility)
        extension: File extension (including dot)
        used_filenames: Set tracking already-used filenames

    Returns:
        Generated filename
    """
    # Note: export_username parameter kept for API compatibility but not used in filename
    _ = export_username

    # Check if this is a merged/duplicate message
    is_merged = "messages" in message and isinstance(message["messages"], list)

    # Get date string
    if is_merged:
        date_str = message.get("primary_created")
        if not date_str and message["messages"]:
            date_str = message["messages"][0].get("created")
    else:
        date_str = message.get("created")

    # Parse date
    if date_str:
        try:
            # Format: "YYYY-MM-DD HH:MM:SS UTC"
            date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S UTC")
        except ValueError:
            date_obj = datetime.now()
    else:
        date_obj = datetime.now()

    date_key = date_obj.strftime("%Y%m%d")

    # Determine conversation identifier
    if is_merged:
        # For merged messages, use simplified naming
        conv_prefix = "merged"
    else:
        conv_type = message.get("conversation_type")
        conv_id = message.get("conversation_id", "")
        conv_title = message.get("conversation_title")

        if conv_type == "group" and conv_title:
            conv_prefix = sanitize_filename(conv_title)
        elif conv_id:
            # For DMs, use the contact identifier
            conv_prefix = sanitize_filename(conv_id)
        else:
            conv_prefix = "unknown"

    # Truncate if too long
    if len(conv_prefix) > 30:
        conv_prefix = conv_prefix[:30]

    # Generate base filename
    base_filename = f"imessage-{conv_prefix}-{date_key}{extension}"

    # Check for collisions
    if base_filename not in used_filenames:
        used_filenames.add(base_filename)
        return base_filename

    # Add sequence number
    sequence = 2
    while True:
        filename = f"imessage-{conv_prefix}-{date_key}_{sequence}{extension}"
        if filename not in used_filenames:
            used_filenames.add(filename)
            return filename
        sequence += 1


def _extract_message_timestamp(message: dict) -> str | None:
    """Extract timestamp string from iMessage message metadata.

    Args:
        message: Message metadata with 'created' or 'primary_created' field

    Returns:
        Timestamp string or None if not found
    """
    if "primary_created" in message:
        return message["primary_created"]
    elif "messages" in message and message["messages"]:
        return message["messages"][0].get("created")
    else:
        return message.get("created")


def _process_file_worker(args_tuple):
    """Worker function for processing files (Phase 1: copy and rename).

    Args:
        args_tuple: Tuple of (media_path, message, export_username, output_dir, output_filename)

    Returns:
        Tuple of (success, output_path, message, export_username)
    """
    media_path, message, export_username, output_dir, output_filename = args_tuple

    try:
        output_path = output_dir / output_filename

        # Copy file
        shutil.copy2(media_path, output_path)

        # Update timestamps
        timestamp_str = _extract_message_timestamp(message)
        update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")

        return (True, str(output_path), message, export_username)

    except Exception as e:
        logger.error(f"Error processing {media_path.name}: {e}")
        return (False, None, message, export_username)


# ============================================================================
# Main Processing Logic
# ============================================================================


def process_logic(
    input_dirs: List[str],
    output_dir: str = None,
    temp_dir: str = "../pre",
    verbose: bool = False,  # noqa: ARG001 - Reserved for future detailed logging
    workers: Optional[int] = None,
) -> bool:
    """Core processing logic for iMessage exports.

    Args:
        input_dirs: List of input directories (raw exports or preprocessed)
        output_dir: Output directory for processed files (final path, no subdirs created)
        temp_dir: Directory for temporary preprocessing files
        verbose: Enable verbose logging
        workers: Number of parallel workers

    Returns:
        True if processing succeeded, False otherwise
    """
    # Check for required tools
    if not check_exiftool():
        print_exiftool_error()
        return False

    logger.info("iMessage Processor")
    logger.info("=" * 50)
    logger.info(f"Processing {len(input_dirs)} export(s)")

    # Convert to paths
    input_paths = [Path(d) for d in input_dirs]

    # Determine output directory (use as-is, subdirectory created by caller)
    if output_dir:
        messages_output_dir = Path(output_dir)
    else:
        messages_output_dir = Path("final_imessage/messages")

    messages_output_dir.mkdir(parents=True, exist_ok=True)

    # Check if all inputs are already preprocessed
    all_preprocessed = all(is_preprocessed(p) for p in input_paths)

    if all_preprocessed and len(input_paths) == 1:
        # Single preprocessed export - use directly
        working_dir = input_paths[0]
        logger.info(f"Using preprocessed export: {working_dir}")
        return _process_working_directory(working_dir, messages_output_dir, workers)
    else:
        # Need to run preprocessing (handles raw exports and consolidation)
        logger.info("Running preprocessing...")

        # Use context manager for temp directory with automatic cleanup
        with temp_processing_directory(temp_dir, "imessage") as temp_dir_path:
            logger.info(f"Preprocessing to: {temp_dir_path}")

            # Run preprocessor with all exports
            preprocessor = IMessagePreprocessor(
                export_paths=input_paths,
                output_dir=temp_dir_path,
                workers=workers,
            )

            if not preprocessor.process():
                logger.error("Preprocessing failed")
                return False

            logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
            return _process_working_directory(
                temp_dir_path, messages_output_dir, workers
            )


def _process_working_directory(
    working_dir: Path, output_dir: Path, workers: Optional[int]
) -> bool:
    """Process a working directory (preprocessed export).

    Args:
        working_dir: Path to preprocessed directory with metadata.json and media/
        output_dir: Output directory for processed files
        workers: Number of parallel workers

    Returns:
        True if processing succeeded, False otherwise
    """
    # Load metadata
    metadata_file = working_dir / "metadata.json"
    media_dir = working_dir / "media"

    if not metadata_file.exists():
        logger.error(f"Metadata file not found: {metadata_file}")
        return False

    if not media_dir.exists():
        logger.error(f"Media directory not found: {media_dir}")
        return False

    logger.info(f"Loading metadata from {metadata_file}...")
    metadata = load_metadata(metadata_file)

    export_username = metadata.get("export_info", {}).get("export_username", "unknown")
    logger.info(f"Export identifier: {export_username}")

    # Build filename index
    filename_index = build_filename_index(metadata)
    logger.info(f"Found {len(filename_index)} media files to process")

    # Scan media directory
    media_files = list(media_dir.iterdir())
    media_files = [f for f in media_files if f.is_file()]
    logger.info(f"Found {len(media_files)} files in media directory")

    # Match files to messages and pre-generate filenames
    logger.info("Matching files and generating filenames...")
    used_filenames = set()
    matched_files = []

    for media_file in media_files:
        if media_file.name in filename_index:
            message = filename_index[media_file.name]
            file_ext = media_file.suffix.lower()

            output_filename = generate_imessage_filename(
                message, export_username, file_ext, used_filenames
            )
            matched_files.append((media_file, message, output_filename))
        else:
            logger.warning(f"No metadata for file: {media_file.name}")

    logger.info(f"Matched {len(matched_files)} files")

    # Process files
    print(f"\nProcessing files to {output_dir}/")
    logger.info("=" * 50)

    num_workers = workers if workers is not None else default_worker_count()
    logger.info(f"Using {num_workers} workers")

    # Prepare arguments
    process_args = [
        (media_file, message, export_username, output_dir, output_filename)
        for media_file, message, output_filename in matched_files
    ]

    # Phase 1: Copy and rename files
    results = []
    if num_workers > 1:
        with Pool(processes=num_workers) as pool:
            results = list(
                progress_bar(
                    pool.imap(_process_file_worker, process_args),
                    PHASE_PROCESS,
                    "Copying files",
                    total=len(process_args),
                )
            )
    else:
        for args in progress_bar(process_args, PHASE_PROCESS, "Copying files"):
            results.append(_process_file_worker(args))

    # Collect successful files for EXIF processing
    success_count = 0
    failed_count = 0
    file_paths = []
    file_info = []

    for success, output_path, message, exp_username in results:
        if success and output_path:
            success_count += 1
            file_paths.append(output_path)
            file_info.append((output_path, message, exp_username))
        else:
            failed_count += 1

    logger.info(f"Copied {success_count} files ({failed_count} failed)")

    # Phase 2: Batch EXIF processing
    exif_rebuilt_count = 0
    if file_paths:
        logger.info("Batch processing EXIF metadata...")

        # Validate and rebuild corrupted EXIF
        corrupted_files = batch_validate_exif(file_paths)
        if corrupted_files:
            logger.info(
                f"Rebuilding {len(corrupted_files)} corrupted EXIF structures..."
            )
            batch_rebuild_exif(list(corrupted_files))
            exif_rebuilt_count = len(corrupted_files)

        # Read existing metadata and write new EXIF data
        existing_metadata_map = batch_read_existing_metadata(file_paths)
        batch_write_metadata_imessage(file_info, existing_metadata_map)
        logger.info(f"EXIF metadata written for {len(file_info)} files")

    # Print summary using shared utility
    print_processing_summary(
        success=success_count,
        failed=failed_count,
        total=len(matched_files),
        output_dir=str(output_dir),
        extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
    )

    return True
