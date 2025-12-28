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
import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

from common.detection import resolve_username, DetectionResult
from common.files.scanner import iter_media_files
from common.preprocess.context_mergers import merge_imessage_context
from common.processing import print_preprocessing_stats, process_parallel_threaded
from common.output.progress import PHASE_PREPROCESS, progress_bar
from common.utils import get_media_type
from common.output.logging import get_processor_logger
from processors.imessage.vcard_parser import VCardParser
from processors.preprocess_base import CopyTask, PreprocessorBase

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


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


def strip_placeholders(text: Optional[str]) -> str:
    """Remove placeholder characters and clean up whitespace.

    Args:
        text: Raw message text (can be None)

    Returns:
        Cleaned text with placeholders removed, or empty string if None
    """
    if text is None:
        return ""
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


class IMessagePreprocessor(PreprocessorBase):
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
    """

    def __init__(
        self,
        export_paths: Union[Path, List[Path]],
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        owner_name: Optional[str] = None,
        final_output_dir: Optional[Path] = None,
    ):
        """Initialize preprocessor.

        Args:
            export_paths: Single path or list of paths to iMessage exports
            output_dir: Output directory for processed files
            workers: Number of parallel workers for hashing/copying (default: 8)
            owner_name: Override for device owner name
            final_output_dir: Final output directory for failure tracking
        """
        # Normalize to list for unified handling
        if isinstance(export_paths, Path):
            export_paths_list = [export_paths]
        else:
            export_paths_list = list(export_paths)

        # Handle default output_dir before calling super().__init__
        if output_dir is None:
            output_dir = export_paths_list[0].parent / "imessage-processed"

        super().__init__(
            processor_name="iMessage",
            export_path=export_paths_list[0],
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        # Store all export paths (multi-export support)
        self.export_paths = export_paths_list

        # Owner name (device owner) - extract from directory name if not provided
        self.owner_name = owner_name or self._extract_owner_name()

        # Set context merger for deduplication (accumulates messages)
        self.set_context_merger(merge_imessage_context)

        # Merged contact map from all vCards
        self.handle_to_name: Dict[str, str] = {}

        # Statistics specific to this preprocessor (preserves shared keys from base)
        self.stats.update(
            {
                "exports_processed": 0,
                "total_attachments": 0,
                "unique_files": 0,
                "duplicate_files": 0,
                "live_photos": 0,
                "contacts_loaded": 0,
                "conversations": 0,
            }
        )

    def _extract_owner_name(self) -> str:
        """Extract owner name (device identifier) from export directory name.

        Expected pattern: imessage-{device}-YYYYMMDD (e.g., imessage-iph17pm-20251121)

        Uses cached detection result from run() pipeline.

        Returns:
            Extracted device name or 'unknown'
        """
        detection = self.ensure_detection_result()
        return detection.username or "unknown"

    def detect_username(self) -> DetectionResult:
        """Detect device identifier from export directory name.

        For iMessage exports, the 'username' is actually the device identifier
        since there's no intrinsic username in iMessage exports.

        Expected pattern: imessage-{device}-YYYYMMDD

        Returns:
            DetectionResult with device identifier and source
        """

        def detect_from_imessage_dirname(path: Path) -> Optional[str]:
            """Extract device name from imessage-{device}-YYYYMMDD pattern."""
            dir_name = path.name
            match = re.match(r"imessage-(\w+)-\d{8}", dir_name)
            if match:
                return match.group(1)
            return None

        return resolve_username(
            self.export_paths[0],
            [
                ("dirname", detect_from_imessage_dirname),
            ],
        )

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

    def validate_export(self) -> bool:
        """Validate that at least one export directory exists with a database.

        Returns:
            True if valid, False otherwise
        """
        for export_path in self.export_paths:
            if not self.require_exists(export_path, label="Export path"):
                return False
            # Check for database (either chat.db or SMS/sms.db)
            chat_db = export_path / "chat.db"
            sms_db = export_path / "SMS" / "sms.db"
            if not chat_db.exists() and not sms_db.exists():
                logger.error(
                    "No iMessage database found in %s (checked chat.db, SMS/sms.db)",
                    export_path,
                )
                return False
        return True

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
        attachments_not_found = 0

        for row in rows:
            # Resolve file path
            file_path = self._resolve_attachment_path(row["filename"], export_path)
            if not file_path:
                attachments_not_found += 1
                # Track as orphaned metadata (database entry with missing file)
                self.failure_tracker.add_orphaned_metadata(
                    metadata_entry={
                        "attachment_id": row["attachment_id"],
                        "filename": row["filename"],
                        "message_date": row["message_date"],
                        "chat_identifier": row["chat_identifier"],
                    },
                    reason="Attachment file not found in filesystem",
                    context={"expected_path": row["filename"]},
                )
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
            # Live Photos can be stored as lp_image.HEIC or lp_image.jpeg
            transfer_name = row["transfer_name"] or ""
            if transfer_name.lower().startswith(
                "lp_image."
            ) and not transfer_name.lower().endswith(".mov"):
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

        if attachments_not_found > 0:
            logger.debug("Attachments not found: %d", attachments_not_found)

        return attachments

    def _deduplicate_and_organize(self, attachments: List[Dict]) -> Dict[str, Dict]:
        """Deduplicate attachments and organize by conversation.

        Groups identical files and organizes into conversations structure
        matching Snapchat messages format. Uses hash_with_dedup() for parallel
        processing of file hashing and copying operations.

        Args:
            attachments: List of attachment dictionaries

        Returns:
            Conversations dictionary
        """
        # Sort attachments by date so oldest is processed first (becomes primary)
        attachments.sort(key=lambda x: x.get("created") or "9999-99-99")

        # Build CopyTask objects from attachments
        copy_tasks: List[CopyTask] = []
        for att in attachments:
            source_path = Path(att["resolved_path"])
            # Use original transfer_name or filename
            dest_filename = att.get("transfer_name") or source_path.name

            # Build message context for merging
            context = {
                "source_export": att["source_export"],
                "conversation_id": att["conversation_id"],
                "conversation_type": att["conversation_type"],
                "conversation_title": att["conversation_title"],
                "sender": att["sender"],
                "created": att["created"],
                "content": att["content"],
                "is_sender": att["is_sender"],
                "mime_type": att.get("mime_type"),
                "is_live_photo_video": att.get("is_live_photo_video", False),
            }

            copy_tasks.append(
                CopyTask(
                    source_path=source_path,
                    dest_filename=dest_filename,
                    context=context,
                )
            )

        self.stats["total_attachments"] = len(copy_tasks)

        # Use hash_with_dedup for parallel hashing and deduplication (no file copying)
        # Files will be copied directly from source to final output during processing
        results = self.hash_with_dedup(copy_tasks)

        # Build conversations from results
        conversations: Dict[str, Dict] = {}
        unique_files_copied = 0
        duplicate_count = 0

        for source_path, result in results.items():
            if result.is_duplicate:
                duplicate_count += 1
                continue

            unique_files_copied += 1

            # Get merged context (includes all duplicate message contexts)
            merged_contexts = self.get_contexts_list_for_file(result.dest_filename)

            # Primary context is the first one (oldest due to sorting)
            # If no merged contexts found, skip this result as we can't process without context
            if not merged_contexts:
                continue
            primary_context = merged_contexts[0]

            primary_conv_id = primary_context.get("conversation_id")
            if not primary_conv_id:
                continue

            # Initialize conversation if needed
            if primary_conv_id not in conversations:
                conversations[primary_conv_id] = {
                    "type": primary_context.get("conversation_type"),
                    "title": primary_context.get("conversation_title"),
                    "message_count": 0,
                    "messages": [],
                }

            # Also ensure all other conversations exist
            for ctx in merged_contexts:
                conv_id = ctx.get("conversation_id")
                if conv_id and conv_id not in conversations:
                    conversations[conv_id] = {
                        "type": ctx.get("conversation_type"),
                        "title": ctx.get("conversation_title"),
                        "message_count": 0,
                        "messages": [],
                    }

            # Determine media type
            mime_type = primary_context.get("mime_type") or ""
            media_type = "IMAGE" if "image" in mime_type else "VIDEO"

            # Check if this has merged duplicates
            if len(merged_contexts) > 1:
                # Create merged message with messages array
                merged_message = {
                    "media_files": [result.dest_filename],
                    "source_paths": {result.dest_filename: str(result.source_path)},
                    "primary_created": primary_context.get("created"),
                    "is_duplicate": True,
                    "messages": [],
                    "media_type": media_type,
                }

                # Add Live Photo marker if applicable
                if primary_context.get("is_live_photo_video"):
                    merged_message["is_live_photo_video"] = True

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

                conversations[primary_conv_id]["messages"].append(merged_message)
                conversations[primary_conv_id]["message_count"] += 1
                duplicate_count += len(merged_contexts) - 1
            else:
                # Single occurrence - create standard message entry
                message = {
                    "source_export": primary_context.get("source_export"),
                    "conversation_id": primary_context.get("conversation_id"),
                    "conversation_type": primary_context.get("conversation_type"),
                    "conversation_title": primary_context.get("conversation_title"),
                    "sender": primary_context.get("sender"),
                    "created": primary_context.get("created"),
                    "content": primary_context.get("content"),
                    "is_sender": primary_context.get("is_sender"),
                    "media_files": [result.dest_filename],
                    "source_paths": {result.dest_filename: str(result.source_path)},
                    "media_type": media_type,
                }

                # Add Live Photo marker if applicable
                if primary_context.get("is_live_photo_video"):
                    message["is_live_photo_video"] = True

                conversations[primary_conv_id]["messages"].append(message)
                conversations[primary_conv_id]["message_count"] += 1

        self.stats["unique_files"] = unique_files_copied
        self.stats["duplicate_files"] = duplicate_count
        self.stats["conversations"] = len(conversations)

        return conversations

    def _detect_orphaned_media(self, processed_attachments: List[Dict]) -> List[Dict]:
        """Detect media files in Attachments folder that aren't in the database.

        Scans the Attachments directory for all media files and compares against
        the list of attachments that were found in the database query.

        Args:
            processed_attachments: List of attachment dicts from database query

        Returns:
            List of orphaned media entries for metadata.json
        """
        # Build set of processed file paths (resolved paths from database)
        processed_paths: set = set()
        for attachment in processed_attachments:
            resolved_path = attachment.get("resolved_path")
            if resolved_path:
                processed_paths.add(Path(resolved_path).resolve())

        orphaned_media: List[Dict] = []
        orphaned_count = 0

        # Scan all export paths for media files not in the database
        for export_path in self.export_paths:
            # Find Attachments directory (Mac or iPhone format)
            attachments_dir = None
            if (export_path / "Attachments").is_dir():
                attachments_dir = export_path / "Attachments"
            elif (export_path / "SMS" / "Attachments").is_dir():
                attachments_dir = export_path / "SMS" / "Attachments"

            if not attachments_dir:
                continue

            # Scan for all media files using shared scanner
            for file_path in iter_media_files(
                attachments_dir,
                banned_filter=self.banned_filter,
                skip_hidden=True,
                skip_overlays=False,  # iMessage doesn't have overlay concept
                skip_thumbnails=False,  # iMessage doesn't have thumbnail files
                recursive=True,
            ):
                # Check if this file was processed from the database
                resolved_path = file_path.resolve()
                if resolved_path not in processed_paths:
                    # This is an orphaned media file
                    orphaned_count += 1

                    # Track with FailureTracker
                    self.failure_tracker.add_orphaned_media(
                        media_path=file_path,
                        reason="Media file not referenced in iMessage database",
                        context={
                            "export_path": str(export_path),
                            "original_location": str(file_path),
                        },
                    )

                    # Create orphaned media entry for metadata.json
                    orphaned_entry = {
                        "conversation_id": None,
                        "conversation_type": None,
                        "conversation_title": None,
                        "sender": None,
                        "media_type": get_media_type(str(file_path)),
                        "created": None,
                        "content": "",
                        "filename": file_path.name,
                        "source_path": str(file_path),
                    }
                    orphaned_media.append(orphaned_entry)

        if orphaned_count > 0:
            logger.info("Found %d orphaned media files", orphaned_count)

        return orphaned_media

    def _generate_output_filename(self, source_path: Path, attachment: Dict) -> str:
        """Generate output filename from attachment metadata.

        Args:
            source_path: Path to source file
            attachment: Attachment metadata

        Returns:
            Output filename
        """
        # Use original transfer_name or filename
        return attachment.get("transfer_name") or source_path.name

    def get_content_key(self) -> str:
        """Return the content key for iMessage metadata."""
        return "conversations"

    @staticmethod
    def get_platform() -> str:
        """Return the platform identifier for iMessage."""
        return "iMessage"

    @staticmethod
    def get_service() -> None:
        """Return the service identifier for iMessage (single-service platform)."""
        return None

    def process_export(self) -> bool:
        """Platform-specific preprocessing logic.

        Processes iMessage exports, handling multiple export directories,
        deduplication, and Live Photo pairs.

        Returns:
            True if processing succeeded, False otherwise
        """
        # Phase 1: Load all contacts (sequential - fast, shared state)
        for export_path in progress_bar(
            self.export_paths, PHASE_PREPROCESS, "Loading contacts"
        ):
            vcf_path = self._find_contacts_vcf(export_path)
            if vcf_path:
                self._merge_contacts(vcf_path)

        logger.info("Loaded %d contact handles", self.stats["contacts_loaded"])

        # Phase 2: Query all databases in parallel
        logger.info(
            "Querying %d databases (using %d workers)...",
            len(self.export_paths),
            self.workers,
        )

        def query_with_path(export_path: Path) -> Tuple[Path, List[Dict]]:
            """Wrapper to include export_path in result for tracking."""
            attachments = self._query_attachments(export_path)
            return (export_path, attachments)

        results = process_parallel_threaded(
            tasks=list(self.export_paths),
            worker_fn=query_with_path,
            workers=self.workers,
            phase=PHASE_PREPROCESS,
            description="Querying databases",
            task_id_fn=lambda p: p.name,
            on_error=lambda tid, e: logger.error("Failed to query %s: %s", tid, e),
        )

        # Collect attachments from successful queries
        all_attachments = []
        for export_path, attachments in results:
            all_attachments.extend(attachments)
            with self.stats_lock:
                self.stats["exports_processed"] += 1

        self.stats["total_attachments"] = len(all_attachments)
        logger.info("Found %d total attachments", len(all_attachments))

        if not all_attachments:
            logger.warning("No attachments found to process")
            # Still create output structure (no media directory needed - files are read from source)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.save_metadata(
                {},
                extra_export_info={
                    "export_paths": [str(p) for p in self.export_paths],
                    "contacts_loaded": self.stats["contacts_loaded"],
                },
            )
            return True

        # Phase 3: Deduplicate and organize by conversation
        logger.info("Deduplicating and organizing attachments...")
        conversations = self._deduplicate_and_organize(all_attachments)
        logger.info(
            f"Organized into {len(conversations)} conversations, "
            f"{self.stats['unique_files']} unique files "
            f"({self.stats['duplicate_files']} duplicates removed)"
        )

        # Phase 3b: Detect orphaned media (files in Attachments not in database)
        orphaned_media = self._detect_orphaned_media(all_attachments)

        # Phase 4: Save metadata
        logger.info("Saving metadata.json")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.set_unmatched_media(orphaned_media)
        self.save_metadata(
            conversations,
            extra_export_info={
                "export_paths": [str(p) for p in self.export_paths],
                "contacts_loaded": self.stats["contacts_loaded"],
            },
        )

        return True

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Exports processed": self.stats["exports_processed"],
            "Contacts loaded": self.stats["contacts_loaded"],
            "Attachments found": self.stats["total_attachments"],
            "Conversations": self.stats["conversations"],
            "Unique files": self.stats["unique_files"],
            "Duplicates removed": self.stats["duplicate_files"],
            "Live Photo pairs": self.stats["live_photos"],
        }
        print_preprocessing_stats("iMessage Preprocessing", stats)
