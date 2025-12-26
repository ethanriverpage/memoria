#!/usr/bin/env python3
"""
Google Voice Messages Preprocessor

Organizes Google Voice message export files and creates cleaned metadata:
- Parses HTML files containing message metadata
- Copies media files to organized output directory
- Creates metadata.json with essential information (sender, timestamps, conversation)
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from collections import defaultdict
from html.parser import HTMLParser

from common.preprocess.context_mergers import merge_first_occurrence
from common.processing import print_preprocessing_stats
from common.detection import resolve_username, detect_from_dirname, DetectionResult
from common.output.logging import get_processor_logger
from common.utils import ALL_MEDIA_EXTENSIONS
from processors.preprocess_base import CopyTask, PreprocessorBase

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class VoiceHTMLParser(HTMLParser):
    """Custom HTML parser for Google Voice export files"""

    def __init__(self):
        super().__init__()
        self.messages = []
        self.participants = []
        self.title = ""
        self.current_message = None
        self.current_tag_stack = []
        self.in_participants = False
        self.in_title = False
        self.in_message = False
        self.in_sender = False
        self.in_timestamp = False
        self.in_fn = False
        self.current_phone = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        self.current_tag_stack.append(tag)

        if tag == "title":
            self.in_title = True
        elif tag == "div" and attrs_dict.get("class") == "participants":
            self.in_participants = True
        elif tag == "div" and attrs_dict.get("class") == "message":
            self.in_message = True
            self.current_message = {"sender": None, "timestamp": None, "media_src": []}
            self.current_phone = None
        elif self.in_message and tag == "abbr" and attrs_dict.get("class") == "dt":
            self.in_timestamp = True
            if "title" in attrs_dict:
                if self.current_message:
                    self.current_message["timestamp"] = attrs_dict["title"]
        elif (
            (self.in_message or self.in_participants)
            and tag == "cite"
            and "sender" in (attrs_dict.get("class") or "")
        ):
            self.in_sender = True
            self.current_phone = None
        elif self.in_sender and tag == "a" and "tel" in (attrs_dict.get("class") or ""):
            # Capture phone number from href
            href = attrs_dict.get("href") or ""
            if href.startswith("tel:"):
                self.current_phone = href[4:]  # Remove "tel:" prefix
        elif (
            (self.in_sender or (self.in_participants and "vcard" in str(attrs)))
            and (tag == "span" or tag == "abbr")
            and attrs_dict.get("class") == "fn"
        ):
            self.in_fn = True
        elif self.in_message and tag == "img":
            if "src" in attrs_dict and self.current_message:
                self.current_message["media_src"].append(attrs_dict["src"])

    def handle_endtag(self, tag):
        if self.current_tag_stack and self.current_tag_stack[-1] == tag:
            self.current_tag_stack.pop()

        if tag == "title":
            self.in_title = False
        elif tag == "div" and self.in_participants:
            self.in_participants = False
        elif tag == "div" and self.in_message:
            if self.current_message and self.current_message["media_src"]:
                # Use phone number as fallback if sender is still None
                if not self.current_message["sender"] and self.current_phone:
                    self.current_message["sender"] = self.current_phone
                self.messages.append(self.current_message)
            self.current_message = None
            self.in_message = False
        elif tag == "cite":
            self.in_sender = False
            self.in_fn = False
        elif (tag == "span" or tag == "abbr") and self.in_fn:
            # If we're ending fn span/abbr and sender is still None, use phone number
            if (
                self.in_message
                and self.current_message
                and not self.current_message["sender"]
                and self.current_phone
            ):
                self.current_message["sender"] = self.current_phone
            self.in_fn = False

    def handle_data(self, data):
        data = data.strip()
        if not data:
            return

        if self.in_title:
            self.title += data
        elif self.in_fn:
            if self.in_participants:
                self.participants.append(data)
            elif (
                self.in_message
                and self.current_message
                and not self.current_message["sender"]
            ):
                self.current_message["sender"] = data


class GoogleVoicePreprocessor(PreprocessorBase):
    """Preprocesses Google Voice message export by organizing files and cleaning metadata"""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        super().__init__(
            processor_name="Google Voice",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        self.voice_dir = self.export_path / "Voice" / "Calls"

        # Export username (detected from directory name)
        self.export_username = None

        # Statistics specific to this preprocessor
        self.stats = {
            "total_conversations": 0,
            "total_messages_with_media": 0,
            "total_media_files": 0,
            "media_copied": 0,
            "missing_files": 0,
            "fuzzy_matches": 0,
            "banned_files_skipped": 0,
        }

        # Track used files for duplicate handling
        self.used_files = defaultdict(int)

        # Cached index to resolve filesystem copy suffixes like "(1)" efficiently.
        # Built from the current media catalog passed into find_media_file().
        self._stripped_catalog_cache_id: Optional[int] = None
        self._stripped_to_original: Dict[str, List[str]] = {}

        # Track which media files were copied (for orphaned media detection)
        self.copied_media_paths = set()

        # Set context merger for deduplication (first occurrence wins)
        self._context_merger = merge_first_occurrence

    @staticmethod
    def _strip_copy_suffix(filename: str) -> str:
        """
        Strip filesystem copy suffixes like "(1)" right before the extension.

        Example:
            "image-1(1).jpg" -> "image-1.jpg"
        """
        try:
            p = Path(filename)
            stem = p.stem
            suffix = p.suffix
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
        """
        try:
            stem = Path(filename).stem
            match = re.search(r"\((\d+)\)$", stem)
            if not match:
                return None
            return int(match.group(1))
        except Exception:
            return None

    def _ensure_stripped_index(self, file_catalog: Dict[str, Path]) -> None:
        """Build (or reuse) an index of stripped-copy filenames -> originals."""
        current_id = id(file_catalog)
        if self._stripped_catalog_cache_id == current_id:
            return

        stripped_to_original: Dict[str, List[str]] = defaultdict(list)
        for filename in file_catalog.keys():
            stripped = self._strip_copy_suffix(filename)
            stripped_to_original[stripped].append(filename)

        self._stripped_catalog_cache_id = current_id
        self._stripped_to_original = dict(stripped_to_original)

    def _try_match_candidate(
        self, candidate: str, file_catalog: Dict[str, Path]
    ) -> Optional[Tuple[str, Path]]:
        """
        Try to match a candidate filename against the catalog, including resolving copy
        suffix variants like "(1)" if the exact candidate isn't present.
        """
        # Exact match
        if candidate in file_catalog and self.used_files[candidate] == 0:
            self.used_files[candidate] += 1
            return (candidate, file_catalog[candidate])

        # Copy-suffix variants: candidate may exist as "candidate(1).ext", etc.
        originals = self._stripped_to_original.get(candidate, [])
        if originals:
            # Prefer the cleanest filename: no "(n)" suffix if it exists
            def sort_key(n: str) -> tuple:
                idx = self._copy_suffix_index(n)
                # None means no suffix, sort first
                return (idx is not None, idx or 0, len(n))

            preferred = sorted(originals, key=sort_key)
            for filename in preferred:
                if self.used_files[filename] == 0:
                    self.used_files[filename] += 1
                    return (filename, file_catalog[filename])

        return None

    def validate_export(self) -> bool:
        """Validate that export directory has required structure"""
        if not self.export_path.exists():
            logger.error("Export path does not exist: %s", self.export_path)
            return False

        if not self.voice_dir.exists():
            logger.error("Voice/Calls directory not found: %s", self.voice_dir)
            return False

        # Check if at least one HTML file exists
        html_files = list(self.voice_dir.glob("*.html"))
        if not html_files:
            logger.error("No HTML files found in %s", self.voice_dir)
            return False

        return True

    def detect_username(self) -> DetectionResult:
        """Detect export username using available sources.

        Priority:
        1. Profile/Profile.json - Google account profile
        2. Google Chat user_info.json - if Chat is in same export
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

        def detect_from_google_chat_user_info(path: Path) -> Optional[str]:
            """Extract username from Google Chat user_info.json if present."""
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
                ("Google Chat user_info.json", detect_from_google_chat_user_info),
                ("dirname", detect_from_dirname_google),
            ],
        )

    def parse_timestamp(self, timestamp_str: str) -> Optional[str]:
        """
        Convert timestamp string to ISO format
        Input format: "2016-05-18T11:00:23.648-04:00"
        Output format: "2016-05-18 11:00:23"
        """
        try:
            # Parse ISO 8601 format
            # Handle timezone offset
            if "T" in timestamp_str:
                dt_part = (
                    timestamp_str.split(".")[0]
                    if "." in timestamp_str
                    else timestamp_str.split("+")[0].split("-")[0:-1]
                )
                if "." in timestamp_str:
                    dt_part = timestamp_str.split(".")[0]
                else:
                    # Handle +/- timezone
                    match = re.match(r"([^+-]+)[+-]\d{2}:\d{2}", timestamp_str)
                    if match:
                        dt_part = match.group(1)
                    else:
                        dt_part = timestamp_str

                dt = datetime.fromisoformat(dt_part.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, AttributeError) as e:
            self.log_message(
                "TIMESTAMP_PARSE_ERROR",
                f"Failed to parse timestamp: {timestamp_str}",
                str(e),
            )
            return None

    def is_text_conversation(self, html_file: Path) -> bool:
        """Check if HTML file is a text conversation (not voicemail or call)"""
        filename = html_file.name

        # Exclude voicemails and calls
        if (
            "Voicemail" in filename
            or "Missed" in filename
            or "Placed" in filename
            or "Received" in filename
        ):
            # Check if it doesn't have " - Text - " in the name
            if " - Text - " not in filename:
                return False

        # Must be a text conversation
        return " - Text - " in filename or "Group Conversation" in filename

    def extract_conversation_info(self, html_file: Path) -> Optional[Dict]:
        """
        Extract conversation information from HTML file
        Returns dict with conversation_name, conversation_type, and messages
        """
        try:
            with open(html_file, "r", encoding="utf-8") as f:
                html_content = f.read()

            parser = VoiceHTMLParser()
            parser.feed(html_content)

            # Determine if it's a group conversation
            is_group = "Group Conversation" in html_file.name

            # Extract conversation name
            if is_group:
                # Group: all participants except "Me"
                participants = [p for p in parser.participants if p != "Me"]
                conversation_name = ", ".join(participants)
                conversation_type = "group"
            else:
                # DM: extract from title "Me to {name}" or from first non-Me sender
                conversation_name = ""
                if "Me to" in parser.title:
                    conversation_name = parser.title.replace("Me to", "").strip()
                    # If empty after removing "Me to", get from filename
                    if not conversation_name:
                        match = re.match(r"(.+) - Text - ", html_file.name)
                        if match:
                            conversation_name = match.group(1)

                # If still no conversation name, find from first non-Me sender
                if not conversation_name:
                    for msg in parser.messages:
                        if msg["sender"] and msg["sender"] != "Me":
                            conversation_name = msg["sender"]
                            break
                    else:
                        # Fallback: parse from filename
                        match = re.match(r"(.+) - Text - ", html_file.name)
                        if match:
                            conversation_name = match.group(1)
                        else:
                            self.log_message(
                                "CONVERSATION_NAME_ERROR",
                                f"Could not determine conversation name for {html_file.name}",
                            )
                            return None
                conversation_type = "dm"

            # Process messages
            messages = []
            for msg in parser.messages:
                # Replace "Me" with export username
                sender = msg["sender"]
                if sender == "Me":
                    sender = self.export_username

                timestamp = (
                    self.parse_timestamp(msg["timestamp"]) if msg["timestamp"] else None
                )

                messages.append(
                    {
                        "sender": sender,
                        "timestamp": timestamp,
                        "timestamp_raw": msg["timestamp"],
                        "conversation": conversation_name,
                        "media_src": msg["media_src"],
                    }
                )

            if not messages:
                return None

            return {
                "conversation_name": conversation_name,
                "conversation_type": conversation_type,
                "messages": messages,
                "html_file": html_file,
            }

        except Exception as e:
            self.log_message(
                "HTML_PARSE_ERROR",
                f"Failed to parse {html_file.name}",
                str(e),
            )
            return None

    def build_media_catalog(self) -> Dict[str, Path]:
        """
        Build file catalog for all media files in Voice/Calls directory
        Returns: Dict[filename, source_path]
        """
        catalog = {}

        # Media extensions to look for
        media_extensions = ALL_MEDIA_EXTENSIONS | {".mp3", ".wav", ".m4a"}

        # Scan Voice/Calls directory for media files
        import os

        with os.scandir(self.voice_dir) as entries:
            for entry in entries:
                if entry.is_file():
                    file_path = Path(entry.path)
                    filename = entry.name

                    # Skip HTML files
                    if filename.endswith(".html"):
                        continue

                    # Skip banned files (system files, thumbnails, etc.)
                    if self.banned_filter.is_banned(file_path):
                        self.stats["banned_files_skipped"] += 1
                        self.log_message(
                            "BANNED_FILE_SKIPPED",
                            f"Skipped banned file: {filename}",
                        )
                        continue

                    # Check if it's a media file
                    if file_path.suffix.lower() in media_extensions:
                        catalog[filename] = file_path

        return catalog

    def find_media_file(
        self, media_src: str, file_catalog: Dict[str, Path]
    ) -> Optional[Tuple[str, Path]]:
        """
        Find media file using fuzzy matching
        HTML src might not have extension, so we need to match
        Returns (matched_filename, source_path) or None
        """
        self._ensure_stripped_index(file_catalog)

        # HTML often stores src as a relative path; match based on the basename
        media_src_key = Path(media_src).name if "/" in media_src else media_src

        # Try exact match first
        direct = self._try_match_candidate(media_src_key, file_catalog)
        if direct:
            return direct

        # Try with common extensions
        extensions = [
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".mp4",
            ".mov",
            ".avi",
            ".webm",
        ]

        for ext in extensions:
            test_name = (
                media_src_key if media_src_key.endswith(ext) else media_src_key + ext
            )
            matched = self._try_match_candidate(test_name, file_catalog)
            if matched:
                if test_name != media_src_key:
                    self.stats["fuzzy_matches"] += 1
                return matched

        # Try stripping trailing "-1" (attachment index) if present
        # Some exports have -1 in HTML but not in actual filenames
        if media_src_key.endswith("-1"):
            alt_src = media_src_key[:-2]  # Remove the "-1"

            # Try exact match without -1
            matched = self._try_match_candidate(alt_src, file_catalog)
            if matched:
                self.stats["fuzzy_matches"] += 1
                return matched

            # Try with extensions without -1
            for ext in extensions:
                test_name = alt_src if alt_src.endswith(ext) else alt_src + ext
                matched = self._try_match_candidate(test_name, file_catalog)
                if matched:
                    self.stats["fuzzy_matches"] += 1
                    return matched

        # Handle suffix patterns like "-10-1" where the first number is an attachment group
        # but files on disk only include the final index (e.g. "-1(1).jpg").
        #
        # Example:
        #   HTML src: "Name - Text - 2016...Z-10-1"
        #   Disk:     "Name - Text - 2016...Z-1(1).jpg"
        src_base = media_src_key
        src_ext = ""
        if "." in media_src_key:
            src_base = Path(media_src_key).stem
            src_ext = Path(media_src_key).suffix

        m = re.match(r"^(.*)-(\d+)-(\d+)$", src_base)
        if m:
            alt_base = f"{m.group(1)}-{m.group(3)}"
            # If src had an extension, preserve it; otherwise try common extensions.
            if src_ext:
                matched = self._try_match_candidate(alt_base + src_ext, file_catalog)
                if matched:
                    self.stats["fuzzy_matches"] += 1
                    return matched
            else:
                for ext in extensions:
                    matched = self._try_match_candidate(alt_base + ext, file_catalog)
                    if matched:
                        self.stats["fuzzy_matches"] += 1
                        return matched

        # Try prefix matching (for files with multiple attachments)
        for filename, path in file_catalog.items():
            if filename.startswith(media_src_key) and self.used_files[filename] == 0:
                self.used_files[filename] += 1
                self.stats["fuzzy_matches"] += 1
                return (filename, path)

        return None

    def scan_conversations(self) -> List[Dict]:
        """
        Scan Voice/Calls directory for all text conversation HTML files
        Parse and group by conversation
        Returns list of conversation dicts
        """
        conversations_dict = {}  # Key: conversation_name, Value: conversation data

        logger.info("Scanning text conversation files...")

        # Find all HTML files
        html_files = sorted(self.voice_dir.glob("*.html"))
        text_files = [f for f in html_files if self.is_text_conversation(f)]

        logger.info(f"   Found {len(text_files)} text conversation files")

        for html_file in text_files:
            conv_info = self.extract_conversation_info(html_file)

            if not conv_info:
                continue

            conv_name = conv_info["conversation_name"]

            # Group messages by conversation
            if conv_name not in conversations_dict:
                conversations_dict[conv_name] = {
                    "conversation_name": conv_name,
                    "conversation_type": conv_info["conversation_type"],
                    "messages": [],
                }

            conversations_dict[conv_name]["messages"].extend(conv_info["messages"])

        # Convert to list
        conversations = list(conversations_dict.values())

        # Count messages with media
        for conv in conversations:
            self.stats["total_messages_with_media"] += len(conv["messages"])

        self.stats["total_conversations"] = len(conversations)

        logger.info(
            "   Processed %d unique conversations", self.stats["total_conversations"]
        )
        logger.info(
            "   Found %d messages with media", self.stats["total_messages_with_media"]
        )

        return conversations

    def copy_media_files(self, conversations: List[Dict]) -> None:
        """Hash media files and build metadata with source_path using hash_with_dedup."""

        logger.info("Collecting media files for copy...")

        # Build media catalog
        media_catalog = self.build_media_catalog()
        self.stats["total_media_files"] = len(media_catalog)
        logger.info(f"   Found {self.stats['total_media_files']} media files")

        # Reset used files tracker
        self.used_files.clear()

        # Phase 1: Collect all files to copy, resolving fuzzy matches
        copy_tasks: List[CopyTask] = []
        # Track which message/media_src pairs map to which source paths
        message_file_map: Dict[Tuple[int, int], Dict[str, Optional[Path]]] = {}

        for conv_idx, conversation in enumerate(conversations):
            for msg_idx, message in enumerate(conversation["messages"]):
                media_src_list = message.get("media_src", [])
                msg_key = (conv_idx, msg_idx)
                message_file_map[msg_key] = {}

                for media_src in media_src_list:
                    # Find the file using fuzzy matching
                    result = self.find_media_file(media_src, media_catalog)

                    if result:
                        matched_filename, source_path = result
                        message_file_map[msg_key][media_src] = source_path

                        # Build context for this file
                        context = {
                            "_source_path": str(source_path),
                            "conversation_name": conversation["conversation_name"],
                            "conversation_type": conversation["conversation_type"],
                            "sender": message.get("sender"),
                            "timestamp": message.get("timestamp"),
                            "media_src": media_src,
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
                        message_file_map[msg_key][media_src] = None
                        self.log_message(
                            "MISSING_FILE",
                            f"File not found: {media_src}",
                        )
                        self.stats["missing_files"] += 1

                        # Track orphaned metadata
                        self.failure_tracker.add_orphaned_metadata(
                            metadata_entry={
                                "conversation_name": conversation["conversation_name"],
                                "media_src": media_src,
                                "message_timestamp": message.get("timestamp"),
                                "sender": message.get("sender"),
                            },
                            reason="Media file not found in filesystem",
                            context={
                                "expected_filename": media_src,
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
            source_path_str = str(source_path)

            if result.is_duplicate:
                duplicate_count += 1
            else:
                self.stats["media_copied"] += 1

            # Map this source path to the resulting (dest_filename, source_path)
            if source_path_str and result.dest_filename:
                source_to_result[source_path_str] = (result.dest_filename, str(result.source_path))

        # Phase 3: Update messages with resulting filenames and source paths
        for conv_idx, conversation in enumerate(conversations):
            for msg_idx, message in enumerate(conversation["messages"]):
                media_src_list = message.get("media_src", [])
                media_entries = []
                msg_key = (conv_idx, msg_idx)

                for media_src in media_src_list:
                    source_path = message_file_map.get(msg_key, {}).get(media_src)
                    if source_path:
                        result_info = source_to_result.get(str(source_path))
                        if result_info:
                            dest_filename, src_path = result_info
                            media_entries.append({
                                "filename": dest_filename,
                                "source_path": src_path,
                            })

                # Update message with matched files (new format with source_path)
                message["media_files"] = [e["filename"] for e in media_entries]
                message["source_paths"] = {e["filename"]: e["source_path"] for e in media_entries}
                # Remove media_src as it's no longer needed
                if "media_src" in message:
                    del message["media_src"]

        logger.info("   Copied %d unique files", self.stats["media_copied"])
        logger.info(f"   Deduplicated {duplicate_count} duplicate files")
        logger.info("   Fuzzy matches: %d", self.stats["fuzzy_matches"])
        logger.info("   Missing files: %d", self.stats["missing_files"])

        # Track orphaned media (files in filesystem that were never copied)
        logger.info("Scanning for orphaned media files...")
        all_media_paths = set(str(path) for path in media_catalog.values())
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
            # Add export info with username detection source
            detection_result = self.detect_username()

            # Build file_hashes lookup table for cross-processor dedup optimization
            file_hashes = {}
            for content_hash, registry_entry in self._hash_registry.items():
                filename = registry_entry.get("filename", "")
                if filename:
                    file_hashes[filename] = content_hash

            # Add export info
            output = {
                "export_info": {
                    "export_path": str(self.export_path),
                    "export_name": self.export_path.name,
                    "export_username": self.export_username,
                    "username_source": detection_result.source,
                    "processed_date": datetime.now().isoformat(),
                    "total_conversations": self.stats["total_conversations"],
                    "total_messages_with_media": self.stats[
                        "total_messages_with_media"
                    ],
                    "total_media_files": self.stats["media_copied"],
                },
                "conversations": conversations,
                "file_hashes": file_hashes,
            }

            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)

            logger.info("Saved metadata to %s", self.metadata_file)
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
            raise

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
        # Detect export username
        logger.info("Detecting export username...")
        result = self.detect_username()
        if not result.username:
            self.log_message(
                "USERNAME_DETECTION_ERROR",
                "All username detection methods failed",
            )
            logger.error("Could not detect export username")
            return False
        self.export_username = result.username
        logger.info("Detected export user: %s (via %s)", result.username, result.source)

        # Scan and parse conversations
        conversations = self.scan_conversations()

        # Copy media files to output directory
        self.copy_media_files(conversations)

        # Save metadata
        self.save_metadata(conversations)

        return True
