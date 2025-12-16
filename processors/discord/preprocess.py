#!/usr/bin/env python3
"""
Discord Export Preprocessor

Parses Discord data exports, downloads media attachments from CDN URLs,
and creates cleaned metadata for processing.

Discord exports are obtained via "Request My Data" in User Settings.
Only messages sent by the exporting user are included.
"""

import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote
import multiprocessing

import requests
import xxhash

from common.filter_banned_files import BannedFilesFilter
from common.failure_tracker import FailureTracker
from common.progress import PHASE_PREPROCESS, futures_progress

# Set up logging
logger = logging.getLogger(__name__)

# Supported media extensions for Discord attachments
MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp",  # Images
    ".mp4", ".webm", ".mov",  # Videos
    ".mp3", ".wav", ".ogg", ".flac",  # Audio
}


def extract_filename_from_url(url: str) -> str:
    """Extract original filename from Discord CDN URL.

    URL format: https://cdn.discordapp.com/attachments/{ch}/{att}/{filename}?...

    Args:
        url: Discord CDN attachment URL

    Returns:
        Original filename from the URL path
    """
    try:
        parsed = urlparse(url)
        path_parts = parsed.path.split("/")
        if path_parts:
            # URL decode the filename
            return unquote(path_parts[-1])
    except Exception:
        pass
    return "unknown"


def is_media_file(filename: str) -> bool:
    """Check if filename has a supported media extension.

    Args:
        filename: Filename to check

    Returns:
        True if file has a media extension
    """
    ext = Path(filename).suffix.lower()
    return ext in MEDIA_EXTENSIONS


def parse_discord_timestamp(timestamp: str) -> datetime:
    """Parse Discord timestamp to datetime.

    Args:
        timestamp: Discord timestamp in "YYYY-MM-DD HH:MM:SS" format

    Returns:
        datetime object (UTC)
    """
    return datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")


def extract_username_from_directory(dir_path: Path) -> str:
    """Extract username from Discord export directory name.

    Directory format: discord-username-YYYYMMDD

    Args:
        dir_path: Path to Discord export directory

    Returns:
        Extracted username or "unknown"
    """
    dir_name = dir_path.name
    # Pattern: discord-{username}-YYYYMMDD or similar variations
    match = re.match(r"discord[-_](.+?)[-_]\d{8}", dir_name, re.IGNORECASE)
    if match:
        return match.group(1)
    return "unknown"


