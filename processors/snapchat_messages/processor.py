"""
Snapchat Messages Processor

This processor is designed to be used through memoria.py.
It handles processing Snapchat chat media with overlays and metadata embedding.
"""
import json
import logging
import os
import re
import shutil
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Optional

from common.dependency_checker import (
    check_exiftool,
    check_ffmpeg,
    print_exiftool_error,
    print_ffmpeg_error,
)
from common.exiftool_batch import (
    batch_validate_exif,
    batch_rebuild_exif,
    batch_read_existing_metadata,
    batch_write_metadata_snapchat_messages,
)
from common.overlay import (
    create_image_with_overlay,
    create_video_with_overlay,
)
from common.processing import print_processing_summary, temp_processing_directory
from common.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    default_worker_count,
    get_media_type,
    is_preprocessed_directory,
    sanitize_filename,
    update_file_timestamps,
)
from processors.base import ProcessorBase
from processors.snapchat_messages.preprocess import SnapchatPreprocessor

# Set up logging
logger = logging.getLogger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory

    Detection criteria for Snapchat Messages:
    - Raw export: Directory contains 'json/' with 'chat_history.json' and 'snap_history.json'
    - Preprocessed: Directory contains 'metadata.json' with 'conversations' key
    - Consolidated: Directory contains 'messages/' subdirectory with above structures

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is a Snapchat Messages export, False otherwise
    """
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
    """Processor for Snapchat Messages exports"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input"""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name"""
        return "Snapchat Messages"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first)"""
        return 85  # High priority - runs before memories

    @staticmethod
    def process(input_dir: str, output_dir: str = None, **kwargs) -> bool:
        """Process Snapchat Messages export

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
                if (json_dir.exists() and (json_dir / "chat_history.json").exists()) or metadata_file.exists():
                    actual_input_dir = str(messages_subdir)
                    logger.info(f"Detected consolidated export structure, using: {actual_input_dir}")
                    # Extract username from parent directory for consolidated exports
                    username_override = _extract_username_from_dir(input_dir)
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
            )
            return True

        except Exception as e:
            logger.error(f"Error in SnapchatMessagesProcessor: {e}")
            return False


def get_processor():
    """Return processor class for auto-discovery

    This function is called by the unified memoria.py during
    automatic processor discovery.

    Returns:
        SnapchatMessagesProcessor class (not instance, as it uses static methods)
    """
    return SnapchatMessagesProcessor


# ============================================================================
# Helper Functions
# ============================================================================


def _extract_username_from_dir(directory: str) -> str:
    """Extract username from Snapchat export directory name
    
    Args:
        directory: Directory path (e.g., snapchat-username-20251007)
    
    Returns:
        Extracted username or "unknown"
    """
    dir_name = Path(directory).name
    # Pattern: snapchat-{username}-YYYY-MM-DD or snapchat-{username}-YYYYMMDD
    match = re.match(r"snapchat-(.+?)-\d{4}-?\d{2}-?\d{2}", dir_name)
    if match:
        return match.group(1)
    return "unknown"


def check_pillow():
    """Check if PIL/Pillow is installed"""
    try:
        __import__("PIL")
        return True
    except ImportError:
        return False


def load_metadata(metadata_path: Path) -> dict:
    """Load and parse metadata.json

    Returns:
        dict with 'export_info' and 'conversations' keys
    """
    with open(metadata_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_media_index(metadata: dict) -> dict:
    """Build index mapping media_id to message metadata

    Args:
        metadata: Parsed metadata.json content

    Returns:
        dict mapping media_id -> message dict
    """
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
    """Build index mapping media_file to message metadata

    This indexes all messages by their matched filenames (from preprocessing)

    Args:
        metadata: Parsed metadata.json content

    Returns:
        dict mapping media_file -> message dict
    """
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
    """Extract media_id from filename

    Patterns:
    - YYYY-MM-DD_b~{media_id}.{ext}

    Args:
        filename: Media file name

    Returns:
        media_id or None
    """
    # Pattern: YYYY-MM-DD_b~{media_id}.{ext}
    match = re.match(r"^\d{4}-\d{2}-\d{2}_b~(.+)\.\w+$", filename)
    if match:
        return "b~" + match.group(1)

    return None


def extract_date_from_filename(filename: str) -> Optional[str]:
    """Extract date from filename

    Args:
        filename: Media file name with YYYY-MM-DD prefix

    Returns:
        date string in "YYYY-MM-DD" format or None
    """
    match = re.match(r"^(\d{4}-\d{2}-\d{2})_", filename)
    if match:
        return match.group(1)
    return None


def generate_chat_filename(
    message: dict, export_username: str, extension: str, used_filenames: set
) -> str:
    """Generate unique filename for a chat message

    Format:
    - Merged (duplicate): snap-messages-{username}-{YYYYMMDD}.{ext}
    - DM: snap-messages-{username}-{other_user}-{YYYYMMDD}.{ext}
    - Group: snap-messages-{username}-{sanitized_title}-{YYYYMMDD}.{ext}
    - Orphaned: snap-messages-{username}-unknown-{YYYYMMDD}.{ext}

    Sequence numbers (_2, _3, etc.) are only added if the base filename is already in use.

    Args:
        message: Message metadata dict (can have 'messages' array for merged duplicates)
        export_username: Username from export
        extension: File extension (including dot)
        used_filenames: Set tracking already-used filenames

    Returns:
        Generated filename
    """
    # Check if this is a merged/duplicate message
    is_merged = "messages" in message and isinstance(message["messages"], list)
    
    # Get date string - use primary_created for merged messages
    if is_merged:
        date_str = message.get("primary_created", message["messages"][0].get("created"))
    else:
        date_str = message.get("created")

    # All messages should have full timestamp format: YYYY-MM-DD HH:MM:SS UTC
    # Fallback to date-only parsing for backwards compatibility with old metadata
    if len(date_str) == 10 and date_str.count(":") == 0:
        # Legacy date-only format: YYYY-MM-DD
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
    else:
        # Standard timestamp format: YYYY-MM-DD HH:MM:SS UTC
        date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S UTC")

    date_key = date_obj.strftime("%Y%m%d")

    # For merged messages, use simplified naming without conversation
    if is_merged:
        base_filename = f"snap-messages-{export_username}-{date_key}{extension}"
    else:
        # Determine conversation type and identifier
        conv_type = message.get("conversation_type")
        conv_id = message.get("conversation_id")

        # Handle orphaned media (no conversation context)
        if conv_id is None or conv_type is None:
            conv_prefix = "unknown"
        elif conv_type == "dm":
            # Direct message - use conversation_id (other user's username)
            identifier = sanitize_filename(conv_id)
            conv_prefix = identifier
        else:
            # Group chat - use conversation_title
            title = message.get("conversation_title", "unknown")
            identifier = sanitize_filename(title)
            conv_prefix = identifier

        # Generate base filename without sequence
        base_filename = (
            f"snap-messages-{export_username}-{conv_prefix}-{date_key}{extension}"
        )

    # Check if base filename is already used
    if base_filename not in used_filenames:
        used_filenames.add(base_filename)
        return base_filename

    # Base filename is taken, find next available sequence number
    sequence = 2
    while True:
        if is_merged:
            filename = f"snap-messages-{export_username}-{date_key}_{sequence}{extension}"
        else:
            filename = f"snap-messages-{export_username}-{conv_prefix}-{date_key}_{sequence}{extension}"
        if filename not in used_filenames:
            used_filenames.add(filename)
            return filename
        sequence += 1


def _update_snapchat_timestamps(file_path: Path, message: dict) -> bool:
    """Update filesystem timestamps to match message date

    Snapchat messages have complex timestamp handling with multiple formats:
    - primary_created for merged messages
    - created for single messages
    - Formats: "YYYY-MM-DD HH:MM:SS UTC" or legacy "YYYY-MM-DD"

    Args:
        file_path: Path to the file
        message: Message metadata with 'created' or 'primary_created' field

    Returns:
        True if successful, False otherwise
    """
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




def _init_worker_logging(log_filename):
    """Initialize logging for worker processes

    Args:
        log_filename: Path to the log file for verbose output
    """
    if log_filename:
        # Set up file handler for worker process
        log_format = "%(asctime)s - %(levelname)s - %(message)s"
        formatter = logging.Formatter(log_format)

        # Set root logger to INFO (to avoid third-party library spam)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)

        # Add file handler
        file_handler = logging.FileHandler(log_filename)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # Set application modules to DEBUG level
        logging.getLogger(__name__).setLevel(logging.DEBUG)
        logging.getLogger("common.overlay").setLevel(
            logging.DEBUG
        )
        logging.getLogger("processors.snapchat_messages.preprocess").setLevel(
            logging.DEBUG
        )
        logging.getLogger("common.video_encoder").setLevel(
            logging.DEBUG
        )

        # Suppress verbose third-party library logging
        logging.getLogger("PIL").setLevel(logging.INFO)


def _create_file_worker(args_tuple):
    """Worker function for creating output files with overlays (Phase 1)

    Args:
        args_tuple: Tuple of (media_path, message, export_username, output_dir,
                             overlays_dir, output_filename)

    Returns:
        Tuple of (success: bool, output_path: str or None, is_mkv: bool, message: dict, export_username: str)
    """
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
):
    """Core processing logic for Snapchat Messages exports

    Args:
        input_dir: Input directory (raw export or preprocessed)
        output_dir: Output directory for processed files
        temp_dir: Directory for temporary preprocessing files
        verbose: Enable verbose logging
        workers: Number of parallel workers (None = CPU count - 1)
        username_override: Optional override for export username (used for consolidated exports)
    """
    # Logging is configured by the main process
    # Verbose mode enables detailed logging to file
    
    # Pass log filename to worker processes if in verbose mode
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
        logger.error("PIL/Pillow is not installed")
        logger.error("Please install Pillow:")
        logger.error("  pip install Pillow")
        return

    logger.info("Snapchat Messages Processor")
    logger.info("=" * 50)

    # Determine if input needs preprocessing
    output_dir = Path(output_dir)

    # Check if input is already preprocessed
    if is_preprocessed_directory(str(input_dir)):
        logger.info(f"Input directory is already preprocessed: {input_dir}")
        _process_snapchat_working_directory(
            input_dir, output_dir, workers, log_filename
        )
    else:
        logger.info(f"Input directory is raw export: {input_dir}")
        logger.info("Running preprocessing...")

        # Use context manager for temp directory with automatic cleanup
        with temp_processing_directory(temp_dir, "snap_msgs") as temp_dir_path:
            logger.info(f"Preprocessing to: {temp_dir_path}")

            # Run preprocessing with final output directory for failure tracking
            # Use messages subdirectory for processor-specific output
            final_output_path = output_dir / "messages"

            preprocessor = SnapchatPreprocessor(
                export_path=Path(input_dir),
                output_dir=temp_dir_path,
                workers=workers,
                final_output_dir=final_output_path,
                username_override=username_override,
            )
            preprocessor.process()

            logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
            _process_snapchat_working_directory(
                temp_dir_path, output_dir, workers, log_filename
            )


def _process_snapchat_working_directory(working_dir, output_dir, workers, log_filename):
    """Process a working directory (preprocessed Snapchat Messages export)

    Args:
        working_dir: Path to preprocessed directory with metadata.json and media/
        output_dir: Output directory for processed files
        workers: Number of parallel workers
        log_filename: Log filename for worker processes
    """
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

    export_username = metadata.get("export_info", {}).get(
        "export_username", "unknown"
    )
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

    # Create output directory (subdirectory already set by caller)
    output_dir.mkdir(exist_ok=True, parents=True)

    # Match media files to messages
    logger.info("\nMatching media files to messages...")
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
    used_filenames = set()
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

        # Generate filename with appropriate extension
        if will_have_overlay:
            output_filename = generate_chat_filename(
                message, export_username, ".mkv", used_filenames
            )
        else:
            output_filename = generate_chat_filename(
                message, export_username, file_ext, used_filenames
            )

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
        with Pool(
            processes=num_workers,
            initializer=_init_worker_logging,
            initargs=(log_filename,),
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

    logger.info(f"\nCreated {success_count} files ({len(file_info)} need EXIF processing)")

    # Phase 3: Batch process EXIF operations on non-MKV files
    exif_rebuilt_count = 0
    if file_paths:
        logger.info("Batch processing EXIF metadata...")

        # Batch validate and rebuild
        corrupted_files = batch_validate_exif(file_paths)
        if corrupted_files:
            logger.info(f"Rebuilding {len(corrupted_files)} corrupted EXIF structures...")
            batch_rebuild_exif(list(corrupted_files))
            exif_rebuilt_count = len(corrupted_files)

        # Batch read and write metadata
        existing_metadata_map = batch_read_existing_metadata(file_paths)
        batch_write_metadata_snapchat_messages(file_info, existing_metadata_map)

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

    print("\nNote: Videos with overlays are saved as multi-track MKV files:")
    print("  - Track 0 (default): Video with overlay embedded")
    print("  - Track 1: Original video without overlay")
    print("  Switch tracks in VLC: Video > Video Track > Select track")

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
                    logger.warning(
                        f"Failed to move {child} to {dest_path}: {e}"
                    )

            logger.info(
                f"Ambiguous cases moved to: {needs_matching_dest.absolute()}"
            )
    except Exception as e:
        logger.warning(
            f"Failed to move needs_matching directory to output: {e}"
        )
