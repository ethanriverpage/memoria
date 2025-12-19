"""Snapchat Messages Processor - handles chat media with overlays and metadata."""

import json
import logging
import re
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
    PLATFORM_SNAPCHAT,
    SERVICE_MESSAGES,
)
from common.dedup.service import is_file_primary_by_hash
from common.files.utils import batch_correct_extensions
from common.dedup.hashing import compute_content_hash
from common.dependency_checker import (
    check_exiftool,
    check_ffmpeg,
    check_pillow,
    print_exiftool_error,
    print_ffmpeg_error,
    print_pillow_error,
)
from common.media.exiftool import (
    batch_validate_exif,
    batch_rebuild_exif,
    batch_read_existing_metadata,
    batch_write_metadata_requests,
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    MetadataWriteRequest,
)
from common.output.logging import init_worker_logging
from common.media.overlay import (
    create_image_with_overlay,
    create_video_with_overlay,
)
from common.processing import (
    get_preprocessing_cache_path,
    is_preprocessing_cache_valid,
    print_processing_summary,
    temp_processing_directory,
)
from common.output.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    default_worker_count,
    extract_username_from_export_dir,
    FilenameGenerator,
    get_media_type,
    is_preprocessed_directory,
    sanitize_filename,
    update_file_timestamps,
)
from processors.base import ProcessorBase
from processors.snapchat_messages.preprocess import (
    SnapchatPreprocessor,
    PreprocessingError,
)

logger = logging.getLogger(__name__)


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
            media_dir = messages_subdir / "media"
            if metadata_file.exists() and media_dir.exists():
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
        media_dir = input_path / "media"

        if metadata_file.exists() and media_dir.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                # Preprocessed Snapchat Messages has 'conversations' key
                if isinstance(metadata, dict) and "conversations" in metadata:
                    # Also check it's not Snapchat Memories (which has different structure)
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
        logger.debug(f"Detection failed for Snapchat Messages: {e}")
        return False


