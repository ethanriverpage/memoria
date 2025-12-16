#!/usr/bin/env python3
"""
Discord Processor

Processes Discord data exports, handling attachment downloads
and metadata embedding for media files.
"""

import json
import logging
import os
import re
import shutil
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Dict, Optional

from common.dependency_checker import (
    check_exiftool,
    print_exiftool_error,
)
from common.exiftool_batch import (
    batch_read_existing_metadata,
    batch_rebuild_exif,
    batch_validate_exif,
    batch_write_metadata_discord,
)
from common.processing import (
    print_processing_summary,
    temp_processing_directory,
)
from common.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    default_worker_count,
    get_media_type,
    is_preprocessed_directory,
    sanitize_filename,
    update_file_timestamps,
)
from processors.base import ProcessorBase
from processors.discord.preprocess import DiscordPreprocessor

# Set up logging
logger = logging.getLogger(__name__)


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory.

    Detection criteria for Discord:
    - Raw export: Directory contains 'Messages/' with 'index.json'
    - Preprocessed: Directory contains 'metadata.json' with Discord-style conversations

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is a Discord export, False otherwise
    """
    try:
        # Check for raw Discord export structure
        messages_dir = input_path / "Messages"
        if messages_dir.exists() and messages_dir.is_dir():
            index_file = messages_dir / "index.json"
            if index_file.exists():
                # Verify at least one channel folder exists
                channel_folders = [
                    d
                    for d in messages_dir.iterdir()
                    if d.is_dir() and d.name.startswith("c")
                ]
                if channel_folders:
                    return True

        # Check for preprocessed Discord export
        metadata_file = input_path / "metadata.json"
        media_dir = input_path / "media"

        if metadata_file.exists() and media_dir.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                # Discord preprocessed format has conversations dict
                if isinstance(metadata, dict) and "conversations" in metadata:
                    export_info = metadata.get("export_info", {})
                    # Check for Discord-specific indicators
                    if (
                        "downloads_successful" in export_info
                        or "downloads_failed" in export_info
                    ):
                        return True
                    # Check for Discord-style conversation structure
                    conversations = metadata.get("conversations", {})
                    if conversations:
                        # Check first conversation for Discord-specific fields
                        first_conv = next(iter(conversations.values()), {})
                        if "guild_name" in first_conv or "original_urls" in str(
                            first_conv
                        ):
                            return True

            except (json.JSONDecodeError, KeyError):
                pass

        return False

    except Exception as e:
        logger.debug(f"Detection failed for Discord: {e}")
        return False


class DiscordProcessor(ProcessorBase):
    """Processor for Discord data exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input."""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name."""
        return "Discord"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first).

        Priority 70: Medium-high priority
        - Requires Messages/ directory with index.json
        - Requires at least one channel folder with messages.json
        - More specific than filename-only detection, but less than
          processors requiring multiple specific files/structures
        """
        return 70

    @staticmethod
    def process(input_dir: str, output_dir: str = None, **kwargs) -> bool:
        """Process Discord export.

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
            # Create messages subdirectory under output
            if output_dir:
                processor_output = str(Path(output_dir) / "messages")
            else:
                processor_output = kwargs.get("output", "final_discord/messages")

            # Call processing logic
            process_logic(
                input_dir=input_dir,
                output_dir=processor_output,
                temp_dir=kwargs.get("temp_dir", "../pre"),
                verbose=kwargs.get("verbose", False),
                workers=kwargs.get("workers"),
            )
            return True

        except Exception as e:
            logger.error(f"Error in DiscordProcessor: {e}")
            import traceback

            traceback.print_exc()
            return False


def get_processor():
    """Return processor class for auto-discovery.

    This function is called by the unified memoria.py during
    automatic processor discovery.

    Returns:
        DiscordProcessor class (not instance, as it uses static methods)
    """
    return DiscordProcessor


# ============================================================================
# Helper Functions
# ============================================================================


