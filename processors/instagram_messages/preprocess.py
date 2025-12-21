#!/usr/bin/env python3
"""
Instagram Messages Preprocessor

Organizes Instagram message export files and creates cleaned metadata:
- Parses HTML files containing message metadata
- Copies media files to organized output directory
- Creates metadata.json with essential information (sender, timestamps, conversation)
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import argparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

from common.detection import resolve_username, detect_from_dirname, DetectionResult
from common.output.logging import get_processor_logger
from common.processing import print_preprocessing_stats
from common.output.progress import PHASE_PREPROCESS, futures_progress
from processors.preprocess_base import PreprocessorBase, CopyTask

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class InstagramMessagesPreprocessor(PreprocessorBase):
    """Preprocesses Instagram message export by organizing files and cleaning metadata"""

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

        # Statistics specific to this preprocessor
        self.stats = {
            "total_conversations": 0,
            "total_messages_with_media": 0,
            "total_media_files": 0,
            "media_copied": 0,
            "orphaned_files": 0,
            "missing_files": 0,
            "banned_files_skipped": 0,
            "unique_files": 0,
            "duplicate_files": 0,
        }

        # Deleted user counter
        self.deleted_user_counter = 0
        self.deleted_user_mapping = {}

        # Track which files were referenced in messages
        self._referenced_files: set = set()

    def validate_export(self) -> bool:
        """Validate that export directory has required structure"""
        if not self.export_path.exists():
            log.file_not_found(str(self.export_path), "Export path")
            return False

        if not self.messages_dir.exists():
            log.file_not_found(str(self.messages_dir), "Messages directory")
            return False

        # Check if at least one conversation folder exists
        conversation_folders = [d for d in self.messages_dir.iterdir() if d.is_dir()]
        if not conversation_folders:
            log.logger.error("No conversation folders found in %s", self.messages_dir)
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
        Convert timestamp string to ISO format
        Input formats:
          - New (2025+): "Sep 22, 2017 6:33 am"
          - Legacy (2022): "Sep 22, 2017, 6:33 AM" (extra comma after year)
        Output format: "2017-09-22 06:33:00"
        """
        # Try both timestamp formats (new format first, then legacy with comma)
        formats = [
            "%b %d, %Y %I:%M %p",  # New format: "Sep 22, 2017 6:33 am"
            "%b %d, %Y, %I:%M %p",  # Legacy format: "Sep 22, 2017, 6:33 AM"
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(timestamp_str, fmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")
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
            # Find all <a> tags with href containing "/photos/"
            links = message_element.find_all("a", href=True)
            for link in links:
                href = link.get("href", "")
                if "/photos/" in href:
                    media_paths.append(href)

            # Also check <img> tags with src containing "/photos/"
            images = message_element.find_all("img", src=True)
            for img in images:
                src = img.get("src", "")
                if "/photos/" in src and src not in media_paths:
                    media_paths.append(src)

        except Exception as e:
            self.log_message(
                "MEDIA_PATH_EXTRACT_ERROR",
                "Failed to extract media paths",
                str(e),
            )

        return media_paths

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

                # Only include messages with media
                if media_paths:
                    message_data = {
                        "sender": sender,
                        "timestamp": timestamp,
                        "timestamp_raw": timestamp_raw,
                        "media_paths": media_paths,
                    }

                    # Log constructed metadata
                    logger.debug(
                        f"Constructed metadata for message in {html_path.name}:"
                    )
                    logger.debug(f"  Sender: {sender}")
                    logger.debug(f"  Timestamp: {timestamp} (raw: {timestamp_raw})")
                    logger.debug(f"  Media files: {len(media_paths)} file(s)")
                    for media_path in media_paths:
                        logger.debug(f"    - {media_path}")

                    messages.append(message_data)

        except Exception as e:
            self.log_message(
                "HTML_PARSE_ERROR",
                f"Failed to parse {html_path.name}",
                str(e),
            )
            log.parse_error(html_path.name, e)

        return messages

    def scan_conversations(self) -> List[Tuple[str, Path]]:
        """
        Scan messages/inbox directory for all conversation folders
        Returns list of (conversation_id, html_path) tuples
        """
        conversations = []

        try:
            # Get all subdirectories
            import os

            with os.scandir(self.messages_dir) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    if not entry.is_dir():
                        continue

                    conv_dir = Path(entry.path)

                    # Skip banned directories
                    if self.banned_filter.is_banned(conv_dir):
                        self.log_message(
                            "BANNED_DIRECTORY",
                            f"Skipping banned directory: {entry.name}",
                        )
                        with self.stats_lock:
                            self.stats["banned_files_skipped"] += 1
                        continue

                    conversation_id = entry.name
                    html_path = conv_dir / "message_1.html"

                    if html_path.exists():
                        conversations.append((conversation_id, html_path))
                    else:
                        self.log_message(
                            "MISSING_HTML",
                            f"No message_1.html found in {conversation_id}",
                        )

        except Exception as e:
            self.log_message(
                "SCAN_ERROR",
                "Failed to scan conversation folders",
                str(e),
            )
            log.logger.error("Failed to scan conversations: %s", e)

        return conversations

    def build_file_catalog(self) -> Dict[str, Path]:
        """
        Scan all photos directories and build catalog mapping filename -> full source path
        Returns: Dict[filename, source_path]
        """
        catalog = {}

        if not self.messages_dir.exists():
            return catalog

        log.logger.info("Scanning media directories...")

        # Recursively find all media files in photos directories
        import os

        with os.scandir(self.messages_dir) as entries:
            for entry in entries:
                if not entry.is_dir():
                    continue

                conv_dir = Path(entry.path)

                # Skip banned directories
                if self.banned_filter.is_banned(conv_dir):
                    continue

                photos_dir = conv_dir / "photos"
                if not photos_dir.exists():
                    continue

                for file_path in photos_dir.rglob("*"):
                    if file_path.is_file():
                        # Skip banned files
                        if self.banned_filter.is_banned(file_path):
                            self.log_message(
                                "BANNED_FILE",
                                f"Skipping banned file: {file_path.name}",
                            )
                            self.stats["banned_files_skipped"] += 1
                            continue

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

        # Process conversations in parallel
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # Submit all conversation processing tasks
            future_to_conversation = {
                executor.submit(
                    self._process_single_conversation, conv_tuple
                ): conv_tuple[0]
                for conv_tuple in conversation_list
            }

            # Collect results as they complete
            for future in futures_progress(
                future_to_conversation,
                PHASE_PREPROCESS,
                "Parsing conversations",
                unit="conv",
            ):
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
                    log.logger.error(
                        "Failed to process conversation %s: %s", conversation_id, e
                    )

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
        """Copy media files referenced in metadata to output directory.

        Uses batch copy_with_dedup for content-based deduplication.
        """
        # Create output media directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

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

        # Execute batch copy with deduplication
        if copy_tasks:
            results = self.copy_with_dedup(copy_tasks)

            # Update messages with copied filenames
            for task in copy_tasks:
                result = results.get(task.source_path)
                if result:
                    path_key = str(task.source_path)
                    if path_key in path_to_messages:
                        for conv, msg, media_path in path_to_messages[path_key]:
                            # Replace media_path with dest_filename in message
                            if "media_files" not in msg:
                                msg["media_files"] = []
                            if result.dest_filename not in msg["media_files"]:
                                msg["media_files"].append(result.dest_filename)

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

        # Report orphaned files (in filesystem but not referenced in HTML)
        orphaned = set(file_catalog.keys()) - self._referenced_files
        self.stats["orphaned_files"] = len(orphaned)
        for filename in orphaned:
            self.log_message(
                "ORPHANED_FILE",
                f"File in filesystem not referenced in any HTML: {filename}",
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
        log.logger.info("   Orphaned files: %d", self.stats["orphaned_files"])

    def save_metadata(self, conversations: List[Dict]) -> None:
        """Save cleaned metadata to metadata.json"""
        try:
            # Detect username from export
            detection_result = self.detect_username()
            export_username = detection_result.username or "unknown"

            # Add export info
            output = {
                "export_info": {
                    "export_path": str(self.export_path),
                    "export_name": self.export_path.name,
                    "export_username": export_username,
                    "username_source": detection_result.source,
                    "processed_date": datetime.now().isoformat(),
                    "total_conversations": self.stats["total_conversations"],
                    "total_messages_with_media": self.stats[
                        "total_messages_with_media"
                    ],
                    "total_media_files": self.stats["media_copied"],
                    "unique_files": self.stats["unique_files"],
                    "duplicate_files": self.stats["duplicate_files"],
                },
                "conversations": conversations,
            }

            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(output, f, indent=2, ensure_ascii=False)

            log.saved_metadata(str(self.metadata_file))
        except Exception as e:
            log.logger.error("Failed to save metadata: %s", e)
            raise RuntimeError(f"Failed to save metadata: {e}")

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Conversations": self.stats["total_conversations"],
            "Messages with media": self.stats["total_messages_with_media"],
            "Media files found": self.stats["total_media_files"],
            "Files copied": self.stats["media_copied"],
            "Missing files": self.stats["missing_files"],
            "Orphaned files": self.stats["orphaned_files"],
            "Unique files": self.stats["unique_files"],
            "Duplicates avoided": self.stats["duplicate_files"],
            "Banned skipped": self.stats["banned_files_skipped"],
        }
        print_preprocessing_stats("Preprocessing Stats", stats)

    def process(self) -> bool:
        """Main processing pipeline

        Returns:
            True if preprocessing succeeded, False otherwise
        """
        log.preprocessor_start(
            "Instagram Messages", str(self.export_path), str(self.output_dir)
        )

        # Validate export structure
        if not self.validate_export():
            return False

        # Build file catalog
        file_catalog = self.build_file_catalog()

        # Create metadata by parsing HTML files
        conversations = self.create_metadata()

        # Copy media files to output directory
        self.copy_media_files(conversations, file_catalog)

        # Save metadata
        self.save_metadata(conversations)

        # Save log file
        self.save_log()

        # Handle failures (copy orphaned files, generate report)
        self.failure_tracker.handle_failures(self.final_output_dir)

        # Print statistics
        self.print_statistics()

        log.preprocessing_complete()
        return True


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Preprocess Instagram messages export: organize media and create cleaned metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process in-place (creates media/ and metadata.json in export directory)
  python modules/preprocess_files.py instagram-username-2025-10-07

  # Process to a separate output directory
  python modules/preprocess_files.py instagram-username-2025-10-07 -o processed/
  
  # Process with custom number of workers
  python modules/preprocess_files.py instagram-username-2025-10-07 --workers 8
        """,
    )

    parser.add_argument(
        "export_directory",
        type=str,
        help="Path to Instagram export directory (contains your_instagram_activity/)",
    )

    parser.add_argument(
        "-o",
        "--output",
        type=str,
        required=True,
        help="Output directory for preprocessed files (required to prevent writing to exports)",
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

    preprocessor = InstagramMessagesPreprocessor(
        export_path, output_path, workers=args.workers
    )
    preprocessor.process()


if __name__ == "__main__":
    main()
