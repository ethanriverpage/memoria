#!/usr/bin/env python3
"""
Instagram Messages Preprocessor

Organizes Instagram message export files and creates cleaned metadata:
- Parses HTML files containing message metadata
- Copies media files to organized output directory
- Creates metadata.json with essential information (sender, timestamps, conversation)
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

from common.detection import resolve_username, detect_from_dirname, DetectionResult
from common.files.scanner import iter_subdirs, iter_media_files
from common.output.logging import get_processor_logger
from common.output.progress import PHASE_PREPROCESS
from common.output.summary import print_preprocessing_stats
from common.processing import process_parallel_threaded
from processors.preprocess_base import CopyTask, HashResult, PreprocessorBase

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class InstagramMessagesPreprocessor(PreprocessorBase):
    """Preprocesses Instagram message export by organizing files and cleaning metadata"""

    # Instagram official export (HTML) timestamps are timezone-less strings, but empirically
    # behave like a fixed UTC−08:00 offset (not DST-aware). We interpret them as UTC−08:00
    # and convert to canonical UTC '...Z' for consistent downstream processing.
    _OFFICIAL_EXPORT_TZ = timezone(timedelta(hours=-8))

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        super().__init__(
            processor_name="Instagram Messages",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        # Detect export format: new (2025+) vs legacy (2022)
        new_format_inbox = (
            self.export_path / "your_instagram_activity" / "messages" / "inbox"
        )
        legacy_format_inbox = self.export_path / "messages" / "inbox"

        if new_format_inbox.exists():
            self.messages_dir = new_format_inbox
            self.is_legacy_format = False
        elif legacy_format_inbox.exists():
            self.messages_dir = legacy_format_inbox
            self.is_legacy_format = True
        else:
            # Will fail validation with clear error
            self.messages_dir = new_format_inbox
            self.is_legacy_format = False

        # No context merger needed - first occurrence wins
        # (no accumulating fields like albums)

        # Statistics specific to this preprocessor (preserves shared keys from base)
        self.stats.update(
            {
                "total_conversations": 0,
                "total_messages_with_media": 0,
                "total_media_files": 0,
                "media_copied": 0,
                # Media present on disk but not referenced by any message metadata.
                "orphaned_media_files": 0,
                "missing_files": 0,
                "banned_files_skipped": 0,
                "unique_files": 0,
                "duplicate_files": 0,
            }
        )

        # Deleted user counter
        self.deleted_user_counter = 0
        self.deleted_user_mapping = {}

        # Track which files were referenced in messages
        self._referenced_files: set = set()

    def validate_export(self) -> bool:
        """Validate that export directory has required structure."""
        if not self.require_exists(self.export_path, label="Export path"):
            return False

        if not self.require_exists(self.messages_dir, label="Messages directory"):
            return False

        # Check if at least one conversation folder exists
        if not self.require_any_dirs(self.messages_dir, label="conversation"):
            return False

        return True

    def detect_username(self) -> DetectionResult:
        """Detect export username using available sources.

        Priority:
        1. personal_information.html - contains Instagram username
        2. Directory name pattern (instagram-username-YYYYMMDD)

        Returns:
            DetectionResult with username and source
        """

        def detect_from_personal_info(path: Path) -> Optional[str]:
            """Extract username from personal_information.html."""
            # Try new format (2025+): personal_information/personal_information/
            new_format_path = (
                path
                / "personal_information"
                / "personal_information"
                / "personal_information.html"
            )
            # Legacy format (2022): personal_information/
            legacy_format_path = (
                path / "personal_information" / "personal_information.html"
            )

            html_path = None
            if new_format_path.exists():
                html_path = new_format_path
            elif legacy_format_path.exists():
                html_path = legacy_format_path

            if not html_path:
                return None

            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    content = f.read()

                # Parse HTML to find Username field
                # Pattern: <td...>Username<div><div>VALUE</div></div></td>
                soup = BeautifulSoup(content, "html.parser")

                # Find all td elements containing "Username"
                for td in soup.find_all("td"):
                    text = td.get_text()
                    if text.startswith("Username"):
                        # Extract the username value after "Username"
                        username = text[len("Username") :].strip()
                        if username:
                            return username
            except Exception:
                return None

            return None

        def detect_from_dirname_instagram(path: Path) -> Optional[str]:
            """Extract username from directory name."""
            return detect_from_dirname(path, "instagram")

        return resolve_username(
            self.export_path,
            [
                ("personal_information.html", detect_from_personal_info),
                ("dirname", detect_from_dirname_instagram),
            ],
        )

    def parse_timestamp(self, timestamp_str: str) -> Optional[str]:
        """
        Convert timestamp string to canonical ISO 8601 format.
        Input formats:
          - New (2025+): "Sep 22, 2017 6:33 am"
          - Legacy (2022): "Sep 22, 2017, 6:33 AM" (extra comma after year)
        Output format: "2017-09-22T06:33:00Z"
        """
        from common.text.parse import CANONICAL_TIMESTAMP_FORMAT

        # Try both timestamp formats (new format first, then legacy with comma)
        formats = [
            "%b %d, %Y %I:%M %p",  # New format: "Sep 22, 2017 6:33 am"
            "%b %d, %Y, %I:%M %p",  # Legacy format: "Sep 22, 2017, 6:33 AM"
        ]

        for fmt in formats:
            try:
                dt_local = datetime.strptime(timestamp_str, fmt)
                dt_utc = dt_local.replace(tzinfo=self._OFFICIAL_EXPORT_TZ).astimezone(
                    timezone.utc
                )
                return dt_utc.strftime(CANONICAL_TIMESTAMP_FORMAT)
            except ValueError:
                continue

        # If all formats fail, log the error
        self.log_message(
            "TIMESTAMP_PARSE_ERROR",
            f"Failed to parse timestamp: {timestamp_str}",
            "No matching format found",
        )
        return None

    def extract_conversation_title(self, html_path: Path, conversation_id: str) -> str:
        """
        Extract conversation title from HTML <title> tag
        Handle deleted users with friendly names
        """
        # Check if this is a deleted user conversation
        if conversation_id.startswith("instagramuser_"):
            # Use mapping to ensure consistent naming across runs
            if conversation_id not in self.deleted_user_mapping:
                self.deleted_user_counter += 1
                self.deleted_user_mapping[conversation_id] = (
                    f"deleted_{self.deleted_user_counter}"
                )
            return self.deleted_user_mapping[conversation_id]

        # Extract from HTML title tag
        try:
            with open(html_path, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f, "html.parser")

            title_tag = soup.find("title")
            if title_tag:
                return title_tag.get_text(strip=True)
            else:
                self.log_message(
                    "TITLE_NOT_FOUND",
                    f"No <title> tag found in {html_path.name}",
                    f"conversation: {conversation_id}",
                )
                return conversation_id
        except Exception as e:
            self.log_message(
                "TITLE_PARSE_ERROR",
                f"Failed to extract title from {html_path.name}",
                str(e),
            )
            return conversation_id

    def extract_media_paths(self, message_element) -> List[str]:
        """
        Extract all media file paths from message element
        Returns list of relative paths like: your_instagram_activity/messages/inbox/.../photos/xxx.jpg
        """
        media_paths = []

        try:
            # Find all <a> tags with href containing photos/ or videos/
            #
            # Instagram exports commonly use relative paths like:
            #   photos/photo_123.jpg
            #   videos/video_456.mp4
            # (no leading slash). Older code only matched "/photos/" which misses real exports.
            links = message_element.find_all("a", href=True)
            for link in links:
                href = link.get("href", "")
                if "photos/" in href or "videos/" in href:
                    media_paths.append(href)

            # Also check <img> tags with src containing photos/
            images = message_element.find_all("img", src=True)
            for img in images:
                src = img.get("src", "")
                if "photos/" in src and src not in media_paths:
                    media_paths.append(src)

            # Also check <video> tags with src containing videos/
            videos = message_element.find_all("video", src=True)
            for video in videos:
                src = video.get("src", "")
                if "videos/" in src and src not in media_paths:
                    media_paths.append(src)

        except Exception as e:
            self.log_message(
                "MEDIA_PATH_EXTRACT_ERROR",
                "Failed to extract media paths",
                str(e),
            )

        return media_paths

    def extract_message_content(self, message_element) -> Optional[str]:
        """
        Extract message text content from the message element.

        HTML structure:
            <div class="_3-95 _a6-p">
              <div>
                <div></div>
                <div>Message text here</div>
                <div></div>
                <div></div>
              </div>
            </div>

        Returns:
            Message text content, or None if no text found.
        """
        try:
            # Find the content container
            content_container = message_element.find("div", class_="_3-95 _a6-p")
            if not content_container:
                return None

            # The content is inside nested divs - find direct text content
            # Get all div children and look for ones with actual text
            inner_div = content_container.find("div", recursive=False)
            if not inner_div:
                return None

            # Look through child divs for text content
            for child_div in inner_div.find_all("div", recursive=False):
                text = child_div.get_text(strip=True)
                if text:
                    # Filter out reaction notifications
                    if text.startswith("Reacted ") and " to your message" in text:
                        continue
                    # Filter out "Liked a message" notifications
                    if text == "Liked a message":
                        continue
                    # Return the first meaningful text found
                    return text

            return None

        except Exception as e:
            self.log_message(
                "CONTENT_EXTRACT_ERROR",
                "Failed to extract message content",
                str(e),
            )
            return None

    def parse_html_file(self, html_path: Path, _conversation_id: str) -> List[Dict]:
        """
        Parse an HTML file and extract messages with media.
        Returns list of message dictionaries (only messages with media).

        Note: _conversation_id is accepted for API consistency but not used here.
        """
        messages = []

        try:
            with open(html_path, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f, "html.parser")

            # Find all message containers
            message_containers = soup.find_all(
                "div", class_="pam _3-95 _2ph- _a6-g uiBoxWhite noborder"
            )

            for container in message_containers:
                # Extract sender
                # New format uses <h2>, legacy format uses <div> with same class
                sender_elem = container.find("h2", class_="_3-95 _2pim _a6-h _a6-i")
                if not sender_elem:
                    sender_elem = container.find(
                        "div", class_="_3-95 _2pim _a6-h _a6-i"
                    )
                sender = sender_elem.get_text(strip=True) if sender_elem else "Unknown"

                # Extract timestamp
                timestamp_elem = container.find("div", class_="_3-94 _a6-o")
                if timestamp_elem:
                    timestamp_str = timestamp_elem.get_text(strip=True)
                    timestamp = self.parse_timestamp(timestamp_str)
                    timestamp_raw = timestamp_str
                else:
                    timestamp = None
                    timestamp_raw = None

                # Extract media paths
                media_paths = self.extract_media_paths(container)

                # Extract message content
                content = self.extract_message_content(container)

                # Only include messages with media
                if media_paths:
                    message_data = {
                        "sender": sender,
                        "timestamp": timestamp,
                        "timestamp_raw": timestamp_raw,
                        "media_paths": media_paths,
                        "content": content,
                    }

                    messages.append(message_data)

        except Exception as e:
            self.log_message(
                "HTML_PARSE_ERROR",
                f"Failed to parse {html_path.name}",
                str(e),
            )
            log.parse_error(html_path.name, e)

        if messages:
            media_refs = sum(len(m.get("media_paths", [])) for m in messages)
            logger.debug(
                "Parsed %s: messages_with_media=%d media_refs=%d",
                html_path.name,
                len(messages),
                media_refs,
            )

        return messages

    def scan_conversations(self) -> List[Tuple[str, Path]]:
        """
        Scan messages/inbox directory for all conversation folders.

        Returns list of (conversation_id, html_path) tuples.

        Uses shared iter_subdirs utility for consistent directory scanning
        with automatic hidden file and banned pattern filtering.
        """
        conversations = []

        for conv_dir in iter_subdirs(
            self.messages_dir,
            banned_filter=self.banned_filter,
            skip_hidden=True,
            sorted_=True,
        ):
            conversation_id = conv_dir.name
            html_path = conv_dir / "message_1.html"

            if html_path.exists():
                conversations.append((conversation_id, html_path))
            else:
                self.log_message(
                    "MISSING_HTML",
                    f"No message_1.html found in {conversation_id}",
                )

        return conversations

    def build_file_catalog(self) -> Dict[str, Path]:
        """
        Scan all photos directories and build catalog mapping filename -> full source path.

        Returns: Dict[filename, source_path]

        Uses shared scanner utilities for consistent filtering.
        """
        catalog = {}

        if not self.messages_dir.exists():
            return catalog

        log.logger.info("Scanning media directories...")

        # Scan conversation directories
        for conv_dir in iter_subdirs(
            self.messages_dir,
            banned_filter=self.banned_filter,
            skip_hidden=True,
        ):
            # Instagram Messages exports store media under both photos/ and videos/
            for media_subdir_name in ("photos", "videos"):
                media_dir = conv_dir / media_subdir_name
                if not media_dir.exists():
                    continue

                # Use iter_media_files for recursive media scanning
                for file_path in iter_media_files(
                    media_dir,
                    banned_filter=self.banned_filter,
                    skip_hidden=True,
                    skip_overlays=False,  # Instagram doesn't have overlay concept
                    skip_thumbnails=False,  # Instagram doesn't have thumbnail files
                    recursive=True,
                ):
                    filename = file_path.name

                    # Map filename to source path
                    if filename in catalog:
                        self.log_message(
                            "DUPLICATE_FILENAME",
                            f"Duplicate filename found: {filename}",
                            f"Previous: {catalog[filename]}, New: {file_path}",
                        )
                    catalog[filename] = file_path
                    self.stats["total_media_files"] += 1

        log.found_files(len(catalog), "media")

        return catalog

    def _process_single_conversation(
        self, conversation_tuple: Tuple[str, Path]
    ) -> Optional[Dict]:
        """
        Process a single conversation (used by multithreaded processing)

        Args:
            conversation_tuple: Tuple of (conversation_id, html_path)

        Returns:
            Conversation data dict or None if conversation has no media messages
        """
        conversation_id, html_path = conversation_tuple

        # Extract conversation title
        conversation_title = self.extract_conversation_title(html_path, conversation_id)

        # Parse messages from HTML
        messages = self.parse_html_file(html_path, conversation_id)

        # Only include conversations that have messages with media
        if messages:
            with self.stats_lock:
                self.stats["total_messages_with_media"] += len(messages)

            conversation_data = {
                "conversation_id": conversation_id,
                "conversation_title": conversation_title,
                "messages": messages,
            }
            return conversation_data

        return None

    def create_metadata(self) -> List[Dict]:
        """
        Main processing: scan conversations and parse HTML files
        Returns list of all conversations with messages (multithreaded)
        """
        all_conversations = []

        log.logger.info("Processing conversation files...")

        # Scan all conversation folders
        conversation_list = self.scan_conversations()
        log.found_items(len(conversation_list), "conversations")

        log.logger.info(
            "Processing %d conversations (using %d workers)...",
            len(conversation_list),
            self.workers,
        )

        def on_conversation_error(tid: Any, e: Exception) -> None:
            self.log_message(
                "CONVERSATION_PROCESSING_ERROR",
                f"Failed to process conversation {tid}",
                str(e),
            )
            log.logger.error("Failed to process conversation %s: %s", tid, e)

        # Process conversations in parallel
        all_conversations = process_parallel_threaded(
            tasks=conversation_list,
            worker_fn=self._process_single_conversation,
            workers=self.workers,
            phase=PHASE_PREPROCESS,
            description="Parsing conversations",
            task_id_fn=lambda t: t[0],  # conversation_id
            on_error=on_conversation_error,
        )

        # Filter out conversations that did not contain any media messages
        all_conversations = [c for c in all_conversations if c is not None]

        with self.stats_lock:
            self.stats["total_conversations"] = len(all_conversations)

        log.logger.info(
            "   Processed %d conversations with media",
            self.stats["total_conversations"],
        )
        log.logger.info(
            "   Found %d messages with media", self.stats["total_messages_with_media"]
        )

        return all_conversations

    def copy_media_files(
        self, conversations: List[Dict], file_catalog: Dict[str, Path]
    ) -> None:
        """Hash media files and build metadata with source_path.

        Uses batch hash_with_dedup for content-based deduplication.
        """

        logger.info("Copying media files (with deduplication)...")

        # Collect all copy tasks from all conversations
        copy_tasks: List[CopyTask] = []
        # Map source_path -> list of (conversation, message) for updating later
        path_to_messages: Dict[str, List[Tuple[Dict, Dict, str]]] = {}

        for conversation in conversations:
            for message in conversation["messages"]:
                media_paths = message.get("media_paths", [])

                for media_path in media_paths:
                    # Extract filename from path
                    filename = Path(media_path).name

                    if filename in file_catalog:
                        source_path = file_catalog[filename]
                        self._referenced_files.add(filename)

                        # Create copy task with context
                        context = {
                            "conversation_id": conversation["conversation_id"],
                            "timestamp": message.get("timestamp"),
                            "sender": message.get("sender"),
                        }
                        copy_tasks.append(
                            CopyTask(
                                source_path=source_path,
                                dest_filename=filename,
                                context=context,
                            )
                        )

                        # Track for updating message later
                        path_key = str(source_path)
                        if path_key not in path_to_messages:
                            path_to_messages[path_key] = []
                        path_to_messages[path_key].append(
                            (conversation, message, media_path)
                        )
                    else:
                        self.log_message(
                            "MISSING_FILE",
                            f"File referenced in HTML not found: {filename}",
                            f"from path: {media_path}",
                        )
                        self.stats["missing_files"] += 1

                        # Track orphaned metadata
                        self.failure_tracker.add_orphaned_metadata(
                            metadata_entry={
                                "conversation_id": conversation["conversation_id"],
                                "conversation_title": conversation[
                                    "conversation_title"
                                ],
                                "media_path": media_path,
                                "message_timestamp": message.get("timestamp"),
                                "sender": message.get("sender"),
                            },
                            reason="Media file not found in filesystem",
                            context={
                                "expected_filename": filename,
                            },
                        )

        # Execute batch hash with deduplication (no file copying)
        # Files will be copied directly from source to final output during processing
        if copy_tasks:
            results = self.hash_with_dedup(copy_tasks)

            # Update messages with filenames and source paths
            for task in copy_tasks:
                result = results.get(task.source_path)
                if result:
                    path_key = str(task.source_path)
                    if path_key in path_to_messages:
                        for conv, msg, media_path in path_to_messages[path_key]:
                            # Replace media_path with dest_filename and source_path in message
                            if "media_files" not in msg:
                                msg["media_files"] = []
                            if "source_paths" not in msg:
                                msg["source_paths"] = {}
                            if result.dest_filename not in msg["media_files"]:
                                msg["media_files"].append(result.dest_filename)
                                msg["source_paths"][result.dest_filename] = str(
                                    result.source_path
                                )

            # Update stats
            unique_files = sum(1 for r in results.values() if not r.is_duplicate)
            duplicate_files = sum(1 for r in results.values() if r.is_duplicate)
            self.stats["media_copied"] = len(copy_tasks)
            self.stats["unique_files"] = unique_files
            self.stats["duplicate_files"] = duplicate_files

        # Clean up media_paths from messages (no longer needed)
        for conversation in conversations:
            for message in conversation["messages"]:
                if "media_paths" in message:
                    del message["media_paths"]

        # Report orphaned media files (media on disk but not referenced in HTML metadata)
        orphaned = set(file_catalog.keys()) - self._referenced_files
        self.stats["orphaned_media_files"] = len(orphaned)
        for filename in orphaned:
            self.log_message(
                "ORPHANED_MEDIA",
                f"Media file on disk not referenced in any HTML metadata: {filename}",
            )

            # Track orphaned media
            source_path = file_catalog[filename]
            self.failure_tracker.add_orphaned_media(
                media_path=source_path,
                reason="No matching metadata found",
                context={
                    "original_location": str(source_path),
                },
            )

        log.logger.info("   Copied %d files", self.stats["media_copied"])
        log.logger.info("   Unique files copied: %d", self.stats["unique_files"])
        log.logger.info("   Duplicates skipped: %d", self.stats["duplicate_files"])
        log.logger.info("   Missing files: %d", self.stats["missing_files"])
        log.logger.info(
            "   Orphaned media files: %d", self.stats["orphaned_media_files"]
        )

    def get_content_key(self) -> str:
        """Return the content key for Instagram Messages metadata."""
        return "conversations"

    @staticmethod
    def get_platform() -> str:
        """Return the platform identifier for Instagram Messages."""
        return "Instagram"

    @staticmethod
    def get_service() -> str:
        """Return the service identifier for Instagram Messages."""
        return "messages"

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Conversations": self.stats["total_conversations"],
            "Messages with media": self.stats["total_messages_with_media"],
            "Media files found": self.stats["total_media_files"],
            "Files copied": self.stats["media_copied"],
            "Missing files": self.stats["missing_files"],
            "Orphaned media files": self.stats["orphaned_media_files"],
            "Unique files": self.stats["unique_files"],
            "Duplicates avoided": self.stats["duplicate_files"],
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
        # Build file catalog
        file_catalog = self.build_file_catalog()

        # Create metadata by parsing HTML files
        conversations = self.create_metadata()

        # Copy media files to output directory
        self.copy_media_files(conversations, file_catalog)

        # Save metadata
        self.save_metadata(
            conversations,
            extra_export_info={
                "total_conversations": self.stats["total_conversations"],
                "total_messages_with_media": self.stats["total_messages_with_media"],
                "total_media_files": self.stats["media_copied"],
                "unique_files": self.stats["unique_files"],
                "duplicate_files": self.stats["duplicate_files"],
            },
        )

        return True
