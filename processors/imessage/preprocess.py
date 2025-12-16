#!/usr/bin/env python3
"""
iMessage Export Preprocessor

Processes iMessage exports from Mac and iPhone backups:
- Parses SQLite databases (chat.db / sms.db)
- Resolves attachment paths and copies media files
- Handles Live Photo pairs (HEIC + MOV sidecars)
- Decodes attributedBody for message text extraction
- Supports cross-export deduplication via xxHash64
- Generates metadata.json matching Snapchat messages structure
"""

import json
import logging
import re
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import xxhash

from common.failure_tracker import FailureTracker
from common.file_utils import detect_and_correct_extension
from common.progress import PHASE_PREPROCESS, progress_bar
from common.filter_banned_files import BannedFilesFilter
from common.utils import get_media_type
from processors.imessage.vcard_parser import VCardParser

logger = logging.getLogger(__name__)


# Apple Cocoa epoch offset: seconds between Unix epoch (1970) and Apple epoch (2001)
APPLE_EPOCH_OFFSET = 978307200


def convert_apple_timestamp(apple_timestamp: int) -> Optional[datetime]:
    """Convert Apple Cocoa timestamp (nanoseconds since 2001-01-01) to datetime.

    Args:
        apple_timestamp: Timestamp in Apple Cocoa format (nanoseconds)

    Returns:
        datetime object in UTC, or None if timestamp is invalid/zero
    """
    if not apple_timestamp or apple_timestamp <= 0:
        return None

    try:
        unix_timestamp = (apple_timestamp / 1_000_000_000) + APPLE_EPOCH_OFFSET
        return datetime.fromtimestamp(unix_timestamp, tz=timezone.utc)
    except (ValueError, OSError, OverflowError):
        return None


def format_timestamp(dt: Optional[datetime]) -> Optional[str]:
    """Format datetime as 'YYYY-MM-DD HH:MM:SS UTC' to match Snapchat format.

    Args:
        dt: datetime object

    Returns:
        Formatted string or None
    """
    if not dt:
        return None
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def decode_attributed_body(blob: bytes) -> Tuple[Optional[str], dict]:
    """Decode text from NSTypedStream-encoded NSAttributedString blob.

    The attributedBody column in iMessage databases contains serialized
    NSAttributedString objects. This function extracts the plain text content.

    Args:
        blob: Raw bytes from attributedBody column

    Returns:
        Tuple of (text, metadata) where:
        - text: Decoded UTF-8 string, empty string for empty messages,
                or None if decoding failed
        - metadata: Dict with rich text attributes found in the blob
    """
    if not blob or len(blob) < 10:
        return None, {}

    # Verify NSTypedStream header
    if not blob.startswith(b"\x04\x0bstreamtyped"):
        return None, {"error": "Invalid NSTypedStream header"}

    # Find the text marker
    idx = blob.find(b"\x01+")
    if idx == -1:
        return None, {"error": "No text marker found"}

    length_start = idx + 2
    if length_start >= len(blob):
        return None, {"error": "Truncated blob"}

    # Decode length
    first_byte = blob[length_start]

    if first_byte == 0:
        return "", {}  # Empty string

    if first_byte < 0x80:
        # Single byte length (1-127)
        text_length = first_byte
        text_start = length_start + 1
    elif first_byte == 0x81:
        # Extended length encoding - format depends on second byte
        if length_start + 2 >= len(blob):
            return None, {"error": "Truncated length"}
        second_byte = blob[length_start + 1]
        if second_byte < 0x80:
            # 2-byte little-endian length (for lengths >= 256)
            if length_start + 3 > len(blob):
                return None, {"error": "Truncated length"}
            third_byte = blob[length_start + 2]
            text_length = second_byte | (third_byte << 8)
            text_start = length_start + 3
        else:
            # Single byte length (128-255) followed by 0x00 separator
            text_length = second_byte
            text_start = length_start + 3
    elif first_byte == 0x82:
        # Extended: 0x82 + 2 byte big-endian length
        if length_start + 2 >= len(blob):
            return None, {"error": "Truncated length"}
        text_length = (blob[length_start + 1] << 8) | blob[length_start + 2]
        text_start = length_start + 3
    elif first_byte == 0x84:
        # Extended: 0x84 + 4 byte big-endian length
        if length_start + 4 >= len(blob):
            return None, {"error": "Truncated length"}
        text_length = (
            (blob[length_start + 1] << 24)
            | (blob[length_start + 2] << 16)
            | (blob[length_start + 3] << 8)
            | blob[length_start + 4]
        )
        text_start = length_start + 5
    else:
        return None, {"error": f"Unknown length encoding: 0x{first_byte:02x}"}

    text_end = text_start + text_length
    if text_end > len(blob):
        return None, {"error": "Text extends beyond blob"}

    # Decode UTF-8 text
    try:
        text = blob[text_start:text_end].decode("utf-8")
    except UnicodeDecodeError:
        text = blob[text_start:text_end].decode("utf-8", errors="replace")

    # Extract metadata about rich attributes
    metadata = {}
    if b"__kIMMentionConfirmedMention" in blob:
        metadata["has_mentions"] = True
    if b"__kIMFileTransferGUIDAttributeName" in blob:
        metadata["has_inline_attachment"] = True

    # Object replacement character indicates inline attachment placeholder
    if "\ufffc" in text:
        metadata["has_object_replacement"] = True

    return text, metadata


