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
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import sys
import argparse
from collections import defaultdict
from html.parser import HTMLParser
from threading import Lock
import multiprocessing

from common.file_utils import detect_and_correct_extension
from common.utils import ALL_MEDIA_EXTENSIONS
from common.filter_banned_files import BannedFilesFilter
from common.failure_tracker import FailureTracker

# Set up logging
logger = logging.getLogger(__name__)


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
            and "sender" in attrs_dict.get("class", "")
        ):
            self.in_sender = True
            self.current_phone = None
        elif self.in_sender and tag == "a" and "tel" in attrs_dict.get("class", ""):
            # Capture phone number from href
            href = attrs_dict.get("href", "")
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


class GoogleVoicePreprocessor:
    """Preprocesses Google Voice message export by organizing files and cleaning metadata"""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        self.export_path = Path(export_path)
        self.voice_dir = self.export_path / "Voice" / "Calls"

        # Output directories
        output_base = Path(output_dir) if output_dir else self.export_path
        self.output_dir = output_base
        self.media_output_dir = output_base / "media"
        self.metadata_file = output_base / "metadata.json"
        self.log_file = output_base / "preprocessing.log"

        # Final output directory for processor (for failure tracking)
        self.final_output_dir = Path(final_output_dir) if final_output_dir else output_base

        # Export username (detected from directory name)
        self.export_username = None

        # Initialize banned files filter
        self.banned_filter = BannedFilesFilter()

        # Initialize failure tracker
        self.failure_tracker = FailureTracker(
            processor_name="Google Voice",
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
                f.write("Google Voice Messages Preprocessing Log\n")
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

        if not self.voice_dir.exists():
            print(f"ERROR: Voice/Calls directory not found: {self.voice_dir}")
            return False

        # Check if at least one HTML file exists
        html_files = list(self.voice_dir.glob("*.html"))
        if not html_files:
            print(f"ERROR: No HTML files found in {self.voice_dir}")
            return False

        return True

    def detect_export_username(self) -> bool:
        """
        Detect export username using common extractor
        Supports patterns like:
        - google-{username}-YYYYMMDD
        - google-{username}-YYYY-MM-DD
        Returns True if successful, False otherwise
        """
        try:
            from common.utils import extract_username_from_export_dir

            username = extract_username_from_export_dir(str(self.export_path), "google")
            if username and username != "unknown":
                self.export_username = username
                print(f"   Detected export user: {self.export_username}")
                return True
        except Exception as e:
            self.log_message(
                "USERNAME_DETECTION_ERROR",
                "Failed to extract username using common utils",
                str(e),
            )

        self.log_message(
            "USERNAME_DETECTION_ERROR",
            f"Could not parse username from directory name: {self.export_path.name}",
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
        # Try exact match first
        if media_src in file_catalog:
            use_count = self.used_files[media_src]
            if use_count == 0:
                self.used_files[media_src] += 1
                return (media_src, file_catalog[media_src])

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
            test_name = media_src if media_src.endswith(ext) else media_src + ext
            if test_name in file_catalog:
                use_count = self.used_files[test_name]
                if use_count == 0:
                    self.used_files[test_name] += 1
                    if test_name != media_src:
                        self.stats["fuzzy_matches"] += 1
                    return (test_name, file_catalog[test_name])

        # Try stripping trailing "-1" (attachment index) if present
        # Some exports have -1 in HTML but not in actual filenames
        if media_src.endswith("-1"):
            alt_src = media_src[:-2]  # Remove the "-1"

            # Try exact match without -1
            if alt_src in file_catalog:
                use_count = self.used_files[alt_src]
                if use_count == 0:
                    self.used_files[alt_src] += 1
                    self.stats["fuzzy_matches"] += 1
                    return (alt_src, file_catalog[alt_src])

            # Try with extensions without -1
            for ext in extensions:
                test_name = alt_src if alt_src.endswith(ext) else alt_src + ext
                if test_name in file_catalog:
                    use_count = self.used_files[test_name]
                    if use_count == 0:
                        self.used_files[test_name] += 1
                        self.stats["fuzzy_matches"] += 1
                        return (test_name, file_catalog[test_name])

        # Try prefix matching (for files with multiple attachments)
        for filename, path in file_catalog.items():
            if filename.startswith(media_src):
                use_count = self.used_files[filename]
                if use_count == 0:
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

        print("\nScanning text conversation files...")

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

        print(f"   Processed {self.stats['total_conversations']} unique conversations")
        logger.info(f"   Found {self.stats['total_messages_with_media']} messages with media")

        return conversations

    def copy_media_files(self, conversations: List[Dict]) -> None:
        """Copy media files referenced in conversations to output directory"""
        # Create output media directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        print("\nCopying media files...")

        # Build media catalog
        media_catalog = self.build_media_catalog()
        self.stats["total_media_files"] = len(media_catalog)
        logger.info(f"   Found {self.stats['total_media_files']} media files")

        # Track destination filenames to avoid collisions
        destination_files = {}  # maps destination_filename -> source_path

        # Reset used files tracker
        self.used_files.clear()

        for conversation in conversations:
            for message in conversation["messages"]:
                media_src_list = message.get("media_src", [])
                copied_files = []

                for media_src in media_src_list:
                    # Find the file using fuzzy matching
                    result = self.find_media_file(media_src, media_catalog)

                    if result:
                        matched_filename, source_path = result

                        # Detect actual file type and correct extension if needed using shared utility
                        correct_ext = detect_and_correct_extension(
                            source_path,
                            matched_filename,
                            log_callback=lambda msg, details: self.log_message("EXTENSION_CORRECTED", msg, details),
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

                        # Create destination filename, handling duplicates
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
                                        f"{base}_dup{counter}.{ext}"
                                        if ext
                                        else f"{base}_dup{counter}"
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

                # Update message with matched filenames
                message["media_files"] = copied_files
                # Remove media_src as it's no longer needed
                del message["media_src"]

        logger.info(f"   Copied {self.stats['media_copied']} files")
        print(f"   Fuzzy matches: {self.stats['fuzzy_matches']}")
        print(f"   Missing files: {self.stats['missing_files']}")
        
        # Track orphaned media (files in filesystem that were never copied)
        print("\nScanning for orphaned media files...")
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
        logger.info("Starting Google Voice Messages Preprocessing")
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

        # Scan and parse conversations
        conversations = self.scan_conversations()

        # Copy media files to output directory
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
        description="Preprocess Google Voice messages export: organize media and create cleaned metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process in-place (creates media/ and metadata.json in export directory)
  python modules/preprocess_files.py originals/google-hexffedit-2025-09-21

  # Process to a separate output directory
  python modules/preprocess_files.py originals/google-hexffedit-2025-09-21 -o processed/
  
  # Process with custom number of workers
  python modules/preprocess_files.py originals/google-hexffedit-2025-09-21 --workers 8
        """,
    )

    parser.add_argument(
        "export_directory",
        type=str,
        help="Path to Google Voice export directory (contains Voice/Calls/)",
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

    preprocessor = GoogleVoicePreprocessor(export_path, output_path, workers=args.workers)
    preprocessor.process()


if __name__ == "__main__":
    main()
