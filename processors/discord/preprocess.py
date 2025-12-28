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
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, unquote

import requests

from common.detection import resolve_username, detect_from_dirname, DetectionResult
from common.output.logging import get_processor_logger
from common.processing import print_preprocessing_stats
from common.output.progress import PHASE_PREPROCESS, futures_progress
from processors.preprocess_base import PreprocessorBase

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


# Supported media extensions for Discord attachments
MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",  # Images
    ".mp4",
    ".webm",
    ".mov",  # Videos
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",  # Audio
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


class DiscordPreprocessor(PreprocessorBase):
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
        super().__init__(
            processor_name="Discord",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        self.messages_dir = self.export_path / "Messages"
        self.servers_dir = self.export_path / "Servers"
        self.index_file = self.messages_dir / "index.json"

        # Downloads directory for downloaded attachments (non-canonical cache location)
        self.downloads_dir = self.output_dir / "downloads"

        # Additional lock for filename collision tracking
        self.filename_lock = Lock()

        # Track used filenames to avoid collisions
        self.used_filenames: Dict[str, int] = {}

        # No context merger needed - first occurrence wins

        # Statistics specific to this preprocessor (preserves shared keys from base)
        self.stats.update(
            {
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
        )

        # Channel index cache
        self.channel_index: Dict[str, str] = {}

        # Server index cache
        self.server_index: Dict[str, str] = {}

        # Export username (populated from cached detection)
        self.export_username: str = ""

    def validate_export(self) -> bool:
        """Validate that export directory has required Discord structure.

        Returns:
            True if valid Discord export, False otherwise
        """
        if not self.require_exists(self.export_path, label="Export path"):
            return False

        if not self.require_exists(self.messages_dir, label="Messages directory"):
            return False

        if not self.require_exists(self.index_file, label="Messages index.json"):
            return False

        # Check for at least one channel folder (folders starting with 'c')
        if not self.require_any_dirs(
            self.messages_dir,
            label="channel",
            filter_fn=lambda d: d.name.startswith("c"),
        ):
            log.logger.error("No channel folders found in %s", self.messages_dir)
            return False

        return True

    def detect_username(self) -> DetectionResult:
        """Detect export username using available sources.

        Priority:
        1. Account/user.json - contains Discord username
        2. Directory name pattern (discord-username-YYYYMMDD)

        Returns:
            DetectionResult with username and source
        """

        def detect_from_user_json(path: Path) -> Optional[str]:
            """Extract username from Account/user.json."""
            user_file = path / "Account" / "user.json"
            if not user_file.exists():
                return None
            try:
                with open(user_file, "r", encoding="utf-8") as f:
                    # Read first part of potentially large JSON
                    content = f.read(4096)
                    # Parse just enough to get username
                    data = json.loads(content.split('"settings"')[0] + "}")
                    return data.get("username")
            except (json.JSONDecodeError, KeyError, IndexError):
                # Fallback: try full parse
                try:
                    with open(user_file, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        return data.get("username")
                except Exception:
                    return None

        def detect_from_dirname_discord(path: Path) -> Optional[str]:
            """Extract username from directory name."""
            return detect_from_dirname(path, "discord")

        return resolve_username(
            self.export_path,
            [
                ("user.json", detect_from_user_json),
                ("dirname", detect_from_dirname_discord),
            ],
        )

    def load_channel_index(self) -> Dict[str, str]:
        """Load Messages/index.json mapping channel IDs to names.

        Returns:
            Dict mapping channel_id -> channel description
        """
        try:
            with open(self.index_file, "r", encoding="utf-8") as f:
                self.channel_index = json.load(f)
            log.loaded_mappings(len(self.channel_index), "channel")
            return self.channel_index
        except Exception as e:
            log.logger.error("Failed to load channel index: %s", e)
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
            log.loaded_mappings(len(self.server_index), "server")
            return self.server_index
        except Exception as e:
            log.logger.warning("Failed to load server index: %s", e)
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

            channel_id = channel_data.get(
                "id", channel_dir.name[1:]
            )  # Strip 'c' prefix
            channel_type = channel_data.get("type", "UNKNOWN")

            # Determine conversation type and title
            if channel_type == "DM":
                conv_type = "dm"
                # Try to get title from index
                title = self.channel_index.get(channel_id, "Direct Message")
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
            log.parse_error(str(channel_dir / "channel.json"), e)
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
                log.logger.warning("messages.json is not a list in %s", channel_dir)
                return []

            return messages

        except Exception as e:
            log.parse_error(str(channel_dir / "messages.json"), e)
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
        # Support offline test fixtures via file:// URLs
        try:
            parsed = urlparse(url)
            if parsed.scheme == "file":
                source_path = Path(unquote(parsed.path))
                if not source_path.exists():
                    return False, f"Local file not found: {source_path}"

                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(source_path, "rb") as src, open(output_path, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 1024)
                return True, None
        except Exception as e:
            return False, f"Local file URL error: {e}"

        error_msg = "Max retries exceeded"
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
                sleep_time = self.RETRY_BACKOFF**attempt
                time.sleep(sleep_time)

        return False, error_msg

    def _download_single_attachment(self, args: Tuple[str, str, int, str, str]) -> Dict:
        """Worker function to download a single attachment.

        Downloads the file, then uses register_downloaded_file() for deduplication.

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
        output_path = self.downloads_dir / output_filename

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

        # File downloaded successfully - use register_downloaded_file for dedup
        context = {
            "channel_id": channel_id,
            "message_id": message_id,
            "timestamp": timestamp,
            "content": content,
        }

        result = self.register_downloaded_file(
            file_path=output_path,
            dest_filename=output_filename,
            context=context,
        )

        # Update stats
        with self.stats_lock:
            self.stats["downloads_successful"] += 1
            if result.is_duplicate:
                self.stats["duplicate_files"] += 1
            else:
                self.stats["unique_files"] += 1

        return {
            "success": True,
            "url": url,
            "filename": result.dest_filename,
            "source_path": str(result.primary_source),
            "original_filename": original_filename,
            "channel_id": channel_id,
            "message_id": message_id,
            "timestamp": timestamp,
            "content": content,
            "is_duplicate": result.is_duplicate,
            "content_hash": result.content_hash,
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

    def build_channel_metadata(
        self, channel_dirs: List[Path]
    ) -> Tuple[Dict, List[Tuple[str, str, int, str, str]]]:
        """Build metadata structure and collect attachment download tasks.

        Args:
            channel_dirs: List of channel directory paths

        Returns:
            Tuple of (conversations dict, list of download task tuples)
        """
        # Use cached detection result from the run() pipeline
        detection = self.ensure_detection_result()
        self.export_username = detection.username or "unknown"

        # Build just the conversations structure - export_info is handled by save_metadata
        conversations: Dict[str, Any] = {}

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
                    if url.strip()
                    and urlparse(url.strip()).scheme in ("http", "https", "file")
                ]

                with self.stats_lock:
                    self.stats["total_attachments"] += len(attachment_urls)

                # Create download tasks for each attachment
                for url in attachment_urls:
                    download_tasks.append((url, channel_id, msg_id, timestamp, content))

                # Create message entry (will be populated with filenames after download)
                channel_messages.append(
                    {
                        "id": msg_id,
                        "timestamp": f"{timestamp} UTC" if timestamp else "",
                        "content": content,
                        "original_urls": attachment_urls,
                        "media_files": [],  # Populated after download
                    }
                )

            # Only add channel if it has messages with attachments
            if channel_messages:
                conversations[channel_id] = {
                    "type": channel_info["type"],
                    "title": channel_info["title"],
                    "guild_name": channel_info.get("guild_name"),
                    "message_count": len(channel_messages),
                    "messages": channel_messages,
                }

        return conversations, download_tasks

    def download_all_attachments(
        self, download_tasks: List[Tuple[str, str, int, str, str]]
    ) -> Dict[Tuple[str, int], Dict[str, Any]]:
        """Download all attachments in parallel.

        Args:
            download_tasks: List of (url, channel_id, message_id, timestamp, content) tuples

        Returns:
            Dict mapping (channel_id, message_id) -> list of downloaded filenames
        """
        if not download_tasks:
            return {}

        # Create downloads directory
        self.downloads_dir.mkdir(parents=True, exist_ok=True)

        log.downloading(len(download_tasks), "attachments")

        # Map to track downloaded files per message
        # Each value is a dict with 'filenames' (list) and 'source_paths' (dict)
        message_files: Dict[Tuple[str, int], Dict[str, Any]] = {}

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
                        message_files[key] = {"filenames": [], "source_paths": {}}
                    message_files[key]["filenames"].append(result["filename"])
                    message_files[key]["source_paths"][result["filename"]] = result[
                        "source_path"
                    ]

        return message_files

    def update_conversations_with_downloads(
        self,
        conversations: Dict[str, Any],
        message_files: Dict[Tuple[str, int], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Update conversations with downloaded filenames and source paths.

        Args:
            conversations: Conversations dict to update
            message_files: Dict mapping (channel_id, message_id) -> {filenames, source_paths}

        Returns:
            Updated conversations dict
        """
        for channel_id, conv_data in conversations.items():
            for message in conv_data["messages"]:
                msg_id = message["id"]
                key = (channel_id, msg_id)

                if key in message_files:
                    message["media_files"] = message_files[key]["filenames"]
                    message["source_paths"] = message_files[key]["source_paths"]
                else:
                    # No files downloaded for this message
                    message["media_files"] = []
                    message["source_paths"] = {}

        # Remove messages with no downloaded files
        for channel_id in list(conversations.keys()):
            conv_data = conversations[channel_id]
            conv_data["messages"] = [
                msg for msg in conv_data["messages"] if msg["media_files"]
            ]
            conv_data["message_count"] = len(conv_data["messages"])

            # Remove empty conversations
            if not conv_data["messages"]:
                del conversations[channel_id]

        return conversations

    def get_content_key(self) -> str:
        """Return the content key for Discord metadata."""
        return "conversations"

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Channels processed": self.stats["total_channels"],
            "Messages scanned": self.stats["total_messages"],
            "With attachments": self.stats["messages_with_attachments"],
            "Attachments found": self.stats["total_attachments"],
            "Downloads OK": self.stats["downloads_successful"],
            "Downloads failed": self.stats["downloads_failed"],
            "Skipped (non-media)": self.stats["downloads_skipped"],
            "Unique files": self.stats["unique_files"],
            "Duplicates avoided": self.stats["duplicate_files"],
            "Banned skipped": self.stats["banned_files_skipped"],
        }
        print_preprocessing_stats("Preprocessing Stats", stats)

    def process_export(self) -> bool:
        """Platform-specific preprocessing logic.

        Downloads Discord attachments and organizes metadata.

        Returns:
            True if processing succeeded, False otherwise
        """
        # Load indexes
        self.load_channel_index()
        self.load_server_index()

        # Scan channel directories
        log.logger.info("Scanning channel directories...")
        channel_dirs = self.scan_channels()
        log.found_items(len(channel_dirs), "channels with messages")

        # Build conversations structure and collect download tasks
        log.parsing("messages")
        conversations, download_tasks = self.build_channel_metadata(channel_dirs)

        # Download attachments
        message_files = self.download_all_attachments(download_tasks)

        # Update conversations with downloaded filenames
        conversations = self.update_conversations_with_downloads(
            conversations, message_files
        )

        # Save metadata
        self.save_metadata(
            conversations,
            extra_export_info={
                "total_channels": self.stats["total_channels"],
                "total_attachments": self.stats["total_attachments"],
                "downloads_successful": self.stats["downloads_successful"],
                "downloads_failed": self.stats["downloads_failed"],
                "unique_files": self.stats["unique_files"],
                "duplicate_files": self.stats["duplicate_files"],
            },
        )

        return True