def strip_placeholders(text: str) -> str:
    """Remove placeholder characters and clean up whitespace.

    Args:
        text: Raw message text

    Returns:
        Cleaned text with placeholders removed
    """
    return text.replace("\ufffc", "").replace("\ufffd", "").strip()


def has_meaningful_text(text: Optional[str]) -> bool:
    """Check if text contains actual content, not just placeholders.

    Args:
        text: Message text to check

    Returns:
        True if text contains meaningful content
    """
    if not text:
        return False
    return len(strip_placeholders(text)) > 0


class IMessagePreprocessor:
    """Preprocessor for iMessage exports.

    Handles both Mac (chat.db) and iPhone (SMS/sms.db) export formats.
    Supports processing single or multiple exports with cross-export
    deduplication.

    Output structure matches Snapchat messages format:
    - metadata.json with export_info, conversations, orphaned_media
    - media/ directory with copied attachment files

    Attributes:
        export_paths: List of export directories to process
        output_dir: Directory for processed output
        handle_to_name: Mapping of handle IDs to display names
        content_hashes: Registry of file hashes for deduplication
    """

    def __init__(
        self,
        export_paths: Union[Path, List[Path]],
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        owner_name: Optional[str] = None,
    ):
        """Initialize preprocessor.

        Args:
            export_paths: Single path or list of paths to iMessage exports
            output_dir: Output directory for processed files
            workers: Number of parallel workers for hashing/copying (default: 8)
            owner_name: Override for device owner name
        """
        # Normalize to list for unified handling
        if isinstance(export_paths, Path):
            self.export_paths = [export_paths]
        else:
            self.export_paths = list(export_paths)

        # Output directories
        if output_dir:
            self.output_dir = Path(output_dir)
        else:
            # Default: create output in first export's parent directory
            self.output_dir = self.export_paths[0].parent / "imessage-processed"

        self.media_dir = self.output_dir / "media"
        self.metadata_file = self.output_dir / "metadata.json"

        # Parallel processing worker count
        self.workers = workers or 8

        # Owner name (device owner) - extract from directory name if not provided
        self.owner_name = owner_name or self._extract_owner_name()

        # Global hash registry for cross-export deduplication
        # hash -> {output_filename, source_path, messages: []}
        self.content_hashes: Dict[str, dict] = {}

        # Merged contact map from all vCards
        self.handle_to_name: Dict[str, str] = {}

        # Banned files filter
        self.banned_filter = BannedFilesFilter()

        # Failure tracker
        self.failure_tracker = FailureTracker(
            processor_name="iMessage",
            export_directory=str(self.export_paths[0]),
        )

        # Statistics
        self.stats = {
            "exports_processed": 0,
            "total_attachments": 0,
            "unique_files": 0,
            "duplicate_files": 0,
            "live_photos": 0,
            "files_copied": 0,
            "contacts_loaded": 0,
            "conversations": 0,
            "extensions_corrected": 0,
        }

    def _extract_owner_name(self) -> str:
        """Extract owner name from export directory name.

        Returns:
            Extracted owner name or 'unknown'
        """
        dir_name = self.export_paths[0].name
        # Pattern: mac-messages-YYYYMMDD or iph*-messages-YYYYMMDD
        match = re.match(r"(mac|iph\w+)-messages-\d{8}", dir_name)
        if match:
            # Use the device type as a fallback
            return match.group(1)
        return "unknown"

    def _detect_export_type(self, export_path: Path) -> str:
        """Detect whether export is from Mac or iPhone.

        Args:
            export_path: Path to export directory

        Returns:
            "mac" or "iphone"
        """
        if (export_path / "chat.db").exists():
            return "mac"
        elif (export_path / "SMS" / "sms.db").exists():
            return "iphone"
        else:
            raise ValueError(f"Unknown export type at {export_path}")

    def _get_database_path(self, export_path: Path) -> Path:
        """Get path to SQLite database for export.

        Args:
            export_path: Path to export directory

        Returns:
            Path to chat.db or sms.db
        """
        export_type = self._detect_export_type(export_path)
        if export_type == "mac":
            return export_path / "chat.db"
        else:
            return export_path / "SMS" / "sms.db"

    def _connect_database(self, export_path: Path) -> sqlite3.Connection:
        """Connect to iMessage SQLite database.

        Args:
            export_path: Path to export directory

        Returns:
            SQLite connection with row factory
        """
        db_path = self._get_database_path(export_path)
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _resolve_attachment_path(
        self, db_path: str, export_path: Path
    ) -> Optional[Path]:
        """Resolve attachment path from database to actual file location.

        Mac exports store paths like ~/Library/Messages/Attachments/...
        iPhone exports store paths like ~/Library/SMS/Attachments/...

        Args:
            db_path: Path string from database (with ~ prefix)
            export_path: Path to export directory

        Returns:
            Resolved Path to attachment file, or None if not found
        """
        if not db_path:
            return None

        export_type = self._detect_export_type(export_path)

        if export_type == "mac":
            # Mac: ~/Library/Messages/Attachments/... -> <export>/Attachments/...
            relative_path = db_path.replace("~/Library/Messages/", "")
            resolved = export_path / relative_path
        else:
            # iPhone: ~/Library/SMS/Attachments/... -> <export>/SMS/Attachments/...
            relative_path = db_path.replace("~/Library/SMS/", "")
            resolved = export_path / "SMS" / relative_path

        if resolved.exists():
            return resolved

        return None

    def _find_contacts_vcf(self, export_path: Path) -> Optional[Path]:
        """Find contacts.vcf file for export.

        Searches in export root, parent directory, and common locations.

        Args:
            export_path: Path to export directory

        Returns:
            Path to contacts.vcf or None
        """
        search_paths = [
            export_path / "contacts.vcf",
            export_path.parent / "contacts.vcf",
            Path("/mnt/media/originals/contacts.vcf"),
        ]

        for path in search_paths:
            if path.exists():
                return path

        return None

    def _merge_contacts(self, vcf_path: Path) -> None:
        """Parse vCard and merge into handle-to-name mapping.

        Later exports override earlier ones for conflicts.

        Args:
            vcf_path: Path to vCard file
        """
        parser = VCardParser(vcf_path)
        new_handles = parser.parse()

        # Merge, allowing later exports to override
        self.handle_to_name.update(new_handles)
        self.stats["contacts_loaded"] = len(self.handle_to_name)

    def _normalize_handle(self, handle_id: str) -> str:
        """Normalize handle ID for contact lookup.

        Args:
            handle_id: Raw handle ID from database

        Returns:
            Normalized handle ID
        """
        if not handle_id:
            return ""

        # Email addresses: lowercase
        if "@" in handle_id:
            return handle_id.lower().strip()

        # Phone numbers: strip non-digits except leading +
        has_plus = handle_id.startswith("+")
        digits = re.sub(r"\D", "", handle_id)
        if has_plus:
            return f"+{digits}"
        return digits

    def _resolve_handle_name(self, handle_id: str) -> str:
        """Resolve handle ID to display name.

        Attempts multiple lookups to handle phone number format variations:
        - Direct normalized lookup
        - US numbers: +1XXXXXXXXXX <-> XXXXXXXXXX (with/without country code)

        Args:
            handle_id: Handle ID from database

        Returns:
            Display name or original handle if not found
        """
        if not handle_id:
            return "Unknown"

        normalized = self._normalize_handle(handle_id)

        # Direct lookup
        if normalized in self.handle_to_name:
            return self.handle_to_name[normalized]

        # For phone numbers, try alternate formats
        if "@" not in handle_id:
            # If number starts with +1 (US), try without country code
            if normalized.startswith("+1") and len(normalized) == 12:
                alt_format = normalized[2:]  # Remove +1
                if alt_format in self.handle_to_name:
                    return self.handle_to_name[alt_format]

            # If number is 10 digits (US without country code), try with +1
            if len(normalized) == 10 and not normalized.startswith("+"):
                alt_format = f"+1{normalized}"
                if alt_format in self.handle_to_name:
                    return self.handle_to_name[alt_format]

        # No match found, return original handle
        return handle_id

    def _get_message_text(self, row: sqlite3.Row) -> Optional[str]:
        """Extract message text from database row.

        Prefers text column, falls back to attributedBody decoding.

        Args:
            row: Database row with text and attributedBody columns

        Returns:
            Cleaned message text or None
        """
        # Try text column first
        if has_meaningful_text(row["text"]):
            return strip_placeholders(row["text"])

        # Fall back to attributedBody
        if row["attributedBody"]:
            text, _metadata = decode_attributed_body(row["attributedBody"])
            if has_meaningful_text(text):
                return strip_placeholders(text)

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
        self, attachments: List[Dict], workers: int = 8
    ) -> Dict[str, List[Dict]]:
        """Hash files in parallel and group by content hash.

        Uses ThreadPoolExecutor to parallelize I/O-bound hashing operations.

        Args:
            attachments: List of attachment dictionaries with resolved_path
            workers: Number of parallel workers (default: 8)

        Returns:
            Dictionary mapping content hash to list of attachments with that hash
        """
        hash_to_attachments: Dict[str, List[Dict]] = {}

        def hash_file(attachment: Dict) -> Tuple[Dict, Optional[str]]:
            """Hash a single file, returning attachment and hash."""
            file_path = Path(attachment["resolved_path"])
            try:
                return (attachment, self._compute_file_hash(file_path))
            except Exception as e:
                logger.warning(f"Failed to hash {file_path}: {e}")
                return (attachment, None)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(hash_file, att) for att in attachments]
            for future in progress_bar(
                as_completed(futures),
                PHASE_PREPROCESS,
                "Hashing files",
                total=len(futures),
                unit="file",
            ):
                attachment, file_hash = future.result()
                if file_hash:
                    attachment["content_hash"] = file_hash
                    hash_to_attachments.setdefault(file_hash, []).append(attachment)

        return hash_to_attachments

    def _copy_files_parallel(
        self, copy_tasks: List[Tuple[Path, Path]], workers: int = 8
    ) -> Dict[Path, bool]:
        """Copy files in parallel.

        Uses ThreadPoolExecutor to parallelize I/O-bound copy operations.

        Args:
            copy_tasks: List of (source_path, dest_path) tuples
            workers: Number of parallel workers (default: 8)

        Returns:
            Dictionary mapping dest_path to success status (True/False)
        """
        results: Dict[Path, bool] = {}

        def copy_file(args: Tuple[Path, Path]) -> Tuple[Path, bool]:
            """Copy a single file, returning dest path and success status."""
            src, dst = args
            try:
                shutil.copy2(src, dst)
                return (dst, True)
            except Exception as e:
                logger.error(f"Failed to copy {src}: {e}")
                return (dst, False)

        with ThreadPoolExecutor(max_workers=workers) as executor:
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

    def _get_conversation_type(self, chat_style: int) -> str:
        """Convert chat style to conversation type.

        Args:
            chat_style: Chat style from database (43=group, 45=dm)

        Returns:
            "group" or "dm"
        """
        return "group" if chat_style == 43 else "dm"

    def _query_attachments(self, export_path: Path) -> List[Dict]:
        """Query attachments from database with message context.

        Args:
            export_path: Path to export directory

        Returns:
            List of attachment dictionaries with metadata
        """
        conn = self._connect_database(export_path)
        cursor = conn.cursor()

        # Main attachment query with message and chat context
        query = """
        SELECT
            a.ROWID as attachment_id,
            a.guid as attachment_guid,
            a.filename,
            a.mime_type,
            a.transfer_name,
            a.total_bytes,
            a.is_outgoing,
            a.created_date as attachment_created,
            m.ROWID as message_id,
            m.guid as message_guid,
            m.text,
            m.attributedBody,
            m.date as message_date,
            m.is_from_me,
            m.handle_id,
            c.ROWID as chat_id,
            c.guid as chat_guid,
            c.display_name as chat_display_name,
            c.chat_identifier,
            c.style as chat_style,
            h.id as sender_id
        FROM attachment a
        JOIN message_attachment_join maj ON a.ROWID = maj.attachment_id
        JOIN message m ON maj.message_id = m.ROWID
        JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
        JOIN chat c ON cmj.chat_id = c.ROWID
        LEFT JOIN handle h ON m.handle_id = h.ROWID
        WHERE a.filename IS NOT NULL
          AND a.transfer_state = 5
        ORDER BY m.date
        """

        cursor.execute(query)
        rows = cursor.fetchall()

        attachments = []
        export_type = self._detect_export_type(export_path)

        for row in rows:
            # Resolve file path
            file_path = self._resolve_attachment_path(row["filename"], export_path)
            if not file_path:
                logger.debug(f"Attachment not found: {row['filename']}")
                continue

            # Skip banned files
            if self.banned_filter.is_banned(file_path):
                continue

            # Skip non-media files
            if not get_media_type(str(file_path)):
                continue

            # Convert timestamp
            msg_date = convert_apple_timestamp(row["message_date"])

            # Determine conversation info
            chat_style = row["chat_style"] or 45  # Default to DM if not specified
            conversation_type = self._get_conversation_type(chat_style)
            chat_name = row["chat_display_name"] or row["chat_identifier"]
            chat_identifier = row["chat_identifier"]

            # Resolve sender
            # For outgoing messages, use "me" since we don't have the owner's name
            # in the iMessage database (unlike Snapchat which includes the username)
            if row["is_from_me"]:
                sender = "me"
            else:
                sender = self._resolve_handle_name(row["sender_id"])

            # Resolve conversation title
            # For groups: use the group display name
            # For DMs: resolve the chat_identifier (phone/email) to a contact name
            if conversation_type == "group":
                conversation_title = chat_name
            else:
                # DM: resolve the other party's identifier to a contact name
                conversation_title = self._resolve_handle_name(chat_identifier)

            # Extract message text
            message_text = self._get_message_text(row)

            attachment = {
                "attachment_id": row["attachment_id"],
                "filename": row["filename"],
                "resolved_path": str(file_path),
                "transfer_name": row["transfer_name"],
                "mime_type": row["mime_type"],
                "total_bytes": row["total_bytes"],
                "is_outgoing": bool(row["is_outgoing"]),
                # Match Snapchat format: "YYYY-MM-DD HH:MM:SS UTC"
                "created": format_timestamp(msg_date),
                "content": message_text or "",
                "is_from_me": bool(row["is_from_me"]),
                "is_sender": bool(row["is_from_me"]),
                "sender": sender,
                "conversation_id": chat_identifier,
                "conversation_type": conversation_type,
                "conversation_title": conversation_title,
                "source_export": export_path.name,  # Just directory name, not full path
                "export_type": export_type,
            }

            attachments.append(attachment)

            # Check for Live Photo video sidecar
            if row["transfer_name"] == "lp_image.HEIC":
                mov_path = file_path.parent / "lp_image.MOV"
                if mov_path.exists():
                    # Create linked attachment entry for the MOV
                    mov_attachment = attachment.copy()
                    mov_attachment["resolved_path"] = str(mov_path)
                    mov_attachment["transfer_name"] = "lp_image.MOV"
                    mov_attachment["mime_type"] = "video/quicktime"
                    mov_attachment["is_live_photo_video"] = True
                    mov_attachment["live_photo_image_id"] = row["attachment_id"]
                    attachments.append(mov_attachment)
                    self.stats["live_photos"] += 1

        conn.close()
        return attachments

    def _deduplicate_and_organize(
        self, attachments: List[Dict]
    ) -> Tuple[Dict[str, Dict], List[Dict]]:
        """Deduplicate attachments and organize by conversation.

        Groups identical files and organizes into conversations structure
        matching Snapchat messages format. Uses parallel processing for
        file hashing and copying operations.

        Args:
            attachments: List of attachment dictionaries

        Returns:
            Tuple of (conversations dict, orphaned_media list)
        """
        # Phase 1: Hash files in parallel
        hash_to_attachments = self._compute_hashes_parallel(
            attachments, workers=self.workers
        )

        # Phase 2: Prepare copy tasks (sequential - handles filename collisions)
        self.media_dir.mkdir(parents=True, exist_ok=True)

        # Track copy info: file_hash -> {source_path, output_path, output_filename, group}
        copy_info: Dict[str, Dict] = {}
        copy_tasks: List[Tuple[Path, Path]] = []

        for file_hash, group in hash_to_attachments.items():
            # Sort by date to find oldest
            group.sort(key=lambda x: x.get("created") or "9999-99-99")

            # Use first (oldest) as the source file
            primary = group[0]
            source_path = Path(primary["resolved_path"])

            # Generate unique output filename
            output_filename = self._generate_output_filename(source_path, primary)
            output_path = self.media_dir / output_filename

            # Handle name collisions
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
                "group": group,
            }

        # Phase 3: Copy files in parallel
        copy_results = self._copy_files_parallel(copy_tasks, workers=self.workers)

        # Phase 4: Build conversations based on copy results
        conversations: Dict[str, Dict] = {}
        unique_files_copied = 0

        for file_hash, info in copy_info.items():
            output_path = info["output_path"]
            output_filename = info["output_filename"]
            source_path = info["source_path"]
            group = info["group"]

            # Check if copy succeeded
            if not copy_results.get(output_path, False):
                continue

            unique_files_copied += 1
            self.stats["files_copied"] += 1

            # Track duplicates
            if len(group) > 1:
                self.stats["duplicate_files"] += len(group) - 1

            # Store in content hash registry
            self.content_hashes[file_hash] = {
                "output_filename": output_filename,
                "source_path": str(source_path),
            }

            # Determine primary conversation (oldest message's conversation)
            primary = group[0]
            primary_conv_id = primary["conversation_id"]
            if not primary_conv_id:
                continue

            # Initialize primary conversation if needed
            if primary_conv_id not in conversations:
                conversations[primary_conv_id] = {
                    "type": primary["conversation_type"],
                    "title": primary["conversation_title"],
                    "message_count": 0,
                    "messages": [],
                }

            # Also ensure all other conversations exist (for non-duplicate messages)
            for att in group:
                conv_id = att["conversation_id"]
                if conv_id and conv_id not in conversations:
                    conversations[conv_id] = {
                        "type": att["conversation_type"],
                        "title": att["conversation_title"],
                        "message_count": 0,
                        "messages": [],
                    }

            # Handle duplicates: create single merged entry in primary conversation
            if len(group) > 1:
                # Create merged message with messages array (Snapchat pattern)
                merged_message = {
                    "media_file": output_filename,
                    "primary_created": primary["created"],
                    "is_duplicate": True,
                    "messages": [],
                    "media_type": (
                        "IMAGE" if "image" in (primary["mime_type"] or "") else "VIDEO"
                    ),
                }

                # Add Live Photo marker if applicable
                if primary.get("is_live_photo_video"):
                    merged_message["is_live_photo_video"] = True

                # Add all occurrences to messages array
                for att in group:
                    merged_message["messages"].append(
                        {
                            "source_export": att["source_export"],
                            "conversation_id": att["conversation_id"],
                            "conversation_type": att["conversation_type"],
                            "conversation_title": att["conversation_title"],
                            "sender": att["sender"],
                            "created": att["created"],
                            "content": att["content"],
                            "is_sender": att["is_sender"],
                        }
                    )

                conversations[primary_conv_id]["messages"].append(merged_message)
                conversations[primary_conv_id]["message_count"] += 1

            else:
                # Single occurrence - create standard message entry
                att = group[0]
                message = {
                    "source_export": att["source_export"],
                    "conversation_id": att["conversation_id"],
                    "conversation_type": att["conversation_type"],
                    "conversation_title": att["conversation_title"],
                    "sender": att["sender"],
                    "created": att["created"],
                    "content": att["content"],
                    "is_sender": att["is_sender"],
                    "media_file": output_filename,
                    "media_type": (
                        "IMAGE" if "image" in (att["mime_type"] or "") else "VIDEO"
                    ),
                }

                # Add Live Photo marker if applicable
                if att.get("is_live_photo_video"):
                    message["is_live_photo_video"] = True

                conversations[primary_conv_id]["messages"].append(message)
                conversations[primary_conv_id]["message_count"] += 1

        self.stats["unique_files"] = unique_files_copied
        self.stats["conversations"] = len(conversations)

        # No orphaned media in iMessage (all attachments are linked to messages)
        orphaned_media: List[Dict] = []

        return conversations, orphaned_media

    def _generate_output_filename(self, source_path: Path, attachment: Dict) -> str:
        """Generate output filename with corrected extension based on actual file type.

        Detects the actual file format using magic bytes and corrects the extension
        if it doesn't match the file content. This prevents metadata write failures
        that occur when exiftool encounters mismatched file types.

        Args:
            source_path: Path to source file
            attachment: Attachment metadata

        Returns:
            Output filename with correct extension
        """
        # Use original transfer_name or filename
        original_name = attachment.get("transfer_name") or source_path.name
        original_path = Path(original_name)
        original_ext = original_path.suffix.lower()

        # Detect actual file type and get correct extension using shared utility
        correct_ext = detect_and_correct_extension(
            source_path,
            source_path.name,
            log_callback=lambda msg, details: self.log_message("EXTENSION_CORRECTED", msg, details),
        )

        # Check if extension needs correction
        if original_ext != correct_ext:
            # Extension mismatch - correct it
            new_name = original_path.stem + correct_ext
            logger.debug(
                f"Correcting extension: {original_name} -> {new_name} "
                f"(was {original_ext}, actually {correct_ext})"
            )
            self.stats["extensions_corrected"] += 1
            return new_name

        return original_name

    def _generate_metadata(
        self, conversations: Dict[str, Dict], orphaned_media: List[Dict]
    ) -> Dict:
        """Generate metadata.json structure matching Snapchat format.

        Args:
            conversations: Conversations dictionary
            orphaned_media: List of orphaned media entries

        Returns:
            Metadata dictionary
        """
        metadata = {
            "export_info": {
                "export_path": str(self.export_paths[0]),
                "export_paths": [str(p) for p in self.export_paths],
                "export_username": self.owner_name,
                "processed_date": datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                ),
                "contacts_loaded": self.stats["contacts_loaded"],
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
        logger.info(
            f"Starting iMessage preprocessing for {len(self.export_paths)} export(s)"
        )

        # Phase 1: Load all contacts
        for export_path in progress_bar(
            self.export_paths, PHASE_PREPROCESS, "Loading contacts", unit="export"
        ):
            vcf_path = self._find_contacts_vcf(export_path)
            if vcf_path:
                self._merge_contacts(vcf_path)

        logger.info(f"Loaded {self.stats['contacts_loaded']} contact handles")

        # Phase 2: Query all databases
        all_attachments = []
        for export_path in progress_bar(
            self.export_paths, PHASE_PREPROCESS, "Querying databases", unit="export"
        ):
            try:
                attachments = self._query_attachments(export_path)
                all_attachments.extend(attachments)
                self.stats["exports_processed"] += 1
            except Exception as e:
                logger.error(f"Failed to query {export_path}: {e}")
                continue

        self.stats["total_attachments"] = len(all_attachments)
        logger.info(f"Found {len(all_attachments)} total attachments")

        if not all_attachments:
            logger.warning("No attachments found to process")
            # Still create output structure
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.media_dir.mkdir(parents=True, exist_ok=True)
            metadata = self._generate_metadata({}, [])
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)
            return True

        # Phase 3: Deduplicate and organize by conversation
        logger.info("Deduplicating and organizing attachments...")
        conversations, orphaned_media = self._deduplicate_and_organize(all_attachments)
        logger.info(
            f"Organized into {len(conversations)} conversations, "
            f"{self.stats['unique_files']} unique files "
            f"({self.stats['duplicate_files']} duplicates removed)"
        )

        # Phase 4: Generate metadata
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
        print("IMESSAGE PREPROCESSING STATISTICS")
        print("=" * 60)
        print(f"Exports processed:          {self.stats['exports_processed']:>6}")
        print(f"Contacts loaded:            {self.stats['contacts_loaded']:>6}")
        print(f"Total attachments found:    {self.stats['total_attachments']:>6}")
        print(f"Conversations:              {self.stats['conversations']:>6}")
        print(f"Unique files:               {self.stats['unique_files']:>6}")
        print(f"Duplicate files removed:    {self.stats['duplicate_files']:>6}")
        print(f"Live Photo pairs:           {self.stats['live_photos']:>6}")
        print(f"Files copied:               {self.stats['files_copied']:>6}")
        print(f"Extensions corrected:       {self.stats['extensions_corrected']:>6}")
        print("=" * 60)
