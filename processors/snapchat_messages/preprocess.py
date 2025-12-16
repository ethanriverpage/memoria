#!/usr/bin/env python3
"""
Snapchat Export Preprocessor

Organizes Snapchat export files and creates cleaned metadata:
- Moves media files to media/ folder
- Moves overlay files to overlays/ folder
- Creates metadata.json with essential information
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
import re
import sys
import argparse
from threading import Lock
import multiprocessing
import xxhash

from common.filter_banned_files import BannedFilesFilter
from common.failure_tracker import FailureTracker

# Set up logging
logger = logging.getLogger(__name__)


class SnapchatPreprocessor:
    """Preprocesses Snapchat export by organizing files and cleaning metadata"""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
        username_override: Optional[str] = None,
    ):
        self.export_path = Path(export_path)
        self.chat_media_dir = self.export_path / "chat_media"
        self.json_dir = self.export_path / "json"
        self.chat_history_file = self.json_dir / "chat_history.json"
        
        # Store username override for consolidated exports
        self.username_override = username_override

        # Output directories - use output_dir if specified, otherwise use export_path
        output_base = Path(output_dir) if output_dir else self.export_path
        self.output_dir = output_base
        self.media_dir = output_base / "media"
        self.overlays_dir = output_base / "overlays"
        self.needs_matching_dir = output_base / "needs_matching"
        self.metadata_file = output_base / "metadata.json"
        self.log_file = output_base / "preprocessing.log"

        # Final output directory for processor (for failure tracking)
        self.final_output_dir = Path(final_output_dir) if final_output_dir else output_base

        # Initialize banned files filter
        self.banned_filter = BannedFilesFilter()

        # Initialize failure tracker
        self.failure_tracker = FailureTracker(
            processor_name="Snapchat Messages",
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
        self.hash_lock = Lock()

        # Statistics
        self.stats = {
            "total_files": 0,
            "media_files": 0,
            "overlay_files": 0,
            "thumbnail_files": 0,
            "system_files": 0,
            "banned_files": 0,
            "skipped_files": 0,
            "conversations": 0,
            "messages": 0,
            "media_messages": 0,
            "duplicate_files": 0,
            "deduplicated_files": 0,
        }

        # Log entries for unmatched files
        self.log_entries = []
        
        # Track file hashes for deduplication across conversations
        # Maps hash -> {"filename": str, "messages": [Dict], "overlay_file": str}
        self.file_hashes = {}

    def log_failure(
        self, category: str, filename: str, reason: str, details: str = ""
    ) -> None:
        """Add a log entry for a file that failed to match (thread-safe)"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] {category}: {filename}"
        if reason:
            entry += f" - {reason}"
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
                f.write("Snapchat Export Preprocessing Log\n")
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

        if not self.chat_media_dir.exists():
            print(f"ERROR: chat_media directory not found: {self.chat_media_dir}")
            return False

        if not self.chat_history_file.exists():
            print(f"ERROR: chat_history.json not found: {self.chat_history_file}")
            return False

        return True

    def load_chat_history(self) -> Dict:
        """Load and parse chat_history.json"""
        try:
            with open(self.chat_history_file, "r", encoding="utf-8") as f:
                chat_history = json.load(f)

            print("SUCCESS: Loaded chat_history.json")
            return chat_history
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse chat_history.json: {e}")
            sys.exit(1)
        except Exception as e:
            print(f"ERROR: Error loading chat_history.json: {e}")
            sys.exit(1)

    def extract_username(self, chat_history: Dict) -> str:
        """Extract exporter's username from directory name or chat history"""
        # Use override if provided (for consolidated exports)
        if self.username_override:
            print(f"SUCCESS: Using username from parent directory: {self.username_override}")
            return self.username_override
        
        # Method 1: From directory name using regex to handle both date formats
        dir_name = self.export_path.name
        # Pattern: snapchat-{username}-YYYY-MM-DD or snapchat-{username}-YYYYMMDD
        match = re.match(r"snapchat-(.+?)-\d{4}-?\d{2}-?\d{2}", dir_name)
        if match:
            username = match.group(1)
            print(f"SUCCESS: Extracted username from directory: {username}")
            return username

        # Method 2: Find messages where IsSender is True
        for messages in chat_history.values():
            for message in messages:
                if message.get("IsSender", False):
                    username = message["From"]
                    print(f"SUCCESS: Extracted username from IsSender: {username}")
                    return username

        print("WARNING: Could not extract username")
        return "unknown"

    def classify_file(self, filename: str) -> str:
        """Classify file by type based on filename pattern"""
        # Skip system files
        if (
            filename.startswith(".")
            or filename.startswith("._")
            or filename.startswith("__")
        ):
            return "system"

        # Base64-encoded media (primary pattern)
        if "_b~" in filename:
            return "media"

        # UUID-based media files (with or without 'zip-')
        if "_media~zip-" in filename or "_media~" in filename:
            return "media"

        # Overlay files (with or without 'zip-')
        if "_overlay~zip-" in filename or "_overlay~" in filename:
            return "overlay"

        # Thumbnail files (with or without 'zip-')
        if "_thumbnail~zip-" in filename or "_thumbnail~" in filename:
            return "thumbnail"

        # Hash-based media (YYYY-MM-DD_<hex_hash>.ext)
        # Pattern: date prefix followed by 32-character hex string (MD5 hash)
        if re.match(
            r"^\d{4}-\d{2}-\d{2}_[a-f0-9]{32}\.(jpg|jpeg|mp4|png|webp)$",
            filename,
            re.IGNORECASE,
        ):
            return "media"

        return "unknown"

    def extract_date_from_filename(self, filename: str) -> Optional[str]:
        """Extract date from filename (YYYY-MM-DD prefix)"""
        match = re.match(r"^(\d{4}-\d{2}-\d{2})_", filename)
        if match:
            return match.group(1)
        return None

    def extract_media_id(self, filename: str) -> Optional[str]:
        """Extract media ID from b~encoded filename"""
        if "_b~" in filename:
            # Extract everything between _b~ and the file extension
            match = re.search(r"_b~([^.]+)", filename)
            if match:
                return f"b~{match.group(1)}"
        return None

    def extract_uuid(self, filename: str) -> Optional[str]:
        """Extract UUID from media~zip or overlay~zip filename"""
        match = re.search(
            r"~zip-([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})",
            filename,
            re.IGNORECASE,
        )
        if match:
            return match.group(1)
        return None

    def extract_hash(self, filename: str) -> Optional[str]:
        """Extract hash from hash-based filename (YYYY-MM-DD_<hash>.ext)"""
        match = re.match(
            r"^\d{4}-\d{2}-\d{2}_([a-f0-9]{32})\.\w+$", filename, re.IGNORECASE
        )
        if match:
            return match.group(1)
        return None

    def get_media_type(self, filename: str) -> str:
        """Determine if file is image or video based on extension - use common utility"""
        from common.utils import get_media_type as common_get_media_type
        result = common_get_media_type(filename)
        return result if result else "unknown"

    def _compute_file_hash(self, file_path: Path) -> str:
        """
        Compute xxHash64 of file for deduplication (fast, non-cryptographic)
        
        Args:
            file_path: Path to file to hash
            
        Returns:
            str: Hexadecimal hash digest
        """
        hasher = xxhash.xxh64()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):  # 64KB chunks
                hasher.update(chunk)
        return hasher.hexdigest()

    def classify_conversation(
        self, conversation_id: str, messages: List[Dict]
    ) -> Tuple[str, Optional[str]]:
        """
        Determine conversation type and title

        Returns: (conversation_type, conversation_title)
            - conversation_type: "dm" or "group"
            - conversation_title: group name or None for DMs
        """
        # Check if any message has a Conversation Title
        for message in messages:
            title = message.get("Conversation Title")
            if title is not None:
                return "group", title

        # UUID pattern indicates group chat
        uuid_pattern = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        if re.match(uuid_pattern, conversation_id):
            return "group", None

        return "dm", None

    def clean_message_metadata(
        self,
        message: Dict,
        conversation_id: str,
        conversation_type: str,
        conversation_title: Optional[str],
    ) -> Dict:
        """Extract and clean essential metadata from message"""
        cleaned = {
            "conversation_id": conversation_id,
            "conversation_type": conversation_type,
            "conversation_title": conversation_title,
            "sender": message.get("From", ""),
            "media_type": message.get("Media Type", ""),
            "created": message.get("Created", ""),
            "content": message.get("Content", ""),
            "is_sender": message.get("IsSender", False),
            "media_id": message.get("Media IDs", ""),
        }

        return cleaned

    def build_file_catalog(self) -> Dict[str, Dict]:
        """
        Scan chat_media directory and build catalog of files

        Returns: Dict mapping filename -> file metadata
        """
        catalog = {}

        import os
        with os.scandir(self.chat_media_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue

                file_path = Path(entry.path)
                self.stats["total_files"] += 1
                filename = entry.name

                # Check if file is banned (NAS system files, etc.)
                if self.banned_filter.is_banned(file_path):
                    self.stats["banned_files"] += 1
                    self.log_failure(
                        "BANNED_FILE",
                        filename,
                        "File matches banned pattern (NAS/system file)",
                        f"Patterns: {', '.join(self.banned_filter.get_patterns())}",
                    )
                    continue

                file_type = self.classify_file(filename)

                if file_type == "system":
                    self.stats["system_files"] += 1
                    continue

                if file_type == "thumbnail":
                    self.stats["thumbnail_files"] += 1
                    continue

                if file_type == "unknown":
                    self.stats["skipped_files"] += 1
                    self.log_failure(
                        "UNKNOWN_FILE", filename, "File type could not be determined"
                    )
                    print(f"WARNING: Unknown file type: {filename}")
                    continue

                # Get file modification time (key for matching media with overlays)
                mtime = file_path.stat().st_mtime

                # Extract metadata from filename
                file_metadata = {
                    "original_path": str(file_path),
                    "filename": filename,
                    "type": file_type,
                    "date": self.extract_date_from_filename(filename),
                    "extension": file_path.suffix,
                    "mtime": mtime,
                }

                if file_type == "media":
                    # Try to extract media ID, UUID, or hash
                    media_id = self.extract_media_id(filename)
                    if media_id:
                        file_metadata["media_id"] = media_id
                    else:
                        uuid = self.extract_uuid(filename)
                        if uuid:
                            file_metadata["uuid"] = uuid
                        else:
                            hash_id = self.extract_hash(filename)
                            if hash_id:
                                file_metadata["hash"] = hash_id

                elif file_type == "overlay":
                    uuid = self.extract_uuid(filename)
                    if uuid:
                        file_metadata["uuid"] = uuid

                catalog[filename] = file_metadata

                # Update stats
                if file_type == "media":
                    self.stats["media_files"] += 1
                elif file_type == "overlay":
                    self.stats["overlay_files"] += 1

        return catalog

    def build_media_overlay_maps(
        self, file_catalog: Dict
    ) -> Tuple[Dict[str, str], Dict[str, str], Dict[float, List[str]]]:
        """
        Build lookup maps for efficient media and overlay matching

        Returns: (media_id_to_filename, media_id_to_overlay, mtime_to_filenames)
            - media_id_to_filename: Maps media_id -> media filename
            - media_id_to_overlay: Maps media_id -> overlay filename (if exists)
            - mtime_to_filenames: Maps modification time -> list of filenames (media and overlay)
        """
        media_id_to_filename = {}
        media_id_to_mtime = {}
        mtime_to_filenames = {}

        for filename, file_info in file_catalog.items():
            mtime = file_info.get("mtime")
            if mtime is None:
                continue

            # Build media_id to filename mapping (for b~ encoded files)
            if file_info["type"] == "media" and "media_id" in file_info:
                media_id = file_info["media_id"]
                media_id_to_filename[media_id] = filename
                media_id_to_mtime[media_id] = mtime

            # Build mtime to filenames mapping (for all files)
            if mtime not in mtime_to_filenames:
                mtime_to_filenames[mtime] = []
            mtime_to_filenames[mtime].append((filename, file_info["type"]))

        # Build media_id to overlay mapping using mtime
        media_id_to_overlay = {}
        for media_id, mtime in media_id_to_mtime.items():
            if mtime in mtime_to_filenames:
                # Find overlay file with same mtime
                for fname, ftype in mtime_to_filenames[mtime]:
                    if ftype == "overlay":
                        media_id_to_overlay[media_id] = fname
                        break

        return media_id_to_filename, media_id_to_overlay, mtime_to_filenames

    def _deduplicate_media_messages(
        self, metadata: Dict, file_catalog: Dict
    ) -> Dict:
        """
        Detect and merge duplicate media files across conversations
        
        Args:
            metadata: Metadata dict with conversations
            file_catalog: File catalog with media file paths
            
        Returns:
            Updated metadata dict with merged duplicates
        """
        # Build map of media filename -> hash
        media_hashes = {}
        for filename, file_info in file_catalog.items():
            if file_info["type"] == "media":
                try:
                    file_path = Path(file_info["original_path"])
                    if file_path.exists():
                        file_hash = self._compute_file_hash(file_path)
                        media_hashes[filename] = file_hash
                except Exception as e:
                    logger.warning(f"Failed to compute hash for {filename}: {e}")
        
        # Track which messages belong to which hash
        # hash -> list of (conv_id, message_index, message_dict)
        hash_to_messages = {}
        
        # Scan all messages and group by media file hash
        for conv_id, conv_data in metadata["conversations"].items():
            messages = conv_data.get("messages", [])
            for idx, message in enumerate(messages):
                # Get media file(s) from message
                media_files = []
                if "media_file" in message:
                    media_files = [message["media_file"]]
                elif "media_files" in message:
                    media_files = message["media_files"]
                
                # For each media file, look up its hash
                for media_file in media_files:
                    if media_file in media_hashes:
                        file_hash = media_hashes[media_file]
                        if file_hash not in hash_to_messages:
                            hash_to_messages[file_hash] = []
                        hash_to_messages[file_hash].append((conv_id, idx, message.copy()))
        
        # Scan orphaned media as well
        orphaned_media = metadata.get("orphaned_media", [])
        for idx, message in enumerate(orphaned_media):
            media_file = message.get("media_file")
            if media_file and media_file in media_hashes:
                file_hash = media_hashes[media_file]
                if file_hash not in hash_to_messages:
                    hash_to_messages[file_hash] = []
                hash_to_messages[file_hash].append((None, idx, message.copy()))
        
        # Identify duplicates (hash with multiple messages)
        duplicates_found = 0
        messages_to_remove = {}  # conv_id -> list of message indices to remove
        orphaned_to_remove = []  # list of orphaned media indices to remove
        
        for file_hash, message_list in hash_to_messages.items():
            if len(message_list) > 1:
                # Found duplicate! Merge messages
                duplicates_found += 1
                
                # Sort by timestamp to find oldest
                sorted_messages = sorted(
                    message_list,
                    key=lambda x: x[2].get("created", "9999-99-99 99:99:99 UTC")
                )
                
                # Use oldest message as base
                oldest_conv_id, oldest_idx, oldest_msg = sorted_messages[0]
                
                # Get the media file from oldest message
                if "media_file" in oldest_msg:
                    primary_media = oldest_msg["media_file"]
                elif "media_files" in oldest_msg and oldest_msg["media_files"]:
                    primary_media = oldest_msg["media_files"][0]
                else:
                    continue  # Skip if no media file
                
                # Get overlay from oldest message (if exists)
                primary_overlay = oldest_msg.get("overlay_file")
                
                # Create merged message structure
                merged_message = {
                    "media_file": primary_media,
                    "messages": [],
                    "primary_created": oldest_msg.get("created"),
                    "is_duplicate": True,
                }
                
                if primary_overlay:
                    merged_message["overlay_file"] = primary_overlay
                
                # Add all message metadata to the messages array
                for conv_id, msg_idx, msg in sorted_messages:
                    merged_message["messages"].append({
                        "conversation_id": msg.get("conversation_id"),
                        "conversation_type": msg.get("conversation_type"),
                        "conversation_title": msg.get("conversation_title"),
                        "sender": msg.get("sender"),
                        "created": msg.get("created"),
                        "content": msg.get("content", ""),
                        "is_sender": msg.get("is_sender"),
                        "media_id": msg.get("media_id", ""),
                    })
                    
                    # Mark for removal (except the oldest one)
                    if conv_id != oldest_conv_id or msg_idx != oldest_idx:
                        if conv_id is not None:
                            if conv_id not in messages_to_remove:
                                messages_to_remove[conv_id] = []
                            messages_to_remove[conv_id].append(msg_idx)
                        else:
                            orphaned_to_remove.append(msg_idx)
                
                # Replace oldest message with merged version
                if oldest_conv_id is not None:
                    conv_messages = metadata["conversations"][oldest_conv_id]["messages"]
                    conv_messages[oldest_idx] = merged_message
                else:
                    orphaned_media[oldest_idx] = merged_message
                
                # Log the deduplication
                conv_list = [m.get("conversation_id") for m in merged_message["messages"]]
                self.log_failure(
                    "DEDUPLICATED",
                    primary_media,
                    f"Found in {len(message_list)} conversations",
                    f"Hash: {file_hash[:16]}..., Conversations: {', '.join(filter(None, conv_list))}"
                )
        
        # Remove duplicate message entries (in reverse order to maintain indices)
        for conv_id, indices in messages_to_remove.items():
            conv_messages = metadata["conversations"][conv_id]["messages"]
            for idx in sorted(indices, reverse=True):
                del conv_messages[idx]
            # Update message count
            metadata["conversations"][conv_id]["message_count"] = len(conv_messages)
        
        # Remove from orphaned media
        for idx in sorted(orphaned_to_remove, reverse=True):
            del orphaned_media[idx]
        
        # Update statistics
        with self.stats_lock:
            self.stats["duplicate_files"] = duplicates_found
            self.stats["deduplicated_files"] = sum(
                len(msgs) - 1 for msgs in hash_to_messages.values() if len(msgs) > 1
            )
        
        return metadata

    def create_metadata(self, chat_history: Dict, file_catalog: Dict) -> Tuple[Dict, List]:
        """
        Create cleaned metadata structure

        Returns: Dict with conversations, messages, and file mappings
        """
        metadata = {
            "export_info": {
                "export_path": str(self.export_path),
                "export_username": self.extract_username(chat_history),
                "processed_date": datetime.now().isoformat(),
            },
            "conversations": {},
            "orphaned_media": [],
        }

        # Build lookup maps for efficient media and overlay matching
        media_id_to_filename, _, mtime_to_filenames = self.build_media_overlay_maps(
            file_catalog
        )

        # Build timestamp-based lookup for UUID media files (for matching by timestamp)
        timestamp_to_uuid_files = {}
        for filename, file_info in file_catalog.items():
            if file_info["type"] == "media" and "media_id" not in file_info:
                # This is a UUID-based media file (no media_id in filename)
                mtime = file_info.get("mtime")
                if mtime:
                    if mtime not in timestamp_to_uuid_files:
                        timestamp_to_uuid_files[mtime] = []
                    timestamp_to_uuid_files[mtime].append(filename)

        # Track which media files have been matched to messages
        matched_media_files = set()
        matched_overlays = set()  # Changed to set to track which overlays are used

        # Track ambiguous cases for needs_matching folder
        ambiguous_cases = []

        # Process each conversation
        for conversation_id, messages in chat_history.items():
            self.stats["conversations"] += 1

            conversation_type, conversation_title = self.classify_conversation(
                conversation_id, messages
            )

            cleaned_messages = []

            for message in messages:
                self.stats["messages"] += 1

                # Skip TEXT messages - only keep media messages
                if message.get("Media Type") == "TEXT":
                    continue

                cleaned_msg = self.clean_message_metadata(
                    message, conversation_id, conversation_type, conversation_title
                )

                # Find and add media file references
                # Note: Media IDs can be multiple, separated by " | "
                media_ids_str = message.get("Media IDs", "")
                if media_ids_str:
                    # Split by " | " to handle multiple media IDs
                    media_ids = [mid.strip() for mid in media_ids_str.split("|")]

                    media_files = []
                    orphaned_media_ids = []  # Track media IDs that couldn't be matched

                    # Phase 1: Match by media_id (existing logic)
                    for media_id in media_ids:
                        if not media_id:
                            continue

                        # Add media filename if found by media_id
                        if media_id in media_id_to_filename:
                            media_file = media_id_to_filename[media_id]
                            media_files.append(media_file)
                            matched_media_files.add(media_file)
                        else:
                            # Track media_id that wasn't found
                            orphaned_media_ids.append(media_id)

                    # Phase 2: Try to match UUID files by timestamp
                    # Calculate message timestamp
                    try:
                        date_str = message.get("Created", "")
                        if date_str:
                            # Parse timestamp: "YYYY-MM-DD HH:MM:SS UTC"
                            # Remove " UTC" suffix and parse as naive datetime
                            date_str_no_tz = date_str.replace(" UTC", "")
                            message_dt = datetime.strptime(
                                date_str_no_tz, "%Y-%m-%d %H:%M:%S"
                            )
                            # Treat as UTC and convert to timestamp
                            message_timestamp = message_dt.replace(
                                tzinfo=timezone.utc
                            ).timestamp()

                            # Check for unmatched UUID files with this timestamp
                            if message_timestamp in timestamp_to_uuid_files:
                                uuid_files_at_timestamp = timestamp_to_uuid_files[
                                    message_timestamp
                                ]
                                # Only match if we haven't matched all media_ids yet
                                unmatched_count = len(media_ids) - len(media_files)
                                if unmatched_count > 0:
                                    for uuid_file in uuid_files_at_timestamp:
                                        if uuid_file not in matched_media_files:
                                            media_files.append(uuid_file)
                                            matched_media_files.add(uuid_file)
                                            unmatched_count -= 1
                                            if unmatched_count == 0:
                                                break
                    except (ValueError, TypeError):
                        # Failed to parse timestamp, skip UUID matching
                        pass

                    # Re-evaluate orphaned media_ids after timestamp matching
                    # If we matched enough files via timestamp, clear false positives
                    if len(media_files) >= len(media_ids):
                        # We found at least as many files as media_ids
                        # Clear orphaned list - these were matched by timestamp
                        orphaned_media_ids = []
                    elif len(media_files) > 0 and len(orphaned_media_ids) > 0:
                        # Partial match - only keep truly unmatched media_ids
                        # We matched some files, so reduce orphaned count accordingly
                        matched_count = len(media_files)
                        expected_count = len(media_ids)
                        truly_unmatched = expected_count - matched_count
                        if truly_unmatched < len(orphaned_media_ids):
                            # Keep only the number of orphaned_media_ids that are truly unmatched
                            orphaned_media_ids = orphaned_media_ids[:truly_unmatched]

                    # Phase 3: Smart overlay matching (only for unambiguous cases)
                    overlay_files = []
                    if media_files:
                        # Get message timestamp for overlay lookup
                        try:
                            date_str = message.get("Created", "")
                            if date_str:
                                # Parse timestamp as UTC
                                date_str_no_tz = date_str.replace(" UTC", "")
                                message_dt = datetime.strptime(
                                    date_str_no_tz, "%Y-%m-%d %H:%M:%S"
                                )
                                message_timestamp = message_dt.replace(
                                    tzinfo=timezone.utc
                                ).timestamp()

                                # Find all overlays with this timestamp
                                overlays_at_timestamp = []
                                if message_timestamp in mtime_to_filenames:
                                    for fname, ftype in mtime_to_filenames[
                                        message_timestamp
                                    ]:
                                        if (
                                            ftype == "overlay"
                                            and fname not in matched_overlays
                                        ):
                                            overlays_at_timestamp.append(fname)

                                # Determine if we can safely match overlays
                                # Only count VIDEO files for overlay matching (images never have overlays)
                                video_count = len([mf for mf in media_files if self.get_media_type(mf) == "video"])
                                media_count = len(media_files)
                                overlay_count = len(overlays_at_timestamp)

                                if video_count == 1 and overlay_count == 1:
                                    # SAFE: Unique 1:1 match (1 video, 1 overlay)
                                    overlay_files.append(overlays_at_timestamp[0])
                                    matched_overlays.add(overlays_at_timestamp[0])
                                elif overlay_count > 0 and (video_count > 1 or overlay_count > 1):
                                    # AMBIGUOUS: Skip overlay matching and save for manual review
                                    ambiguous_case = {
                                        "timestamp": date_str,
                                        "message_metadata": {
                                            "conversation_id": conversation_id,
                                            "conversation_type": conversation_type,
                                            "conversation_title": conversation_title,
                                            "sender": message.get("From", ""),
                                            "content": message.get("Content", ""),
                                            "is_sender": message.get("IsSender", False),
                                            "created": message.get("Created", ""),
                                        },
                                        "media_files": [],
                                        "overlays": [],
                                    }

                                    # Add media file details (only videos, since images never have overlays)
                                    for media_file in media_files:
                                        file_info = file_catalog.get(media_file, {})
                                        media_type = self.get_media_type(media_file)
                                        # Only include videos in ambiguous cases (images are processed normally)
                                        if media_type == "video":
                                            ambiguous_case["media_files"].append(
                                                {
                                                    "filename": media_file,
                                                    "media_id": file_info.get("media_id"),
                                                    "uuid": file_info.get("uuid"),
                                                    "type": media_type,
                                                    "extension": Path(media_file).suffix,
                                                }
                                            )

                                    # Add overlay details
                                    for overlay_file in overlays_at_timestamp:
                                        ambiguous_case["overlays"].append(
                                            {
                                                "filename": overlay_file,
                                                "uuid": self.extract_uuid(overlay_file),
                                            }
                                        )

                                    # Add analysis/hints
                                    # Calculate counts from original media_files (not ambiguous_case which only has videos)
                                    image_count = sum(
                                        1
                                        for mf in media_files
                                        if self.get_media_type(mf) == "image"
                                    )
                                    # video_count was already calculated earlier for the ambiguity check

                                    hint = f"{image_count} images + {video_count} videos, but only {overlay_count} overlays."
                                    if video_count == overlay_count and image_count > 0:
                                        hint += " Images don't need overlays and are processed normally."
                                    elif overlay_count == 0:
                                        hint += " Videos can exist without overlays."

                                    ambiguous_case["analysis"] = {
                                        "media_count": media_count,
                                        "overlay_count": overlay_count,
                                        "images": image_count,
                                        "videos": video_count,
                                        "hint": hint,
                                    }

                                    ambiguous_cases.append(ambiguous_case)

                                    self.log_failure(
                                        "AMBIGUOUS_OVERLAY",
                                        f"{video_count} videos at {date_str}",
                                        "Cannot determine correct overlay match",
                                        f"{overlay_count} overlays available",
                                    )
                        except (ValueError, TypeError):
                            # Failed to parse timestamp, skip overlay matching
                            pass

                    # Store as array if multiple, or single value for backwards compatibility
                    if len(media_files) == 1:
                        cleaned_msg["media_file"] = media_files[0]
                    elif len(media_files) > 1:
                        cleaned_msg["media_files"] = media_files

                    if len(overlay_files) == 1:
                        cleaned_msg["overlay_file"] = overlay_files[0]
                    elif len(overlay_files) > 1:
                        cleaned_msg["overlay_files"] = overlay_files
                    
                    # Track orphaned metadata (media IDs referenced but files not found)
                    # Only track if still orphaned after all matching phases
                    if orphaned_media_ids:
                        for media_id in orphaned_media_ids:
                            self.failure_tracker.add_orphaned_metadata(
                                metadata_entry={
                                    "conversation_id": conversation_id,
                                    "conversation_type": conversation_type,
                                    "conversation_title": conversation_title,
                                    "message": cleaned_msg,
                                    "media_id": media_id,
                                },
                                reason=f"Media ID '{media_id}' referenced in message but file not found (after all matching attempts)",
                                context={
                                    "timestamp": message.get("Created", ""),
                                    "sender": message.get("From", ""),
                                    "content": message.get("Content", ""),
                                    "matched_files_count": len(media_files),
                                    "expected_files_count": len(media_ids),
                                    "matching_phases_completed": "media_id + timestamp",
                                }
                            )

                cleaned_messages.append(cleaned_msg)

                # Track media messages
                if message.get("Media Type") in ["MEDIA", "IMAGE", "VIDEO"]:
                    self.stats["media_messages"] += 1

            # Only store conversation if it has media messages
            if cleaned_messages:
                metadata["conversations"][conversation_id] = {
                    "type": conversation_type,
                    "title": conversation_title,
                    "message_count": len(cleaned_messages),
                    "messages": cleaned_messages,
                }

        # Create entries for orphaned media files (not matched to any message)
        orphaned_count = 0
        for filename, file_info in file_catalog.items():
            if file_info["type"] == "media" and filename not in matched_media_files:
                # Use filesystem mtime for created date (format to match conversation messages)
                created_date = None
                if "mtime" in file_info:
                    created_date = datetime.fromtimestamp(file_info["mtime"]).strftime(
                        "%Y-%m-%d %H:%M:%S UTC"
                    )
                else:
                    created_date = file_info.get("date")

                orphaned_entry = {
                    "conversation_id": None,
                    "conversation_type": None,
                    "conversation_title": None,
                    "sender": None,
                    "media_type": "MEDIA",
                    "created": created_date,
                    "content": "",
                    "is_sender": None,
                    "media_id": file_info.get("media_id", ""),
                    "media_file": filename,
                }

                # Try to find matching overlay using mtime (only if unambiguous)
                mtime = file_info.get("mtime")
                overlay_found = False
                if mtime and mtime in mtime_to_filenames:
                    # Count available overlays at this timestamp
                    available_overlays = [
                        fname
                        for fname, ftype in mtime_to_filenames[mtime]
                        if ftype == "overlay" and fname not in matched_overlays
                    ]

                    # Only match if there's exactly 1 unmatched overlay
                    if len(available_overlays) == 1:
                        orphaned_entry["overlay_file"] = available_overlays[0]
                        matched_overlays.add(available_overlays[0])
                        overlay_found = True

                # Log the orphaned media file
                media_id = file_info.get("media_id", "N/A")
                overlay_status = "with overlay" if overlay_found else "no overlay"
                self.log_failure(
                    "ORPHANED_MEDIA",
                    filename,
                    "Not matched to any message",
                    f"media_id={media_id}, {overlay_status}",
                )

                metadata["orphaned_media"].append(orphaned_entry)
                orphaned_count += 1
                
                # Track with FailureTracker
                media_path = Path(file_info["original_path"])
                self.failure_tracker.add_orphaned_media(
                    media_path=media_path,
                    reason="Not matched to any message in chat_history.json",
                    context={
                        "media_id": media_id,
                        "overlay_status": overlay_status,
                        "date": file_info.get("date"),
                        "mtime": file_info.get("mtime"),
                        "has_overlay": overlay_found,
                    }
                )

        # Store statistics
        self.stats["matched_overlays"] = len(matched_overlays)
        self.stats["orphaned_media"] = orphaned_count
        self.stats["ambiguous_cases"] = len(ambiguous_cases)

        # Deduplicate media files across conversations
        metadata = self._deduplicate_media_messages(metadata, file_catalog)

        return metadata, ambiguous_cases

    def organize_files(self, file_catalog: Dict) -> None:
        """Copy files to appropriate directories"""
        # Create output base directory if it doesn't exist
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Create output directories
        self.media_dir.mkdir(exist_ok=True)
        self.overlays_dir.mkdir(exist_ok=True)

        print("\nOrganizing files...")
        print(f"   Output directory: {self.output_dir}")

        for filename, file_info in file_catalog.items():
            source = Path(file_info["original_path"])

            if not source.exists():
                self.log_failure(
                    "FILE_NOT_FOUND",
                    filename,
                    "File missing at source",
                    f"expected at {source}",
                )
                print(f"WARNING: File not found: {source}")
                continue

            # Determine destination
            if file_info["type"] == "media":
                destination = self.media_dir / filename
            elif file_info["type"] == "overlay":
                destination = self.overlays_dir / filename
            else:
                continue

            try:
                shutil.copy2(str(source), str(destination))
                # Update path in catalog
                file_info["new_path"] = str(destination)
            except Exception as e:
                self.log_failure(
                    "COPY_FAILED",
                    filename,
                    f"Failed to copy file: {e}",
                    f"source={source}, dest={destination}",
                )
                print(f"ERROR: Failed to copy {filename}: {e}")

    def organize_ambiguous_files(
        self, ambiguous_cases: List[Dict], export_username: str
    ) -> None:
        """Organize ambiguous matching cases into needs_matching/ directory"""
        if not ambiguous_cases:
            return

        self.needs_matching_dir.mkdir(exist_ok=True)

        print(
            f"\nOrganizing {len(ambiguous_cases)} ambiguous cases for manual matching..."
        )

        for case in ambiguous_cases:
            # Create timestamped subfolder (sanitized)
            timestamp = case["timestamp"].replace(" ", "_").replace(":", "-")
            case_dir = self.needs_matching_dir / timestamp
            case_dir.mkdir(exist_ok=True)

            # Create media and overlays subdirectories
            media_subdir = case_dir / "media"
            overlay_subdir = case_dir / "overlays"
            media_subdir.mkdir(exist_ok=True)
            overlay_subdir.mkdir(exist_ok=True)

            # Copy media files
            for media_info in case["media_files"]:
                filename = media_info["filename"]
                source = self.media_dir / filename
                dest = media_subdir / filename
                if source.exists():
                    try:
                        shutil.copy2(source, dest)
                    except Exception as e:
                        print(f"WARNING: Failed to copy {filename}: {e}")
                else:
                    print(f"WARNING: Source file not found: {source}")

            # Copy overlay files
            for overlay_info in case["overlays"]:
                filename = overlay_info["filename"]
                source = self.overlays_dir / filename
                dest = overlay_subdir / filename
                if source.exists():
                    try:
                        shutil.copy2(source, dest)
                    except Exception as e:
                        print(f"WARNING: Failed to copy {filename}: {e}")
                else:
                    print(f"WARNING: Source overlay not found: {source}")

            # Add export username to case
            case["export_username"] = export_username

            # Write match_info.json
            match_info_path = case_dir / "match_info.json"
            try:
                with open(match_info_path, "w", encoding="utf-8") as f:
                    json.dump(case, f, indent=2, ensure_ascii=False)
            except Exception as e:
                print(f"ERROR: Failed to write {match_info_path}: {e}")

        print(
            f"SUCCESS: Created {len(ambiguous_cases)} folders in {self.needs_matching_dir}"
        )

    def save_metadata(self, metadata: Dict) -> None:
        """Save cleaned metadata to metadata.json"""
        try:
            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

            logger.info(f"SUCCESS: Saved metadata to {self.metadata_file}")
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
            sys.exit(1)

    def print_statistics(self) -> None:
        """Print processing statistics"""
        print("\n" + "=" * 60)
        print("PREPROCESSING STATISTICS")
        print("=" * 60)
        print(f"Total files scanned:        {self.stats['total_files']:>6}")
        print(f"  Media files:              {self.stats['media_files']:>6}")
        print(f"  Overlay files:            {self.stats['overlay_files']:>6}")
        print(f"  Matched overlays:         {self.stats.get('matched_overlays', 0):>6}")
        print(f"  Thumbnail files (skipped):{self.stats['thumbnail_files']:>6}")
        print(f"  System files (skipped):   {self.stats['system_files']:>6}")
        print(f"  Banned files (skipped):   {self.stats['banned_files']:>6}")
        print(f"  Unknown/skipped:          {self.stats['skipped_files']:>6}")
        print()
        print(f"Conversations:              {self.stats['conversations']:>6}")
        print(f"Total messages:             {self.stats['messages']:>6}")
        print(f"Media messages:             {self.stats['media_messages']:>6}")
        print(f"Orphaned media files:       {self.stats.get('orphaned_media', 0):>6}")
        print(f"Ambiguous cases (manual):   {self.stats.get('ambiguous_cases', 0):>6}")
        print()
        print(f"Duplicate media detected:   {self.stats.get('duplicate_files', 0):>6}")
        print(f"Messages consolidated:      {self.stats.get('deduplicated_files', 0):>6}")
        print("=" * 60)

    def process(self) -> None:
        """Main processing pipeline"""
        logger.info("Starting Snapchat Export Preprocessing")
        print(f"Export path: {self.export_path}")
        print(f"Output path: {self.output_dir}\n")

        # Validate export structure
        if not self.validate_export():
            sys.exit(1)

        # Load chat history
        chat_history = self.load_chat_history()

        # Build file catalog
        print("\nScanning chat_media directory...")
        file_catalog = self.build_file_catalog()

        # Create cleaned metadata
        print("\nProcessing metadata...")
        metadata, ambiguous_cases = self.create_metadata(chat_history, file_catalog)

        # Organize files
        self.organize_files(file_catalog)

        # Organize ambiguous cases for manual matching
        export_username = metadata.get("export_info", {}).get(
            "export_username", "unknown"
        )
        self.organize_ambiguous_files(ambiguous_cases, export_username)

        # Save metadata
        self.save_metadata(metadata)

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
        description="Preprocess Snapchat export: organize media and create cleaned metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process in-place (creates media/ and overlays/ in export directory)
  python preprocess_files.py snapchat-username-2025-10-17

  # Process to a separate output directory
  python preprocess_files.py snapchat-username-2025-10-17 -o snapchat-username-2025-10-17_media/
  
  # Process with custom number of workers
  python preprocess_files.py snapchat-username-2025-10-17 --workers 8
        """,
    )

    parser.add_argument(
        "export_directory",
        type=str,
        help="Path to Snapchat export directory (contains chat_media/ and json/)",
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

    preprocessor = SnapchatPreprocessor(export_path, output_path, workers=args.workers)
    preprocessor.process()


if __name__ == "__main__":
    main()
