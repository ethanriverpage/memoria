#!/usr/bin/env python3
"""
Google Chat Messages Preprocessor

Organizes Google Chat message export files and creates cleaned metadata:
- Parses JSON files containing message metadata
- Copies media files to organized output directory
- Creates metadata.json with essential information (sender, timestamps, conversation)
"""

import json
import logging
import shutil
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import sys
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import multiprocessing
from tqdm import tqdm
import magic

from common.utils import ALL_MEDIA_EXTENSIONS
from common.filter_banned_files import BannedFilesFilter
from common.failure_tracker import FailureTracker

# Set up logging
logger = logging.getLogger(__name__)


class GoogleChatPreprocessor:
    """Preprocesses Google Chat message export by organizing files and cleaning metadata"""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        
        self.export_path = Path(export_path)
        self.chat_dir = self.export_path / "Google Chat"
        self.groups_dir = self.chat_dir / "Groups"
        self.users_dir = self.chat_dir / "Users"

        # Output directories
        output_base = Path(output_dir) if output_dir else self.export_path
        self.output_dir = output_base
        self.media_output_dir = output_base / "media"
        self.metadata_file = output_base / "metadata.json"
        self.log_file = output_base / "preprocessing.log"

        # Final output directory for processor (for failure tracking)
        self.final_output_dir = Path(final_output_dir) if final_output_dir else output_base

        # Export username (detected from user_info.json)
        self.export_username = None
        self.export_email = None

        # Initialize banned files filter
        self.banned_filter = BannedFilesFilter()

        # Initialize failure tracker
        self.failure_tracker = FailureTracker(
            processor_name="Google Chat",
            export_directory=str(export_path),
        )

        # Threading configuration
        if workers is None:
            # Default to CPU count - 1, minimum 1
            self.workers = max(1, multiprocessing.cpu_count() - 1)
        else:
            self.workers = max(1, workers)
        
        # Thread-safe locks for shared data structures
        self.stats_lock = Lock()
        self.log_lock = Lock()
        self.destination_lock = Lock()

        # Statistics
        self.stats = {
            "total_conversations": 0,
            "total_messages_with_media": 0,
            "total_media_files": 0,
            "media_copied": 0,
            "missing_files": 0,
            "fuzzy_matches": 0,
            "extensions_corrected": 0,
            "banned_files_skipped": 0,
        }

        # Log entries
        self.log_entries = []

        # Track used files for duplicate handling
        self.used_files = defaultdict(int)

        # Track which media files were copied (for orphaned media detection)
        self.copied_media_paths = set()

    def log_message(self, category: str, message: str, details: str = "") -> None:
        """Add a log entry (thread-safe)"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] {category}: {message}"
        if details:
            entry += f" ({details})"
        with self.log_lock:
            self.log_entries.append(entry)

    def save_log(self) -> None:
        """Save log entries to log file"""
        if not self.log_entries:
            return

        try:
            with open(self.log_file, "w", encoding="utf-8") as f:
                f.write("Google Chat Messages Preprocessing Log\n")
                f.write("=" * 80 + "\n")
                f.write(f"Export: {self.export_path}\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 80 + "\n\n")

                for entry in self.log_entries:
                    f.write(entry + "\n")

                f.write(f"\nTotal logged issues: {len(self.log_entries)}\n")

            print(f"SUCCESS: Saved preprocessing log to {self.log_file}")
        except Exception as e:
            print(f"WARNING: Failed to save log file: {e}")

    def validate_export(self) -> bool:
        """Validate that export directory has required structure"""
        if not self.export_path.exists():
            print(f"ERROR: Export path does not exist: {self.export_path}")
            return False

        if not self.chat_dir.exists():
            print(f"ERROR: Google Chat directory not found: {self.chat_dir}")
            return False

        if not self.groups_dir.exists():
            print(f"ERROR: Groups directory not found: {self.groups_dir}")
            return False

        # Check if at least one conversation folder exists
        conversation_folders = [d for d in self.groups_dir.iterdir() if d.is_dir()]
        if not conversation_folders:
            print(f"ERROR: No conversation folders found in {self.groups_dir}")
            return False

        return True

    def detect_export_username(self) -> bool:
        """
        Detect export username from user_info.json
        Returns True if successful, False otherwise
        """
        if not self.users_dir.exists():
            self.log_message(
                "USERNAME_DETECTION_ERROR",
                f"Users directory not found: {self.users_dir}",
            )
            return False

        # Find the user info file (should be only one)
        user_dirs = [d for d in self.users_dir.iterdir() if d.is_dir()]
        if not user_dirs:
            self.log_message(
                "USERNAME_DETECTION_ERROR",
                "No user directories found",
            )
            return False

        user_info_path = user_dirs[0] / "user_info.json"
        if not user_info_path.exists():
            self.log_message(
                "USERNAME_DETECTION_ERROR",
                f"user_info.json not found in {user_dirs[0]}",
            )
            return False

        try:
            with open(user_info_path, "r", encoding="utf-8") as f:
                user_data = json.load(f)

            email = user_data.get("user", {}).get("email")
            if not email:
                self.log_message(
                    "USERNAME_DETECTION_ERROR",
                    "Email not found in user_info.json",
                )
                return False

            self.export_email = email
            # Strip @gmail.com to get username
            self.export_username = email.replace("@gmail.com", "")

            print(f"   Detected export user: {self.export_username}")
            return True

        except Exception as e:
            self.log_message(
                "USERNAME_DETECTION_ERROR",
                "Failed to read user_info.json",
                str(e),
            )
            return False

    def sanitize_name(self, name: str) -> str:
        """
        Sanitize a name for use in filenames
        Replaces spaces and special characters with underscores
        """
        # Replace spaces with underscores
        sanitized = name.replace(" ", "_")
        # Remove or replace other problematic characters
        sanitized = re.sub(r'[<>:"/\\|?*]', "_", sanitized)
        # Remove quotes, parentheses, commas, and other punctuation
        sanitized = re.sub(r'["\',()[\]{}!@#$%^&*+=;~`]', "", sanitized)
        # Clean up multiple underscores
        sanitized = re.sub(r"_+", "_", sanitized)
        # Strip leading/trailing underscores
        sanitized = sanitized.strip("_")
        return sanitized

    def extract_conversation_name(
        self, group_info: Dict, conversation_type: str, conversation_id: str
    ) -> str:
        """
        Extract conversation name from group_info
        For DMs: name of the other participant
        For Spaces: group name or constructed from members
        Returns the ORIGINAL name with spaces intact (sanitization happens at filename creation time)
        """
        members = group_info.get("members", [])

        if conversation_type == "DM":
            # Find the member who isn't the export user
            for member in members:
                member_email = member.get("email", "")
                member_name = member.get("name", "")
                if member_email != self.export_email:
                    return member_name  # Return original name, not sanitized

            self.log_message(
                "CONVERSATION_NAME_ERROR",
                f"Could not determine DM partner for {conversation_id}",
            )
            return conversation_id

        elif conversation_type == "Space":
            # Check if there's a custom name
            group_name = group_info.get("name", "")
            if group_name and group_name != "Group Chat":
                return group_name  # Return original name, not sanitized

            # Construct from members (excluding export user)
            member_names = []
            for member in members:
                member_email = member.get("email", "")
                member_name = member.get("name", "")
                if member_email != self.export_email and member_name:
                    # Extract first name
                    first_name = member_name.split()[0] if member_name else member_name
                    member_names.append(first_name)

            if member_names:
                return ", ".join(member_names)  # Join with comma-space, not underscore

            self.log_message(
                "CONVERSATION_NAME_ERROR",
                f"Could not determine Space name for {conversation_id}",
            )
            return conversation_id

        return conversation_id

    def parse_timestamp(self, timestamp_str: str) -> Optional[str]:
        """
        Convert timestamp string to ISO format
        Input format: "Wednesday, May 4, 2016 at 4:20:19 AM UTC"
        Output format: "2016-05-04 04:20:19"
        """
        try:
            # Parse the timestamp
            dt = datetime.strptime(timestamp_str, "%A, %B %d, %Y at %I:%M:%S %p %Z")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError) as e:
            self.log_message(
                "TIMESTAMP_PARSE_ERROR",
                f"Failed to parse timestamp: {timestamp_str}",
                str(e),
            )
            return None

    def normalize_filename(self, export_name: str) -> str:
        """
        Normalize export_name to match filesystem filename
        Handles URL encoding and special character substitutions
        """
        # Handle URL encoding: \u003d -> =
        normalized = export_name.replace("\\u003d", "=")
        # Handle ? -> _
        normalized = normalized.replace("?", "_")
        # Handle ' -> _ (apostrophes get converted to underscores in filesystem)
        normalized = normalized.replace("'", "_")
        return normalized

    def find_media_file(
        self, export_name: str, file_catalog: Dict[str, Path]
    ) -> Optional[Tuple[str, Path]]:
        """
        Find media file using fuzzy matching
        Returns (matched_filename, source_path) or None
        """
        # Normalize the export name
        normalized = self.normalize_filename(export_name)

        # Try exact match first
        if normalized in file_catalog:
            # Check if this file has been used before
            use_count = self.used_files[normalized]
            if use_count == 0:
                self.used_files[normalized] += 1
                return (normalized, file_catalog[normalized])
            else:
                # Look for numbered version
                base, ext = (
                    normalized.rsplit(".", 1) if "." in normalized else (normalized, "")
                )
                numbered_name = (
                    f"{base}({use_count}).{ext}" if ext else f"{base}({use_count})"
                )
                if numbered_name in file_catalog:
                    self.used_files[normalized] += 1
                    self.stats["fuzzy_matches"] += 1
                    return (numbered_name, file_catalog[numbered_name])

        # Try variations
        variations = [
            export_name,  # Original
            export_name.replace("?", "_"),  # ? -> _
            export_name.replace("\\u003d", "="),  # URL encoding
            export_name.replace("'", "_"),  # ' -> _
            export_name.replace("'", "_").replace("?", "_"),  # Combined
        ]

        for variation in variations:
            if variation in file_catalog:
                use_count = self.used_files[variation]
                if use_count == 0:
                    self.used_files[variation] += 1
                    self.stats["fuzzy_matches"] += 1
                    return (variation, file_catalog[variation])
                else:
                    base, ext = (
                        variation.rsplit(".", 1)
                        if "." in variation
                        else (variation, "")
                    )
                    numbered_name = (
                        f"{base}({use_count}).{ext}" if ext else f"{base}({use_count})"
                    )
                    if numbered_name in file_catalog:
                        self.used_files[variation] += 1
                        self.stats["fuzzy_matches"] += 1
                        return (numbered_name, file_catalog[numbered_name])

        # Try prefix matching for truncated filenames
        # Filesystem limits can truncate long filenames (typically at 255 chars)
        if "." in normalized:
            base_name, ext = normalized.rsplit(".", 1)
            ext_lower = ext.lower()

            # Try matching files with same extension that start with base_name
            best_match = None
            best_match_length = 0

            for filename in file_catalog.keys():
                if filename.lower().endswith(f".{ext_lower}"):
                    file_base = filename.rsplit(".", 1)[0]
                    # Check if filesystem filename is a truncated version
                    # Match if one is a prefix of the other
                    # Use a minimum prefix length of 30 chars (lowered from 40)
                    # Also handle apostrophe variations
                    base_name_normalized = base_name.replace("'", "_")

                    if (
                        base_name_normalized.startswith(file_base)
                        and len(file_base) >= 30
                    ):
                        # File on disk is truncated version of expected name
                        if len(file_base) > best_match_length:
                            best_match = filename
                            best_match_length = len(file_base)
                    elif (
                        file_base.startswith(base_name_normalized)
                        and len(base_name_normalized) >= 30
                    ):
                        # Expected name is truncated version of file on disk (shouldn't happen but be safe)
                        if len(base_name_normalized) > best_match_length:
                            best_match = filename
                            best_match_length = len(base_name_normalized)

            if best_match and self.used_files[best_match] == 0:
                self.used_files[best_match] += 1
                self.stats["fuzzy_matches"] += 1
                self.log_message(
                    "TRUNCATED_MATCH",
                    f"Matched truncated filename: {export_name} -> {best_match}",
                )
                return (best_match, file_catalog[best_match])

        return None

    def scan_conversations(self) -> List[Tuple[str, str, Path]]:
        """
        Scan Groups directory for all conversation folders
        Returns list of (conversation_type, conversation_id, folder_path) tuples
        """
        conversations = []

        try:
            import os
            with os.scandir(self.groups_dir) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    if not entry.is_dir():
                        continue

                    conv_dir = Path(entry.path)

                    # Parse folder name: "DM xxxxx" or "Space xxxxx"
                    folder_name = entry.name
                    parts = folder_name.split(" ", 1)

                    if len(parts) != 2:
                        self.log_message(
                            "FOLDER_NAME_ERROR",
                            f"Unexpected folder name format: {folder_name}",
                        )
                        continue

                    conversation_type, conversation_id = parts

                    if conversation_type not in ["DM", "Space"]:
                        self.log_message(
                            "FOLDER_TYPE_ERROR",
                            f"Unknown conversation type: {conversation_type}",
                        )
                        continue

                    group_info_path = conv_dir / "group_info.json"
                    messages_path = conv_dir / "messages.json"

                    if not group_info_path.exists():
                        self.log_message(
                            "MISSING_GROUP_INFO",
                            f"No group_info.json found in {folder_name}",
                        )
                        continue

                    if not messages_path.exists():
                        self.log_message(
                            "MISSING_MESSAGES",
                            f"No messages.json found in {folder_name}",
                        )
                        continue

                    conversations.append((conversation_type, conversation_id, conv_dir))

        except Exception as e:
            self.log_message(
                "SCAN_ERROR",
                "Failed to scan conversation folders",
                str(e),
            )
            logger.error(f"Failed to scan conversations: {e}")

        return conversations

    def build_conversation_catalog(self, conv_folder: Path) -> Dict[str, Path]:
        """
        Build file catalog for a specific conversation folder
        Returns: Dict[filename, source_path]
        """
        catalog = {}

        # Media extensions to look for
        media_extensions = ALL_MEDIA_EXTENSIONS

        # Look for media files directly in the conversation folder
        import os
        with os.scandir(conv_folder) as entries:
            for entry in entries:
                if entry.is_file():
                    file_path = Path(entry.path)
                    filename = entry.name

                    # Skip banned files
                    if self.banned_filter.is_banned(file_path):
                        with self.stats_lock:
                            self.stats["banned_files_skipped"] += 1
                        continue

                    # Skip JSON files
                    if filename.endswith(".json"):
                        continue

                    # Skip system files
                    if filename.startswith(".") or filename.startswith("_"):
                        continue

                    # Check if it's a media file
                    if file_path.suffix.lower() in media_extensions:
                        catalog[filename] = file_path

        return catalog

    def count_total_media_files(self) -> int:
        """
        Count total media files across all conversation folders
        """
        total = 0

        # Media extensions to look for
        media_extensions = ALL_MEDIA_EXTENSIONS

        # Scan all conversation folders
        import os
        with os.scandir(self.groups_dir) as conv_entries:
            for conv_entry in conv_entries:
                if not conv_entry.is_dir():
                    continue

                with os.scandir(conv_entry.path) as file_entries:
                    for file_entry in file_entries:
                        if file_entry.is_file():
                            file_path = Path(file_entry.path)
                            filename = file_entry.name

                            # Skip banned files
                            if self.banned_filter.is_banned(file_path):
                                continue

                            if (
                                not filename.endswith(".json")
                                and not filename.startswith(".")
                                and not filename.startswith("_")
                                and file_path.suffix.lower() in media_extensions
                            ):
                                total += 1

        return total

    def detect_and_correct_extension(
        self, file_path: Path, original_filename: str
    ) -> str:
        """
        Detect actual file type and return correct extension

        Fixes misnamed files from Google export (e.g., WebM files with .jpg extension)

        Args:
            file_path: Path to the actual file
            original_filename: Original filename with potentially wrong extension

        Returns:
            Correct file extension (with dot), or original if detection fails
        """
        original_ext = Path(original_filename).suffix.lower()

        # Try python-magic first (most reliable)
        try:
            mime = magic.from_file(str(file_path), mime=True)

            # Map common MIME types to extensions
            mime_to_ext = {
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "image/webp": ".webp",
                "video/mp4": ".mp4",
                "video/quicktime": ".mov",
                "video/x-msvideo": ".avi",
                "video/webm": ".webm",
                "video/x-matroska": ".mkv",
            }

            detected_ext = mime_to_ext.get(mime)
            if detected_ext and detected_ext != original_ext:
                return detected_ext
            elif detected_ext:
                return original_ext  # Extension is correct

        except Exception as e:
            self.log_message(
                "EXTENSION_DETECTION_ERROR",
                f"python-magic failed for {original_filename}",
                str(e),
            )

        # Fallback: Check file signatures (magic bytes)
        try:
            with open(file_path, "rb") as f:
                header = f.read(32)

            # Check file signatures
            if header.startswith(b"\xff\xd8\xff"):
                detected_ext = ".jpg"
            elif header.startswith(b"\x89PNG"):
                detected_ext = ".png"
            elif header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
                detected_ext = ".gif"
            elif header.startswith(b"RIFF") and b"WEBP" in header[:12]:
                detected_ext = ".webp"
            elif b"\x1a\x45\xdf\xa3" in header[:32]:  # EBML/WebM/MKV signature
                # Distinguish WebM from MKV by reading more
                with open(file_path, "rb") as f:
                    more_data = f.read(4096)
                if b"webm" in more_data.lower():
                    detected_ext = ".webm"
                else:
                    detected_ext = ".mkv"
            elif (
                header[4:12] == b"ftypmp42"
                or header[4:12] == b"ftypisom"
                or header[4:8] == b"ftyp"
            ):
                detected_ext = ".mp4"
            elif header[4:8] == b"moov" or header[4:8] == b"mdat":
                detected_ext = ".mov"
            else:
                # Unknown format, keep original
                return original_ext

            if detected_ext != original_ext:
                return detected_ext

        except Exception as e:
            self.log_message(
                "EXTENSION_DETECTION_ERROR",
                f"Signature check failed for {original_filename}",
                str(e),
            )

        # Keep original extension if all detection methods failed
        return original_ext

    def parse_messages_file(
        self, messages_path: Path, conversation_name: str
    ) -> List[Dict]:
        """
        Parse messages.json file and extract messages with media
        Returns list of message dictionaries (only messages with media)
        """
        messages = []

        try:
            with open(messages_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            message_list = data.get("messages", [])

            for message in message_list:
                # Only process messages with attached files
                attached_files = message.get("attached_files", [])
                if not attached_files:
                    continue

                # Extract sender
                creator = message.get("creator", {})
                sender = creator.get("name", "Unknown")

                # Extract timestamp
                created_date = message.get("created_date")
                timestamp = self.parse_timestamp(created_date) if created_date else None

                # Extract media export names
                export_names = [
                    af.get("export_name")
                    for af in attached_files
                    if af.get("export_name")
                ]

                if export_names:
                    message_data = {
                        "sender": sender,
                        "timestamp": timestamp,
                        "timestamp_raw": created_date,
                        "conversation": conversation_name,
                        "export_names": export_names,
                    }
                    messages.append(message_data)

        except Exception as e:
            self.log_message(
                "JSON_PARSE_ERROR",
                f"Failed to parse {messages_path.name}",
                str(e),
            )
            print(f"ERROR: Failed to parse {messages_path}: {e}")

        return messages

    def _process_single_conversation(self, conversation_tuple: Tuple[str, str, Path]) -> Optional[Dict]:
        """
        Process a single conversation (used by multithreaded processing)
        
        Args:
            conversation_tuple: Tuple of (conversation_type, conversation_id, conv_dir)
            
        Returns:
            Conversation data dict or None if conversation has no media messages
        """
        conversation_type, conversation_id, conv_dir = conversation_tuple
        
        # Read group info
        group_info_path = conv_dir / "group_info.json"
        try:
            with open(group_info_path, "r", encoding="utf-8") as f:
                group_info = json.load(f)
        except Exception as e:
            self.log_message(
                "GROUP_INFO_PARSE_ERROR",
                f"Failed to parse {group_info_path}",
                str(e),
            )
            return None

        # Extract conversation name
        conversation_name = self.extract_conversation_name(
            group_info, conversation_type, conversation_id
        )

        # Parse messages
        messages_path = conv_dir / "messages.json"
        messages = self.parse_messages_file(messages_path, conversation_name)

        # Only include conversations that have messages with media
        if messages:
            with self.stats_lock:
                self.stats["total_messages_with_media"] += len(messages)
            
            conversation_data = {
                "conversation_id": conversation_id,
                "conversation_type": conversation_type,
                "conversation_name": conversation_name,
                "messages": messages,
            }
            return conversation_data
        
        return None

    def create_metadata(self) -> List[Dict]:
        """
        Main processing: scan conversations and parse JSON files
        Returns list of all conversations with messages (multithreaded)
        """
        all_conversations = []

        print("\nProcessing conversation files...")

        # Scan all conversation folders
        conversation_list = self.scan_conversations()
        logger.info(f"   Found {len(conversation_list)} conversations")

        print(f"Processing {len(conversation_list)} conversations (using {self.workers} workers)...")

        # Process conversations in parallel
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # Submit all conversation processing tasks
            future_to_conversation = {
                executor.submit(self._process_single_conversation, conv_tuple): conv_tuple[1]
                for conv_tuple in conversation_list
            }
            
            # Collect results as they complete
            for future in tqdm(as_completed(future_to_conversation), total=len(future_to_conversation), desc="Processing conversations", unit="conv"):
                conversation_id = future_to_conversation[future]
                try:
                    conversation_data = future.result()
                    if conversation_data:
                        all_conversations.append(conversation_data)
                except Exception as e:
                    self.log_message(
                        "CONVERSATION_PROCESSING_ERROR",
                        f"Failed to process conversation {conversation_id}",
                        str(e),
                    )
                    logger.error(f"Failed to process conversation {conversation_id}: {e}")

        with self.stats_lock:
            self.stats["total_conversations"] = len(all_conversations)

        print(
            f"   Processed {self.stats['total_conversations']} conversations with media"
        )
        logger.info(f"   Found {self.stats['total_messages_with_media']} messages with media")

        return all_conversations

    def copy_media_files(self, conversations: List[Dict]) -> None:
        """Copy media files referenced in metadata to output directory using conversation-scoped matching"""
        # Create output media directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        print("\nCopying media files...")

        # Track destination filenames to avoid collisions
        destination_files = {}  # maps destination_filename -> source_path

        for conversation in conversations:
            conv_id = conversation["conversation_id"]
            conv_type = conversation["conversation_type"]
            conv_folder = self.groups_dir / f"{conv_type} {conv_id}"

            # Build conversation-specific catalog
            conv_catalog = self.build_conversation_catalog(conv_folder)

            # Reset used_files tracker for this conversation
            self.used_files.clear()

            for message in conversation["messages"]:
                export_names = message.get("export_names", [])
                copied_files = []

                for export_name in export_names:
                    # Find the file using fuzzy matching in this conversation's catalog
                    result = self.find_media_file(export_name, conv_catalog)

                    if result:
                        matched_filename, source_path = result

                        # Detect actual file type and correct extension if needed
                        correct_ext = self.detect_and_correct_extension(
                            source_path, matched_filename
                        )
                        original_ext = Path(matched_filename).suffix.lower()

                        if correct_ext != original_ext:
                            # Extension mismatch detected, correct it
                            base_name = Path(matched_filename).stem
                            corrected_filename = base_name + correct_ext

                            self.log_message(
                                "EXTENSION_CORRECTED",
                                f"Fixed extension: {matched_filename} -> {corrected_filename}",
                                f"Actual format: {correct_ext}",
                            )
                            self.stats["extensions_corrected"] += 1
                            matched_filename = corrected_filename

                        # Create destination filename, handling duplicates from other conversations
                        dest_filename = matched_filename
                        dest_path = self.media_output_dir / dest_filename

                        # If this exact file already exists at destination, use it
                        if dest_filename in destination_files:
                            if destination_files[dest_filename] == source_path:
                                # Same source file already copied, just reference it
                                copied_files.append(dest_filename)
                                continue
                            else:
                                # Different source file with same name, create unique name
                                base, ext = (
                                    matched_filename.rsplit(".", 1)
                                    if "." in matched_filename
                                    else (matched_filename, "")
                                )
                                counter = 1
                                while dest_filename in destination_files:
                                    dest_filename = (
                                        f"{base}_c{counter}.{ext}"
                                        if ext
                                        else f"{base}_c{counter}"
                                    )
                                    counter += 1
                                dest_path = self.media_output_dir / dest_filename

                        try:
                            shutil.copy2(source_path, dest_path)
                            copied_files.append(dest_filename)
                            destination_files[dest_filename] = source_path
                            self.stats["media_copied"] += 1
                            # Track that this file was copied
                            self.copied_media_paths.add(str(source_path))
                        except Exception as e:
                            self.log_message(
                                "COPY_ERROR",
                                f"Failed to copy {matched_filename}",
                                str(e),
                            )
                            print(f"ERROR: Failed to copy {matched_filename}: {e}")
                    else:
                        self.log_message(
                            "MISSING_FILE",
                            f"File not found in conversation {conv_id}: {export_name}",
                        )
                        self.stats["missing_files"] += 1
                        
                        # Track orphaned metadata
                        self.failure_tracker.add_orphaned_metadata(
                            metadata_entry={
                                "conversation_id": conv_id,
                                "conversation_name": conversation["conversation_name"],
                                "export_name": export_name,
                                "message_timestamp": message.get("timestamp"),
                                "sender": message.get("sender"),
                            },
                            reason="Media file not found in filesystem",
                            context={
                                "conversation_id": conv_id,
                                "expected_filename": export_name,
                            },
                        )

                # Update message with matched filenames
                message["media_files"] = copied_files
                # Remove export_names as it's no longer needed
                del message["export_names"]

        logger.info(f"   Copied {self.stats['media_copied']} files")
        print(f"   Fuzzy matches: {self.stats['fuzzy_matches']}")
        print(f"   Missing files: {self.stats['missing_files']}")
        
        # Track orphaned media (files in filesystem that were never copied)
        print("\nScanning for orphaned media files...")
        all_media_paths = set()
        for conversation in conversations:
            conv_id = conversation["conversation_id"]
            conv_type = conversation["conversation_type"]
            conv_folder = self.groups_dir / f"{conv_type} {conv_id}"
            conv_catalog = self.build_conversation_catalog(conv_folder)
            for file_path in conv_catalog.values():
                all_media_paths.add(str(file_path))
        
        orphaned_media = all_media_paths - self.copied_media_paths
        for orphaned_path in orphaned_media:
            orphaned_path_obj = Path(orphaned_path)
            self.failure_tracker.add_orphaned_media(
                media_path=orphaned_path_obj,
                reason="No matching metadata found",
                context={
                    "original_location": str(orphaned_path),
                },
            )
        
        if orphaned_media:
            logger.info(f"   Found {len(orphaned_media)} orphaned media files")

    def save_metadata(self, conversations: List[Dict]) -> None:
        """Save cleaned metadata to metadata.json"""
        try:
            # Add export info
            output = {
                "export_info": {
                    "export_path": str(self.export_path),
                    "export_name": self.export_path.name,
                    "export_username": self.export_username,
                    "processed_date": datetime.now().isoformat(),
                    "total_conversations": self.stats["total_conversations"],
                    "total_messages_with_media": self.stats[
                        "total_messages_with_media"
                    ],
                    "total_media_files": self.stats["media_copied"],
                },
                "conversations": conversations,
            }

            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)

            print(f"\nSUCCESS: Saved metadata to {self.metadata_file}")
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
            sys.exit(1)

    def print_statistics(self) -> None:
        """Print processing statistics"""
        print("\n" + "=" * 70)
        print("PREPROCESSING STATISTICS")
        print("=" * 70)
        print(
            f"Total conversations processed:    {self.stats['total_conversations']:>6}"
        )
        print(
            f"Total messages with media:        {self.stats['total_messages_with_media']:>6}"
        )
        print(f"Total media files found:          {self.stats['total_media_files']:>6}")
        print(f"Media files copied:               {self.stats['media_copied']:>6}")
        print(f"Fuzzy matches applied:            {self.stats['fuzzy_matches']:>6}")
        print(
            f"Extensions corrected:             {self.stats['extensions_corrected']:>6}"
        )
        print(
            f"Banned files skipped:             {self.stats['banned_files_skipped']:>6}"
        )
        print(f"Missing files:                    {self.stats['missing_files']:>6}")
        print("=" * 70)

    def process(self) -> None:
        """Main processing pipeline"""
        logger.info("Starting Google Chat Messages Preprocessing")
        print(f"Export path: {self.export_path}")
        print(f"Output path: {self.output_dir}\n")

        # Validate export structure
        if not self.validate_export():
            sys.exit(1)

        # Detect export username
        print("\nDetecting export username...")
        if not self.detect_export_username():
            print("ERROR: Could not detect export username")
            sys.exit(1)

        # Count total media files for statistics
        print("\nScanning media directories...")
        self.stats["total_media_files"] = self.count_total_media_files()
        logger.info(f"   Found {self.stats['total_media_files']} media files")

        # Create metadata by parsing JSON files
        conversations = self.create_metadata()

        # Copy media files to output directory using conversation-scoped matching
        self.copy_media_files(conversations)

        # Save metadata
        self.save_metadata(conversations)

        # Save log file
        self.save_log()

        # Handle failures (copy orphaned files, generate report)
        self.failure_tracker.handle_failures(self.final_output_dir)

        # Print statistics
        self.print_statistics()

        logger.info("Preprocessing complete!")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Preprocess Google Chat messages export: organize media and create cleaned metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process in-place (creates media/ and metadata.json in export directory)
  python modules/preprocess_files.py google-username-2025-10-07

  # Process to a separate output directory
  python modules/preprocess_files.py google-username-2025-10-07 -o processed/
  
  # Process with custom number of workers
  python modules/preprocess_files.py google-username-2025-10-07 --workers 8
        """,
    )

    parser.add_argument(
        "export_directory",
        type=str,
        help="Path to Google Chat export directory (contains Google Chat/)",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Optional output directory (default: creates folders in export directory)",
        default=None,
    )

    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=None,
        help="Number of parallel workers (default: CPU count - 1)",
    )

    args = parser.parse_args()

    export_path = Path(args.export_directory)
    output_path = Path(args.output) if args.output else None

    preprocessor = GoogleChatPreprocessor(export_path, output_path, workers=args.workers)
    preprocessor.process()


if __name__ == "__main__":
    main()