def load_metadata(metadata_path: Path) -> dict:
    """Load and parse metadata.json.

    Args:
        metadata_path: Path to metadata.json

    Returns:
        Parsed metadata dict
    """
    with open(metadata_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_filename_index(metadata: dict) -> Dict[str, dict]:
    """Build index mapping media filename to message metadata.

    Args:
        metadata: Parsed metadata.json content

    Returns:
        Dict mapping filename -> message dict
    """
    filename_index = {}

    for channel_id, conv_data in metadata.get("conversations", {}).items():
        for message in conv_data.get("messages", []):
            media_files = message.get("media_files", [])
            for filename in media_files:
                # Store both message and channel context
                filename_index[filename] = {
                    "message": message,
                    "channel_id": channel_id,
                    "channel_type": conv_data.get("type"),
                    "channel_title": conv_data.get("title"),
                    "guild_name": conv_data.get("guild_name"),
                }

    return filename_index


def generate_output_filename(
    message_info: dict,
    export_username: str,
    extension: str,
    used_filenames: set,
) -> str:
    """Generate unique output filename for a Discord media file.

    Format: discord-{username}-{channel}-{YYYYMMDD}[-{seq}].{ext}

    Args:
        message_info: Message and channel metadata dict
        export_username: Username from export
        extension: File extension (including dot)
        used_filenames: Set tracking already-used filenames

    Returns:
        Generated unique filename
    """
    message = message_info["message"]
    channel_title = message_info.get("channel_title", "unknown")
    channel_type = message_info.get("channel_type", "unknown")

    # Parse timestamp
    timestamp_str = message.get("timestamp", "")
    try:
        # Remove " UTC" suffix if present
        timestamp_clean = timestamp_str.replace(" UTC", "").strip()
        if timestamp_clean:
            date_obj = datetime.strptime(timestamp_clean, "%Y-%m-%d %H:%M:%S")
        else:
            date_obj = datetime.now()
    except ValueError:
        date_obj = datetime.now()

    date_key = date_obj.strftime("%Y%m%d")

    # Sanitize channel title for filename
    if channel_type == "dm":
        # Extract username from DM title if possible
        # Format: "Direct Message with username#0"
        match = re.search(r"Direct Message with (.+?)(?:#\d+)?$", channel_title)
        if match:
            channel_part = sanitize_filename(match.group(1))
        else:
            channel_part = "dm"
    elif channel_type == "group_dm":
        channel_part = "group"
    else:
        # Server channel - use channel name
        channel_part = sanitize_filename(
            channel_title.split(" in ")[0] if " in " in channel_title else channel_title
        )

    # Truncate channel part if too long
    if len(channel_part) > 30:
        channel_part = channel_part[:30]

    # Generate base filename
    base_filename = f"discord-{export_username}-{channel_part}-{date_key}{extension}"

    # Check if base filename is already used
    if base_filename not in used_filenames:
        used_filenames.add(base_filename)
        return base_filename

    # Add sequence number for duplicates
    sequence = 2
    while True:
        filename = (
            f"discord-{export_username}-{channel_part}-{date_key}_{sequence}{extension}"
        )
        if filename not in used_filenames:
            used_filenames.add(filename)
            return filename
        sequence += 1


def _process_file_worker(args_tuple):
    """Worker function for processing individual files.

    Args:
        args_tuple: Tuple of (media_path, message_info, export_username,
                              output_dir, output_filename)

    Returns:
        Tuple of (success, output_path, message_info, export_username)
    """
    media_path, message_info, export_username, output_dir, output_filename = args_tuple

    try:
        output_path = output_dir / output_filename

        # Copy file to output
        shutil.copy2(media_path, output_path)

        # Update filesystem timestamps - Discord uses "YYYY-MM-DD HH:MM:SS UTC" format
        timestamp_str = message_info["message"].get("timestamp", "")
        if timestamp_str:
            update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")

        return (True, str(output_path), message_info, export_username)

    except Exception as e:
        logger.error(f"Error processing {media_path.name}: {e}")
        return (False, None, message_info, export_username)


def process_logic(
    input_dir: str,
    output_dir: str = "processed_discord",
    temp_dir: str = "../pre",
    verbose: bool = False,
    workers: Optional[int] = None,
):
    """Core processing logic for Discord exports.

    Args:
        input_dir: Input directory (raw export or preprocessed)
        output_dir: Output directory for processed files
        temp_dir: Directory for temporary preprocessing files
        verbose: Enable verbose logging
        workers: Number of parallel workers
    """
    # Validate input directory exists
    input_dir = Path(input_dir)
    if not input_dir.exists():
        logger.error(f"Input directory not found: {input_dir}")
        return

    # Check for required tools
    if not check_exiftool():
        print_exiftool_error()
        return

    logger.info("Discord Processor")
    logger.info("=" * 50)

    # Determine if input needs preprocessing
    output_dir = Path(output_dir)

    # Check if input is already preprocessed
    if is_preprocessed_directory(str(input_dir)):
        logger.info(f"Input directory is already preprocessed: {input_dir}")
        _process_working_directory(input_dir, output_dir, workers)
    else:
        logger.info(f"Input directory is raw export: {input_dir}")
        logger.info("Running preprocessing (downloading attachments)...")

        # Use context manager for temp directory with automatic cleanup
        with temp_processing_directory(temp_dir, "discord") as temp_dir_path:
            logger.info(f"Preprocessing to: {temp_dir_path}")

            # Run preprocessing
            # Note: output_dir already has /messages appended by the caller (process method)
            preprocessor = DiscordPreprocessor(
                export_path=input_dir,
                output_dir=temp_dir_path,
                workers=workers,
                final_output_dir=output_dir,
            )
            preprocessor.process()

            logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
            _process_working_directory(temp_dir_path, output_dir, workers)


def _process_working_directory(
    working_dir: Path, output_dir: Path, workers: Optional[int]
):
    """Process a working directory (preprocessed export)

    Args:
        working_dir: Path to preprocessed directory with metadata.json and media/
        output_dir: Output directory for processed files
        workers: Number of parallel workers
    """
    # Configuration
    metadata_file = working_dir / "metadata.json"
    media_dir = working_dir / "media"

    # Check for metadata file
    if not metadata_file.exists():
        logger.error(f"Metadata file not found: {metadata_file}")
        return

    # Check for media directory
    if not media_dir.exists():
        logger.warning(f"Media directory not found: {media_dir}")
        logger.warning("No media files to process.")
        return

    # Load metadata
    logger.info(f"Loading metadata from {metadata_file}...")
    metadata = load_metadata(metadata_file)

    export_username = metadata.get("export_info", {}).get("export_username", "unknown")
    logger.info(f"Export username: {export_username}")

    # Build filename index
    logger.info("Building filename index...")
    filename_index = build_filename_index(metadata)
    logger.info(f"Found {len(filename_index)} media files in metadata")

    # Scan media directory for actual files
    logger.info(f"Scanning media directory: {media_dir}")
    media_files = list(media_dir.glob("*"))
    media_files = [f for f in media_files if f.is_file() and get_media_type(f)]
    logger.info(f"Found {len(media_files)} media files on disk")

    if not media_files:
        logger.warning("No media files found to process.")
        return

    # Create output directory (subdirectory already set by caller)
    output_dir.mkdir(exist_ok=True, parents=True)

    # Match files to metadata and generate output filenames
    logger.info("\nMatching files to metadata...")
    matched_files = []
    unmatched_files = []
    used_filenames = set()

    for media_file in media_files:
        if media_file.name in filename_index:
            message_info = filename_index[media_file.name]
            # Generate output filename
            ext = media_file.suffix.lower()
            output_filename = generate_output_filename(
                message_info, export_username, ext, used_filenames
            )
            matched_files.append((media_file, message_info, output_filename))
        else:
            unmatched_files.append(media_file)

    logger.info(f"Matched {len(matched_files)} files")
    logger.info(f"Unmatched {len(unmatched_files)} files")

    # Process matched files
    logger.info(f"\nProcessing {len(matched_files)} files to {output_dir}/")
    logger.info("=" * 50)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    logger.info(f"Using {num_workers} parallel workers")

    # Prepare arguments for parallel processing
    process_args = []
    for media_file, message_info, output_filename in matched_files:
        process_args.append(
            (
                media_file,
                message_info,
                export_username,
                output_dir,
                output_filename,
            )
        )

    # Process files in parallel
    success_count = 0
    failed_count = 0

    if num_workers > 1:
        with Pool(processes=num_workers) as pool:
            results = list(
                progress_bar(
                    pool.imap(_process_file_worker, process_args),
                    PHASE_PROCESS,
                    "Processing files",
                    total=len(process_args),
                )
            )
    else:
        # Single-threaded for debugging
        results = []
        for args_tuple in progress_bar(process_args, PHASE_PROCESS, "Processing files"):
            results.append(_process_file_worker(args_tuple))

    # Collect results for EXIF processing
    file_paths = []
    file_info = []

    for success, output_path, message_info, exp_username in results:
        if success and output_path:
            success_count += 1
            file_paths.append(output_path)
            file_info.append((output_path, message_info, exp_username))
        else:
            failed_count += 1

    logger.info(
        f"\nCopied {success_count} files ({len(file_info)} for EXIF processing)"
    )

    # Batch process EXIF operations
    exif_rebuilt_count = 0
    if file_paths:
        logger.info("Batch processing EXIF metadata...")

        # Batch validate and rebuild
        corrupted_files = batch_validate_exif(file_paths)
        if corrupted_files:
            logger.info(
                f"Rebuilding {len(corrupted_files)} corrupted EXIF structures..."
            )
            batch_rebuild_exif(list(corrupted_files))
            exif_rebuilt_count = len(corrupted_files)

        # Batch read and write metadata
        existing_metadata_map = batch_read_existing_metadata(file_paths)
        batch_write_metadata_discord(file_info, existing_metadata_map)

    # Handle unmatched files
    if unmatched_files:
        logger.info(f"\nProcessing {len(unmatched_files)} unmatched files...")
        failed_matching_dir = output_dir / "issues" / "failed-matching" / "media"
        failed_matching_dir.mkdir(exist_ok=True, parents=True)

        for media_file in unmatched_files:
            try:
                shutil.copy2(media_file, failed_matching_dir / media_file.name)
            except Exception as e:
                logger.error(f"Error copying unmatched file {media_file.name}: {e}")

    # Print summary using shared utility
    print_processing_summary(
        success=success_count,
        failed=failed_count,
        total=len(media_files),
        output_dir=str(output_dir),
        extra_stats={
            "EXIF structures rebuilt": exif_rebuilt_count,
            "Unmatched": len(unmatched_files),
        },
    )

    if unmatched_files:
        logger.info(
            f"Unmatched files saved to: {(output_dir / 'issues' / 'failed-matching' / 'media').absolute()}"
        )
