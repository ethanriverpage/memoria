#!/usr/bin/env python3
"""
Snapchat Export Preprocessor

Preprocesses Snapchat export files with hash-only deduplication:
- Hashes media files and builds source_path references (no file copying)
- Matches overlays to videos by timestamp, storing overlay_source_path
- Creates metadata.json with conversation context and source_path entries
- Records ambiguous overlay cases for processor materialization into issues/
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
import re

from common.detection import DetectionResult
from common.files.scanner import iter_files
from common.preprocess.context_mergers import merge_snapchat_context
from common.output.logging import get_processor_logger
from common.processing import print_preprocessing_stats
from processors.preprocess_base import PreprocessorBase, CopyTask

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class SnapchatPreprocessor(PreprocessorBase):
    """Preprocesses Snapchat export by organizing files and cleaning metadata"""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
        username_override: Optional[str] = None,
    ):
        super().__init__(
            processor_name="Snapchat Messages",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        self.chat_media_dir = self.export_path / "chat_media"
        self.json_dir = self.export_path / "json"
        self.chat_history_file = self.json_dir / "chat_history.json"

        # Store username override for consolidated exports
        self.username_override = username_override

        # Set up context merger for message accumulation
        self.set_context_merger(merge_snapchat_context)

        # Statistics specific to this preprocessor (preserves shared keys from base)
        self.stats.update(
            {
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
        )

    def log_failure(
        self, category: str, filename: str, reason: str, details: str = ""
    ) -> None:
        """Add a log entry for a file that failed to match (thread-safe)"""
        message = filename
        if reason:
            message += f" - {reason}"
        self.log_message(category, message, details)

    def detect_username(self) -> DetectionResult:
        """Detect export username from directory name or chat history.

        Returns:
            DetectionResult with username and source
        """
        # Use override if provided (for consolidated exports)
        if self.username_override:
            return DetectionResult(username=self.username_override, source="override")

        # From directory name using regex
        dir_name = self.export_path.name
        match = re.match(r"snapchat-(.+?)-\d{4}-?\d{2}-?\d{2}", dir_name)
        if match:
            return DetectionResult(username=match.group(1), source="directory_name")

        return DetectionResult(username="unknown", source="none")

    def validate_export(self) -> bool:
        """Validate that export directory has required structure."""
        if not self.require_exists(self.export_path, label="Export path"):
            return False

        if not self.require_exists(self.chat_media_dir, label="chat_media directory"):
            return False

        if not self.require_exists(self.chat_history_file, label="chat_history.json"):
            return False

        return True

    def load_chat_history(self) -> Optional[Dict]:
        """Load and parse chat_history.json.

        Returns:
            Chat history dict, or None if loading fails
        """
        try:
            with open(self.chat_history_file, "r", encoding="utf-8") as f:
                chat_history = json.load(f)

            logger.info("Loaded chat_history.json")
            return chat_history
        except json.JSONDecodeError as e:
            logger.error("Failed to parse chat_history.json: %s", e)
            return None
        except OSError as e:
            logger.error("Error loading chat_history.json: %s", e)
            return None

    def extract_username(self, chat_history: Dict) -> str:
        """Extract exporter's username from directory name or chat history"""
        # Use override if provided (for consolidated exports)
        if self.username_override:
            logger.info(
                "Using username from parent directory: %s", self.username_override
            )
            return self.username_override

        # Method 1: From directory name using regex to handle both date formats
        dir_name = self.export_path.name
        # Pattern: snapchat-{username}-YYYY-MM-DD or snapchat-{username}-YYYYMMDD
        match = re.match(r"snapchat-(.+?)-\d{4}-?\d{2}-?\d{2}", dir_name)
        if match:
            username = match.group(1)
            logger.info("Extracted username from directory: %s", username)
            return username

        # Method 2: Find messages where IsSender is True
        for messages in chat_history.values():
            for message in messages:
                if message.get("IsSender", False):
                    username = message["From"]
                    logger.info("Extracted username from IsSender: %s", username)
                    return username

        logger.warning("Could not extract username")
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

    def build_file_catalog(self) -> Dict[str, Dict]:
        """
        Scan chat_media directory and build catalog of files.

        Returns: Dict mapping filename -> file metadata

        Uses shared iter_files utility for consistent file scanning
        with automatic hidden file and banned pattern filtering.
        """
        catalog = {}

        # Use shared scanner for consistent filtering of hidden and banned files
        for file_path in iter_files(
            self.chat_media_dir,
            banned_filter=self.banned_filter,
            extensions=None,  # Accept all files, classify below
            skip_hidden=True,
        ):
            self.stats["total_files"] += 1
            filename = file_path.name

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
                logger.warning("Unknown file type: %s", filename)
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

    def collect_media_tasks(
        self, chat_history: Dict, file_catalog: Dict
    ) -> Tuple[List[CopyTask], Dict[str, Dict], List[Dict]]:
        """
        Parse chat_history and collect media copy tasks.

        Returns:
            Tuple of (copy_tasks, file_catalog, ambiguous_cases)
        """
        copy_tasks: List[CopyTask] = []
        ambiguous_cases: List[Dict] = []

        # Build lookup maps
        media_id_to_filename, _, mtime_to_filenames = self.build_media_overlay_maps(
            file_catalog
        )

        # Build timestamp-based lookup for UUID media files
        timestamp_to_uuid_files = {}
        for filename, file_info in file_catalog.items():
            if file_info["type"] == "media" and "media_id" not in file_info:
                mtime = file_info.get("mtime")
                if mtime:
                    if mtime not in timestamp_to_uuid_files:
                        timestamp_to_uuid_files[mtime] = []
                    timestamp_to_uuid_files[mtime].append(filename)

        # Track matched files and overlays
        matched_media_files: set = set()
        matched_overlays: set = set()

        # Process each conversation
        for conversation_id, messages in chat_history.items():
            self.stats["conversations"] += 1

            conversation_type, conversation_title = self.classify_conversation(
                conversation_id, messages
            )

            for message in messages:
                self.stats["messages"] += 1

                # Skip TEXT messages
                if message.get("Media Type") == "TEXT":
                    continue

                # Track media messages
                if message.get("Media Type") in ["MEDIA", "IMAGE", "VIDEO"]:
                    self.stats["media_messages"] += 1

                # Build message context
                msg_context = {
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

                # Parse media IDs
                media_ids_str = message.get("Media IDs", "")
                if not media_ids_str:
                    continue

                media_ids = [mid.strip() for mid in media_ids_str.split("|")]
                media_files = []
                orphaned_media_ids = []

                # Phase 1: Match by media_id
                for media_id in media_ids:
                    if not media_id:
                        continue

                    if media_id in media_id_to_filename:
                        media_file = media_id_to_filename[media_id]
                        media_files.append(media_file)
                        matched_media_files.add(media_file)
                    else:
                        orphaned_media_ids.append(media_id)

                # Phase 2: Match UUID files by timestamp
                try:
                    date_str = message.get("Created", "")
                    if date_str:
                        date_str_no_tz = date_str.replace(" UTC", "")
                        message_dt = datetime.strptime(
                            date_str_no_tz, "%Y-%m-%d %H:%M:%S"
                        )
                        message_timestamp = message_dt.replace(
                            tzinfo=timezone.utc
                        ).timestamp()

                        if message_timestamp in timestamp_to_uuid_files:
                            uuid_files = timestamp_to_uuid_files[message_timestamp]
                            unmatched_count = len(media_ids) - len(media_files)
                            if unmatched_count > 0:
                                for uuid_file in uuid_files:
                                    if uuid_file not in matched_media_files:
                                        media_files.append(uuid_file)
                                        matched_media_files.add(uuid_file)
                                        unmatched_count -= 1
                                        if unmatched_count == 0:
                                            break
                except (ValueError, TypeError):
                    pass

                # Re-evaluate orphaned media_ids
                if len(media_files) >= len(media_ids):
                    orphaned_media_ids = []
                elif len(media_files) > 0 and len(orphaned_media_ids) > 0:
                    truly_unmatched = len(media_ids) - len(media_files)
                    if truly_unmatched < len(orphaned_media_ids):
                        orphaned_media_ids = orphaned_media_ids[:truly_unmatched]

                # Phase 3: Overlay matching
                overlay_file = None
                overlay_source_path = None
                if media_files:
                    try:
                        date_str = message.get("Created", "")
                        if date_str:
                            date_str_no_tz = date_str.replace(" UTC", "")
                            message_dt = datetime.strptime(
                                date_str_no_tz, "%Y-%m-%d %H:%M:%S"
                            )
                            message_timestamp = message_dt.replace(
                                tzinfo=timezone.utc
                            ).timestamp()

                            # Find overlays at this timestamp
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

                            video_count = len(
                                [
                                    mf
                                    for mf in media_files
                                    if self.get_media_type(mf) == "video"
                                ]
                            )
                            overlay_count = len(overlays_at_timestamp)

                            if video_count == 1 and overlay_count == 1:
                                # Safe 1:1 match - get overlay file and its source path
                                overlay_file = overlays_at_timestamp[0]
                                overlay_info = file_catalog.get(overlay_file, {})
                                overlay_source_path = overlay_info.get("original_path")
                                matched_overlays.add(overlay_file)
                            elif overlay_count > 0 and (
                                video_count > 1 or overlay_count > 1
                            ):
                                # Ambiguous case
                                self._record_ambiguous_case(
                                    ambiguous_cases,
                                    message,
                                    media_files,
                                    overlays_at_timestamp,
                                    file_catalog,
                                    conversation_id,
                                    conversation_type,
                                    conversation_title,
                                )
                    except (ValueError, TypeError):
                        pass

                # Create copy tasks for each media file
                for media_file in media_files:
                    file_info = file_catalog.get(media_file, {})
                    source_path = Path(file_info.get("original_path", ""))

                    if not source_path.exists():
                        continue

                    # Build context with messages list for merging
                    context = {
                        "messages": [msg_context.copy()],
                        "overlay_file": overlay_file,
                        "overlay_source_path": overlay_source_path,
                        "primary_created": msg_context.get("created"),
                    }

                    copy_tasks.append(
                        CopyTask(
                            source_path=source_path,
                            dest_filename=media_file,
                            context=context,
                        )
                    )

                # Track orphaned metadata
                for media_id in orphaned_media_ids:
                    self.failure_tracker.add_orphaned_metadata(
                        metadata_entry={
                            "conversation_id": conversation_id,
                            "conversation_type": conversation_type,
                            "conversation_title": conversation_title,
                            "message": msg_context,
                            "media_id": media_id,
                        },
                        reason=f"Media ID '{media_id}' referenced but file not found",
                        context={
                            "timestamp": message.get("Created", ""),
                            "sender": message.get("From", ""),
                            "content": message.get("Content", ""),
                            "matched_files_count": len(media_files),
                            "expected_files_count": len(media_ids),
                        },
                    )

        # Collect orphaned media files (not matched to any message)
        # Per Failure-Handling.md:
        # - If has timestamp -> unmatched_media (processable with file-derived timestamp)
        # - If no timestamp -> orphaned_media via FailureTracker (invalid)
        for filename, file_info in file_catalog.items():
            if file_info["type"] == "media" and filename not in matched_media_files:
                source_path = Path(file_info["original_path"])
                if not source_path.exists():
                    continue

                # Try to get timestamp from file mtime or filename-derived date
                created_date = None
                mtime = file_info.get("mtime")
                if mtime:
                    created_date = datetime.fromtimestamp(mtime).strftime(
                        "%Y-%m-%d %H:%M:%S UTC"
                    )
                else:
                    created_date = file_info.get("date")

                media_id = file_info.get("media_id", "N/A")

                if created_date is None:
                    # No timestamp = orphaned media (invalid, FailureTracker only)
                    self.failure_tracker.add_orphaned_media(
                        media_path=source_path,
                        reason="Not matched to any message and no timestamp available",
                        context={
                            "media_id": media_id,
                            "date": file_info.get("date"),
                            "mtime": mtime,
                        },
                    )
                    continue

                # Has timestamp = unmatched media (processable with incomplete context)
                # Try to find overlay
                overlay_file = None
                overlay_source_path = None
                if mtime and mtime in mtime_to_filenames:
                    available_overlays = [
                        fname
                        for fname, ftype in mtime_to_filenames[mtime]
                        if ftype == "overlay" and fname not in matched_overlays
                    ]
                    if len(available_overlays) == 1:
                        overlay_file = available_overlays[0]
                        overlay_info = file_catalog.get(overlay_file, {})
                        overlay_source_path = overlay_info.get("original_path")
                        matched_overlays.add(overlay_file)

                orphaned_context = {
                    "messages": [
                        {
                            "conversation_id": None,
                            "conversation_type": None,
                            "conversation_title": None,
                            "sender": None,
                            "media_type": "MEDIA",
                            "created": created_date,
                            "content": "",
                            "is_sender": None,
                            "media_id": media_id,
                        }
                    ],
                    "overlay_file": overlay_file,
                    "overlay_source_path": overlay_source_path,
                    "primary_created": created_date,
                    "is_orphaned": True,
                }

                copy_tasks.append(
                    CopyTask(
                        source_path=source_path,
                        dest_filename=filename,
                        context=orphaned_context,
                    )
                )

        self.stats["matched_overlays"] = len(matched_overlays)
        self.stats["ambiguous_cases"] = len(ambiguous_cases)

        return copy_tasks, file_catalog, ambiguous_cases

    def _record_ambiguous_case(
        self,
        ambiguous_cases: List[Dict],
        message: Dict,
        media_files: List[str],
        overlays_at_timestamp: List[str],
        file_catalog: Dict,
        conversation_id: str,
        conversation_type: str,
        conversation_title: Optional[str],
    ) -> None:
        """Record an ambiguous overlay matching case with source_path for materialization."""
        date_str = message.get("Created", "")

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

        # Add video file details only (with source_path for materialization)
        image_count = 0
        video_count = 0
        for media_file in media_files:
            file_info = file_catalog.get(media_file, {})
            media_type = self.get_media_type(media_file)
            if media_type == "video":
                video_count += 1
                ambiguous_case["media_files"].append(
                    {
                        "filename": media_file,
                        "source_path": file_info.get("original_path"),
                        "media_id": file_info.get("media_id"),
                        "uuid": file_info.get("uuid"),
                        "type": media_type,
                        "extension": Path(media_file).suffix,
                    }
                )
            else:
                image_count += 1

        # Add overlay details (with source_path for materialization)
        for overlay_file in overlays_at_timestamp:
            overlay_info = file_catalog.get(overlay_file, {})
            ambiguous_case["overlays"].append(
                {
                    "filename": overlay_file,
                    "source_path": overlay_info.get("original_path"),
                    "uuid": self.extract_uuid(overlay_file),
                }
            )

        # Add analysis
        overlay_count = len(overlays_at_timestamp)
        hint = f"{image_count} images + {video_count} videos, but only {overlay_count} overlays."
        if video_count == overlay_count and image_count > 0:
            hint += " Images don't need overlays and are processed normally."
        elif overlay_count == 0:
            hint += " Videos can exist without overlays."

        ambiguous_case["analysis"] = {
            "media_count": len(media_files),
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

    def validate_overlay_files(self, file_catalog: Dict) -> None:
        """Validate overlay files exist (no copying - uses source_path directly)."""
        for filename, file_info in file_catalog.items():
            if file_info["type"] != "overlay":
                continue

            source = Path(file_info["original_path"])
            if not source.exists():
                self.log_failure(
                    "MISSING_OVERLAY",
                    filename,
                    "Overlay file not found",
                    f"expected={source}",
                )
                logger.warning("Overlay file not found: %s", source)

    def get_content_key(self) -> str:
        """Return the content key for Snapchat Messages metadata."""
        return "conversations"

    @staticmethod
    def get_platform() -> str:
        """Return the platform identifier for Snapchat Messages."""
        return "Snapchat"

    @staticmethod
    def get_service() -> str:
        """Return the service identifier for Snapchat Messages."""
        return "messages"

    def build_metadata_from_results(self, chat_history: Dict) -> Dict:
        """Build metadata from hash_with_dedup results."""
        # Get merged contexts from base class
        merged_contexts = self.get_merged_contexts()

        metadata = {
            "conversations": {},
        }

        # Group entries by conversation
        conversation_entries: Dict[str, List[Dict]] = {}
        orphaned_count = 0

        for content_hash, context in merged_contexts.items():
            registry_entry = self._hash_registry.get(content_hash, {})
            filename = registry_entry.get("filename", "")
            source_path = registry_entry.get("source_path", "")

            messages = context.get("messages", [])
            overlay_file = context.get("overlay_file")
            is_orphaned = context.get("is_orphaned", False)

            # Check if this is a duplicate (multiple messages)
            is_duplicate = len(messages) > 1

            if is_orphaned or (messages and messages[0].get("conversation_id") is None):
                # Orphaned media: record as schema-compliant unmatched_media entry.
                #
                # NOTE:
                # - `unmatched_media` entries are NOT message entries.
                # - They must include: filename + source_path (see common/metadata/schema.py).
                timestamp = context.get("primary_created")
                media_id = messages[0].get("media_id", "") if messages else ""
                try:
                    if filename and source_path:
                        self.add_unmatched_media(
                            filename=filename,
                            source_path=Path(source_path),
                            content_hash=content_hash,
                            timestamp=timestamp,
                            media_id=media_id,
                            overlay_file=overlay_file,
                            overlay_source_path=context.get("overlay_source_path"),
                        )
                        orphaned_count += 1
                except Exception:
                    # If something goes wrong here, let save_metadata validation surface it
                    # with a clear error rather than crashing mid-preprocess.
                    pass
            elif is_duplicate:
                # Duplicate media across conversations - create merged entry
                conv_id = messages[0].get("conversation_id")
                if conv_id is None:
                    continue
                entry = {
                    "media_files": [filename],
                    "source_paths": {filename: source_path},
                    "messages": messages,
                    "primary_created": context.get("primary_created"),
                    "is_duplicate": True,
                }
                if overlay_file:
                    entry["overlay_file"] = overlay_file
                overlay_source_path = context.get("overlay_source_path")
                if overlay_source_path:
                    entry["overlay_source_path"] = overlay_source_path

                if conv_id not in conversation_entries:
                    conversation_entries[conv_id] = []
                conversation_entries[conv_id].append(entry)
            else:
                # Single occurrence
                msg = messages[0] if messages else {}
                conv_id = msg.get("conversation_id")
                if conv_id is None:
                    continue
                entry = {
                    "conversation_id": conv_id,
                    "conversation_type": msg.get("conversation_type"),
                    "conversation_title": msg.get("conversation_title"),
                    "sender": msg.get("sender"),
                    "media_type": msg.get("media_type"),
                    "created": msg.get("created"),
                    "content": msg.get("content"),
                    "is_sender": msg.get("is_sender"),
                    "media_id": msg.get("media_id"),
                    "media_files": [filename],
                    "source_paths": {filename: source_path},
                }
                if overlay_file:
                    entry["overlay_file"] = overlay_file
                overlay_source_path = context.get("overlay_source_path")
                if overlay_source_path:
                    entry["overlay_source_path"] = overlay_source_path

                if conv_id not in conversation_entries:
                    conversation_entries[conv_id] = []
                conversation_entries[conv_id].append(entry)

        # Build conversations structure
        for conv_id, entries in conversation_entries.items():
            if conv_id is None:
                continue

            # Get conversation metadata from first entry
            first_entry = entries[0]
            if "messages" in first_entry:
                # Duplicate entry - get from first message
                first_msg = first_entry["messages"][0]
                conv_type = first_msg.get("conversation_type", "dm")
                conv_title = first_msg.get("conversation_title")
            else:
                conv_type = first_entry.get("conversation_type", "dm")
                conv_title = first_entry.get("conversation_title")

            metadata["conversations"][conv_id] = {
                "type": conv_type,
                "title": conv_title,
                "message_count": len(entries),
                "messages": entries,
            }

        # Update stats
        total_unique = len(merged_contexts)
        total_messages = sum(
            len(ctx.get("messages", [])) for ctx in merged_contexts.values()
        )
        duplicates = total_messages - total_unique
        self.stats["duplicate_files"] = duplicates if duplicates > 0 else 0
        self.stats["deduplicated_files"] = duplicates if duplicates > 0 else 0
        self.stats["orphaned_media"] = orphaned_count

        return metadata

    def prepare_ambiguous_cases(
        self, ambiguous_cases: List[Dict], export_username: str
    ) -> List[Dict]:
        """Prepare ambiguous matching cases for storage in metadata.json.

        The processor will materialize these into output_dir/issues/needs_matching/
        using the source_path fields during processing.
        """
        if not ambiguous_cases:
            return []

        logger.info(
            "Recording %d ambiguous cases for manual matching...", len(ambiguous_cases)
        )

        for case in ambiguous_cases:
            case["export_username"] = export_username

        return ambiguous_cases

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Files scanned": self.stats["total_files"],
            "Media files": self.stats["media_files"],
            "Overlay files": self.stats["overlay_files"],
            "Matched overlays": self.stats.get("matched_overlays", 0),
            "Conversations": self.stats["conversations"],
            "Messages": self.stats["messages"],
            "Media messages": self.stats["media_messages"],
            "Orphaned media": self.stats.get("orphaned_media", 0),
            "Duplicates": self.stats.get("duplicate_files", 0),
            "Banned skipped": self.stats["banned_files"],
        }
        print_preprocessing_stats("Preprocessing Stats", stats)

    def process_export(self) -> bool:
        """Platform-specific preprocessing logic.

        Processes Snapchat chat media, matching files to messages
        and performing deduplication.

        Returns:
            True if processing succeeded, False otherwise
        """
        # Load chat history
        chat_history = self.load_chat_history()
        if chat_history is None:
            return False

        # Build file catalog
        logger.info("Scanning chat_media directory...")
        file_catalog = self.build_file_catalog()

        # Collect copy tasks
        logger.info("Processing metadata and collecting media tasks...")
        copy_tasks, file_catalog, ambiguous_cases = self.collect_media_tasks(
            chat_history, file_catalog
        )

        # Hash media files with deduplication (no file copying)
        # Files will be copied directly from source to final output during processing
        if copy_tasks:
            logger.info("Hashing %d media files with deduplication...", len(copy_tasks))
            results = self.hash_with_dedup(copy_tasks)

            # Count unique vs duplicates
            unique = sum(1 for r in results.values() if not r.is_duplicate)
            duplicates = sum(1 for r in results.values() if r.is_duplicate)
            logger.info("   Copied %d unique files, %d duplicates", unique, duplicates)

        # Validate overlay files exist (no copying - uses source_path during processing)
        logger.info("Validating overlay files...")
        self.validate_overlay_files(file_catalog)

        # Build metadata from dedup results
        logger.info("Building metadata...")
        metadata = self.build_metadata_from_results(chat_history)

        # Prepare ambiguous cases for storage in metadata (materialized during processing)
        export_username = metadata.get("export_info", {}).get(
            "export_username", "unknown"
        )
        prepared_cases = self.prepare_ambiguous_cases(ambiguous_cases, export_username)

        # Save metadata (with unmatched_media and ambiguous_cases)
        self.set_ambiguous_cases(prepared_cases)
        self.save_metadata(
            metadata["conversations"],
            extra_export_info={},
        )

        return True
