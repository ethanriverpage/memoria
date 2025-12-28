"""Snapchat Messages Processor - handles chat media with overlays and metadata."""

import json
import logging
import re
from multiprocessing import Pool
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
    build_source_line,
    build_message_context,
    PLATFORM_SNAPCHAT,
    SERVICE_MESSAGES,
)
from common.files.utils import copy_file
from common.media.exiftool import (
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    MetadataWriteRequest,
    build_datetime_tags,
)
from common.files.utils import short_path
from common.output.logging import init_worker_logging
from common.media.overlay import (
    create_image_with_overlay,
    create_video_with_overlay,
    OverlayProcessingError,
)
from common.processing import (
    print_processing_summary,
)
from common.output.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    default_worker_count,
    FilenameGenerator,
    get_media_type,
    is_preprocessed_directory,
    update_file_timestamps,
)
from common.output.logging import get_processor_logger
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.snapchat_messages.preprocess import SnapchatPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class SnapchatMessagesProcessor(ProcessorBase):
    """Processor for Snapchat Messages exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this is a Snapchat Messages export."""
        try:
            # Check for consolidated structure first (messages/ subdirectory)
            messages_subdir = input_path / "messages"
            if messages_subdir.exists() and messages_subdir.is_dir():
                # Check for raw export in messages/ subdirectory
                json_dir = messages_subdir / "json"
                if json_dir.exists() and json_dir.is_dir():
                    chat_history = json_dir / "chat_history.json"
                    snap_history = json_dir / "snap_history.json"
                    if chat_history.exists() and snap_history.exists():
                        return True

                # Check for preprocessed in messages/ subdirectory
                metadata_file = messages_subdir / "metadata.json"
                if metadata_file.exists():
                    try:
                        with open(metadata_file, "r", encoding="utf-8") as f:
                            metadata = json.load(f)
                        if isinstance(metadata, dict) and "conversations" in metadata:
                            export_info = metadata.get("export_info", {})
                            if (
                                "export_username" in export_info
                                or len(metadata.get("conversations", {})) > 0
                            ):
                                return True
                    except (json.JSONDecodeError, KeyError):
                        pass

            # Check for direct structure (old format)
            # Check for raw export pattern
            json_dir = input_path / "json"
            if json_dir.exists() and json_dir.is_dir():
                chat_history = json_dir / "chat_history.json"
                snap_history = json_dir / "snap_history.json"

                if chat_history.exists() and snap_history.exists():
                    return True

            # Check for preprocessed pattern
            metadata_file = input_path / "metadata.json"

            if metadata_file.exists():
                try:
                    with open(metadata_file, "r", encoding="utf-8") as f:
                        metadata = json.load(f)

                    # Preprocessed Snapchat Messages has 'conversations' key
                    if isinstance(metadata, dict) and "conversations" in metadata:
                        # Also check it's not Snapchat Memories
                        export_info = metadata.get("export_info", {})
                        # Snapchat Messages should have export_username
                        if (
                            "export_username" in export_info
                            or len(metadata.get("conversations", {})) > 0
                        ):
                            return True

                except (json.JSONDecodeError, KeyError):
                    pass

            return False

        except Exception as e:
            log.detection_failed("Snapchat Messages", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "Snapchat Messages"

    @staticmethod
    def get_priority() -> int:
        return 85

    @staticmethod
    def get_required_dependencies() -> List[str]:
        return ["exiftool", "ffmpeg", "pillow"]

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Snapchat",
            service="messages",
            username_prefix="snapchat",
        )

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if Snapchat Messages export needs preprocessing."""
        # Check if already preprocessed
        if is_preprocessed_directory(input_dir):
            return False

        input_path = Path(input_dir)
        # Check for raw export structure (chat_media/ directory)
        chat_media_dir = input_path / "chat_media"
        if chat_media_dir.exists():
            return True

        # Check for consolidated export structure (messages/chat_media/)
        consolidated_chat_media = input_path / "messages" / "chat_media"
        if consolidated_chat_media.exists():
            return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw Snapchat Messages export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        # Check for consolidated export structure (messages/chat_media/)
        # If found, preprocess from the messages/ subdirectory
        messages_subdir = input_path / "messages"
        if messages_subdir.exists() and messages_subdir.is_dir():
            chat_media_check = messages_subdir / "chat_media"
            json_check = messages_subdir / "json"
            if chat_media_check.exists() or json_check.exists():
                logger.info(
                    "Detected consolidated export structure, preprocessing from: %s",
                    messages_subdir,
                )
                input_path = messages_subdir

        logger.info("Preprocessing Snapchat Messages export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = SnapchatPreprocessor(
            export_path=input_path,
            output_dir=cache_path,
            workers=workers,
        )

        if preprocessor.run():
            return str(cache_path)
        return None

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata (generator).

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
            if "sender" in entry:
                result["sender"] = entry["sender"]
            if "conversation_title" in entry:
                result["conversation"] = entry["conversation_title"]
            if "created" in entry:
                result["timestamp"] = entry["created"]
            return result

        catalog_iter = SnapchatMessagesProcessor.catalog_from_preprocessed_if_available(
            processor_name="Snapchat Messages",
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
        """Process Snapchat Messages export."""
        # Check for required dependencies
        if not cls.check_dependencies():
            return False

        try:
            # Check if this is a consolidated export with messages/ subdirectory
            input_path = Path(input_dir)
            messages_subdir = input_path / "messages"

            # Use messages/ subdirectory if it exists, otherwise use root
            if messages_subdir.exists() and messages_subdir.is_dir():
                # Verify it's actually a messages export in the subdirectory
                json_dir = messages_subdir / "json"
                metadata_file = messages_subdir / "metadata.json"
                if (
                    json_dir.exists() and (json_dir / "chat_history.json").exists()
                ) or metadata_file.exists():
                    actual_input_dir = str(messages_subdir)
                    logger.info(
                        f"Detected consolidated export structure, using: {actual_input_dir}"
                    )
                else:
                    actual_input_dir = input_dir
            else:
                actual_input_dir = input_dir

            # Create messages subdirectory under output
            if output_dir:
                processor_output = str(Path(output_dir) / "messages")
            else:
                processor_output = kwargs.get("output", "final_snapmsgs/messages")

            # Call processing logic directly
            process_logic(
                input_dir=actual_input_dir,
                output_dir=processor_output,
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.error("Error in SnapchatMessagesProcessor: %s", e)
            return False


def _build_description(message: dict, export_username: str) -> str:
    """Build the description string for a Snapchat message using unified format."""
    is_merged = "messages" in message and isinstance(message["messages"], list)

    # Build source line using unified format
    source_entry = SourceEntry(
        processor=PLATFORM_SNAPCHAT,
        username=export_username,
        service=SERVICE_MESSAGES,
    )
    source_line = build_source_line(source_entry)
    description_parts = [source_line]

    if is_merged:
        # Merged message - list all conversations in unified format
        for msg in message["messages"]:
            conv_type = msg.get("conversation_type")
            conv_id = msg.get("conversation_id")
            sender = msg.get("sender", "unknown")
            content = msg.get("content", "")

            if conv_id:
                if conv_type == "dm":
                    conv_name = f"DM with {conv_id}"
                else:
                    conv_name = msg.get("conversation_title", "Unknown Group")

                # Use unified message context format
                msg_entry = SourceEntry(
                    processor=PLATFORM_SNAPCHAT,
                    sender=sender,
                    conversation=conv_name,
                    content=content,
                )
                msg_line = build_message_context(msg_entry)
                if msg_line:
                    description_parts.append(msg_line)

        return "&#xa;".join(description_parts)
    else:
        # Single message - use same unified format
        conv_type = message.get("conversation_type")
        conv_id = message.get("conversation_id")

        if conv_id is None or conv_type is None:
            # Orphaned media - minimal metadata
            return build_description(source_entry)
        else:
            # Normal message with unified format
            if conv_type == "dm":
                conv_name = f"DM with {conv_id}"
            else:
                conv_name = message.get("conversation_title", "Unknown Group")

            sender = message.get("sender", "unknown")
            content = message.get("content", "")

            # Use unified message context format
            msg_entry = SourceEntry(
                processor=PLATFORM_SNAPCHAT,
                sender=sender,
                conversation=conv_name,
                content=content,
            )
            msg_line = build_message_context(msg_entry)
            if msg_line:
                description_parts.append(msg_line)

            return "&#xa;".join(description_parts)


def _build_metadata_requests(
    file_info: List[Tuple[Optional[str], dict, str, Optional[str], Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Snapchat Messages files.

    Args:
        file_info: List of (file_path, message, export_username, content_hash,
                   original_filename) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for (
        file_path,
        message,
        export_username,
        content_hash,
        original_filename,
    ) in file_info:
        if file_path is None:
            continue
        # Skip MKV files (they already have metadata from overlay creation)
        if file_path.endswith(".mkv"):
            continue

        # Check if this is a merged message
        is_merged = "messages" in message and isinstance(message["messages"], list)

        # Build datetime tags (handles both legacy date-only and standard formats)
        date_str = (
            message.get("canonical_timestamp")
            or (message.get("primary_created") if is_merged else message.get("created"))
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
                processor_name="snapchat_messages",
                content_hash=content_hash,
                original_filename=original_filename,
            )
        )

    return requests


def build_media_index(metadata: dict) -> dict:
    """Build index mapping media_id to message metadata."""
    media_index = {}

    # Index messages from conversations
    for conv_data in metadata.get("conversations", {}).values():
        for message in conv_data.get("messages", []):
            media_id = message.get("media_id", "")
            if media_id:
                # Split by " | " to handle multiple media IDs
                media_ids = [mid.strip() for mid in media_id.split("|")]
                for mid in media_ids:
                    if mid:
                        media_index[mid] = message

    # Index unmatched media
    unmatched_media = metadata.get("unmatched_media", [])
    for message in unmatched_media:
        # Unmatched media entries follow a different schema than messages.
        # Prefer top-level media_id, but also allow it under metadata{}.
        media_id = message.get("media_id", "") or (
            (message.get("metadata") or {}).get("media_id", "")
            if isinstance(message, dict)
            else ""
        )
        if media_id:
            # Split by " | " to handle multiple media IDs
            media_ids = [mid.strip() for mid in media_id.split("|")]
            for mid in media_ids:
                if mid:
                    media_index[mid] = message

    return media_index


def build_filename_index(metadata: dict) -> dict:
    """Build index mapping media filename to message metadata."""
    return ProcessorBase.build_media_filename_index(
        metadata,
        media_keys=["media_files"],
        include_orphaned=True,
    )


def extract_media_id_from_filename(filename: str) -> Optional[str]:
    """Extract media_id from filename (pattern: YYYY-MM-DD_b~{media_id}.{ext})."""
    # Pattern: YYYY-MM-DD_b~{media_id}.{ext}
    match = re.match(r"^\d{4}-\d{2}-\d{2}_b~(.+)\.\w+$", filename)
    if match:
        return "b~" + match.group(1)

    return None


def extract_date_from_filename(filename: str) -> Optional[str]:
    """Extract date from filename with YYYY-MM-DD prefix."""
    match = re.match(r"^(\d{4}-\d{2}-\d{2})_", filename)
    if match:
        return match.group(1)
    return None


def _get_message_timestamp(message: dict) -> Optional[str]:
    """Extract timestamp string from message metadata."""
    is_merged = "messages" in message and isinstance(message["messages"], list)

    if is_merged:
        return message.get("primary_created") or (
            message["messages"][0].get("created") if message["messages"] else None
        )
    else:
        # Snapchat Messages uses `created`. Unmatched media entries use `timestamp`.
        return message.get("created") or message.get("timestamp")


def _update_snapchat_timestamps(file_path: Path, message: dict) -> bool:
    """Update filesystem timestamps to match message date."""
    # Parse date from message - use primary_created for merged messages
    if "primary_created" in message:
        date_str = message["primary_created"]
    else:
        date_str = message.get("created")

    if not date_str:
        return False

    # All messages should have full timestamp format: YYYY-MM-DD HH:MM:SS UTC
    # Fallback to date-only parsing for backwards compatibility with old metadata
    if len(date_str) == 10 and date_str.count(":") == 0:
        # Legacy date-only format: YYYY-MM-DD
        timestamp_format = "%Y-%m-%d"
    else:
        # Standard timestamp format: YYYY-MM-DD HH:MM:SS UTC
        timestamp_format = "%Y-%m-%d %H:%M:%S"

    return update_file_timestamps(file_path, date_str, timestamp_format)


def _create_file_worker(args_tuple):
    """Worker function for creating output files with overlays.

    Args:
        args_tuple: (media_path, message, export_username, output_dir,
                    output_filename, content_hash, original_filename)

    Returns:
        Tuple of (success, output_path, is_mkv, message, export_username, content_hash,
        original_filename)
    """
    (
        media_path,
        message,
        export_username,
        output_dir,
        output_filename,
        content_hash,
        original_filename,
    ) = args_tuple

    try:
        # Determine file type
        is_video = get_media_type(media_path) == "video"
        is_image = get_media_type(media_path) == "image"

        # Get file extension for filename generation
        file_ext = media_path.suffix.lower()

        # Get overlay from message metadata (matched during preprocessing)
        # Uses overlay_source_path directly from metadata (hash-only preprocessing)
        overlay_path = None
        overlay_source_path = message.get("overlay_source_path")
        if overlay_source_path:
            potential_overlay = Path(overlay_source_path)
            if potential_overlay.exists():
                overlay_path = potential_overlay

        output_path = output_dir / output_filename

        # Process based on file type and overlay availability
        if overlay_path:
            if is_video:
                # Video with overlay - create multi-track MKV
                # Build video metadata for date/GPS embedding
                if "primary_created" in message:
                    # Merged message format
                    video_metadata = {
                        "date": message["primary_created"],
                    }
                else:
                    # Single message format
                    video_metadata = {
                        "date": message.get("created"),
                    }

                # Build description with full message context (handles merged messages)
                video_description = _build_description(message, export_username)

                try:
                    success = create_video_with_overlay(
                        media_path,
                        overlay_path,
                        output_path,
                        video_metadata,
                        export_username=export_username,
                        description=video_description,
                        original_filename=original_filename,
                    )

                    if success:
                        _update_snapchat_timestamps(output_path, message)
                        # MKV files already have metadata embedded (including original_filename)
                        return (
                            True,
                            str(output_path),
                            True,
                            message,
                            export_username,
                            content_hash,
                            original_filename,
                        )
                    else:
                        # Fallback: copy original video with original extension
                        fallback_filename = output_filename.replace(".mkv", file_ext)
                        fallback_path = output_dir / fallback_filename
                        copy_file(media_path, fallback_path)
                        _update_snapchat_timestamps(fallback_path, message)
                        # Fallback file needs batch EXIF processing
                        return (
                            True,
                            str(fallback_path),
                            False,
                            message,
                            export_username,
                            content_hash,
                            original_filename,
                        )
                except OverlayProcessingError as e:
                    # Overlay corrupt - soft-fail, copy base video
                    logger.warning(
                        "Overlay processing failed for %s: %s - copying base video",
                        media_path.name,
                        e,
                    )
                    fallback_filename = output_filename.replace(".mkv", file_ext)
                    fallback_path = output_dir / fallback_filename
                    copy_file(media_path, fallback_path)
                    _update_snapchat_timestamps(fallback_path, message)
                    raise  # Re-raise for outer exception handler to track

            elif is_image:
                # Image with overlay - composite
                try:
                    create_image_with_overlay(
                        media_path, overlay_path, output_path
                    )
                    _update_snapchat_timestamps(output_path, message)
                    # Image needs batch EXIF processing
                    return (
                        True,
                        str(output_path),
                        False,
                        message,
                        export_username,
                        content_hash,
                        original_filename,
                    )
                except OverlayProcessingError as e:
                    # Overlay corrupt - soft-fail, copy base image
                    logger.warning(
                        "Overlay processing failed for %s: %s - copying base image",
                        media_path.name,
                        e,
                    )
                    copy_file(media_path, output_path)
                    _update_snapchat_timestamps(output_path, message)
                    raise  # Re-raise for outer exception handler to track
            else:
                # Unknown file type with overlay - just copy
                copy_file(media_path, output_path)
                _update_snapchat_timestamps(output_path, message)
                return (
                    True,
                    str(output_path),
                    False,
                    message,
                    export_username,
                    content_hash,
                    original_filename,
                )
        else:
            # No overlay - just copy
            copy_file(media_path, output_path)
            _update_snapchat_timestamps(output_path, message)
            # File needs batch EXIF processing
            return (
                True,
                str(output_path),
                False,
                message,
                export_username,
                content_hash,
                original_filename,
            )

    except OverlayProcessingError:
        # Overlay corrupt - base file was already copied by inner handler
        # Treat as success (file was produced) but log warning was already emitted
        return (
            True,
            str(output_path),
            False,
            message,
            export_username,
            content_hash,
            original_filename,
        )
    except Exception as e:
        logger.error("Error processing %s: %s", media_path.name, e)
        return (
            False,
            None,
            False,
            message,
            export_username,
            content_hash,
            original_filename,
        )


def process_logic(
    input_dir,
    output_dir="processed_messages",
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Snapchat Messages exports.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling SnapchatMessagesProcessor.preprocess() directly).
    """
    log_filename = None

    # Validate input directory exists
    input_dir = Path(input_dir)
    if not input_dir.exists():
        logger.error("Input directory not found: %s", short_path(input_dir))
        return

    # Check for required tools
    if not SnapchatMessagesProcessor.check_dependencies():
        return

    logger.info("Snapchat Messages Processor")

    output_dir = Path(output_dir)

    # Require preprocessed input
    if not is_preprocessed_directory(str(input_dir)):
        logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            input_dir,
        )
        return False

    logger.info("Processing preprocessed directory: %s", short_path(input_dir))
    _process_snapchat_working_directory(
        input_dir, output_dir, workers, log_filename, dedup_manifest
    )


