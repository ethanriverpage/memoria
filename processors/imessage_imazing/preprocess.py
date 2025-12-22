#!/usr/bin/env python3
"""
iMazing iMessage Export Preprocessor

Processes iMazing-format iMessage exports which use:
- Flat file structure with filename-encoded metadata
- CSV files for message text and metadata
- Individual vCard files for contacts

Output structure matches the standard iMessage processor format:
- metadata.json with export_info, conversations, orphaned_media
- media/ directory with copied attachment files
"""

import csv
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from common.detection import DetectionResult
from common.output.logging import get_processor_logger
from common.preprocess.context_mergers import merge_imessage_context
from common.processing import print_preprocessing_stats
from common.output.progress import PHASE_PREPROCESS, progress_bar
from processors.preprocess_base import CopyTask, PreprocessorBase

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)

# Media file extensions - import from common (single source of truth)
from common.utils import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
    ALL_EXTENSIONS as ALL_MEDIA_EXTENSIONS,
)

# Filename pattern for iMazing exports:
# "YYYY-MM-DD HH MM SS - Contact Name - OriginalFile.ext"
FILENAME_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2} \d{2} \d{2}) - (.+?) - (.+)$")


def parse_imazing_filename(filename: str) -> Optional[Dict]:
    """Parse metadata from iMazing-format filename.

    Format: "YYYY-MM-DD HH MM SS - Contact/Group Name - OriginalFilename.ext"

    Args:
        filename: The filename to parse (without path)

    Returns:
        Dict with 'timestamp', 'conversation', 'original_filename' or None if no match
    """
    match = FILENAME_PATTERN.match(filename)
    if not match:
        return None

    timestamp_str, conversation, original_filename = match.groups()

    # Convert "YYYY-MM-DD HH MM SS" to datetime
    try:
        # Replace spaces in time portion with colons
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H %M %S")
        # Assume local time, convert to UTC for consistency
        dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

    return {
        "timestamp": dt,
        "timestamp_str": dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "conversation": conversation.strip(),
        "original_filename": original_filename,
    }


def is_group_chat(conversation_name: str) -> bool:
    """Determine if a conversation name represents a group chat.

    iMazing uses " & " to separate multiple participants in group chats.

    Args:
        conversation_name: The conversation name from the filename

    Returns:
        True if this appears to be a group chat
    """
    return " & " in conversation_name


def get_media_type(file_path: Path) -> Optional[str]:
    """Get media type from file extension.

    Args:
        file_path: Path to the file

    Returns:
        "IMAGE", "VIDEO", "AUDIO", or None if not a media file
    """
    ext = file_path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "IMAGE"
    elif ext in VIDEO_EXTENSIONS:
        return "VIDEO"
    elif ext in AUDIO_EXTENSIONS:
        return "AUDIO"
    return None


