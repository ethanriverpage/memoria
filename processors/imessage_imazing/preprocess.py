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
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import xxhash

from common.failure_tracker import FailureTracker
from common.file_utils import detect_and_correct_extension
from common.filter_banned_files import BannedFilesFilter
from common.progress import PHASE_PREPROCESS, progress_bar

logger = logging.getLogger(__name__)

# Media file extensions to process
IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".tiff",
    ".tif",
    ".dng",
    ".avif",
}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".wav", ".aac", ".caf"}
ALL_MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

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


class ImazingPreprocessor:
    """Preprocessor for iMazing-format iMessage exports.

    Handles the flat file structure with filename-encoded metadata,
    optionally cross-referencing with CSV files for additional context.

    Output structure matches standard iMessage processor format:
    - metadata.json with export_info, conversations, orphaned_media
    - media/ directory with copied attachment files

    Attributes:
        export_path: Path to the iMazing export directory
        output_dir: Directory for processed output
        content_hashes: Registry of file hashes for deduplication
    """

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        owner_name: Optional[str] = None,
    ):
        """Initialize preprocessor.

        Args:
            export_path: Path to iMazing export directory
            output_dir: Output directory for processed files
            workers: Number of parallel workers for hashing/copying (default: 8)
            owner_name: Override for device owner name
        """
        self.export_path = Path(export_path)

        # Output directories
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            self.output_dir = self.export_path.parent / "imessage-imazing-processed"

        self.media_dir = self.output_dir / "media"
        self.metadata_file = self.output_dir / "metadata.json"

        # Parallel processing worker count
        self.workers = workers or 8

        # Owner name (device owner) - extract from Device-Info.txt if not provided
        self.owner_name = owner_name or self._extract_owner_name()

        # Global hash registry for deduplication
        # hash -> {output_filename, source_path}
        self.content_hashes: Dict[str, dict] = {}

        # CSV message cache for cross-reference
        # (timestamp, attachment_filename) -> message_info
        self.csv_message_cache: Dict[Tuple[str, str], Dict] = {}

        # Banned files filter
        self.banned_filter = BannedFilesFilter()

        # Failure tracker
        self.failure_tracker = FailureTracker(
            processor_name="iMessage-iMazing",
            export_directory=str(self.export_path),
        )

        # Statistics
        self.stats = {
            "total_files_scanned": 0,
            "media_files_found": 0,
            "unique_files": 0,
            "duplicate_files": 0,
            "files_copied": 0,
            "csv_messages_loaded": 0,
            "conversations": 0,
            "extensions_corrected": 0,
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
                # Look for "Name: Ethan's iPhone" pattern
                match = re.search(r"^Name:\s*(.+)$", content, re.MULTILINE)
                if match:
                    return match.group(1).strip()
            except Exception:
                pass

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
            return self.csv_message_cache[cache_key]

        # Try with slight timestamp variations
        # (CSV might have slightly different precision)
        for key, value in self.csv_message_cache.items():
            csv_ts, csv_file = key
            if csv_file == original_filename and csv_ts.startswith(timestamp_str[:16]):
                return value

        return None

    def _compute_file_hash(self, file_path: Path) -> str:
        """Compute xxHash64 of file for deduplication.

        Args:
            file_path: Path to file

        Returns:
            Hexadecimal hash digest
        """
        hasher = xxhash.xxh64()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def _compute_hashes_parallel(
        self, media_files: List[Path]
    ) -> Dict[str, List[Dict]]:
        """Hash files in parallel and group by content hash.

        Args:
            media_files: List of media file paths

        Returns:
            Dictionary mapping content hash to list of file info dicts
        """
        hash_to_files: Dict[str, List[Dict]] = {}

        def hash_file(file_path: Path) -> Tuple[Path, Optional[str], Optional[Dict]]:
            """Hash a single file, returning path, hash, and parsed metadata."""
            try:
                file_hash = self._compute_file_hash(file_path)
                parsed = parse_imazing_filename(file_path.name)
                return (file_path, file_hash, parsed)
            except Exception as e:
                logger.warning(f"Failed to hash {file_path}: {e}")
                return (file_path, None, None)

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = [executor.submit(hash_file, f) for f in media_files]

            for future in progress_bar(
                as_completed(futures),
                PHASE_PREPROCESS,
                "Hashing files",
                total=len(futures),
                unit="file",
            ):
                file_path, file_hash, parsed = future.result()

                if file_hash and parsed:
                    file_info = {
                        "path": file_path,
                        "hash": file_hash,
                        "parsed": parsed,
                    }
                    hash_to_files.setdefault(file_hash, []).append(file_info)

        return hash_to_files

    def _copy_files_parallel(
        self, copy_tasks: List[Tuple[Path, Path]]
    ) -> Dict[Path, bool]:
        """Copy files in parallel.

        Args:
            copy_tasks: List of (source_path, dest_path) tuples

        Returns:
            Dictionary mapping dest_path to success status
        """
        results: Dict[Path, bool] = {}

        def copy_file(args: Tuple[Path, Path]) -> Tuple[Path, bool]:
            """Copy a single file."""
            src, dst = args
            try:
                shutil.copy2(src, dst)
                return (dst, True)
            except Exception as e:
                logger.error(f"Failed to copy {src}: {e}")
                return (dst, False)

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = [executor.submit(copy_file, task) for task in copy_tasks]

            for future in progress_bar(
                as_completed(futures),
                PHASE_PREPROCESS,
                "Copying files",
                total=len(futures),
                unit="file",
            ):
                dest, success = future.result()
                results[dest] = success

        return results

    def _generate_output_filename(self, source_path: Path) -> str:
        """Generate output filename with corrected extension based on actual file type.

        Detects the actual file format using python-magic and corrects the extension
        if it doesn't match the file content. This prevents metadata write failures
        that occur when exiftool encounters mismatched file types.

        Args:
            source_path: Path to source file

        Returns:
            Output filename with correct extension
        """
        original_name = source_path.name

        def log_correction(msg, details):
            logger.debug(f"{msg} - {details}")
            self.stats["extensions_corrected"] += 1

        return detect_and_correct_extension(
            source_path, original_name, log_callback=log_correction
        )

    def _build_conversations(
        self, hash_to_files: Dict[str, List[Dict]]
    ) -> Tuple[Dict[str, Dict], List[Dict]]:
        """Build conversations structure and copy files.

        Args:
            hash_to_files: Dict mapping content hash to list of file info

        Returns:
            Tuple of (conversations dict, orphaned_media list)
        """
        self.media_dir.mkdir(parents=True, exist_ok=True)

        # Prepare copy tasks
        copy_tasks: List[Tuple[Path, Path]] = []
        copy_info: Dict[str, Dict] = {}  # hash -> copy info

        for file_hash, files in hash_to_files.items():
            # Sort by timestamp to get oldest first
            files.sort(key=lambda x: x["parsed"]["timestamp"])
            primary = files[0]

            source_path = primary["path"]
            output_filename = self._generate_output_filename(source_path)

            # Handle name collisions
            output_path = self.media_dir / output_filename
            counter = 1
            base_stem = output_path.stem
            while output_path.exists():
                output_filename = f"{base_stem}_{counter}{output_path.suffix}"
                output_path = self.media_dir / output_filename
                counter += 1

            copy_tasks.append((source_path, output_path))
            copy_info[file_hash] = {
                "source_path": source_path,
                "output_path": output_path,
                "output_filename": output_filename,
                "files": files,
            }

        # Copy files in parallel
        copy_results = self._copy_files_parallel(copy_tasks)

        # Build conversations structure
        conversations: Dict[str, Dict] = {}

        for file_hash, info in copy_info.items():
            output_path = info["output_path"]
            output_filename = info["output_filename"]
            files = info["files"]

            # Check if copy succeeded
            if not copy_results.get(output_path, False):
                continue

            self.stats["files_copied"] += 1
            self.stats["unique_files"] += 1

            if len(files) > 1:
                self.stats["duplicate_files"] += len(files) - 1

            # Use primary file's metadata
            primary = files[0]
            parsed = primary["parsed"]
            conversation_name = parsed["conversation"]
            timestamp_str = parsed["timestamp_str"]
            original_filename = parsed["original_filename"]

            # Determine conversation type
            conv_type = "group" if is_group_chat(conversation_name) else "dm"

            # Try to get additional info from CSV
            csv_timestamp = parsed["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            csv_info = self._lookup_csv_message(csv_timestamp, original_filename)

            # Determine sender and is_sender
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
                # Without CSV, we can't determine direction
                is_sender = False  # Default to received
                sender = conversation_name
                content = ""

            # Initialize conversation if needed
            conv_id = conversation_name  # Use conversation name as ID
            if conv_id not in conversations:
                conversations[conv_id] = {
                    "type": conv_type,
                    "title": conversation_name,
                    "message_count": 0,
                    "messages": [],
                }

            # Get media type
            media_type = get_media_type(primary["path"])

            # Handle duplicates vs single files
            if len(files) > 1:
                # Create merged message entry
                merged_message = {
                    "media_file": output_filename,
                    "primary_created": timestamp_str,
                    "is_duplicate": True,
                    "messages": [],
                    "media_type": media_type,
                }

                for file_info in files:
                    fp = file_info["parsed"]
                    file_csv_ts = fp["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
                    file_csv_info = self._lookup_csv_message(
                        file_csv_ts, fp["original_filename"]
                    )

                    if file_csv_info:
                        file_is_sender = file_csv_info.get("type", "") == "Outgoing"
                        file_sender = (
                            "me"
                            if file_is_sender
                            else file_csv_info.get("sender_name", "")
                            or fp["conversation"]
                        )
                        file_content = file_csv_info.get("text", "")
                    else:
                        file_is_sender = False
                        file_sender = fp["conversation"]
                        file_content = ""

                    merged_message["messages"].append(
                        {
                            "source_export": self.export_path.name,
                            "conversation_id": fp["conversation"],
                            "conversation_type": (
                                "group" if is_group_chat(fp["conversation"]) else "dm"
                            ),
                            "conversation_title": fp["conversation"],
                            "sender": file_sender,
                            "created": fp["timestamp_str"],
                            "content": file_content,
                            "is_sender": file_is_sender,
                        }
                    )

                conversations[conv_id]["messages"].append(merged_message)
                conversations[conv_id]["message_count"] += 1

            else:
                # Single file - create standard message entry
                message = {
                    "source_export": self.export_path.name,
                    "conversation_id": conv_id,
                    "conversation_type": conv_type,
                    "conversation_title": conversation_name,
                    "sender": sender,
                    "created": timestamp_str,
                    "content": content,
                    "is_sender": is_sender,
                    "media_file": output_filename,
                    "media_type": media_type,
                }

                conversations[conv_id]["messages"].append(message)
                conversations[conv_id]["message_count"] += 1

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

    def process(self) -> bool:
        """Run the preprocessing pipeline.

        Returns:
            True if processing succeeded
        """
        logger.info(f"Starting iMazing iMessage preprocessing for {self.export_path}")

        # Phase 1: Load CSV message data for cross-reference
        logger.info("Loading CSV message data...")
        self._load_csv_messages()

        # Phase 2: Scan for media files
        logger.info("Scanning for media files...")
        media_files = self._scan_media_files()
        self.stats["media_files_found"] = len(media_files)
        logger.info(f"Found {len(media_files)} media files")

        if not media_files:
            logger.warning("No media files found to process")
            # Still create output structure
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.media_dir.mkdir(parents=True, exist_ok=True)
            metadata = self._generate_metadata({}, [])
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            return True

        # Phase 3: Hash files for deduplication
        logger.info("Hashing files for deduplication...")
        hash_to_files = self._compute_hashes_parallel(media_files)
        logger.info(f"Found {len(hash_to_files)} unique files")

        # Phase 4: Build conversations and copy files
        logger.info("Building conversations and copying files...")
        conversations, orphaned_media = self._build_conversations(hash_to_files)
        logger.info(
            f"Organized into {len(conversations)} conversations, "
            f"{self.stats['unique_files']} unique files "
            f"({self.stats['duplicate_files']} duplicates removed)"
        )

        # Phase 5: Generate metadata
        logger.info("Generating metadata.json")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        metadata = self._generate_metadata(conversations, orphaned_media)

        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        # Handle failures
        self.failure_tracker.handle_failures(self.output_dir)

        logger.info(f"Preprocessing complete. Output: {self.output_dir}")
        self._print_statistics()

        return True

    def _print_statistics(self) -> None:
        """Print processing statistics."""
        print("\n" + "=" * 60)
        print("IMAZING IMESSAGE PREPROCESSING STATISTICS")
        print("=" * 60)
        print(f"Media files found:          {self.stats['media_files_found']:>6}")
        print(f"CSV messages loaded:        {self.stats['csv_messages_loaded']:>6}")
        print(f"Conversations:              {self.stats['conversations']:>6}")
        print(f"Unique files:               {self.stats['unique_files']:>6}")
        print(f"Duplicate files removed:    {self.stats['duplicate_files']:>6}")
        print(f"Files copied:               {self.stats['files_copied']:>6}")
        print(f"Extensions corrected:       {self.stats['extensions_corrected']:>6}")
        print("=" * 60)
