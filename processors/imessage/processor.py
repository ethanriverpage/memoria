#!/usr/bin/env python3
"""
iMessage Processor

This processor handles iMessage exports from Mac and iPhone backups.
It supports cross-export consolidation for deduplication across multiple devices.
"""

import json
import logging
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.catalog import catalog_media_files
from common.dedup.hashing import (
    find_live_photo_video,
    find_live_photo_videos_in_files,
)
from common.dedup.service import is_file_primary_by_hash
from common.dedup.description import (
    SourceEntry,
    build_source_line,
    build_message_context,
    PLATFORM_IMESSAGE,
)
from common.files.utils import copy_file
from common.media.exiftool import (
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    MetadataWriteRequest,
    build_datetime_tags,
)
from common.processing import (
    print_processing_summary,
)
from common.output.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    ALL_MEDIA_EXTENSIONS,
    default_worker_count,
    FilenameGenerator,
    sanitize_filename,
    update_file_timestamps,
)
from common.output.logging import get_processor_logger
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.imessage.preprocess import IMessagePreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


# ============================================================================
# Processor Detection and Registration
# ============================================================================


class IMessageProcessor(ProcessorBase):
    """Processor for iMessage exports from Mac and iPhone."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory.

        Detection criteria for iMessage:
        - Mac export: chat.db + Attachments/ directory
        - iPhone export: SMS/sms.db + SMS/Attachments/ directory
        - Preprocessed: metadata.json with conversations + media/ directory
        """
        try:
            # Check for Mac export: chat.db + Attachments/
            if (input_path / "chat.db").exists() and (
                input_path / "Attachments"
            ).is_dir():
                return True

            # Check for iPhone export: SMS/sms.db + SMS/Attachments/
            sms_dir = input_path / "SMS"
            if sms_dir.exists():
                if (sms_dir / "sms.db").exists() and (
                    sms_dir / "Attachments"
                ).is_dir():
                    return True

            # Check for preprocessed iMessage export
            metadata_file = input_path / "metadata.json"

            if metadata_file.exists():
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
            log.detection_failed("iMessage", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "iMessage"

    @staticmethod
    def get_priority() -> int:
        return 80  # High priority - requires specific SQLite database structure

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="iMessage",
            service=None,
            username_prefix="imessage",
        )

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
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if iMessage export needs preprocessing."""
        input_path = Path(input_dir)

        # Check if already preprocessed (has metadata.json)
        metadata_file = input_path / "metadata.json"
        if metadata_file.exists():
            return False

        # Check for raw export structure (chat.db or SMS/sms.db)
        if (input_path / "chat.db").exists():
            return True
        sms_dir = input_path / "SMS"
        if sms_dir.exists() and (sms_dir / "sms.db").exists():
            return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw iMessage export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        logger.info("Preprocessing iMessage export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = IMessagePreprocessor(
            export_paths=input_path,
            output_dir=cache_path,
            workers=workers,
        )

        if preprocessor.run():
            return str(cache_path)
        return None

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

        # Load metadata for preprocessed exports
        metadata_index: Dict[str, Any] = {}
        metadata_file = input_path / "metadata.json"
        if metadata_file.exists():
            try:
                metadata = IMessageProcessor.load_metadata(metadata_file)
                metadata_index = build_filename_index(metadata)
            except Exception as e:
                logger.warning(f"Could not load metadata.json: {e}")

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

        # Collect all media files
        all_files = []
        for file_path in attachments_dir.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in ALL_MEDIA_EXTENSIONS:
                all_files.append(file_path)

        # Identify Live Photo pairs and filter out videos
        # Videos will be linked via live_photo_video field on their paired images
        live_photo_videos = find_live_photo_videos_in_files(all_files)
        media_files = [f for f in all_files if f not in live_photo_videos]

        if live_photo_videos:
            log.logger.info(
                "Found %d media files to catalog (%d Live Photo videos grouped with images)",
                len(media_files),
                len(live_photo_videos),
            )

        def extract_metadata(file_path: Path) -> Dict[str, Any]:
            """Extract iMessage metadata for a media file."""
            result: Dict[str, Any] = {}

            # Extract message context from metadata index
            message = metadata_index.get(file_path.name, {})
            if message:
                if message.get("sender"):
                    result["sender"] = message["sender"]
                if message.get("conversation_title"):
                    result["conversation"] = message["conversation_title"]
                if message.get("created"):
                    result["timestamp"] = message["created"]
                if message.get("content"):
                    result["content"] = message["content"]

            # Check for Live Photo video component
            video_path = find_live_photo_video(file_path)
            if video_path:
                result["live_photo_video"] = str(video_path)

            return result

        yield from catalog_media_files(
            processor_name="iMessage",
            input_path=input_path,
            media_files=media_files,
            workers=workers,
            metadata_extractor=extract_metadata,
        )

    @staticmethod
    def process_consolidated(
        input_dirs: List[str],
        output_dir: Optional[str] = None,
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


# ============================================================================
# Helper Functions
# ============================================================================


def _extract_date_from_source_export(source_export: str) -> Optional[str]:
    """Extract date string from source_export directory name.

    Examples:
        "imessage-iph13p-20220426" -> "20220426"
        "imessage-m3_mbp-20251202" -> "20251202"

    Args:
        source_export: Directory name like "imessage-{device}-YYYYMMDD"

    Returns:
        Date string (YYYYMMDD), or None if pattern doesn't match
    """
    if not source_export or not source_export.startswith("imessage-"):
        return None

    # Format: imessage-{device}-YYYYMMDD
    # The date is always the last 8 characters after the final hyphen
    parts = source_export.rsplit("-", 1)
    if len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit():
        return parts[1]

    return None


def _extract_device_from_source_export(source_export: str) -> str:
    """Extract device identifier from source_export directory name.

    Examples:
        "imessage-iph13p-20220426" -> "iph13p"
        "imessage-m3_mbp-20251202" -> "m3_mbp"

    Args:
        source_export: Directory name like "imessage-{device}-YYYYMMDD"

    Returns:
        Device identifier, or "unknown" if pattern doesn't match
    """
    if not source_export or not source_export.startswith("imessage-"):
        return "unknown"

    # Format: imessage-{device}-YYYYMMDD
    # Remove "imessage-" prefix, then extract device before the date suffix
    remainder = source_export[9:]  # len("imessage-") = 9
    # Find the last hyphen followed by 8 digits (date)
    parts = remainder.rsplit("-", 1)
    if len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit():
        return parts[0]

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
                    date_str = _extract_date_from_source_export(source_export)
                    if date_str:
                        export_date = dt.strptime(date_str, "%Y%m%d")
                        if (
                            oldest_export_date is None
                            or export_date < oldest_export_date
                        ):
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
    file_info: List[Tuple[Optional[str], dict, str, Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for iMessage files.

    Args:
        file_info: List of (file_path, message, export_username, content_hash) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for file_path, message, export_username, content_hash in file_info:
        if file_path is None:
            continue
        is_merged = "messages" in message and isinstance(message["messages"], list)

        # Build datetime tags (iMessage format: "YYYY-MM-DD HH:MM:SS UTC")
        date_str = (
            message.get("primary_created") if is_merged else message.get("created")
        )
        datetime_tags = build_datetime_tags(date_str, strip_utc=True)

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
                content_hash=content_hash,
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

    if not metadata_file.exists():
        return False

    # Verify it's an iMessage preprocessed export
    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        # Basic structure checks
        if "conversations" not in metadata or "export_info" not in metadata:
            return False

        # New format: file_hashes present (source_path-based, no media/ dir needed)
        # Old format: media/ directory must exist
        has_file_hashes = "file_hashes" in metadata
        has_media_dir = media_dir.exists()

        return has_file_hashes or has_media_dir
    except (json.JSONDecodeError, KeyError):
        return False


def build_filename_index(metadata: dict) -> dict:
    """Build index mapping media_file to message metadata.

    Args:
        metadata: Parsed metadata.json content

    Returns:
        Dict mapping media_file -> message dict

    Note:
        Only adds orphaned_media entries if they don't already exist in the index.
        Orphaned files can have the same filename as database entries (e.g., IMG_0001.jpeg),
        but we prefer the database entry which has valid metadata.
    """
    return ProcessorBase.build_media_filename_index(
        metadata,
        media_keys=["media_file"],
        include_orphaned=True,
        orphaned_prefer_existing=True,
    )


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
        args_tuple: Tuple of (media_path, message, export_username, output_dir, output_filename, content_hash)

    Returns:
        Tuple of (success, output_path, message, export_username, content_hash)
    """
    media_path, message, export_username, output_dir, output_filename, content_hash = (
        args_tuple
    )

    try:
        output_path = output_dir / output_filename

        # Copy file
        copy_file(media_path, output_path)

        # Update timestamps
        timestamp_str = _extract_message_timestamp(message)
        update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")

        return (True, str(output_path), message, export_username, content_hash)

    except Exception as e:
        logger.error(f"Error processing {media_path.name}: {e}")
        return (False, None, message, export_username, content_hash)


