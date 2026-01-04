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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from collections import defaultdict

from common.detection import resolve_username, detect_from_dirname, DetectionResult
from common.files.scanner import iter_subdirs, iter_files
from common.files.types import ALL_MEDIA_EXTENSIONS
from common.output.logging import get_processor_logger
from common.output.progress import PHASE_PREPROCESS
from common.output.summary import print_preprocessing_stats
from common.processing import process_parallel_threaded
from processors.preprocess_base import CopyTask, HashResult, PreprocessorBase

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class GoogleChatPreprocessor(PreprocessorBase):
    """Preprocesses Google Chat message export by organizing files and cleaning metadata"""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        threads: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        super().__init__(
            processor_name="Google Chat",
            export_path=export_path,
            output_dir=output_dir,
            threads=threads,
            final_output_dir=final_output_dir,
        )

        self.chat_dir = self.export_path / "Google Chat"
        self.groups_dir = self.chat_dir / "Groups"
        self.users_dir = self.chat_dir / "Users"

        # Export username (detected from user_info.json)
        self.export_username = None
        # Export identity for DM partner detection
        self.export_email: Optional[str] = None
        self.export_display_name: Optional[str] = None

        # Statistics specific to this preprocessor (preserves shared keys from base)
        self.stats.update(
            {
                "total_conversations": 0,
                "total_messages_with_media": 0,
                "total_media_files": 0,
                "media_copied": 0,
                "missing_files": 0,
                "fuzzy_matches": 0,
                "banned_files_skipped": 0,
            }
        )

        # Track used files for duplicate handling
        self.used_files = defaultdict(int)

        # Track which media files were copied (for orphaned media detection)
        self.copied_media_paths = set()

    def validate_export(self) -> bool:
        """Validate that export directory has required structure."""
        if not self.require_exists(self.export_path, label="Export path"):
            return False

        if not self.require_exists(self.chat_dir, label="Google Chat directory"):
            return False

        if not self.require_exists(self.groups_dir, label="Groups directory"):
            return False

        # Check if at least one conversation folder exists
        if not self.require_any_dirs(self.groups_dir, label="conversation"):
            return False

        return True

    def detect_username(self) -> DetectionResult:
        """Detect export username using available sources.

        Priority:
        1. Profile/Profile.json - Google account profile
        2. Google Chat user_info.json - chat-specific user info
        3. Directory name pattern (google-username-YYYYMMDD)

        Returns:
            DetectionResult with username and source
        """

        def detect_from_profile_json(path: Path) -> Optional[str]:
            """Extract username from Profile/Profile.json."""
            profile_path = path / "Profile" / "Profile.json"
            if not profile_path.exists():
                return None
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Get first email and strip @gmail.com
                emails = data.get("emails", [])
                if emails:
                    email = emails[0].get("value", "")
                    if "@gmail.com" in email:
                        return email.replace("@gmail.com", "")
                    # For non-gmail, use the part before @
                    if "@" in email:
                        return email.split("@")[0]
                return None
            except Exception:
                return None

        def detect_from_user_info_json(path: Path) -> Optional[str]:
            """Extract username from Google Chat user_info.json."""
            users_dir = path / "Google Chat" / "Users"
            if not users_dir.exists():
                return None
            try:
                user_dirs = [d for d in users_dir.iterdir() if d.is_dir()]
                if not user_dirs:
                    return None
                user_info_path = user_dirs[0] / "user_info.json"
                if not user_info_path.exists():
                    return None
                with open(user_info_path, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
                email = user_data.get("user", {}).get("email")
                if email:
                    return email.replace("@gmail.com", "")
                return None
            except Exception:
                return None

        def detect_from_dirname_google(path: Path) -> Optional[str]:
            """Extract username from directory name."""
            return detect_from_dirname(path, "google")

        return resolve_username(
            self.export_path,
            [
                ("Profile.json", detect_from_profile_json),
                ("user_info.json", detect_from_user_info_json),
                ("dirname", detect_from_dirname_google),
            ],
        )

    def detect_export_identity(self) -> Tuple[Optional[str], Optional[str]]:
        """Detect export user's email and display name for DM partner identification.

        Detection sources (in priority order):
        1. Profile/Profile.json - Google account profile (email + displayName)
        2. Google Chat/Users/*/user_info.json - chat-specific user info (email + name)

        Returns:
            Tuple of (export_email, export_display_name), either may be None
        """
        export_email: Optional[str] = None
        export_display_name: Optional[str] = None

        # Try Profile/Profile.json first
        profile_path = self.export_path / "Profile" / "Profile.json"
        if profile_path.exists():
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Get first email
                emails = data.get("emails", [])
                if emails:
                    export_email = emails[0].get("value", "")
                # Get display name
                export_display_name = data.get("displayName", "")
            except Exception as e:
                logger.debug("Failed to parse Profile.json for identity: %s", e)

        # Try Google Chat/Users/*/user_info.json as fallback
        if not export_email:
            users_dir = self.export_path / "Google Chat" / "Users"
            if users_dir.exists():
                try:
                    user_dirs = [d for d in users_dir.iterdir() if d.is_dir()]
                    if user_dirs:
                        user_info_path = user_dirs[0] / "user_info.json"
                        if user_info_path.exists():
                            with open(user_info_path, "r", encoding="utf-8") as f:
                                user_data = json.load(f)
                            user_obj = user_data.get("user", {})
                            if not export_email:
                                export_email = user_obj.get("email", "")
                            if not export_display_name:
                                export_display_name = user_obj.get("name", "")
                except Exception as e:
                    logger.debug("Failed to parse user_info.json for identity: %s", e)

        # Normalize empty strings to None
        if not export_email:
            export_email = None
        if not export_display_name:
            export_display_name = None

        if export_email:
            logger.info("Export identity detected: email=%s", export_email)
        else:
            logger.warning("Could not detect export email for DM partner identification")

        return (export_email, export_display_name)

    def _normalize_name(self, name: str) -> str:
        """Normalize a display name for comparison (lowercase, strip whitespace)."""
        return name.lower().strip() if name else ""

    def extract_conversation_name(
        self, group_info: Dict, conversation_type: str, conversation_id: str
    ) -> str:
        """
        Extract conversation name from group_info
        For DMs: "DM with <other participant>" (exactly 2 members expected)
        For Spaces: group name or constructed from members
        Returns the ORIGINAL name with spaces intact (sanitization happens at filename creation time)
        """
        members = group_info.get("members", [])

        if conversation_type == "DM":
            # DMs should have exactly 2 members (export user + other)
            if len(members) != 2:
                self.log_message(
                    "CONVERSATION_NAME_ERROR",
                    f"Unexpected DM member count ({len(members)}) for {conversation_id}",
                )
                return conversation_id

            # Find the member who isn't the export user
            other_member = None

            if self.export_email:
                # Prefer email-based identification (most reliable)
                # Verify export email matches one member, then pick the other
                export_member_found = False
                for member in members:
                    member_email = member.get("email", "")
                    if member_email == self.export_email:
                        export_member_found = True
                    elif member_email:
                        other_member = member
                # Only use result if we found the export user
                if not export_member_found:
                    other_member = None
            elif self.export_display_name:
                # Fall back to display name matching if email unavailable
                # Verify export display name matches one member, then pick the other
                export_name_normalized = self._normalize_name(self.export_display_name)
                export_member_found = False
                for member in members:
                    member_name = member.get("name", "")
                    if self._normalize_name(member_name) == export_name_normalized:
                        export_member_found = True
                    else:
                        other_member = member
                # Only use result if we found the export user
                if not export_member_found:
                    other_member = None

            if other_member is None:
                # Could not reliably identify which member is the export user
                self.log_message(
                    "CONVERSATION_NAME_ERROR",
                    f"Could not identify export user to determine DM partner for {conversation_id}",
                )
                return conversation_id

            other_name = other_member.get("name", "")
            if not other_name:
                self.log_message(
                    "CONVERSATION_NAME_ERROR",
                    f"DM partner has no name for {conversation_id}",
                )
                return conversation_id

            return f"DM with {other_name}"

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
        Convert timestamp string to canonical ISO 8601 format.
        Input format: "Wednesday, May 4, 2016 at 4:20:19 AM UTC"
        Output format: "2016-05-04T04:20:19Z"
        """
        from common.text.parse import CANONICAL_TIMESTAMP_FORMAT

        try:
            # Parse the timestamp
            dt = datetime.strptime(timestamp_str, "%A, %B %d, %Y at %I:%M:%S %p %Z")
            return dt.strftime(CANONICAL_TIMESTAMP_FORMAT)
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

    @staticmethod
    def _strip_copy_suffix(filename: str) -> str:
        """
        Strip filesystem copy suffixes like "(1)" right before the extension.

        Example:
            "File-abc(1).jpg" -> "File-abc.jpg"
        """
        try:
            p = Path(filename)
            stem = p.stem
            suffix = p.suffix
            # Only strip if the stem ends with "(digits)"
            match = re.search(r"\(\d+\)$", stem)
            if not match:
                return filename
            return stem[: match.start()] + suffix
        except Exception:
            return filename

    @staticmethod
    def _copy_suffix_index(filename: str) -> Optional[int]:
        """
        Return copy-suffix index if filename ends with "(N)" before extension.

        Examples:
            "a.jpg" -> None
            "a(1).jpg" -> 1
            "a(12).jpg" -> 12
        """
        try:
            stem = Path(filename).stem
            match = re.search(r"\((\d+)\)$", stem)
            if not match:
                return None
            return int(match.group(1))
        except Exception:
            return None

    def _allocate_copy_variant(
        self, canonical_filename: str, file_catalog: Dict[str, Path]
    ) -> Optional[Tuple[str, Path]]:
        """
        Allocate the correct on-disk variant for a canonical filename across multiple
        references in message order:
          1st reference -> canonical file (no suffix) if present, else first available variant
          2nd reference -> "(1)" variant
          3rd reference -> "(2)" variant
          ...

        Uses self.used_files[canonical_filename] as the counter (canonical key), and also
        marks the chosen actual filename as used to avoid reusing a file via other fuzzy paths.
        """
        use_count = self.used_files[canonical_filename]

        # Prefer exact canonical file for first use if it exists and is unused.
        if use_count == 0 and canonical_filename in file_catalog:
            if self.used_files[canonical_filename] == 0:
                self.used_files[canonical_filename] += 1
                return (canonical_filename, file_catalog[canonical_filename])

        # Next, try the explicit numbered variant for this use_count (e.g. (1), (2), ...)
        base, ext = (
            canonical_filename.rsplit(".", 1)
            if "." in canonical_filename
            else (canonical_filename, "")
        )
        numbered_name = f"{base}({use_count}).{ext}" if ext else f"{base}({use_count})"
        if numbered_name in file_catalog and self.used_files[numbered_name] == 0:
            self.used_files[canonical_filename] += 1
            self.used_files[numbered_name] += 1
            return (numbered_name, file_catalog[numbered_name])

        # Fallback: choose the next unused variant with the same stripped base, ordered by suffix index.
        family = [
            fn
            for fn in file_catalog.keys()
            if self._strip_copy_suffix(fn) == canonical_filename
        ]
        if not family:
            return None

        def sort_key(fn: str) -> tuple:
            idx = self._copy_suffix_index(fn)
            # None means "no suffix" -> treat as 0 and sort first
            return (idx is not None, idx or 0)

        for fn in sorted(family, key=sort_key):
            if self.used_files[fn] == 0:
                self.used_files[canonical_filename] += 1
                self.used_files[fn] += 1
                return (fn, file_catalog[fn])

        return None

    @staticmethod
    def _common_prefix_len(a: str, b: str) -> int:
        """Return the length of the common prefix between two strings."""
        i = 0
        limit = min(len(a), len(b))
        while i < limit and a[i] == b[i]:
            i += 1
        return i

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

            # Try common-prefix matching for long "File-..." names where only the tail differs.
            # This catches cases where the export_name includes additional tail tokens
            # (e.g., "...15_n.jpg") but the filesystem name differs slightly or includes "(1)".
            if len(base_name) >= 40:
                best_match = None
                best_score = 0

                for filename in file_catalog.keys():
                    if not filename.lower().endswith(f".{ext_lower}"):
                        continue

                    candidate = self._strip_copy_suffix(filename)
                    candidate_base = (
                        candidate.rsplit(".", 1)[0] if "." in candidate else candidate
                    )

                    common = self._common_prefix_len(
                        base_name.lower(), candidate_base.lower()
                    )
                    shorter = min(len(base_name), len(candidate_base))

                    # Require a very strong match to avoid false positives:
                    # - at least 40 chars in common
                    # - at least 90% of the shorter base name matches
                    if common >= 40 and common >= int(shorter * 0.90):
                        if common > best_score:
                            best_score = common
                            best_match = filename

                if best_match and self.used_files[best_match] == 0:
                    canonical = self._strip_copy_suffix(best_match)
                    allocated = self._allocate_copy_variant(canonical, file_catalog)
                    if not allocated:
                        return None
                    self.stats["fuzzy_matches"] += 1
                    self.log_message(
                        "COMMON_PREFIX_MATCH",
                        f"Matched by common prefix: {export_name} -> {allocated[0]}",
                    )
                    return allocated

        return None

    def scan_conversations(self) -> List[Tuple[str, str, Path]]:
        """
        Scan Groups directory for all conversation folders.

        Returns list of (conversation_type, conversation_id, folder_path) tuples.
        """
        conversations = []

        for conv_dir in iter_subdirs(
            self.groups_dir,
            banned_filter=self.banned_filter,
            skip_hidden=True,
            sorted_=True,
        ):
            # Parse folder name: "DM xxxxx" or "Space xxxxx"
            folder_name = conv_dir.name
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

        return conversations

    def build_conversation_catalog(self, conv_folder: Path) -> Dict[str, Path]:
        """
        Build file catalog for a specific conversation folder.

        Returns: Dict[filename, source_path]

        Uses shared iter_files utility for consistent file scanning
        with automatic hidden file and banned pattern filtering.
        """
        catalog = {}

        # Use shared scanner for consistent filtering
        for file_path in iter_files(
            conv_folder,
            banned_filter=self.banned_filter,
            extensions=ALL_MEDIA_EXTENSIONS,
            skip_hidden=True,
        ):
            catalog[file_path.name] = file_path

        return catalog

    def count_total_media_files(self) -> int:
        """
        Count total media files across all conversation folders.

        Uses shared scanner utilities for consistent filtering.
        """
        total = 0

        # Scan all conversation folders
        for conv_dir in iter_subdirs(
            self.groups_dir,
            banned_filter=self.banned_filter,
            skip_hidden=True,
        ):
            # Count media files in each conversation
            for _ in iter_files(
                conv_dir,
                banned_filter=self.banned_filter,
                extensions=ALL_MEDIA_EXTENSIONS,
                skip_hidden=True,
            ):
                total += 1

        return total

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
            logger.error("Failed to parse %s: %s", messages_path, e)

        return messages

    def _process_single_conversation(
        self, conversation_tuple: Tuple[str, str, Path]
    ) -> Optional[Dict]:
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

        logger.info("Processing conversation files...")

        # Scan all conversation folders
        conversation_list = self.scan_conversations()
        logger.info("   Found %d conversations", len(conversation_list))

        logger.info(
            "Processing %d conversations (using %d threads)...",
            len(conversation_list),
            self.threads,
        )

        def on_conversation_error(tid: Any, e: Exception) -> None:
            self.log_message(
                "CONVERSATION_PROCESSING_ERROR",
                f"Failed to process conversation {tid}",
                str(e),
            )
            logger.error("Failed to process conversation %s: %s", tid, e)

        # Process conversations in parallel
        all_conversations = process_parallel_threaded(
            tasks=conversation_list,
            worker_fn=self._process_single_conversation,
            threads=self.threads,
            phase=PHASE_PREPROCESS,
            description="Parsing conversations",
            task_id_fn=lambda t: t[1],  # conversation_id
            on_error=on_conversation_error,
        )

        # Filter out conversations that did not contain any media messages
        all_conversations = [c for c in all_conversations if c is not None]

        with self.stats_lock:
            self.stats["total_conversations"] = len(all_conversations)

        logger.info(
            "   Processed %d conversations with media",
            self.stats["total_conversations"],
        )
        logger.info(
            "   Found %d messages with media",
            self.stats["total_messages_with_media"],
        )

        return all_conversations

    def copy_media_files(self, conversations: List[Dict]) -> None:
        """Hash media files and build metadata with source_path using hash_with_dedup."""

        logger.info("Collecting media files for copy...")

        # Phase 1: Collect all files to copy, resolving fuzzy matches
        # Map from (source_path, export_name) -> CopyTask
        copy_tasks: List[CopyTask] = []
        # Track which message/export_name pairs map to which source paths
        message_file_map: Dict[Tuple[int, int], Dict[str, Optional[Path]]] = {}

        for conv_idx, conversation in enumerate(conversations):
            conv_id = conversation["conversation_id"]
            conv_type = conversation["conversation_type"]
            conv_folder = self.groups_dir / f"{conv_type} {conv_id}"

            # Build conversation-specific catalog
            conv_catalog = self.build_conversation_catalog(conv_folder)

            # Reset used_files tracker for this conversation
            self.used_files.clear()

            for msg_idx, message in enumerate(conversation["messages"]):
                export_names = message.get("export_names", [])
                msg_key = (conv_idx, msg_idx)
                message_file_map[msg_key] = {}

                for export_name in export_names:
                    # Find the file using fuzzy matching in this conversation's catalog
                    result = self.find_media_file(export_name, conv_catalog)

                    if result:
                        matched_filename, source_path = result
                        message_file_map[msg_key][export_name] = source_path

                        # Build context for this file (include _source_path for tracking)
                        context = {
                            "_source_path": str(source_path),
                            "conversation_id": conv_id,
                            "conversation_type": conv_type,
                            "conversation_name": conversation["conversation_name"],
                            "sender": message.get("sender"),
                            "timestamp": message.get("timestamp"),
                            "export_name": export_name,
                        }

                        copy_tasks.append(
                            CopyTask(
                                source_path=source_path,
                                dest_filename=matched_filename,
                                context=context,
                            )
                        )
                        # Track that this file was referenced
                        self.copied_media_paths.add(str(source_path))
                    else:
                        message_file_map[msg_key][export_name] = None
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

        # Phase 2: Hash files with deduplication (no file copying)
        # Files will be copied directly from source to final output during processing
        logger.info("Hashing %d media files with deduplication...", len(copy_tasks))
        results = self.hash_with_dedup(copy_tasks)

        # Build a map from source_path to (dest_filename, source_path) for updating messages
        source_to_result: Dict[str, Tuple[str, str]] = {}
        duplicate_count = 0

        for source_path, result in results.items():
            # The result contains the dest_filename and whether it's a duplicate
            source_path_str = str(source_path)

            if result.is_duplicate:
                duplicate_count += 1
            else:
                self.stats["media_copied"] += 1

            # Map this source path to the resulting (dest_filename, source_path)
            if source_path_str and result.dest_filename:
                source_to_result[source_path_str] = (
                    result.dest_filename,
                    str(result.source_path),
                )

        # Phase 3: Update messages with resulting filenames and source paths
        for conv_idx, conversation in enumerate(conversations):
            for msg_idx, message in enumerate(conversation["messages"]):
                export_names = message.get("export_names", [])
                media_entries = []
                msg_key = (conv_idx, msg_idx)

                for export_name in export_names:
                    source_path = message_file_map.get(msg_key, {}).get(export_name)
                    if source_path:
                        result_info = source_to_result.get(str(source_path))
                        if result_info:
                            dest_filename, src_path = result_info
                            media_entries.append(
                                {
                                    "filename": dest_filename,
                                    "source_path": src_path,
                                }
                            )

                # Update message with matched files (new format with source_path)
                message["media_files"] = [e["filename"] for e in media_entries]
                message["source_paths"] = {
                    e["filename"]: e["source_path"] for e in media_entries
                }
                # Remove export_names as it's no longer needed
                if "export_names" in message:
                    del message["export_names"]

        logger.info("   Copied %d unique files", self.stats["media_copied"])
        logger.info("   Deduplicated %d duplicate files", duplicate_count)
        logger.info("   Fuzzy matches: %d", self.stats["fuzzy_matches"])
        logger.info("   Missing files: %d", self.stats["missing_files"])

        # Track orphaned media (files in filesystem that were never copied)
        logger.info("Scanning for orphaned media files...")
        all_media_paths: set[str] = set()
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
            logger.info("   Found %d orphaned media files", len(orphaned_media))

    def get_content_key(self) -> str:
        """Return the content key for Google Chat metadata."""
        return "conversations"

    @staticmethod
    def get_platform() -> str:
        """Return the platform identifier for Google Chat."""
        return "Google"

    @staticmethod
    def get_service() -> str:
        """Return the service identifier for Google Chat."""
        return "chat"

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Conversations": self.stats["total_conversations"],
            "Messages with media": self.stats["total_messages_with_media"],
            "Media files found": self.stats["total_media_files"],
            "Files copied": self.stats["media_copied"],
            "Fuzzy matches": self.stats["fuzzy_matches"],
            "Missing files": self.stats["missing_files"],
            "Banned skipped": self.stats["banned_files_skipped"],
        }
        print_preprocessing_stats("Preprocessing Stats", stats)

    def process_export(self) -> bool:
        """Platform-specific preprocessing logic.

        Processes all conversations, matching media files to messages
        and performing deduplication.

        Returns:
            True if processing succeeded, False otherwise
        """
        # Use cached detection result from run() pipeline
        detection = self.ensure_detection_result()
        if not detection.username:
            self.log_message(
                "USERNAME_DETECTION_ERROR",
                "All username detection methods failed",
            )
            logger.error("Could not detect export username")
            return False
        self.export_username = detection.username

        # Detect export identity (email + display name) for DM partner identification
        self.export_email, self.export_display_name = self.detect_export_identity()

        # Count total media files for statistics
        logger.info("Scanning media directories...")
        self.stats["total_media_files"] = self.count_total_media_files()
        logger.info("   Found %d media files", self.stats["total_media_files"])

        # Create metadata by parsing JSON files
        conversations = self.create_metadata()

        # Copy media files to output directory using conversation-scoped matching
        self.copy_media_files(conversations)

        # Save metadata
        self.save_metadata(
            conversations,
            extra_export_info={
                "total_conversations": self.stats["total_conversations"],
                "total_messages_with_media": self.stats["total_messages_with_media"],
                "total_media_files": self.stats["media_copied"],
            },
        )

        return True