class SnapchatMessagesProcessor(ProcessorBase):
    """Processor for Snapchat Messages exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        return "Snapchat Messages"

    @staticmethod
    def get_priority() -> int:
        return 85

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata (generator)."""
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        # Determine the working directory for cataloging
        # Check for preprocessed export first (has media/ directory)
        working_dir = None
        media_dir = input_path / "media"

        if media_dir.exists():
            # Already preprocessed at root level
            working_dir = input_path
        else:
            # Check for consolidated structure with messages/ subdirectory
            messages_subdir = input_path / "messages"
            if messages_subdir.exists():
                media_subdir = messages_subdir / "media"
                if media_subdir.exists():
                    # Already preprocessed in messages/ subdirectory
                    working_dir = messages_subdir

        # If no preprocessed directory found, check for raw export and auto-preprocess
        if working_dir is None:
            # Check for raw export structure (json/chat_history.json)
            raw_export_dir = None
            json_dir = input_path / "json"
            if json_dir.exists() and (json_dir / "chat_history.json").exists():
                raw_export_dir = input_path
            else:
                # Check in messages/ subdirectory
                messages_subdir = input_path / "messages"
                if messages_subdir.exists():
                    json_subdir = messages_subdir / "json"
                    if (
                        json_subdir.exists()
                        and (json_subdir / "chat_history.json").exists()
                    ):
                        raw_export_dir = messages_subdir

            if raw_export_dir is None:
                logger.warning(f"No media directory found in: {input_path}")
                return

            # Check for existing preprocessing cache
            cache_path = get_preprocessing_cache_path(input_path, "Snapchat Messages")

            if is_preprocessing_cache_valid(cache_path):
                logger.info(f"Using preprocessing cache: {cache_path}")
                working_dir = cache_path
            else:
                # Auto-preprocess to cache directory
                logger.info(
                    f"Raw export detected, preprocessing to cache: {cache_path}"
                )
                cache_path.mkdir(parents=True, exist_ok=True)

                # Extract username from directory name for consolidated exports
                username_override = None
                if raw_export_dir != input_path:
                    username_override = extract_username_from_export_dir(
                        str(input_path), "snapchat"
                    )

                preprocessor = SnapchatPreprocessor(
                    export_path=raw_export_dir,
                    output_dir=cache_path,
                    workers=workers,
                    username_override=username_override,
                )
                try:
                    preprocessor.process()
                except PreprocessingError as e:
                    logger.error(f"Preprocessing failed: {e}")
                    return

                working_dir = cache_path

        # Now catalog from the working directory
        media_dir = working_dir / "media"
        if not media_dir.exists():
            logger.warning(
                f"No media directory found after preprocessing: {working_dir}"
            )
            return

        media_files = [
            f for f in media_dir.iterdir() if f.is_file() and get_media_type(f)
        ]

        yield from catalog_media_files(
            processor_name="Snapchat Messages",
            input_path=working_dir,
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
        """Process Snapchat Messages export."""
        # Check for required dependencies
        if not check_exiftool():
            print_exiftool_error()
            return False

        if not check_ffmpeg():
            print_ffmpeg_error()
            return False

        try:
            # Check if this is a consolidated export with messages/ subdirectory
            input_path = Path(input_dir)
            messages_subdir = input_path / "messages"

            # Use messages/ subdirectory if it exists, otherwise use root
            username_override = None
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
                    # Extract username from parent directory for consolidated exports
                    username_override = extract_username_from_export_dir(
                        input_dir, "snapchat"
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
                temp_dir=kwargs.get("temp_dir", "../pre"),
                verbose=kwargs.get("verbose", False),
                workers=kwargs.get("workers"),
                username_override=username_override,
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.error(f"Error in SnapchatMessagesProcessor: {e}")
            return False


def get_processor():
    """Return processor class for auto-discovery."""
    return SnapchatMessagesProcessor


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
    file_info: List[Tuple[str, dict, str]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Snapchat Messages files."""
    requests: List[MetadataWriteRequest] = []

    for file_path, message, export_username in file_info:
        # Skip MKV files (they already have metadata from overlay creation)
        if file_path.endswith(".mkv"):
            continue

        # Check if this is a merged message
        is_merged = "messages" in message and isinstance(message["messages"], list)

        # Build datetime tags
        datetime_tags: List[Tuple[str, str]] = []
        if is_merged:
            date_str = message.get("primary_created")
        else:
            date_str = message.get("created")

        if date_str:
            # Handle both legacy date-only and standard timestamp formats
            if len(date_str) == 10 and date_str.count(":") == 0:
                exif_date = date_str.replace("-", ":") + " 12:00:00"
            else:
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
                processor_name="snapchat_messages",
            )
        )

    return requests


def load_metadata(metadata_path: Path) -> dict:
    """Load and parse metadata.json."""
    with open(metadata_path, "r", encoding="utf-8") as f:
        return json.load(f)


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

    # Index orphaned media
    for message in metadata.get("orphaned_media", []):
        media_id = message.get("media_id", "")
        if media_id:
            # Split by " | " to handle multiple media IDs
            media_ids = [mid.strip() for mid in media_id.split("|")]
            for mid in media_ids:
                if mid:
                    media_index[mid] = message

    return media_index


def build_filename_index(metadata: dict) -> dict:
    """Build index mapping media_file to message metadata."""
    filename_index = {}

    # Index messages from conversations
    for conv_data in metadata.get("conversations", {}).values():
        for message in conv_data.get("messages", []):
            # Handle both singular media_file and plural media_files
            if "media_file" in message:
                filename_index[message["media_file"]] = message
            elif "media_files" in message:
                for media_file in message["media_files"]:
                    filename_index[media_file] = message

    # Index orphaned media by filename
    for message in metadata.get("orphaned_media", []):
        # Handle both singular media_file and plural media_files
        if "media_file" in message:
            filename_index[message["media_file"]] = message
        elif "media_files" in message:
            for media_file in message["media_files"]:
                filename_index[media_file] = message

    return filename_index


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


def _build_chat_identifier(message: dict, export_username: str) -> str:
    """Build identifier for snapchat message filename based on conversation type."""
    is_merged = "messages" in message and isinstance(message["messages"], list)

    if is_merged:
        # Merged messages use simplified naming without conversation
        return export_username

    # Determine conversation type and identifier
    conv_type = message.get("conversation_type")
    conv_id = message.get("conversation_id")

    # Handle orphaned media (no conversation context)
    if conv_id is None or conv_type is None:
        return f"{export_username}-unknown"
    elif conv_type == "dm":
        # Direct message - use conversation_id (other user's username)
        return f"{export_username}-{sanitize_filename(conv_id)}"
    else:
        # Group chat - use conversation_title
        title = message.get("conversation_title", "unknown")
        return f"{export_username}-{sanitize_filename(title)}"


def _get_message_timestamp(message: dict) -> Optional[str]:
    """Extract timestamp string from message metadata."""
    is_merged = "messages" in message and isinstance(message["messages"], list)

    if is_merged:
        return message.get("primary_created") or (
            message["messages"][0].get("created") if message["messages"] else None
        )
    else:
        return message.get("created")


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
    """Worker function for creating output files with overlays."""
    media_path, message, export_username, output_dir, overlays_dir, output_filename = (
        args_tuple
    )

    try:
        # Determine file type
        is_video = get_media_type(media_path) == "video"
        is_image = get_media_type(media_path) == "image"

        # Get file extension for filename generation
        file_ext = media_path.suffix.lower()

        # Get overlay from message metadata (matched during preprocessing)
        overlay_path = None
        overlay_filename = message.get("overlay_file")
        if overlay_filename:
            potential_overlay = overlays_dir / overlay_filename
            if potential_overlay.exists():
                overlay_path = potential_overlay

        output_path = output_dir / output_filename

        # Process based on file type and overlay availability
        if overlay_path:
            if is_video:
                # Video with overlay - create multi-track MKV
                # Handle both single and merged message formats
                if "primary_created" in message:
                    # Merged message format
                    video_metadata = {
                        "date": message["primary_created"],
                        "conversation_type": "multiple",
                        "conversation_id": "merged",
                        "conversation_title": f"{len(message['messages'])} conversations",
                        "sender": "multiple",
                        "content": message.get("content", ""),
                    }
                else:
                    # Single message format
                    video_metadata = {
                        "date": message.get("created"),
                        "conversation_type": message.get("conversation_type"),
                        "conversation_id": message.get("conversation_id"),
                        "conversation_title": message.get("conversation_title"),
                        "sender": message.get("sender"),
                        "content": message.get("content", ""),
                    }

                success = create_video_with_overlay(
                    media_path,
                    overlay_path,
                    output_path,
                    video_metadata,
                    export_username=export_username,
                )

                if success:
                    _update_snapchat_timestamps(output_path, message)
                    # MKV files already have metadata embedded
                    return (True, str(output_path), True, message, export_username)
                else:
                    # Fallback: copy original video with original extension
                    fallback_filename = output_filename.replace(".mkv", file_ext)
                    fallback_path = output_dir / fallback_filename
                    shutil.copy2(media_path, fallback_path)
                    _update_snapchat_timestamps(fallback_path, message)
                    # Fallback file needs batch EXIF processing
                    return (True, str(fallback_path), False, message, export_username)

            elif is_image:
                # Image with overlay - composite
                success = create_image_with_overlay(
                    media_path, overlay_path, output_path
                )

                if success:
                    _update_snapchat_timestamps(output_path, message)
                    # Image needs batch EXIF processing
                    return (True, str(output_path), False, message, export_username)
                else:
                    # Fallback: copy original
                    shutil.copy2(media_path, output_path)
                    _update_snapchat_timestamps(output_path, message)
                    return (True, str(output_path), False, message, export_username)
            else:
                # Unknown file type with overlay - just copy
                shutil.copy2(media_path, output_path)
                _update_snapchat_timestamps(output_path, message)
                return (True, str(output_path), False, message, export_username)
        else:
            # No overlay - just copy
            shutil.copy2(media_path, output_path)
            _update_snapchat_timestamps(output_path, message)
            # File needs batch EXIF processing
            return (True, str(output_path), False, message, export_username)

    except Exception as e:
        logger.error(f"Error processing {media_path.name}: {e}")
        return (False, None, False, message, export_username)


def process_logic(
    input_dir,
    output_dir="processed_messages",
    temp_dir="../pre",
    verbose=False,
    workers=None,
    username_override=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Snapchat Messages exports."""
    log_filename = None

    # Validate input directory exists
    input_dir = Path(input_dir)
    if not input_dir.exists():
        logger.error(f"Input directory not found: {input_dir}")
        return

    # Check for required tools
    if not check_exiftool():
        print_exiftool_error()
        return

    if not check_ffmpeg():
        print_ffmpeg_error()
        return

    if not check_pillow():
        print_pillow_error()
        return

    logger.info("Snapchat Messages Processor")
    logger.info("=" * 50)

    # Determine if input needs preprocessing
    output_dir = Path(output_dir)

    # Check if input is already preprocessed
    if is_preprocessed_directory(str(input_dir)):
        logger.info(f"Input directory is already preprocessed: {input_dir}")
        _process_snapchat_working_directory(
            input_dir, output_dir, workers, log_filename, dedup_manifest
        )
    else:
        # Check for preprocessing cache from catalog phase
        # Get the original input path for cache lookup (before messages/ subdir extraction)
        original_input_path = (
            input_dir.parent if input_dir.name == "messages" else input_dir
        )
        cache_path = get_preprocessing_cache_path(
            original_input_path, "Snapchat Messages"
        )

        if is_preprocessing_cache_valid(cache_path):
            logger.info(f"Using preprocessing cache from catalog phase: {cache_path}")
            _process_snapchat_working_directory(
                cache_path, output_dir, workers, log_filename, dedup_manifest
            )
        else:
            logger.info(f"Input directory is raw export: {input_dir}")
            logger.info("Running preprocessing...")

            # Use context manager for temp directory with automatic cleanup
            with temp_processing_directory(temp_dir, "snap_msgs") as temp_dir_path:
                logger.info(f"Preprocessing to: {temp_dir_path}")

                # Run preprocessing with final output directory for failure tracking
                # Note: output_dir already has /messages appended by the caller (process method)
                preprocessor = SnapchatPreprocessor(
                    export_path=Path(input_dir),
                    output_dir=temp_dir_path,
                    workers=workers,
                    final_output_dir=output_dir,
                    username_override=username_override,
                )
                preprocessor.process()

                logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
                _process_snapchat_working_directory(
                    temp_dir_path, output_dir, workers, log_filename, dedup_manifest
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
    media_dir = working_dir / "media"
    overlays_dir = working_dir / "overlays"

    # Check for metadata file (should exist after preprocessing or if already preprocessed)
    if not metadata_file.exists():
        logger.error(f"Metadata file not found: {metadata_file}")
        return

    # Check for media directory
    if not media_dir.exists():
        logger.error(f"Media directory not found: {media_dir}")
        return

    # Load metadata
    logger.info(f"Loading metadata from {metadata_file}...")
    metadata = load_metadata(metadata_file)

    export_username = metadata.get("export_info", {}).get("export_username", "unknown")
    logger.info(f"Export username: {export_username}")

    # Build media index
    logger.info("Building media index...")
    media_index = build_media_index(metadata)
    filename_index = build_filename_index(metadata)
    logger.info(f"Found {len(media_index)} messages with media IDs")
    logger.info(f"Found {len(filename_index)} orphaned media entries by filename")

    # Scan media directory
    logger.info(f"Scanning media directory: {media_dir}")
    media_files = []
    for ext in ["*.jpg", "*.jpeg", "*.png", "*.mp4", "*.mov", "*.webp"]:
        media_files.extend(media_dir.glob(ext))

    logger.info(f"Found {len(media_files)} media files")

    # Filter out secondary files based on dedup manifest (using hash-based lookup)
    if dedup_manifest:
        original_count = len(media_files)
        filtered_files = []
        for f in media_files:
            content_hash = compute_content_hash(f)
            if is_file_primary_by_hash(
                dedup_manifest, content_hash, "Snapchat Messages"
            ):
                filtered_files.append(f)
        media_files = filtered_files
        skipped_count = original_count - len(media_files)
        if skipped_count > 0:
            logger.info(f"Skipped {skipped_count} secondary files (dedup)")

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

    logger.info(f"Matched {len(matched_files)} files")
    logger.info(f"Unmatched {len(unmatched_files)} files")

    # Pre-generate all output filenames to ensure deterministic naming
    logger.info("Pre-generating output filenames...")
    filename_gen = FilenameGenerator(
        "snap-messages", timestamp_format="%Y-%m-%d %H:%M:%S UTC"
    )
    output_filenames = []  # List of output filenames in same order as matched_files

    for media_file, message in matched_files:
        is_video = get_media_type(media_file) == "video"

        # Get file extension for filename generation
        file_ext = media_file.suffix.lower()

        # Check if this file will get an overlay and become MKV
        overlay_filename = message.get("overlay_file")
        will_have_overlay = False
        if overlay_filename:
            potential_overlay = overlays_dir / overlay_filename
            if potential_overlay.exists() and is_video:
                will_have_overlay = True

        # Build identifier (policy) and delegate to FilenameGenerator (mechanism)
        identifier = _build_chat_identifier(message, export_username)
        timestamp = _get_message_timestamp(message)
        ext = ".mkv" if will_have_overlay else file_ext
        output_filename = filename_gen.generate(timestamp, identifier, ext)

        output_filenames.append(output_filename)

    # Process matched files with parallel processing
    logger.info(f"Processing {len(matched_files)} matched files to {output_dir}/")
    logger.info("=" * 50)

    # Determine number of parallel workers
    num_workers = workers if workers is not None else default_worker_count()
    logger.info(f"Using {num_workers} parallel workers")

    # Prepare arguments for parallel processing
    process_args = []
    for idx, (media_file, message) in enumerate(matched_files):
        output_filename = output_filenames[idx]
        process_args.append(
            (
                media_file,
                message,
                export_username,
                output_dir,
                overlays_dir,
                output_filename,
            )
        )

    # Process files in parallel
    # Phase 1: Create all files with overlays in parallel
    success_count = 0
    failed_count = 0

    if num_workers > 1:
        # Specify app modules for DEBUG level in workers
        worker_app_modules = [
            __name__,
            "common.overlay",
            "common.video_encoder",
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
    file_paths = []
    file_info = []

    for success, output_path, is_mkv, message, exp_username in results:
        if success and output_path:
            success_count += 1
            # Only add non-MKV files to batch processing list
            # (MKV files already have metadata from overlay creation)
            if not is_mkv:
                file_paths.append(output_path)
                file_info.append((output_path, message, exp_username))
        else:
            failed_count += 1

    logger.info(
        f"\nCreated {success_count} files ({len(file_info)} need EXIF processing)"
    )

    # Phase 3: Correct file extensions based on actual content
    if file_paths:
        corrections = batch_correct_extensions([Path(p) for p in file_paths])
        if corrections:
            # Update file_paths and file_info with corrected paths
            file_paths = [corrections.get(p, p) for p in file_paths]
            file_info = [
                (corrections.get(fp, fp), msg, eu) for fp, msg, eu in file_info
            ]

    # Phase 4: Batch process EXIF operations on non-MKV files
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
        requests = _build_metadata_requests(file_info)
        batch_write_metadata_requests(requests, existing_metadata_map)

    # Handle unmatched files - copy to failed-matching directory
    if unmatched_files:
        logger.info(f"Processing {len(unmatched_files)} unmatched files...")
        failed_matching_dir = output_dir / "issues" / "failed-matching" / "media"
        failed_matching_dir.mkdir(exist_ok=True, parents=True)

        for media_file in unmatched_files:
            try:
                # Copy to failed-matching directory with original filename
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

    logger.info("Note: Videos with overlays are saved as multi-track MKV files:")
    logger.info("  - Track 0 (default): Video with overlay embedded")
    logger.info("  - Track 1: Original video without overlay")
    logger.info("  Switch tracks in VLC: Video > Video Track > Select track")

    # Move any needs_matching folders from the preprocessing temp dir
    # into the final processed output so they persist after cleanup.
    try:
        needs_matching_src = working_dir / "needs_matching"
        if needs_matching_src.exists() and needs_matching_src.is_dir():
            needs_matching_dest = output_dir / "needs_matching"
            needs_matching_dest.mkdir(parents=True, exist_ok=True)

            for child in needs_matching_src.iterdir():
                dest_path = needs_matching_dest / child.name
                try:
                    # If destination exists, merge by moving contents where possible
                    if dest_path.exists() and child.is_dir():
                        # Move each item within the child directory
                        for sub in child.iterdir():
                            shutil.move(str(sub), str(dest_path / sub.name))
                        try:
                            child.rmdir()
                        except Exception:
                            pass
                    else:
                        shutil.move(str(child), str(dest_path))
                except Exception as e:
                    logger.warning(f"Failed to move {child} to {dest_path}: {e}")

            logger.info(f"Ambiguous cases moved to: {needs_matching_dest.absolute()}")
    except Exception as e:
        logger.warning(f"Failed to move needs_matching directory to output: {e}")