# ============================================================================
# Main Processing Logic
# ============================================================================


def process_logic(
    input_dirs: List[str],
    output_dir: Optional[str] = None,
    verbose: bool = False,  # noqa: ARG001 - Reserved for future detailed logging
    workers: Optional[int] = None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Core processing logic for iMessage exports.

    Expects a single preprocessed input directory (via memoria.py preprocessing
    phase or by calling IMessageProcessor.preprocess() directly).

    Args:
        input_dirs: List containing a single preprocessed input directory
        output_dir: Output directory for processed files (final path, no subdirs created)
        verbose: Enable verbose logging
        workers: Number of parallel workers
        dedup_manifest: Optional deduplication manifest for filtering secondary files

    Returns:
        True if processing succeeded, False otherwise
    """
    # Check for required tools
    if not IMessageProcessor.check_dependencies():
        return False

    logger.info("iMessage Processor")
    logger.info("=" * 50)

    # Convert to paths
    input_paths = [Path(d) for d in input_dirs]

    # Require single preprocessed input
    if len(input_paths) != 1:
        logger.error(
            "iMessage process_logic requires a single preprocessed input. "
            "Got %d inputs. Use preprocess() to consolidate multiple exports first.",
            len(input_paths),
        )
        return False

    working_dir = input_paths[0]

    if not is_preprocessed(working_dir):
        logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            working_dir,
        )
        return False

    logger.info(f"Processing preprocessed export: {working_dir}")

    # Determine output directory (use as-is, subdirectory created by caller)
    if output_dir:
        messages_output_dir = Path(output_dir)
    else:
        messages_output_dir = Path("final_imessage/messages")

    messages_output_dir.mkdir(parents=True, exist_ok=True)

    return _process_working_directory(
        working_dir, messages_output_dir, workers, dedup_manifest
    )


def _process_working_directory(
    working_dir: Path,
    output_dir: Path,
    workers: Optional[int],
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Process a working directory (preprocessed export).

    Args:
        working_dir: Path to preprocessed directory with metadata.json
        output_dir: Output directory for processed files
        workers: Number of parallel workers
        dedup_manifest: Optional deduplication manifest for filtering secondary files

    Returns:
        True if processing succeeded, False otherwise
    """
    # Load metadata
    metadata_file = working_dir / "metadata.json"

    if not metadata_file.exists():
        logger.error(f"Metadata file not found: {metadata_file}")
        return False

    logger.info(f"Loading metadata from {metadata_file}...")
    metadata = IMessageProcessor.load_metadata(metadata_file)

    export_username = metadata.get("export_info", {}).get("export_username", "unknown")
    logger.info(f"Export identifier: {export_username}")

    # Build filename index
    filename_index = build_filename_index(metadata)
    logger.info(f"Found {len(filename_index)} media files to process")

    # Check if this is new format (source_path in metadata) or legacy format (media/ directory)
    # Sample the first message to detect format
    sample_message = (
        next(iter(filename_index.values()), None) if filename_index else None
    )
    use_legacy_format = sample_message is None or "source_path" not in sample_message

    if use_legacy_format:
        # Legacy format: scan media/ directory
        media_dir = working_dir / "media"
        if not media_dir.exists():
            logger.error(f"Media directory not found: {media_dir}")
            return False
        logger.info("Using legacy format (media/ directory)")
        media_files = list(media_dir.iterdir())
        media_files = [f for f in media_files if f.is_file()]
        logger.info(f"Found {len(media_files)} files in media directory")
    else:
        logger.info("Using new format (source_path in metadata)")

    # Filter out secondary files based on dedup manifest (using hash-based lookup)
    # Use cached file_hashes from preprocessing when available to avoid re-hashing
    file_hashes = metadata.get("file_hashes", {})
    skipped_dedup = 0

    # Match files to messages and pre-generate filenames
    logger.info("Matching files and generating filenames...")
    filename_gen = FilenameGenerator(
        "imessage", timestamp_format="%Y-%m-%d %H:%M:%S UTC"
    )
    matched_files = []

    if use_legacy_format:
        # Legacy format: match files from media/ directory to metadata
        for media_file in media_files:
            if media_file.name in filename_index:
                message = filename_index[media_file.name]

                # Dedup filtering for legacy format
                if dedup_manifest:
                    content_hash = file_hashes.get(media_file.name)
                    if content_hash:
                        if not is_file_primary_by_hash(
                            dedup_manifest, content_hash, "iMessage", str(media_file)
                        ):
                            skipped_dedup += 1
                            continue
                    else:
                        if IMessageProcessor.should_skip_for_dedup(
                            str(media_file), dedup_manifest, "iMessage"
                        ):
                            skipped_dedup += 1
                            continue

                file_ext = media_file.suffix.lower()
                identifier = _build_imessage_identifier(message)
                timestamp = _extract_message_timestamp(message)
                output_filename = filename_gen.generate(timestamp, identifier, file_ext)
                # Get content_hash for EXIF metadata
                content_hash = file_hashes.get(media_file.name)
                matched_files.append(
                    (media_file, message, output_filename, content_hash)
                )
            else:
                logger.warning(f"No metadata for file: {media_file.name}")
    else:
        # New format: read source_path from metadata directly
        for media_filename, message in filename_index.items():
            source_path_str = message.get("source_path")
            if not source_path_str:
                logger.warning(f"No source_path for file: {media_filename}")
                continue

            source_path = Path(source_path_str)
            if not source_path.exists():
                logger.warning(f"Source file not found: {source_path}")
                continue

            # Dedup filtering for new format
            if dedup_manifest:
                content_hash = file_hashes.get(media_filename)
                if content_hash:
                    # Pass source_path for within-processor dedup (e.g., same file
                    # in multiple iMessage exports from different devices)
                    if not is_file_primary_by_hash(
                        dedup_manifest, content_hash, "iMessage", source_path_str
                    ):
                        skipped_dedup += 1
                        continue
                else:
                    if IMessageProcessor.should_skip_for_dedup(
                        str(source_path), dedup_manifest, "iMessage"
                    ):
                        skipped_dedup += 1
                        continue

            file_ext = source_path.suffix.lower()
            identifier = _build_imessage_identifier(message)
            timestamp = _extract_message_timestamp(message)
            output_filename = filename_gen.generate(timestamp, identifier, file_ext)
            # Get content_hash for EXIF metadata
            content_hash = file_hashes.get(media_filename)
            matched_files.append((source_path, message, output_filename, content_hash))

    if skipped_dedup > 0:
        logger.info(f"Skipped {skipped_dedup} secondary files (dedup)")

    logger.info(f"Matched {len(matched_files)} files")

    # Process files
    logger.info("Processing files to %s/", output_dir)
    logger.info("=" * 50)

    num_workers = workers if workers is not None else default_worker_count()
    logger.info(f"Using {num_workers} workers")

    # Prepare arguments (include content_hash for EXIF)
    process_args = [
        (
            media_file,
            message,
            export_username,
            output_dir,
            output_filename,
            content_hash,
        )
        for media_file, message, output_filename, content_hash in matched_files
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
    # Result tuple: (success, output_path, message, exp_username, content_hash)
    def extract_info(r):
        return (r[1], r[2], r[3], r[4])  # (output_path, message, exp_username, hash)

    collected = IMessageProcessor.collect_processing_results(results, extract_info)

    logger.info(
        f"Copied {collected.success_count} files ({collected.failed_count} failed)"
    )

    # Phase 2: Correct file extensions and batch process EXIF metadata
    logger.info("Batch processing EXIF metadata...")
    file_paths, file_info, exif_rebuilt_count = IMessageProcessor.finalize_files(
        collected.file_paths,
        collected.file_info,
        _build_metadata_requests,
        dedup_manifest,
        path_index=0,
    )
    if file_paths:
        logger.info(f"EXIF metadata written for {len(file_info)} files")

    # Print summary using shared utility
    print_processing_summary(
        success=collected.success_count,
        failed=collected.failed_count,
        total=len(matched_files),
        output_dir=str(output_dir),
        extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
    )

    return True
