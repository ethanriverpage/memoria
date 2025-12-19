#!/usr/bin/env python3
"""
iMessage Processor

This processor handles iMessage exports from Mac and iPhone backups.
It supports cross-export consolidation for deduplication across multiple devices.
"""

import json
import logging
import shutil
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.catalog import catalog_media_files
from common.dedup.description import (
    SourceEntry,
    build_description,
    build_source_line,
    build_message_context,
    PLATFORM_IMESSAGE,
)
from common.dependency_checker import check_exiftool, print_exiftool_error
from common.files.utils import batch_correct_extensions
from common.dedup.service import is_file_primary_by_hash
from common.dedup.hashing import compute_content_hash
from common.media.exiftool import (
    batch_read_existing_metadata,
    batch_rebuild_exif,
    batch_validate_exif,
    batch_write_metadata_requests,
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    MetadataWriteRequest,
)
from common.processing import (
    print_processing_summary,
    temp_processing_directory,
)
from common.output.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    ALL_MEDIA_EXTENSIONS,
    default_worker_count,
    FilenameGenerator,
    sanitize_filename,
    update_file_timestamps,
)
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
    def supports_dedup() -> bool:
        """Return True - iMessage supports cross-processor deduplication."""
        return True

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata.

        Scans all media files in the iMessage export, computes hashes,
        and returns a catalog structure without copying any files.

        Args:
            input_dir: Path to input directory
            **kwargs: Additional arguments (verbose, workers)

        Returns:
            Catalog dictionary with processor name and file entries.
        """
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        # Find attachments directory (Mac or iPhone format)
        attachments_dir = None
        if (input_path / "Attachments").is_dir():
            attachments_dir = input_path / "Attachments"
        elif (input_path / "SMS" / "Attachments").is_dir():
            attachments_dir = input_path / "SMS" / "Attachments"
        elif (input_path / "media").is_dir():
            # Preprocessed format
            attachments_dir = input_path / "media"

        if not attachments_dir or not attachments_dir.exists():
            logger.warning(f"Attachments directory not found in: {input_path}")
            return

        media_files = []
        for file_path in attachments_dir.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in ALL_MEDIA_EXTENSIONS:
                media_files.append(file_path)

        yield from catalog_media_files(
            processor_name="iMessage",
            input_path=input_path,
            media_files=media_files,
            workers=workers,
        )

    @staticmethod
    def process_consolidated(
        input_dirs: List[str],
        output_dir: str = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process multiple iMessage exports as a consolidated unit.

        Called by memoria.py when CONSOLIDATE_EXPORTS=true (default) and
        multiple iMessage exports are detected. Enables cross-export
        deduplication before any files are copied.

        Args:
            input_dirs: List of paths to iMessage export directories
            output_dir: Base output directory
            dedup_manifest: Optional deduplication manifest
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
            dedup_manifest=dedup_manifest,
        )

    @staticmethod
    def process(
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process a single iMessage export.

        Delegates to process_consolidated with a single-element list,
        ensuring the same code path handles both modes.

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            dedup_manifest: Optional deduplication manifest
            **kwargs: Additional arguments (verbose, workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        return IMessageProcessor.process_consolidated(
            [input_dir], output_dir, dedup_manifest=dedup_manifest, **kwargs
        )


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


def _extract_device_from_source_export(source_export: str) -> str:
    """Extract device identifier from source_export directory name.

    Examples:
        "iph13p-messages-20220426" -> "iph13p"
        "mac-messages-20240601" -> "mac"

    Args:
        source_export: Directory name like "{device}-messages-YYYYMMDD"

    Returns:
        Device identifier, or "unknown" if pattern doesn't match
    """
    if not source_export:
        return "unknown"

    if "-messages-" in source_export:
        return source_export.split("-messages-")[0]

    return "unknown"


def _build_description(message: dict, export_username: str) -> str:
    """Build the description string for an iMessage using unified format.

    Args:
        message: Message data dictionary.
        export_username: Username of the export owner.

    Returns:
        Formatted description string.
    """
    from datetime import datetime as dt

    is_merged = "messages" in message and isinstance(message["messages"], list)

    # Determine the correct source device identifier
    if is_merged:
        # For merged messages, find the oldest export by export date
        oldest_msg = None
        oldest_export_date = None
        for msg in message["messages"]:
            source_export = msg.get("source_export")
            if source_export:
                try:
                    date_str = source_export.split("-messages-")[1]
                    export_date = dt.strptime(date_str, "%Y%m%d")
                    if oldest_export_date is None or export_date < oldest_export_date:
                        oldest_export_date = export_date
                        oldest_msg = msg
                except (ValueError, IndexError):
                    pass

        # Extract device from oldest export's source_export
        if oldest_msg and oldest_msg.get("source_export"):
            source_device = _extract_device_from_source_export(
                oldest_msg["source_export"]
            )
        else:
            source_device = export_username
            for msg in message["messages"]:
                if msg.get("source_export"):
                    source_device = _extract_device_from_source_export(
                        msg["source_export"]
                    )
                    break
    else:
        if message.get("source_export"):
            source_device = _extract_device_from_source_export(message["source_export"])
        else:
            source_device = export_username

    # Build source line using unified format
    source_entry = SourceEntry(
        processor=PLATFORM_IMESSAGE,
        username=source_device,
    )
    source_line = build_source_line(source_entry)
    description_parts = [source_line]

    if is_merged:
        # Merged message - list all conversations, deduplicating identical lines
        seen_lines: set = set()
        for msg in message["messages"]:
            conv_type = msg.get("conversation_type")
            conv_title = msg.get("conversation_title")
            conv_id = msg.get("conversation_id", "Unknown")
            sender = msg.get("sender", "unknown")
            content = msg.get("content", "")

            if conv_type == "dm":
                display_name = conv_title if conv_title else conv_id
                conv_name = f"DM with {display_name}"
            else:
                conv_name = conv_title or "Unknown"

            # Use unified message context format
            msg_entry = SourceEntry(
                processor=PLATFORM_IMESSAGE,
                sender=sender,
                conversation=conv_name,
                content=content,
            )
            msg_line = build_message_context(msg_entry)

            if msg_line and msg_line not in seen_lines:
                seen_lines.add(msg_line)
                description_parts.append(msg_line)

        source_description = "&#xa;".join(description_parts)
    else:
        conv_type = message.get("conversation_type")
        conv_title = message.get("conversation_title")
        conv_id = message.get("conversation_id", "Unknown")
        sender = message.get("sender", "unknown")
        content = message.get("content", "")

        if conv_type == "dm":
            display_name = conv_title if conv_title else conv_id
            conv_name = f"DM with {display_name}"
        else:
            conv_name = conv_title or "Unknown"

        # Use unified message context format
        msg_entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            sender=sender,
            conversation=conv_name,
            content=content,
        )
        msg_line = build_message_context(msg_entry)
        if msg_line:
            description_parts.append(msg_line)

        source_description = "&#xa;".join(description_parts)

    # Add Live Photo marker if applicable
    if message.get("is_live_photo_video"):
        source_description += "&#xa;[Live Photo Video]"

    return source_description


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for iMessage files.

    Args:
        file_info: List of (file_path, message, export_username) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for file_path, message, export_username in file_info:
        is_merged = "messages" in message and isinstance(message["messages"], list)

        # Build datetime tags
        datetime_tags: List[Tuple[str, str]] = []
        if is_merged:
            date_str = message.get("primary_created")
        else:
            date_str = message.get("created")

        if date_str:
            # Convert "YYYY-MM-DD HH:MM:SS UTC" to EXIF format
            exif_date = date_str.replace("-", ":").replace(" UTC", "")
            datetime_tags = [
                ("DateTimeOriginal", exif_date),
                ("CreateDate", exif_date),
                ("ModifyDate", exif_date),
            ]

        # Build description
        default_description = _build_description(message, export_username)

        requests.append(
            MetadataWriteRequest(
                file_path=file_path,
                arg_preamble=DEFAULT_PREAMBLE,
                datetime_tags=datetime_tags,
                gps=None,
                description=DescriptionWriteSpec(
                    default_description=default_description,
                ),
                processor_name="imessage",
            )
        )

    return requests


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


def _build_imessage_identifier(message: dict) -> str:
    """Build identifier for iMessage filename (policy only).

    Determines the conversation part of the filename:
    - Merged: "merged"
    - Group: sanitized group title
    - DM: sanitized contact ID
    - Unknown: "unknown"

    Args:
        message: Message metadata dict

    Returns:
        Identifier string for use in filename (truncated to 30 chars)
    """
    is_merged = "messages" in message and isinstance(message["messages"], list)

    if is_merged:
        return "merged"

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

    return conv_prefix


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
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Core processing logic for iMessage exports.

    Args:
        input_dirs: List of input directories (raw exports or preprocessed)
        output_dir: Output directory for processed files (final path, no subdirs created)
        temp_dir: Directory for temporary preprocessing files
        verbose: Enable verbose logging
        workers: Number of parallel workers
        dedup_manifest: Optional deduplication manifest for filtering secondary files

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
        return _process_working_directory(
            working_dir, messages_output_dir, workers, dedup_manifest
        )
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
                temp_dir_path, messages_output_dir, workers, dedup_manifest
            )


def _process_working_directory(
    working_dir: Path,
    output_dir: Path,
    workers: Optional[int],
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Process a working directory (preprocessed export).

    Args:
        working_dir: Path to preprocessed directory with metadata.json and media/
        output_dir: Output directory for processed files
        workers: Number of parallel workers
        dedup_manifest: Optional deduplication manifest for filtering secondary files

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

    # Filter out secondary files based on dedup manifest (using hash-based lookup)
    if dedup_manifest:
        original_count = len(media_files)
        filtered_files = []
        for f in media_files:
            content_hash = compute_content_hash(f)
            if is_file_primary_by_hash(dedup_manifest, content_hash, "iMessage"):
                filtered_files.append(f)
        media_files = filtered_files
        skipped_count = original_count - len(media_files)
        if skipped_count > 0:
            logger.info(f"Skipped {skipped_count} secondary files (dedup)")

    # Match files to messages and pre-generate filenames
    logger.info("Matching files and generating filenames...")
    filename_gen = FilenameGenerator(
        "imessage", timestamp_format="%Y-%m-%d %H:%M:%S UTC"
    )
    matched_files = []

    for media_file in media_files:
        if media_file.name in filename_index:
            message = filename_index[media_file.name]
            file_ext = media_file.suffix.lower()

            # Build identifier (policy) and delegate to FilenameGenerator (mechanism)
            identifier = _build_imessage_identifier(message)
            timestamp = _extract_message_timestamp(message)
            output_filename = filename_gen.generate(timestamp, identifier, file_ext)
            matched_files.append((media_file, message, output_filename))
        else:
            logger.warning(f"No metadata for file: {media_file.name}")

    logger.info(f"Matched {len(matched_files)} files")

    # Process files
    logger.info("Processing files to %s/", output_dir)
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

    # Phase 2: Correct file extensions based on actual content
    if file_paths:
        corrections = batch_correct_extensions([Path(p) for p in file_paths])
        if corrections:
            # Update file_paths and file_info with corrected paths
            file_paths = [corrections.get(p, p) for p in file_paths]
            file_info = [
                (corrections.get(fp, fp), msg, eu) for fp, msg, eu in file_info
            ]

    # Phase 3: Batch EXIF processing
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
        requests = _build_metadata_requests(file_info)
        batch_write_metadata_requests(requests, existing_metadata_map)
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
