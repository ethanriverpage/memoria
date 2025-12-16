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
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import sys
import argparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import multiprocessing
from common.filter_banned_files import BannedFilesFilter
from common.progress import PHASE_PREPROCESS, futures_progress
from common.failure_tracker import FailureTracker

# Set up logging
logger = logging.getLogger(__name__)

class InstagramMessagesPreprocessor:
    """Preprocesses Instagram message export by organizing files and cleaning metadata"""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        self.export_path = Path(export_path)

        # Detect export format: new (2025+) vs legacy (2022)
        new_format_inbox = self.export_path / "your_instagram_activity" / "messages" / "inbox"
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

        # Output directories
        output_base = Path(output_dir) if output_dir else self.export_path
        self.output_dir = output_base
        self.media_output_dir = output_base / "media"
        self.metadata_file = output_base / "metadata.json"
        self.log_file = output_base / "preprocessing.log"

        # Final output directory for processor (for failure tracking)
        self.final_output_dir = Path(final_output_dir) if final_output_dir else output_base

        # Initialize banned files filter
        self.banned_filter = BannedFilesFilter()

        # Initialize failure tracker
        self.failure_tracker = FailureTracker(
            processor_name="Instagram Messages",
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
            "orphaned_files": 0,
            "missing_files": 0,
            "banned_files_skipped": 0,
        }

        # Log entries
        self.log_entries = []

        # Deleted user counter
        self.deleted_user_counter = 0
        self.deleted_user_mapping = {}

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
                f.write("Instagram Messages Preprocessing Log\n")
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

        if not self.messages_dir.exists():
            print(f"ERROR: Messages directory not found: {self.messages_dir}")
            return False

        # Check if at least one conversation folder exists
        conversation_folders = [d for d in self.messages_dir.iterdir() if d.is_dir()]
        if not conversation_folders:
            print(f"ERROR: No conversation folders found in {self.messages_dir}")
            return False

        return True

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
            "%b %d, %Y %I:%M %p",   # New format: "Sep 22, 2017 6:33 am"
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

    def parse_html_file(self, html_path: Path, conversation_id: str) -> List[Dict]:
        """
        Parse an HTML file and extract messages with media
        Returns list of message dictionaries (only messages with media)
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
                    sender_elem = container.find("div", class_="_3-95 _2pim _a6-h _a6-i")
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
                    logger.debug(f"Constructed metadata for message in {html_path.name}:")
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
            print(f"ERROR: Failed to parse {html_path.name}: {e}")

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
            logger.error(f"Failed to scan conversations: {e}")

        return conversations

    def build_file_catalog(self) -> Dict[str, Path]:
        """
        Scan all photos directories and build catalog mapping filename -> full source path
        Returns: Dict[filename, source_path]
        """
        catalog = {}

        if not self.messages_dir.exists():
            return catalog

        print("\nScanning media directories...")

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

        logger.info(f"   Found {len(catalog)} media files")

        return catalog

    def copy_media_files(
        self, conversations: List[Dict], file_catalog: Dict[str, Path]
    ) -> None:
        """Copy media files referenced in metadata to output directory"""
        # Create output media directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        print("\nCopying media files...")

        matched_files = set()

        for conversation in conversations:
            for message in conversation["messages"]:
                media_paths = message.get("media_paths", [])
                copied_files = []

                for media_path in media_paths:
                    # Extract filename from path
                    filename = Path(media_path).name

                    if filename in file_catalog:
                        source_path = file_catalog[filename]
                        dest_path = self.media_output_dir / filename

                        try:
                            shutil.copy2(source_path, dest_path)
                            copied_files.append(filename)
                            matched_files.add(filename)
                            self.stats["media_copied"] += 1
                        except Exception as e:
                            self.log_message(
                                "COPY_ERROR",
                                f"Failed to copy {filename}",
                                str(e),
                            )
                            print(f"ERROR: Failed to copy {filename}: {e}")
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
                                "conversation_title": conversation["conversation_title"],
                                "media_path": media_path,
                                "message_timestamp": message.get("timestamp"),
                                "sender": message.get("sender"),
                            },
                            reason="Media file not found in filesystem",
                            context={
                                "expected_filename": filename,
                            },
                        )

                # Update message with just filenames (not full paths)
                message["media_files"] = copied_files
                # Remove media_paths as it's no longer needed
                del message["media_paths"]

        # Report orphaned files (in filesystem but not referenced in HTML)
        orphaned = set(file_catalog.keys()) - matched_files
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

        logger.info(f"   Copied {self.stats['media_copied']} files")
        print(f"   Missing files: {self.stats['missing_files']}")
        print(f"   Orphaned files: {self.stats['orphaned_files']}")

    def _process_single_conversation(self, conversation_tuple: Tuple[str, Path]) -> Optional[Dict]:
        """
        Process a single conversation (used by multithreaded processing)
        
        Args:
            conversation_tuple: Tuple of (conversation_id, html_path)
            
        Returns:
            Conversation data dict or None if conversation has no media messages
        """
        conversation_id, html_path = conversation_tuple
        
        # Extract conversation title
        conversation_title = self.extract_conversation_title(
            html_path, conversation_id
        )

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

        print("\nProcessing conversation files...")

        # Scan all conversation folders
        conversation_list = self.scan_conversations()
        logger.info(f"   Found {len(conversation_list)} conversations")

        print(f"Processing {len(conversation_list)} conversations (using {self.workers} workers)...")

        # Process conversations in parallel
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # Submit all conversation processing tasks
            future_to_conversation = {
                executor.submit(self._process_single_conversation, conv_tuple): conv_tuple[0]
                for conv_tuple in conversation_list
            }
            
            # Collect results as they complete
            for future in futures_progress(future_to_conversation, PHASE_PREPROCESS, "Parsing conversations", unit="conv"):
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

    def save_metadata(self, conversations: List[Dict]) -> None:
        """Save cleaned metadata to metadata.json"""
        try:
            # Add export info
            output = {
                "export_info": {
                    "export_path": str(self.export_path),
                    "export_name": self.export_path.name,
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
        print(f"Missing files:                    {self.stats['missing_files']:>6}")
        print(f"Orphaned files:                   {self.stats['orphaned_files']:>6}")
        print(
            f"Banned files skipped:             {self.stats['banned_files_skipped']:>6}"
        )
        print("=" * 70)

    def process(self) -> None:
        """Main processing pipeline"""
        logger.info("Starting Instagram Messages Preprocessing")
        print(f"Export path: {self.export_path}")
        print(f"Output path: {self.output_dir}\n")

        # Validate export structure
        if not self.validate_export():
            sys.exit(1)

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

        logger.info("Preprocessing complete!")


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

    preprocessor = InstagramMessagesPreprocessor(export_path, output_path, workers=args.workers)
    preprocessor.process()


if __name__ == "__main__":
    main()