def _process_snapchat_working_directory(
    working_dir,
    output_dir,
    workers,
    log_filename,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Process a working directory (preprocessed Snapchat Messages export)."""
    # Ensure working_dir is a Path
    working_dir = Path(working_dir)

    # Configuration
    metadata_file = working_dir / "metadata.json"

    # Check for metadata file (should exist after preprocessing or if already preprocessed)
    if not metadata_file.exists():
        logger.error("Metadata file not found: %s", short_path(metadata_file))
        return

    # Load metadata
    logger.info("Loading metadata from %s...", short_path(metadata_file))
    metadata = SnapchatMessagesProcessor.load_metadata(
        metadata_file,
        validate=True,
        require_source_paths=True,
        processor_name="Snapchat Messages",
    )

    export_info = metadata.get("export_info", {})
    export_username = export_info.get("export_username")
    log.export_username(export_username)

    # Initialize failure tracker for this export
    failure_tracker = FailureTracker(
        processor_name="Snapchat Messages",
        export_directory=str(working_dir),
    )

    # Build media index
    logger.info("Building media index...")
    media_index = build_media_index(metadata)
    filename_index = build_filename_index(metadata)
    logger.info("Found %d messages with media IDs", len(media_index))
    logger.info("Found %d orphaned media entries by filename", len(filename_index))

    # Build list of source files from metadata.
    #
    # Conversation messages store:
    #   media_files + source_paths{filename -> absolute path}
    # Unmatched media entries store:
    #   filename + source_path
    media_files: list[Path] = []
    missing_source_path = 0
    missing_path_examples: list[str] = []
    missing_file_count = 0
    missing_file_examples: list[str] = []
    for filename, ctx in filename_index.items():
        source_path_str: Optional[str] = None
        if isinstance(ctx, dict) and ctx.get("source_path"):
            source_path_str = ctx.get("source_path")
        else:
            source_paths = ctx.get("source_paths", {}) if isinstance(ctx, dict) else {}
            if isinstance(source_paths, dict):
                source_path_str = source_paths.get(filename)

        if not source_path_str:
            missing_source_path += 1
            if len(missing_path_examples) < 5:
                missing_path_examples.append(filename)
            continue

        source_path = Path(source_path_str)
        if source_path.exists():
            media_files.append(source_path)
        else:
            missing_file_count += 1
            if len(missing_file_examples) < 5:
                missing_file_examples.append(short_path(source_path))

    if missing_source_path > 0:
        logger.info(
            "Missing source_path for %d files (e.g. %s)",
            missing_source_path,
            ", ".join(missing_path_examples),
        )
    if missing_file_count > 0:
        logger.info(
            "Source file not found for %d files (e.g. %s)",
            missing_file_count,
            ", ".join(missing_file_examples),
        )

    logger.info("Found %d media files from metadata", len(media_files))

    # Filter out secondary files based on dedup manifest (using hash-based lookup)
    file_hashes = metadata.get("file_hashes", {})
    skipped_secondary_files: list[str] = []
    if dedup_manifest:
        original_count = len(media_files)
        filtered_files = []
        for f in media_files:
            content_hash = file_hashes.get(f.name)
            source_path_str = str(f)
            if SnapchatMessagesProcessor.should_skip_for_dedup(
                dedup_manifest, content_hash, "Snapchat Messages", source_path_str
            ):
                skipped_secondary_files.append(source_path_str)
            else:
                filtered_files.append(f)
        media_files = filtered_files
        skipped_count = original_count - len(media_files)
        if skipped_count > 0:
            log.skipped_secondary(skipped_count)

    # Check for no media files (export-level soft-fail)
    if not media_files:
        logger.warning("No media files found to process.")
        failure_tracker.add_export_failure(
            reason="no_media_files",
            error_details="No media files found in export to process",
        )
        failure_tracker.handle_failures(output_dir)
        return False

    # Create output directory (subdirectory already set by caller)
    output_dir.mkdir(exist_ok=True, parents=True)

    # Match media files to messages
    logger.info("Matching media files to messages...")
    matched_files = []
    unmatched_files = []

    for media_file in media_files:
        # Try to extract media_id from filename
        media_id = extract_media_id_from_filename(media_file.name)

        if media_id and media_id in media_index:
            # Direct match by media_id
            message = media_index[media_id]
            matched_files.append((media_file, message))
        elif media_file.name in filename_index:
            # Match by filename (for orphaned media without media_id)
            message = filename_index[media_file.name]
            matched_files.append((media_file, message))
        else:
            # Could not match - add to unmatched
            unmatched_files.append(media_file)

    logger.info("Matched %d files", len(matched_files))
    logger.info("Unmatched %d files", len(unmatched_files))

    # Pre-generate all output filenames to ensure deterministic naming
    logger.info("Pre-generating output filenames...")
    filename_gen = FilenameGenerator(
        "snap",
        export_username,
        service="msgs",
        timestamp_format="%Y-%m-%d %H:%M:%S UTC",
    )
    output_filenames = []  # List of output filenames in same order as matched_files

    for media_file, message in matched_files:
        is_video = get_media_type(media_file) == "video"

        # Get file extension for filename generation
        file_ext = media_file.suffix.lower()

        # Check if this file will get an overlay and become MKV.
        # Conversation messages use top-level overlay_source_path.
        # Unmatched media entries store extra fields under metadata{}.
        overlay_source_path = message.get("overlay_source_path") or (
            (message.get("metadata") or {}).get("overlay_source_path", "")
            if isinstance(message, dict)
            else ""
        )
        will_have_overlay = False
        if overlay_source_path and is_video:
            if Path(overlay_source_path).exists():
                will_have_overlay = True

        # Generate output filename from canonical timestamp (dedup merged earliest preferred)
        timestamp = _get_message_timestamp(message)
        content_hash = file_hashes.get(media_file.name)
        canonical_ts = get_canonical_timestamp(dedup_manifest, content_hash, timestamp)
        message["canonical_timestamp"] = canonical_ts
        ext = ".mkv" if will_have_overlay else file_ext
        output_filename = filename_gen.generate(canonical_ts, ext)

        output_filenames.append(output_filename)

    # Process matched files with parallel processing
    logger.info(
        "Processing %d matched files to %s/",
        len(matched_files),
        short_path(output_dir),
    )

    # Determine number of parallel workers
    num_workers = workers if workers is not None else default_worker_count()
    logger.info("Using %d parallel workers", num_workers)

    # Prepare arguments for parallel processing
    process_args = []
    for idx, (media_file, message) in enumerate(matched_files):
        output_filename = output_filenames[idx]
        # Get content_hash from cached file_hashes (computed during catalog phase)
        content_hash = file_hashes.get(media_file.name)
        # Get original filename for XMP:PreservedFileName embedding
        original_filename = media_file.name
        process_args.append(
            (
                media_file,
                message,
                export_username,
                output_dir,
                output_filename,
                content_hash,
                original_filename,
            )
        )

    # Process files in parallel
    # Phase 1: Create all files with overlays in parallel
    if num_workers > 1:
        # Specify app modules for DEBUG level in workers
        worker_app_modules = [
            __name__,
            "common.media.overlay",
            "common.media.encoder",
            "processors.snapchat_messages.preprocess",
        ]
        with Pool(
            processes=num_workers,
            initializer=init_worker_logging,
            initargs=(log_filename, worker_app_modules),
        ) as pool:
            results = list(
                progress_bar(
                    pool.imap(_create_file_worker, process_args),
                    PHASE_PROCESS,
                    "Creating files",
                    total=len(process_args),
                )
            )
    else:
        # Single-threaded for debugging
        results = []
        for args_tuple in progress_bar(process_args, PHASE_PROCESS, "Creating files"):
            results.append(_create_file_worker(args_tuple))

    # Phase 2: Collect non-MKV files for batch EXIF processing
    # Result tuple: (success, output_path, is_mkv, message, exp_username, content_hash, original_filename)
    # Skip MKV files in file_info (they already have metadata from overlay creation)
    def extract_non_mkv_info(r):
        is_mkv = r[2]
        if is_mkv:
            return None  # Skip MKV files from EXIF processing
        return (
            r[1],
            r[3],
            r[4],
            r[5],
            r[6],
        )  # (output_path, message, exp_username, hash, original_filename)

    collected = SnapchatMessagesProcessor.collect_processing_results(
        results, extract_non_mkv_info
    )

    logger.info(
        "Created %d files (%d need EXIF processing)",
        collected.success_count,
        len(collected.file_info),
    )

    # Phase 3: Correct file extensions and batch process EXIF metadata
    logger.info("Batch processing EXIF metadata...")
    file_paths, file_info, exif_rebuilt_count = (
        SnapchatMessagesProcessor.finalize_files(
            collected.file_paths,
            collected.file_info,
            _build_metadata_requests,
            dedup_manifest,
            path_index=0,
        )
    )

    # Handle unmatched files via FailureTracker
    if unmatched_files:
        logger.info("Processing %d unmatched files...", len(unmatched_files))
        for media_file in unmatched_files:
            failure_tracker.add_orphaned_media(
                source_path=media_file,
                reason="No matching metadata found",
            )

    # Handle failures - copy orphaned files and generate report
    failure_tracker.handle_failures(output_dir)

    # Save dedup references for skipped secondary files
    if dedup_manifest and skipped_secondary_files:
        save_dedup_metadata(
            manifest=dedup_manifest,
            skipped_files=skipped_secondary_files,
            processor_name="Snapchat Messages",
            output_dir=output_dir,
        )

    # Print summary using shared utility
    print_processing_summary(
        success=collected.success_count,
        failed=collected.failed_count,
        total=len(media_files),
        output_dir=str(output_dir),
        extra_stats={
            "EXIF structures rebuilt": exif_rebuilt_count,
            "Unmatched": len(unmatched_files),
        },
    )

    if unmatched_files:
        logger.info(
            "Unmatched files were tracked via FailureTracker (see issues/failure-report.json)."
        )

    logger.info("Note: Videos with overlays are saved as multi-track MKV files:")
    logger.info("  - Track 0 (default): Video with overlay embedded")
    logger.info("  - Track 1: Original video without overlay")
    logger.info("  Switch tracks in VLC: Video > Video Track > Select track")

    # Materialize ambiguous cases from metadata into output/issues/needs_matching/
    # These were recorded during preprocessing with source_path for each file
    try:
        materialized = SnapchatMessagesProcessor.materialize_ambiguous_cases(
            metadata, output_dir
        )
        if materialized > 0:
            needs_matching_dest = output_dir / "issues" / "needs_matching"
            logger.info(
                "Ambiguous cases created in: %s", short_path(needs_matching_dest)
            )
    except Exception as e:
        logger.warning("Failed to materialize ambiguous cases: %s", e)
