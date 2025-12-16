#!/usr/bin/env python3
"""
iMazing iMessage Processor

This processor handles iMazing-format iMessage exports which use:
- Flat file structure with filename-encoded metadata
- CSV files for message text and metadata
- Individual vCard files for contacts

Unlike the standard iMessage processor which uses SQLite databases,
this processor parses the filename metadata and CSV files directly.
"""

import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Optional

from common.dependency_checker import check_exiftool, print_exiftool_error
from common.exiftool_batch import (
    batch_read_existing_metadata,
    batch_rebuild_exif,
    batch_validate_exif,
    batch_write_metadata_imessage,
)
from common.progress import PHASE_PROCESS, progress_bar
from common.utils import default_worker_count, sanitize_filename, update_file_timestamps
from processors.base import ProcessorBase
from processors.imessage_imazing.preprocess import ImazingPreprocessor

logger = logging.getLogger(__name__)


# ============================================================================
# Processor Detection and Registration
# ============================================================================


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory.

    Detection criteria for iMazing exports:
    - Device-Info.txt exists with "iMazing" reference
    - OR: Messages CSV files with specific pattern + media files with timestamp prefix

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is an iMazing iMessage export, False otherwise
    """
    try:
        # Check for Device-Info.txt with iMazing reference
        device_info = input_path / "Device-Info.txt"
        if device_info.exists():
            try:
                content = device_info.read_text(encoding="utf-8")
                # iMazing exports typically contain "iMazing" in the content
                if "iMazing" in content or "DigiDNA" in content:
                    return True
            except Exception:
                pass

        # Check for iMazing-style vCard files (contain iMazing PRODID)
        vcf_files = list(input_path.glob("Contact - *.vcf"))
        if vcf_files:
            try:
                content = vcf_files[0].read_text(encoding="utf-8")
                if "iMazing" in content or "DigiDNA" in content:
                    return True
            except Exception:
                pass

        # Check for pattern: Messages CSV + media files with timestamp prefix
        message_csvs = list(input_path.glob("Messages - *.csv"))
        if message_csvs:
            # Check for media files with iMazing timestamp pattern
            # Pattern: "YYYY-MM-DD HH MM SS - Name - filename.ext"
            import re

            pattern = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2} \d{2} \d{2} - .+ - .+$")
            media_extensions = {
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".mov",
                ".mp4",
                ".m4a",
                ".heic",
            }

            for f in input_path.iterdir():
                if f.is_file() and f.suffix.lower() in media_extensions:
                    if pattern.match(f.stem + f.suffix):
                        return True

        # Check for preprocessed iMazing export
        metadata_file = input_path / "metadata.json"
        media_dir = input_path / "media"

        if metadata_file.exists() and media_dir.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                # iMazing preprocessed exports have export_format: "imazing"
                if isinstance(metadata, dict) and "conversations" in metadata:
                    export_info = metadata.get("export_info", {})
                    if export_info.get("export_format") == "imazing":
                        return True
            except (json.JSONDecodeError, KeyError):
                pass

        return False

    except Exception as e:
        logger.debug(f"Detection failed for iMazing: {e}")
        return False


class ImazingProcessor(ProcessorBase):
    """Processor for iMazing-format iMessage exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input."""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name."""
        return "iMessage-iMazing"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first).

        Priority 85: Very high priority - requires specific iMazing file structure.
        Higher than standard iMessage (80) to ensure iMazing exports are detected first.
        """
        return 85

    @staticmethod
    def supports_consolidation() -> bool:
        """iMazing exports are typically standalone, no consolidation needed."""
        return False

    @staticmethod
    def process(input_dir: str, output_dir: str = None, **kwargs) -> bool:
        """Process an iMazing iMessage export.

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            **kwargs: Additional arguments (verbose, workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        return process_logic(
            input_dir=input_dir,
            output_dir=output_dir,
            temp_dir=kwargs.get("temp_dir", "../pre"),
            verbose=kwargs.get("verbose", False),
            workers=kwargs.get("workers"),
        )


def get_processor():
    """Return processor class for auto-discovery.

    This function is called by memoria.py during automatic processor discovery.

    Returns:
        ImazingProcessor class
    """
    return ImazingProcessor


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

    # Verify it's an iMazing preprocessed export
    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        return (
            "conversations" in metadata
            and "export_info" in metadata
            and metadata.get("export_info", {}).get("export_format") == "imazing"
        )
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
    """Extract timestamp string from iMazing message metadata.

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
    input_dir: str,
    output_dir: str = None,
    temp_dir: str = "../pre",
    verbose: bool = False,  # noqa: ARG001 - Reserved for future detailed logging
    workers: Optional[int] = None,
) -> bool:
    """Core processing logic for iMazing iMessage exports.

    Args:
        input_dir: Input directory (raw export or preprocessed)
        output_dir: Output directory for processed files
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

    logger.info("iMazing iMessage Processor")
    logger.info("=" * 50)

    input_path = Path(input_dir)

    # Determine output directory
    if output_dir:
        output_path = Path(output_dir)
    else:
        output_path = Path("final_imessage_imazing")

    # Create output subdirectory for messages
    messages_output_dir = output_path / "messages"
    messages_output_dir.mkdir(parents=True, exist_ok=True)

    # Check if input is already preprocessed
    already_preprocessed = is_preprocessed(input_path)
    temp_dir_created = None
    working_dir = None

    try:
        if already_preprocessed:
            # Use preprocessed export directly
            working_dir = input_path
            logger.info(f"Using preprocessed export: {working_dir}")
        else:
            # Need to run preprocessing
            logger.info("Running preprocessing...")

            # Create temp directory
            temp_base = Path(temp_dir).resolve()
            temp_base.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            temp_dir_created = temp_base / f"imazing_temp_{timestamp}_{unique_id}"
            temp_dir_created.mkdir(parents=True, exist_ok=True)

            logger.info(f"Preprocessing to: {temp_dir_created}")

            # Run preprocessor
            preprocessor = ImazingPreprocessor(
                export_path=input_path,
                output_dir=temp_dir_created,
                workers=workers,
            )

            if not preprocessor.process():
                logger.error("Preprocessing failed")
                return False

            working_dir = temp_dir_created
            logger.info(f"Preprocessing complete. Using: {working_dir}")

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

        export_username = metadata.get("export_info", {}).get(
            "export_username", "unknown"
        )
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
        logger.info(f"Processing files to {messages_output_dir}/")
        logger.info("=" * 50)

        num_workers = workers if workers is not None else default_worker_count()
        logger.info(f"Using {num_workers} workers")

        # Prepare arguments
        process_args = [
            (media_file, message, export_username, messages_output_dir, output_filename)
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

        # Summary
        logger.info("")
        logger.info("=" * 50)
        logger.info("Processing complete!")
        logger.info(f"  Successfully processed: {success_count}")
        logger.info(f"  Failed: {failed_count}")
        logger.info(f"  EXIF structures rebuilt: {exif_rebuilt_count}")
        logger.info(f"Processed files saved to: {messages_output_dir.absolute()}")

        return True

    finally:
        # Clean up temp directory
        if temp_dir_created and temp_dir_created.exists():
            from common.utils import should_cleanup_temp

            if should_cleanup_temp():
                logger.info(f"Cleaning up temporary directory: {temp_dir_created}")
                shutil.rmtree(temp_dir_created)
            else:
                logger.info(
                    f"Temp cleanup disabled. Directory preserved: {temp_dir_created}"
                )