class DiscordPreprocessor:
    """Preprocesses Discord export by downloading attachments and organizing metadata."""

    # Download configuration
    DOWNLOAD_TIMEOUT = 30  # seconds
    MAX_RETRIES = 3
    RETRY_BACKOFF = 2  # exponential backoff base

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        """Initialize Discord preprocessor.

        Args:
            export_path: Path to Discord export directory
            output_dir: Output directory for processed files
            workers: Number of parallel workers for downloads
            final_output_dir: Final output directory for failure tracking
        """
        self.export_path = Path(export_path)
        self.messages_dir = self.export_path / "Messages"
        self.servers_dir = self.export_path / "Servers"
        self.index_file = self.messages_dir / "index.json"

        # Output directories
        output_base = Path(output_dir) if output_dir else self.export_path
        self.output_dir = output_base
        self.media_output_dir = output_base / "media"
        self.metadata_file = output_base / "metadata.json"
        self.log_file = output_base / "preprocessing.log"

        # Final output directory for processor (for failure tracking)
        self.final_output_dir = (
            Path(final_output_dir) if final_output_dir else output_base
        )

        # Initialize banned files filter
        self.banned_filter = BannedFilesFilter()

        # Initialize failure tracker
        self.failure_tracker = FailureTracker(
            processor_name="Discord",
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
        self.filename_lock = Lock()
        self.dedup_lock = Lock()

        # Track used filenames to avoid collisions
        self.used_filenames: Dict[str, int] = {}

        # Content hash registry for deduplication
        # Maps content_hash -> {filename, first_occurrence: {channel_id, message_id, timestamp, content}}
        self.content_hashes: Dict[str, Dict] = {}

        # Statistics
        self.stats = {
            "total_channels": 0,
            "total_messages": 0,
            "messages_with_attachments": 0,
            "total_attachments": 0,
            "downloads_successful": 0,
            "downloads_failed": 0,
            "downloads_skipped": 0,  # Non-media files
            "banned_files_skipped": 0,
            "unique_files": 0,
            "duplicate_files": 0,
        }

        # Log entries
        self.log_entries: List[str] = []

        # Channel index cache
        self.channel_index: Dict[str, str] = {}

        # Server index cache
        self.server_index: Dict[str, str] = {}

    def log_message(self, category: str, message: str, details: str = "") -> None:
        """Add a log entry (thread-safe).

        Args:
            category: Log category (e.g., "DOWNLOAD_ERROR")
            message: Log message
            details: Optional additional details
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] {category}: {message}"
        if details:
            entry += f" ({details})"
        with self.log_lock:
            self.log_entries.append(entry)

    def save_log(self) -> None:
        """Save log entries to log file."""
        if not self.log_entries:
            return

        try:
            with open(self.log_file, "w", encoding="utf-8") as f:
                f.write("Discord Export Preprocessing Log\n")
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
        """Validate that export directory has required Discord structure.

        Returns:
            True if valid Discord export, False otherwise
        """
        if not self.export_path.exists():
            print(f"ERROR: Export path does not exist: {self.export_path}")
            return False

        if not self.messages_dir.exists():
            print(f"ERROR: Messages directory not found: {self.messages_dir}")
            return False

        if not self.index_file.exists():
            print(f"ERROR: Messages index.json not found: {self.index_file}")
            return False

        # Check for at least one channel folder
        channel_folders = [
            d for d in self.messages_dir.iterdir()
            if d.is_dir() and d.name.startswith("c")
        ]
        if not channel_folders:
            print(f"ERROR: No channel folders found in {self.messages_dir}")
            return False

        return True

    def load_channel_index(self) -> Dict[str, str]:
        """Load Messages/index.json mapping channel IDs to names.

        Returns:
            Dict mapping channel_id -> channel description
        """
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                self.channel_index = json.load(f)
            logger.info(f"Loaded {len(self.channel_index)} channel mappings")
            return self.channel_index
        except Exception as e:
            logger.error(f"Failed to load channel index: {e}")
            return {}

    def load_server_index(self) -> Dict[str, str]:
        """Load Servers/index.json mapping server IDs to names.

        Returns:
            Dict mapping server_id -> server name
        """
        server_index_file = self.servers_dir / "index.json"
        if not server_index_file.exists():
            return {}

        try:
            with open(server_index_file, "r", encoding="utf-8") as f:
                self.server_index = json.load(f)
            logger.info(f"Loaded {len(self.server_index)} server mappings")
            return self.server_index
        except Exception as e:
            logger.warning(f"Failed to load server index: {e}")
            return {}

    def parse_channel_json(self, channel_dir: Path) -> Optional[Dict]:
        """Parse channel.json to extract channel context.

        Args:
            channel_dir: Path to channel directory (Messages/c{id}/)

        Returns:
            Channel metadata dict or None if parsing fails
        """
        channel_file = channel_dir / "channel.json"
        if not channel_file.exists():
            return None

        try:
            with open(channel_file, "r", encoding="utf-8") as f:
                channel_data = json.load(f)

            channel_id = channel_data.get("id", channel_dir.name[1:])  # Strip 'c' prefix
            channel_type = channel_data.get("type", "UNKNOWN")

            # Determine conversation type and title
            if channel_type == "DM":
                conv_type = "dm"
                # Try to get title from index
                title = self.channel_index.get(
                    channel_id, "Direct Message"
                )
                guild_name = None
            elif channel_type == "GROUP_DM":
                conv_type = "group_dm"
                title = self.channel_index.get(channel_id, "Group DM")
                guild_name = None
            elif channel_type in ("GUILD_TEXT", "PUBLIC_THREAD", "PRIVATE_THREAD"):
                conv_type = "server"
                channel_name = channel_data.get("name", "unknown")
                guild_info = channel_data.get("guild", {})
                guild_name = guild_info.get("name")
                if guild_name:
                    title = f"{channel_name} in {guild_name}"
                else:
                    title = channel_name
            else:
                conv_type = "unknown"
                title = self.channel_index.get(channel_id, "Unknown Channel")
                guild_name = None

            return {
                "id": channel_id,
                "type": conv_type,
                "title": title,
                "guild_name": guild_name,
                "raw_type": channel_type,
            }

        except Exception as e:
            logger.warning(f"Failed to parse channel.json in {channel_dir}: {e}")
            return None

    def parse_messages_json(self, channel_dir: Path) -> List[Dict]:
        """Parse messages.json to extract messages with attachments.

        Args:
            channel_dir: Path to channel directory

        Returns:
            List of message dicts with attachment info
        """
        messages_file = channel_dir / "messages.json"
        if not messages_file.exists():
            return []

        try:
            with open(messages_file, "r", encoding="utf-8") as f:
                messages = json.load(f)

            if not isinstance(messages, list):
                logger.warning(f"messages.json is not a list in {channel_dir}")
                return []

            return messages

        except Exception as e:
            logger.warning(f"Failed to parse messages.json in {channel_dir}: {e}")
            return []

    def generate_unique_filename(self, original_filename: str, message_id: int) -> str:
        """Generate a unique filename for downloaded attachment.

        Uses message ID to ensure uniqueness across channels.

        Args:
            original_filename: Original filename from URL
            message_id: Discord message snowflake ID

        Returns:
            Unique filename for the attachment
        """
        with self.filename_lock:
            # Create base filename with message ID prefix for uniqueness
            base_name = Path(original_filename).stem
            ext = Path(original_filename).suffix.lower()

            # Sanitize filename
            base_name = re.sub(r'[<>:"/\\|?*]', "_", base_name)

            # Truncate base_name to prevent overly long filenames
            # Reserve space for message_id (max 20 chars), underscore, extension, and counter
            max_base_len = 150
            if len(base_name) > max_base_len:
                base_name = base_name[:max_base_len]

            # Create filename with message ID
            filename = f"{message_id}_{base_name}{ext}"

            # Handle duplicates (same message with multiple attachments)
            if filename in self.used_filenames:
                self.used_filenames[filename] += 1
                count = self.used_filenames[filename]
                filename = f"{message_id}_{base_name}_{count}{ext}"
            else:
                self.used_filenames[filename] = 0

            return filename

    def download_attachment(
        self, url: str, output_path: Path
    ) -> Tuple[bool, Optional[str]]:
        """Download single attachment with retry logic.

        Args:
            url: Discord CDN URL
            output_path: Path to save downloaded file

        Returns:
            Tuple of (success, error_message)
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                response = requests.get(
                    url,
                    timeout=self.DOWNLOAD_TIMEOUT,
                    stream=True,
                    headers={
                        "User-Agent": "Mozilla/5.0 (compatible; DiscordExportProcessor/1.0)"
                    },
                )
                response.raise_for_status()

                # Ensure parent directory exists
                output_path.parent.mkdir(parents=True, exist_ok=True)

                # Write file in chunks
                with open(output_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)

                return True, None

            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    return False, "File not found (404) - URL may have expired"
                elif e.response.status_code == 403:
                    return False, "Access denied (403) - URL may have expired"
                error_msg = f"HTTP {e.response.status_code}"
            except requests.exceptions.Timeout:
                error_msg = "Request timed out"
            except requests.exceptions.ConnectionError:
                error_msg = "Connection error"
            except requests.exceptions.RequestException as e:
                error_msg = str(e)
            except Exception as e:
                error_msg = f"Unexpected error: {e}"

            # Retry with exponential backoff
            if attempt < self.MAX_RETRIES - 1:
                sleep_time = self.RETRY_BACKOFF ** attempt
                time.sleep(sleep_time)

        return False, error_msg

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

    def _download_single_attachment(
        self, args: Tuple[str, str, int, str, str]
    ) -> Dict:
        """Worker function to download a single attachment.

        Downloads the file, computes its hash for deduplication, and either
        keeps it (if unique) or removes it and references the existing file
        (if duplicate).

        Args:
            args: Tuple of (url, channel_id, message_id, timestamp, content)

        Returns:
            Result dict with download status, metadata, and deduplication info
        """
        url, channel_id, message_id, timestamp, content = args

        # Extract original filename
        original_filename = extract_filename_from_url(url)

        # Check if this is a media file
        if not is_media_file(original_filename):
            with self.stats_lock:
                self.stats["downloads_skipped"] += 1
            return {
                "success": False,
                "skipped": True,
                "url": url,
                "reason": "Non-media file",
            }

        # Check if file is banned
        if self.banned_filter.is_banned(Path(original_filename)):
            with self.stats_lock:
                self.stats["banned_files_skipped"] += 1
            self.log_message(
                "BANNED_FILE",
                f"Skipping banned file: {original_filename}",
            )
            return {
                "success": False,
                "skipped": True,
                "url": url,
                "reason": "Banned file pattern",
            }

        # Generate unique filename
        output_filename = self.generate_unique_filename(original_filename, message_id)
        output_path = self.media_output_dir / output_filename

        # Download the file
        success, error_msg = self.download_attachment(url, output_path)

        if not success:
            with self.stats_lock:
                self.stats["downloads_failed"] += 1
            self.log_message(
                "DOWNLOAD_FAILED",
                f"Failed to download: {original_filename}",
                f"URL: {url[:100]}..., Error: {error_msg}",
            )
            self.failure_tracker.add_orphaned_metadata(
                metadata_entry={
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "url": url,
                    "filename": original_filename,
                },
                reason=f"Download failed: {error_msg}",
                context={
                    "timestamp": timestamp,
                    "content": content[:100] if content else "",
                },
            )
            return {
                "success": False,
                "skipped": False,
                "url": url,
                "error": error_msg,
            }

        # File downloaded successfully - compute hash for deduplication
        try:
            content_hash = self._compute_file_hash(output_path)
        except Exception as e:
            logger.warning(f"Failed to hash {output_path}: {e}")
            # Continue without deduplication if hashing fails
            with self.stats_lock:
                self.stats["downloads_successful"] += 1
                self.stats["unique_files"] += 1
            return {
                "success": True,
                "url": url,
                "filename": output_filename,
                "original_filename": original_filename,
                "channel_id": channel_id,
                "message_id": message_id,
                "timestamp": timestamp,
                "content": content,
                "is_duplicate": False,
            }

        # Check for duplicate using content hash
        with self.dedup_lock:
            if content_hash in self.content_hashes:
                # Duplicate found - use existing file
                existing_info = self.content_hashes[content_hash]
                existing_filename = existing_info["filename"]

                # Delete the duplicate file we just downloaded
                try:
                    os.remove(output_path)
                except Exception as e:
                    logger.warning(f"Failed to remove duplicate file {output_path}: {e}")

                with self.stats_lock:
                    self.stats["downloads_successful"] += 1
                    self.stats["duplicate_files"] += 1

                return {
                    "success": True,
                    "url": url,
                    "filename": existing_filename,  # Reference the existing file
                    "original_filename": original_filename,
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "timestamp": timestamp,
                    "content": content,
                    "is_duplicate": True,
                    "content_hash": content_hash,
                }
            else:
                # New unique file - register it
                self.content_hashes[content_hash] = {
                    "filename": output_filename,
                    "first_occurrence": {
                        "channel_id": channel_id,
                        "message_id": message_id,
                        "timestamp": timestamp,
                        "content": content,
                    },
                }

                with self.stats_lock:
                    self.stats["downloads_successful"] += 1
                    self.stats["unique_files"] += 1

                return {
                    "success": True,
                    "url": url,
                    "filename": output_filename,
                    "original_filename": original_filename,
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "timestamp": timestamp,
                    "content": content,
                    "is_duplicate": False,
                    "content_hash": content_hash,
                }

    def scan_channels(self) -> List[Path]:
        """Scan Messages directory for channel folders.

        Returns:
            List of channel directory paths
        """
        channels = []

        for item in sorted(self.messages_dir.iterdir()):
            if item.is_dir() and item.name.startswith("c"):
                # Validate channel has messages.json
                if (item / "messages.json").exists():
                    channels.append(item)

        return channels

    def build_metadata(
        self, channel_dirs: List[Path]
    ) -> Tuple[Dict, List[Tuple[str, str, int, str, str]]]:
        """Build metadata structure and collect attachment download tasks.

        Args:
            channel_dirs: List of channel directory paths

        Returns:
            Tuple of (metadata dict, list of download task tuples)
        """
        metadata = {
            "export_info": {
                "export_path": str(self.export_path),
                "export_username": extract_username_from_directory(self.export_path),
                "processed_date": datetime.now().isoformat(),
            },
            "conversations": {},
            "orphaned_media": [],
        }

        download_tasks = []

        for channel_dir in channel_dirs:
            with self.stats_lock:
                self.stats["total_channels"] += 1

            # Parse channel context
            channel_info = self.parse_channel_json(channel_dir)
            if not channel_info:
                # Fallback: use directory name
                channel_id = channel_dir.name[1:]  # Strip 'c' prefix
                channel_info = {
                    "id": channel_id,
                    "type": "unknown",
                    "title": self.channel_index.get(channel_id, "Unknown Channel"),
                    "guild_name": None,
                }

            channel_id = channel_info["id"]

            # Parse messages
            messages = self.parse_messages_json(channel_dir)
            with self.stats_lock:
                self.stats["total_messages"] += len(messages)

            # Process messages with attachments
            channel_messages = []
            for msg in messages:
                msg_id = msg.get("ID")
                timestamp = msg.get("Timestamp", "")
                content = msg.get("Contents", "")
                attachments_str = msg.get("Attachments", "")

                # Skip messages without attachments
                if not attachments_str:
                    continue

                with self.stats_lock:
                    self.stats["messages_with_attachments"] += 1

                # Split multiple attachments (space-separated)
                attachment_urls = [
                    url.strip()
                    for url in attachments_str.split(" ")
                    if url.strip() and url.strip().startswith("http")
                ]

                with self.stats_lock:
                    self.stats["total_attachments"] += len(attachment_urls)

                # Create download tasks for each attachment
                for url in attachment_urls:
                    download_tasks.append((url, channel_id, msg_id, timestamp, content))

                # Create message entry (will be populated with filenames after download)
                channel_messages.append({
                    "id": msg_id,
                    "timestamp": f"{timestamp} UTC" if timestamp else "",
                    "content": content,
                    "original_urls": attachment_urls,
                    "media_files": [],  # Populated after download
                })

            # Only add channel if it has messages with attachments
            if channel_messages:
                metadata["conversations"][channel_id] = {
                    "type": channel_info["type"],
                    "title": channel_info["title"],
                    "guild_name": channel_info.get("guild_name"),
                    "message_count": len(channel_messages),
                    "messages": channel_messages,
                }

        return metadata, download_tasks

    def download_all_attachments(
        self, download_tasks: List[Tuple[str, str, int, str, str]]
    ) -> Dict[Tuple[str, int], List[str]]:
        """Download all attachments in parallel.

        Args:
            download_tasks: List of (url, channel_id, message_id, timestamp, content) tuples

        Returns:
            Dict mapping (channel_id, message_id) -> list of downloaded filenames
        """
        if not download_tasks:
            return {}

        # Create media output directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        print(f"\nDownloading {len(download_tasks)} attachments...")

        # Map to track downloaded files per message
        message_files: Dict[Tuple[str, int], List[str]] = {}

        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # Submit all download tasks
            future_to_task = {
                executor.submit(self._download_single_attachment, task): task
                for task in download_tasks
            }

            # Collect results with progress bar
            for future in futures_progress(
                future_to_task,
                PHASE_PREPROCESS,
                "Downloading attachments",
                unit="file",
            ):
                result = future.result()

                if result.get("success"):
                    key = (result["channel_id"], result["message_id"])
                    if key not in message_files:
                        message_files[key] = []
                    message_files[key].append(result["filename"])

        return message_files

    def update_metadata_with_downloads(
        self, metadata: Dict, message_files: Dict[Tuple[str, int], List[str]]
    ) -> Dict:
        """Update metadata with downloaded filenames.

        Args:
            metadata: Metadata dict to update
            message_files: Dict mapping (channel_id, message_id) -> filenames

        Returns:
            Updated metadata dict
        """
        for channel_id, conv_data in metadata["conversations"].items():
            for message in conv_data["messages"]:
                msg_id = message["id"]
                key = (channel_id, msg_id)

                if key in message_files:
                    message["media_files"] = message_files[key]
                else:
                    # No files downloaded for this message
                    message["media_files"] = []

        # Remove messages with no downloaded files
        for channel_id in list(metadata["conversations"].keys()):
            conv_data = metadata["conversations"][channel_id]
            conv_data["messages"] = [
                msg for msg in conv_data["messages"]
                if msg["media_files"]
            ]
            conv_data["message_count"] = len(conv_data["messages"])

            # Remove empty conversations
            if not conv_data["messages"]:
                del metadata["conversations"][channel_id]

        return metadata

    def save_metadata(self, metadata: Dict) -> None:
        """Save cleaned metadata to metadata.json.

        Args:
            metadata: Metadata dict to save
        """
        try:
            # Add final statistics to export_info
            metadata["export_info"]["export_name"] = self.export_path.name
            metadata["export_info"]["total_channels"] = self.stats["total_channels"]
            metadata["export_info"]["total_attachments"] = self.stats["total_attachments"]
            metadata["export_info"]["downloads_successful"] = self.stats["downloads_successful"]
            metadata["export_info"]["downloads_failed"] = self.stats["downloads_failed"]
            metadata["export_info"]["unique_files"] = self.stats["unique_files"]
            metadata["export_info"]["duplicate_files"] = self.stats["duplicate_files"]

            with open(self.metadata_file, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

            print(f"\nSUCCESS: Saved metadata to {self.metadata_file}")
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
            sys.exit(1)

    def print_statistics(self) -> None:
        """Print processing statistics."""
        print("\n" + "=" * 70)
        print("PREPROCESSING STATISTICS")
        print("=" * 70)
        print(f"Total channels processed:         {self.stats['total_channels']:>6}")
        print(f"Total messages scanned:           {self.stats['total_messages']:>6}")
        print(f"Messages with attachments:        {self.stats['messages_with_attachments']:>6}")
        print(f"Total attachments found:          {self.stats['total_attachments']:>6}")
        print(f"Downloads successful:             {self.stats['downloads_successful']:>6}")
        print(f"Downloads failed:                 {self.stats['downloads_failed']:>6}")
        print(f"Downloads skipped (non-media):    {self.stats['downloads_skipped']:>6}")
        print(f"Banned files skipped:             {self.stats['banned_files_skipped']:>6}")
        print("=" * 70)

        # Deduplication summary
        if self.stats["unique_files"] > 0 or self.stats["duplicate_files"] > 0:
            print("\nDEDUPLICATION SUMMARY:")
            print(f"  Unique media files:             {self.stats['unique_files']:>6}")
            print(f"  Duplicate instances avoided:    {self.stats['duplicate_files']:>6}")
            print("=" * 70)

    def process(self) -> None:
        """Main processing pipeline."""
        logger.info("Starting Discord Export Preprocessing")
        print(f"Export path: {self.export_path}")
        print(f"Output path: {self.output_dir}\n")

        # Validate export structure
        if not self.validate_export():
            sys.exit(1)

        # Load indexes
        self.load_channel_index()
        self.load_server_index()

        # Scan channel directories
        print("\nScanning channel directories...")
        channel_dirs = self.scan_channels()
        print(f"Found {len(channel_dirs)} channels with messages")

        # Build metadata and collect download tasks
        print("\nParsing messages...")
        metadata, download_tasks = self.build_metadata(channel_dirs)

        # Download attachments
        message_files = self.download_all_attachments(download_tasks)

        # Update metadata with downloaded filenames
        metadata = self.update_metadata_with_downloads(metadata, message_files)

        # Save metadata
        self.save_metadata(metadata)

        # Save log file
        self.save_log()

        # Handle failures
        self.failure_tracker.handle_failures(self.final_output_dir)

        # Print statistics
        self.print_statistics()

        logger.info("Preprocessing complete!")


def main():
    """Main entry point for standalone preprocessing."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Preprocess Discord export: download attachments and create metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process in-place (creates media/ and metadata.json in export directory)
  python preprocess.py discord-username-20251215

  # Process to a separate output directory
  python preprocess.py discord-username-20251215 -o processed/
  
  # Process with custom number of workers
  python preprocess.py discord-username-20251215 --workers 8
        """,
    )

    parser.add_argument(
        "export_directory",
        type=str,
        help="Path to Discord export directory (contains Messages/)",
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
        help="Number of parallel download workers (default: CPU count - 1)",
    )

    args = parser.parse_args()

    export_path = Path(args.export_directory)
    output_path = Path(args.output) if args.output else None

    preprocessor = DiscordPreprocessor(export_path, output_path, workers=args.workers)
    preprocessor.process()


if __name__ == "__main__":
    main()