class ImazingPreprocessor(PreprocessorBase):
    """Preprocessor for iMazing-format iMessage exports.

    Handles the flat file structure with filename-encoded metadata,
    optionally cross-referencing with CSV files for additional context.

    Output structure matches standard iMessage processor format:
    - metadata.json with export_info, conversations, orphaned_media
    - media/ directory with copied attachment files

    Attributes:
        export_path: Path to the iMazing export directory
        output_dir: Directory for processed output
    """

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        owner_name: Optional[str] = None,
        final_output_dir: Optional[Path] = None,
    ):
        """Initialize preprocessor.

        Args:
            export_path: Path to iMazing export directory
            output_dir: Output directory for processed files
            workers: Number of parallel workers for hashing/copying (default: 8)
            owner_name: Override for device owner name
            final_output_dir: Final output directory for failure tracking
        """
        # Handle default output_dir before calling super().__init__
        if output_dir is None:
            output_dir = Path(export_path).parent / "imessage-imazing-processed"

        super().__init__(
            processor_name="iMessage-iMazing",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        # Override media_dir to match expected name
        self.media_dir = self.media_output_dir

        # Owner name (device owner) - extract from Device-Info.txt if not provided
        self.owner_name = owner_name or self._extract_owner_name()

        # Set context merger for deduplication (accumulates messages)
        self._context_merger = merge_imessage_context

        # CSV message cache for cross-reference
        # (timestamp, attachment_filename) -> message_info
        self.csv_message_cache: Dict[Tuple[str, str], Dict] = {}

        # Track which CSV entries were matched to media files
        self.matched_csv_keys: set = set()

        # Statistics specific to this preprocessor
        self.stats = {
            "total_files_scanned": 0,
            "media_files_found": 0,
            "unique_files": 0,
            "duplicate_files": 0,
            "files_copied": 0,
            "csv_messages_loaded": 0,
            "conversations": 0,
        }

    def _extract_owner_name(self) -> str:
        """Extract owner name from Device-Info.txt if present.

        Returns:
            Extracted owner name or 'unknown'
        """
        device_info = self.export_path / "Device-Info.txt"
        if device_info.exists():
            try:
                content = device_info.read_text(encoding="utf-8")
                # Look for "Name: <device name>" pattern
                match = re.search(r"^Name:\s*(.+)$", content, re.MULTILINE)
                if match:
                    return match.group(1).strip()
            except Exception as e:
                log.logger.debug(
                    "Failed to extract device name from %s: %s", device_info, e
                )

        # Fallback: extract from directory name
        dir_name = self.export_path.name
        match = re.match(r"(iph\w+)-messages-\d{8}", dir_name)
        if match:
            return match.group(1)

        return "unknown"

    def _scan_media_files(self) -> List[Path]:
        """Scan export directory for media files.

        Returns:
            List of paths to media files
        """
        media_files = []

        for file_path in self.export_path.iterdir():
            if not file_path.is_file():
                continue

            # Skip banned files
            if self.banned_filter.is_banned(file_path):
                continue

            # Check if it's a media file
            ext = file_path.suffix.lower()
            if ext not in ALL_MEDIA_EXTENSIONS:
                continue

            # Check if filename matches iMazing pattern
            parsed = parse_imazing_filename(file_path.name)
            if parsed:
                media_files.append(file_path)
            else:
                # Track as orphaned media (filename doesn't match iMazing pattern)
                self.failure_tracker.add_orphaned_media(
                    media_path=file_path,
                    reason="Filename does not match iMazing pattern",
                    context={"filename": file_path.name},
                )

        return media_files

    def _load_csv_messages(self) -> None:
        """Load message data from CSV files for cross-reference.

        Parses all "Messages - *.csv" files and caches the data
        for looking up sender info, message type, etc.
        """
        csv_files = list(self.export_path.glob("Messages - *.csv"))

        for csv_file in progress_bar(
            csv_files, PHASE_PREPROCESS, "Loading CSV files", unit="file"
        ):
            try:
                self._parse_csv_file(csv_file)
            except Exception as e:
                logger.warning(f"Failed to parse CSV {csv_file.name}: {e}")

        self.stats["csv_messages_loaded"] = len(self.csv_message_cache)
        logger.info(f"Loaded {len(self.csv_message_cache)} message records from CSV")

    def _parse_csv_file(self, csv_file: Path) -> None:
        """Parse a single CSV message file.

        CSV columns:
        Chat Session, Message Date, Delivered Date, Read Date, Service,
        Type, Sender ID, Sender Name, Status, Replying to, Subject, Text,
        Attachment, Attachment type

        Args:
            csv_file: Path to CSV file
        """
        with open(csv_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)

            for row in reader:
                attachment = row.get("Attachment", "").strip()
                if not attachment:
                    continue

                message_date = row.get("Message Date", "").strip()
                if not message_date:
                    continue

                # Create cache key: (timestamp, attachment_filename)
                # Normalize timestamp format
                cache_key = (message_date, attachment)

                self.csv_message_cache[cache_key] = {
                    "chat_session": row.get("Chat Session", ""),
                    "message_date": message_date,
                    "service": row.get("Service", ""),
                    "type": row.get("Type", ""),  # Incoming/Outgoing
                    "sender_id": row.get("Sender ID", ""),
                    "sender_name": row.get("Sender Name", ""),
                    "status": row.get("Status", ""),
                    "text": row.get("Text", ""),
                    "attachment_type": row.get("Attachment type", ""),
                }

    def _lookup_csv_message(
        self, timestamp_str: str, original_filename: str
    ) -> Optional[Dict]:
        """Look up message info from CSV cache.

        Args:
            timestamp_str: Timestamp string in "YYYY-MM-DD HH:MM:SS" format
            original_filename: Original filename of the attachment

        Returns:
            Message info dict or None
        """
        # Try direct lookup
        cache_key = (timestamp_str, original_filename)
        if cache_key in self.csv_message_cache:
            self.matched_csv_keys.add(cache_key)
            return self.csv_message_cache[cache_key]

        # Try with slight timestamp variations
        # (CSV might have slightly different precision)
        for key, value in self.csv_message_cache.items():
            csv_ts, csv_file = key
            if csv_file == original_filename and csv_ts.startswith(timestamp_str[:16]):
                self.matched_csv_keys.add(key)
                return value

        return None

    def _detect_orphaned_csv_metadata(self) -> None:
        """Detect CSV entries that reference attachments without matching media files.

        Iterates through the CSV message cache and tracks any entries that
        weren't matched to a media file during _build_conversations.
        """
        orphaned_count = 0
        for cache_key, message_info in self.csv_message_cache.items():
            if cache_key not in self.matched_csv_keys:
                orphaned_count += 1
                timestamp_str, attachment_filename = cache_key
                self.failure_tracker.add_orphaned_metadata(
                    metadata_entry={
                        "attachment": attachment_filename,
                        "message_date": timestamp_str,
                        "chat_session": message_info.get("chat_session", ""),
                        "sender_name": message_info.get("sender_name", ""),
                        "text": message_info.get("text", ""),
                    },
                    reason="CSV references attachment but media file not found",
                    context={
                        "expected_attachment": attachment_filename,
                        "message_date": message_info.get("message_date", ""),
                    },
                )

        if orphaned_count > 0:
            logger.info(f"Found {orphaned_count} orphaned CSV metadata entries")

    def validate_export(self) -> bool:
        """Validate that export directory exists.

        Returns:
            True if valid, False otherwise
        """
        if not self.export_path.exists():
            log.logger.error("Export path does not exist: %s", self.export_path)
            return False
        return True

    def detect_username(self) -> DetectionResult:
        """Detect device owner name.

        Returns:
            DetectionResult with owner name and source
        """
        return DetectionResult(username=self.owner_name, source="device_info")

    def _generate_output_filename(self, source_path: Path) -> str:
        """Generate output filename from source path.

        Args:
            source_path: Path to source file

        Returns:
            Output filename
        """
        return source_path.name

    def _build_conversations(
        self, media_files: List[Path]
    ) -> Tuple[Dict[str, Dict], List[Dict]]:
        """Build conversations structure and copy files using copy_with_dedup.

        Args:
            media_files: List of media file paths to process

        Returns:
            Tuple of (conversations dict, orphaned_media list)
        """

        # Sort files by parsed timestamp so oldest is processed first (becomes primary)
        def get_timestamp(file_path: Path) -> datetime:
            parsed = parse_imazing_filename(file_path.name)
            if parsed:
                return parsed["timestamp"]
            return datetime.max.replace(tzinfo=timezone.utc)

        media_files.sort(key=get_timestamp)

        # Build CopyTask objects from media files
        copy_tasks: List[CopyTask] = []
        for file_path in media_files:
            parsed = parse_imazing_filename(file_path.name)
            if not parsed:
                continue

            # Get additional info from CSV for context
            csv_timestamp = parsed["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            csv_info = self._lookup_csv_message(
                csv_timestamp, parsed["original_filename"]
            )

            # Determine sender and is_sender
            conversation_name = parsed["conversation"]
            conv_type = "group" if is_group_chat(conversation_name) else "dm"

            if csv_info:
                msg_type = csv_info.get("type", "")
                is_sender = msg_type == "Outgoing"
                sender = (
                    "me"
                    if is_sender
                    else csv_info.get("sender_name", "") or conversation_name
                )
                content = csv_info.get("text", "")
            else:
                is_sender = False
                sender = conversation_name
                content = ""

            # Build context for merging
            context = {
                "source_export": self.export_path.name,
                "conversation_id": conversation_name,
                "conversation_type": conv_type,
                "conversation_title": conversation_name,
                "sender": sender,
                "created": parsed["timestamp_str"],
                "content": content,
                "is_sender": is_sender,
                "media_type": get_media_type(file_path),
            }

            copy_tasks.append(
                CopyTask(
                    source_path=file_path,
                    dest_filename=file_path.name,
                    context=context,
                )
            )

        # Use copy_with_dedup for parallel hashing, deduplication, and copying
        self.media_dir.mkdir(parents=True, exist_ok=True)
        results = self.copy_with_dedup(copy_tasks)

        # Build conversations from results
        conversations: Dict[str, Dict] = {}
        unique_files_copied = 0
        duplicate_count = 0

        for source_path, result in results.items():
            if result.is_duplicate:
                duplicate_count += 1
                continue

            unique_files_copied += 1
            self.stats["files_copied"] += 1

            # Get merged context (includes all duplicate message contexts)
            merged_contexts = self.get_contexts_list_for_file(result.dest_filename)

            # Primary context is the first one (oldest due to sorting)
            # If no merged contexts found, skip this result as we can't process without context
            if not merged_contexts:
                continue
            primary_context = merged_contexts[0]

            conv_id = primary_context.get("conversation_id")
            if not conv_id:
                continue

            # Initialize conversation if needed
            if conv_id not in conversations:
                conversations[conv_id] = {
                    "type": primary_context.get("conversation_type"),
                    "title": primary_context.get("conversation_title"),
                    "message_count": 0,
                    "messages": [],
                }

            media_type = primary_context.get("media_type")

            # Check if this has merged duplicates
            if len(merged_contexts) > 1:
                # Create merged message with messages array
                merged_message = {
                    "media_file": result.dest_filename,
                    "primary_created": primary_context.get("created"),
                    "is_duplicate": True,
                    "messages": [],
                    "media_type": media_type,
                }

                # Add all occurrences to messages array
                for ctx in merged_contexts:
                    merged_message["messages"].append(
                        {
                            "source_export": ctx.get("source_export"),
                            "conversation_id": ctx.get("conversation_id"),
                            "conversation_type": ctx.get("conversation_type"),
                            "conversation_title": ctx.get("conversation_title"),
                            "sender": ctx.get("sender"),
                            "created": ctx.get("created"),
                            "content": ctx.get("content"),
                            "is_sender": ctx.get("is_sender"),
                        }
                    )

                conversations[conv_id]["messages"].append(merged_message)
                conversations[conv_id]["message_count"] += 1
                duplicate_count += len(merged_contexts) - 1
            else:
                # Single occurrence - create standard message entry
                message = {
                    "source_export": primary_context.get("source_export"),
                    "conversation_id": conv_id,
                    "conversation_type": primary_context.get("conversation_type"),
                    "conversation_title": primary_context.get("conversation_title"),
                    "sender": primary_context.get("sender"),
                    "created": primary_context.get("created"),
                    "content": primary_context.get("content"),
                    "is_sender": primary_context.get("is_sender"),
                    "media_file": result.dest_filename,
                    "media_type": media_type,
                }

                conversations[conv_id]["messages"].append(message)
                conversations[conv_id]["message_count"] += 1

        self.stats["unique_files"] = unique_files_copied
        self.stats["duplicate_files"] = duplicate_count
        self.stats["conversations"] = len(conversations)

        # No orphaned media in this format
        orphaned_media: List[Dict] = []

        return conversations, orphaned_media

    def _generate_metadata(
        self, conversations: Dict[str, Dict], orphaned_media: List[Dict]
    ) -> Dict:
        """Generate metadata.json structure matching iMessage format.

        Args:
            conversations: Conversations dictionary
            orphaned_media: List of orphaned media entries

        Returns:
            Metadata dictionary
        """
        metadata = {
            "export_info": {
                "export_path": str(self.export_path),
                "export_paths": [str(self.export_path)],
                "export_username": self.owner_name,
                "export_format": "imazing",
                "processed_date": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                ),
                "csv_messages_loaded": self.stats["csv_messages_loaded"],
            },
            "conversations": conversations,
            "orphaned_media": orphaned_media,
        }

        return metadata

    def process_export(self) -> bool:
        """Platform-specific preprocessing logic.

        Processes iMazing-format iMessage exports with flat file structure.

        Returns:
            True if processing succeeded, False otherwise
        """
        # Phase 1: Load CSV message data for cross-reference
        log.logger.info("Loading CSV message data...")
        self._load_csv_messages()

        # Phase 2: Scan for media files
        log.logger.info("Scanning for media files...")
        media_files = self._scan_media_files()
        self.stats["media_files_found"] = len(media_files)
        log.found_files(len(media_files), "media")

        if not media_files:
            log.logger.warning("No media files found to process")
            # Still create output structure
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.media_dir.mkdir(parents=True, exist_ok=True)
            metadata = self._generate_metadata({}, [])
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            return True

        # Phase 3: Build conversations and copy files (with deduplication)
        log.logger.info("Building conversations and copying files...")
        conversations, orphaned_media = self._build_conversations(media_files)
        log.logger.info(
            "Organized into %d conversations, %d unique files (%d duplicates removed)",
            len(conversations),
            self.stats["unique_files"],
            self.stats["duplicate_files"],
        )

        # Phase 4b: Detect orphaned CSV metadata entries
        self._detect_orphaned_csv_metadata()

        # Phase 5: Generate metadata
        log.logger.info("Generating metadata.json")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        metadata = self._generate_metadata(conversations, orphaned_media)

        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        return True

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Media files found": self.stats["media_files_found"],
            "CSV messages loaded": self.stats["csv_messages_loaded"],
            "Conversations": self.stats["conversations"],
            "Unique files": self.stats["unique_files"],
            "Duplicates removed": self.stats["duplicate_files"],
            "Files copied": self.stats["files_copied"],
        }
        print_preprocessing_stats("iMazing Preprocessing", stats)
