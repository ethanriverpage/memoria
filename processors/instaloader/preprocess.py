#!/usr/bin/env python3
"""
Instaloader Preprocessor

Organizes media files scraped using Instaloader (https://instaloader.github.io/)
and creates cleaned metadata:
- Scans media files and groups carousel posts
- Extracts captions from .txt files
- Extracts metadata from .json files
- Creates metadata.json with essential information (captions, timestamps)

Note: This is NOT an official Instagram export format - it handles scrape data from Instaloader.
"""

import json
import logging
import lzma
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from common.output.progress import PHASE_PREPROCESS
from common.processing import print_preprocessing_stats, process_parallel_threaded
from common.detection import resolve_username, detect_from_dirname, DetectionResult
from common.files.scanner import iter_files
from common.output.logging import get_processor_logger
from common.utils import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, normalize_text
from processors.preprocess_base import CopyTask, PreprocessorBase

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class InstaloaderPreprocessor(PreprocessorBase):
    """Preprocesses Instaloader scrape data by organizing files and cleaning metadata"""

    # Use common media extensions (images and videos only)
    MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        super().__init__(
            processor_name="Instaloader",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        # Statistics specific to this preprocessor (preserves shared keys from base)
        self.stats.update(
            {
                "total_posts": 0,
                "total_media_files": 0,
                "media_copied": 0,
                "duplicate_files": 0,
                "posts_with_json": 0,
                "posts_with_txt": 0,
                "posts_with_only_media": 0,
                "carousel_posts": 0,
                "banned_files_skipped": 0,
            }
        )

    def validate_export(self) -> bool:
        """Validate that export directory exists and contains media files."""
        if not self.require_exists(self.export_path, label="Export path"):
            return False

        if not self.export_path.is_dir():
            logger.error("Export path is not a directory: %s", self.export_path)
            return False

        # Check if at least one media file exists (any supported extension)
        has_media = any(
            self.require_any_files(self.export_path, f"*{ext}", label="media")
            for ext in self.MEDIA_EXTENSIONS
        )
        if not has_media:
            logger.error("No media files found in %s", self.export_path)
            # Export-level soft-fail per policy A
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self.failure_tracker.add_export_failure(
                reason="no_media_files",
                error_details=f"No media files found in {self.export_path}",
            )
            self.failure_tracker.handle_failures(self.output_dir)
            return False

        return True

    def detect_username(self) -> DetectionResult:
        """Detect export username using available sources.

        Priority:
        1. JSON metadata files (.json.xz or .json) - contains owner.username
        2. Directory name pattern (instagram-username-YYYYMMDD)

        Returns:
            DetectionResult with username and source
        """

        def detect_from_json_metadata(path: Path) -> Optional[str]:
            """Extract username from Instaloader JSON metadata."""
            # Try .json.xz files first (compressed), then .json
            json_files = list(path.glob("*.json.xz")) or list(path.glob("*.json"))

            if not json_files:
                return None

            # Try the first few files until we find owner.username
            for json_file in json_files[:5]:
                try:
                    if json_file.suffix == ".xz":
                        with lzma.open(json_file, "rt", encoding="utf-8") as f:
                            data = json.load(f)
                    else:
                        with open(json_file, "r", encoding="utf-8") as f:
                            data = json.load(f)

                    # Navigate to owner.username (may be nested under 'node')
                    if "node" in data:
                        data = data["node"]

                    owner = data.get("owner", {})
                    username = owner.get("username")
                    if username:
                        return username
                except Exception:
                    continue

            return None

        def detect_from_dirname_instagram(path: Path) -> Optional[str]:
            """Extract username from directory name."""
            return detect_from_dirname(path, "instagram")

        return resolve_username(
            self.export_path,
            [
                ("json_metadata", detect_from_json_metadata),
                ("dirname", detect_from_dirname_instagram),
            ],
        )

    def parse_timestamp_from_filename(self, filename: str) -> Optional[str]:
        """
        Parse timestamp from filename
        Input format: "2016-08-13_00-57-19_UTC"
        Output format: "2016-08-13 00:57:19"
        """
        try:
            # Match pattern YYYY-MM-DD_HH-MM-SS_UTC
            pattern = r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})_UTC"
            match = re.search(pattern, filename)
            if match:
                year, month, day, hour, minute, second = match.groups()
                return f"{year}-{month}-{day} {hour}:{minute}:{second}"
            return None
        except Exception as e:
            self.log_message(
                "TIMESTAMP_PARSE_ERROR",
                f"Failed to parse timestamp from filename: {filename}",
                str(e),
            )
            return None

    def extract_base_filename(self, filename: str) -> Tuple[str, Optional[int]]:
        """
        Extract base filename and carousel index
        Returns: (base_name, index)

        Examples:
        - "2016-08-13_00-57-19_UTC.jpg" -> ("2016-08-13_00-57-19_UTC", None)
        - "2017-02-25_19-19-47_UTC_1.jpg" -> ("2017-02-25_19-19-47_UTC", 1)
        - "2017-02-25_19-19-47_UTC_2.jpg" -> ("2017-02-25_19-19-47_UTC", 2)
        """
        stem = Path(filename).stem

        # Check for carousel pattern: ends with _N where N is a digit
        pattern = r"^(.+)_(\d+)$"
        match = re.match(pattern, stem)

        if match:
            return match.group(1), int(match.group(2))
        else:
            return stem, None

    def build_file_catalog(self) -> Dict[str, Dict]:
        """
        Scan export directory and build catalog of posts with their media files.

        Returns: Dict[base_name, {"media_files": [filenames], "txt": path, "json": path}]

        Uses shared iter_files utility for consistent file scanning
        with automatic hidden file and banned pattern filtering.
        """
        catalog = {}

        logger.info("Scanning export directory...")

        # Use shared scanner for consistent filtering
        for file_path in iter_files(
            self.export_path,
            banned_filter=self.banned_filter,
            extensions=None,  # Accept all files, filter below by type
            skip_hidden=True,
        ):
            filename = file_path.name
            suffix = file_path.suffix.lower()

            # Handle media files
            if suffix in self.MEDIA_EXTENSIONS:
                base_name, _ = self.extract_base_filename(filename)

                if base_name not in catalog:
                    catalog[base_name] = {
                        "media_files": [],
                        "txt": None,
                        "json": None,
                    }

                catalog[base_name]["media_files"].append(filename)
                self.stats["total_media_files"] += 1

            # Handle .txt files
            elif suffix == ".txt":
                base_name = file_path.stem
                if base_name not in catalog:
                    catalog[base_name] = {
                        "media_files": [],
                        "txt": None,
                        "json": None,
                    }
                catalog[base_name]["txt"] = file_path

            # Handle .json and .json.xz files (skip _comments.json)
            elif suffix == ".json" or (
                suffix == ".xz" and filename.endswith(".json.xz")
            ):
                if "_comments.json" in filename:
                    continue  # Skip comments metadata

                # For .json.xz, we need to strip both suffixes to get base_name
                if suffix == ".xz":
                    base_name = Path(file_path.stem).stem  # Remove .xz then .json
                else:
                    base_name = file_path.stem

                if base_name not in catalog:
                    catalog[base_name] = {
                        "media_files": [],
                        "txt": None,
                        "json": None,
                    }
                catalog[base_name]["json"] = file_path

        # Sort media files within each post (for carousel posts)
        for base_name, entry in catalog.items():
            entry["media_files"].sort()

        # Track entries with metadata but no media files as orphaned metadata
        orphaned_count = 0
        for base_name, entry in catalog.items():
            if not entry["media_files"] and (entry["txt"] or entry["json"]):
                orphaned_count += 1
                metadata_content = {}
                if entry["json"]:
                    try:
                        json_path = entry["json"]
                        if json_path.suffix == ".xz":
                            with lzma.open(json_path, "rt", encoding="utf-8") as f:
                                metadata_content = json.load(f)
                        else:
                            with open(json_path, "r", encoding="utf-8") as f:
                                metadata_content = json.load(f)
                    except Exception:
                        metadata_content = {"base_name": base_name}

                self.failure_tracker.add_orphaned_metadata(
                    metadata_entry=metadata_content,
                    reason="Metadata exists but no matching media file found",
                    context={
                        "base_name": base_name,
                        "has_txt": entry["txt"] is not None,
                        "has_json": entry["json"] is not None,
                        "txt_path": str(entry["txt"]) if entry["txt"] else None,
                        "json_path": str(entry["json"]) if entry["json"] else None,
                    },
                )

        if orphaned_count > 0:
            logger.info("Found %d orphaned metadata entries", orphaned_count)

        # Filter out entries with no media files
        catalog = {k: v for k, v in catalog.items() if v["media_files"]}

        logger.info("   Found %d posts", len(catalog))
        logger.info("   Found %d media files", self.stats["total_media_files"])

        return catalog

    def extract_caption_from_txt(self, txt_path: Path) -> str:
        """Extract caption from .txt file"""
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                caption = f.read().strip()
            return normalize_text(caption)
        except Exception as e:
            self.log_message(
                "TXT_READ_ERROR",
                f"Failed to read txt file: {txt_path.name}",
                str(e),
            )
            return ""

    def extract_metadata_from_json(self, json_path: Path) -> Dict:
        """
        Extract metadata from .json or .json.xz file
        Returns dict with: caption, timestamp, timestamp_raw, media_type
        """
        metadata = {
            "caption": "",
            "timestamp": None,
            "timestamp_raw": None,
            "media_type": None,
        }

        try:
            # Handle compressed .json.xz files
            if json_path.suffix == ".xz":
                with lzma.open(json_path, "rt", encoding="utf-8") as f:
                    data = json.load(f)
            else:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

            # Extract data from nested structure
            node = data.get("node", {})

            # Extract caption
            caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
            if caption_edges and len(caption_edges) > 0:
                caption_text = caption_edges[0].get("node", {}).get("text", "")
                metadata["caption"] = normalize_text(caption_text)

            # Extract timestamp
            timestamp_raw = node.get("taken_at_timestamp")
            if timestamp_raw:
                metadata["timestamp_raw"] = timestamp_raw
                # Convert Unix timestamp to ISO format
                dt = datetime.fromtimestamp(timestamp_raw)
                metadata["timestamp"] = dt.strftime("%Y-%m-%d %H:%M:%S")

            # Extract media type
            typename = node.get("__typename", "")
            if typename:
                metadata["media_type"] = typename

        except json.JSONDecodeError as e:
            self.log_message(
                "JSON_PARSE_ERROR",
                f"Failed to parse JSON file: {json_path.name}",
                str(e),
            )
        except Exception as e:
            self.log_message(
                "METADATA_EXTRACT_ERROR",
                f"Failed to extract metadata from: {json_path.name}",
                str(e),
            )

        return metadata

    def _process_single_post(self, post_tuple: Tuple[str, Dict]) -> Dict:
        """
        Process a single post (used by multithreaded processing)

        Args:
            post_tuple: Tuple of (base_name, entry dict)

        Returns:
            Post data dict
        """
        base_name, entry = post_tuple

        post_data = {
            "media_type": "posts",
            "caption": "",
            "timestamp": None,
            "timestamp_raw": None,
            "latitude": None,
            "longitude": None,
            "media_files": entry["media_files"],
        }

        # Track statistics
        is_carousel = len(entry["media_files"]) > 1
        if is_carousel:
            with self.stats_lock:
                self.stats["carousel_posts"] += 1

        # Extract metadata from .json if available
        if entry["json"]:
            json_metadata = self.extract_metadata_from_json(entry["json"])
            post_data["caption"] = json_metadata["caption"]
            post_data["timestamp"] = json_metadata["timestamp"]
            post_data["timestamp_raw"] = json_metadata["timestamp_raw"]
            with self.stats_lock:
                self.stats["posts_with_json"] += 1

        # Extract caption from .txt if available (and no json caption)
        if entry["txt"] and not post_data["caption"]:
            txt_caption = self.extract_caption_from_txt(entry["txt"])
            post_data["caption"] = txt_caption
            if txt_caption:
                with self.stats_lock:
                    self.stats["posts_with_txt"] += 1

        # If no timestamp from JSON, parse from filename
        if not post_data["timestamp"]:
            timestamp_from_filename = self.parse_timestamp_from_filename(base_name)
            if timestamp_from_filename:
                post_data["timestamp"] = timestamp_from_filename

        # Track posts with only media (no metadata)
        if not entry["json"] and not entry["txt"]:
            with self.stats_lock:
                self.stats["posts_with_only_media"] += 1

        return post_data

    def create_metadata(self, catalog: Dict[str, Dict]) -> List[Dict]:
        """
        Create metadata for all posts (multithreaded)
        Returns list of post dictionaries
        """
        all_posts = []

        logger.info("Processing posts (using %d workers)...", self.workers)

        catalog_items = list(catalog.items())

        # Process posts in parallel
        all_posts = process_parallel_threaded(
            tasks=catalog_items,
            worker_fn=self._process_single_post,
            workers=self.workers,
            phase=PHASE_PREPROCESS,
            description="Parsing posts",
            task_id_fn=lambda t: t[0],  # post_name
            on_error=lambda tid, e: (
                self.log_message(
                    "POST_PROCESSING_ERROR",
                    f"Failed to process post {tid}",
                    str(e),
                ),
                logger.error("Failed to process post %s: %s", tid, e),
            ),
        )

        with self.stats_lock:
            self.stats["total_posts"] = len(all_posts)

        return all_posts

    def copy_media_files(self, metadata: List[Dict]) -> None:
        """Hash media files and build metadata with source_path using hash_with_dedup."""

        logger.info("Collecting media files for hashing...")
        logger.debug(
            f"Hashing {self.stats['total_media_files']} media files for deduplication"
        )

        # Phase 1: Collect all files to copy
        copy_tasks: List[CopyTask] = []
        # Map from (post_idx, filename) to source_path for updating metadata
        file_map: Dict[Tuple[int, str], Path] = {}

        for post_idx, post in enumerate(metadata):
            media_files = post.get("media_files", [])

            for filename in media_files:
                source_path = self.export_path / filename

                if not source_path.exists():
                    self.log_message(
                        "MISSING_FILE",
                        f"Media file not found: {filename}",
                    )
                    logger.warning("Media file not found: %s", filename)
                    continue

                file_map[(post_idx, filename)] = source_path

                # Build context for this file
                context = {
                    "_source_path": str(source_path),
                    "_original_filename": filename,
                    "media_type": post.get("media_type"),
                    "caption": post.get("caption", ""),
                    "timestamp": post.get("timestamp"),
                }

                copy_tasks.append(
                    CopyTask(
                        source_path=source_path,
                        dest_filename=filename,
                        context=context,
                    )
                )

        # Phase 2: Hash files with deduplication (no file copying)
        # Files will be copied directly from source to final output during processing
        logger.info("Hashing %d media files with deduplication...", len(copy_tasks))
        results = self.hash_with_dedup(copy_tasks)

        # Build a map from source_path to (dest_filename, source_path) for updating posts
        source_to_result: Dict[str, Tuple[str, str]] = {}

        for source_path, result in results.items():
            source_path_str = str(source_path)

            if result.is_duplicate:
                self.stats["duplicate_files"] += 1
            else:
                self.stats["media_copied"] += 1

            # Map this source path to the resulting (dest_filename, source_path)
            if source_path_str and result.dest_filename:
                source_to_result[source_path_str] = (
                    result.dest_filename,
                    str(result.source_path),
                )

        # Phase 3: Update posts with resulting filenames and source paths
        for post_idx, post in enumerate(metadata):
            media_files = post.get("media_files", [])
            media_entries = []

            for filename in media_files:
                source_path = file_map.get((post_idx, filename))
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

            # Update post with filenames and source paths (new format)
            post["media_files"] = [e["filename"] for e in media_entries]
            post["source_paths"] = {
                e["filename"]: e["source_path"] for e in media_entries
            }

        logger.info("   Copied %d unique files", self.stats["media_copied"])
        logger.info("   Deduplicated %d duplicate files", self.stats["duplicate_files"])

    def get_content_key(self) -> str:
        """Return the content key for Instaloader metadata."""
        return "media"

    @staticmethod
    def get_platform() -> str:
        """Return the platform identifier for Instaloader."""
        return "Instagram"

    @staticmethod
    def get_service() -> str:
        """Return the service identifier for Instaloader."""
        return "posts"

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Posts processed": self.stats["total_posts"],
            "Media files found": self.stats["total_media_files"],
            "Files copied": self.stats["media_copied"],
            "Carousel posts": self.stats["carousel_posts"],
            "With JSON metadata": self.stats["posts_with_json"],
            "With TXT captions": self.stats["posts_with_txt"],
            "Media only": self.stats["posts_with_only_media"],
            "Banned skipped": self.stats["banned_files_skipped"],
        }
        print_preprocessing_stats("Preprocessing Stats", stats)

    def process_export(self) -> bool:
        """Platform-specific preprocessing logic.

        Processes all posts, matching media files to metadata
        and performing deduplication.

        Returns:
            True if processing succeeded, False otherwise
        """
        # Build file catalog
        catalog = self.build_file_catalog()

        # Create metadata
        metadata = self.create_metadata(catalog)

        # Copy media files to output directory
        self.copy_media_files(metadata)

        # Save metadata
        self.save_metadata(
            metadata,
            extra_export_info={
                "total_posts": self.stats["total_posts"],
                "total_media_files": self.stats["media_copied"],
            },
        )

        return True
