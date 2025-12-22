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
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from common.output.progress import PHASE_PREPROCESS, futures_progress
from common.preprocess.context_mergers import merge_first_occurrence
from common.processing import print_preprocessing_stats
from common.detection import resolve_username, detect_from_dirname, DetectionResult
from common.utils import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from processors.preprocess_base import CopyTask, PreprocessorBase

# Set up logging
logger = logging.getLogger(__name__)


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

        # Statistics specific to this preprocessor
        self.stats = {
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

        # Set context merger for deduplication (first occurrence wins)
        self._context_merger = merge_first_occurrence

    def normalize_text(self, text: str) -> str:
        """
        Normalize Unicode characters in text to ASCII equivalents
        Replaces smart quotes and other Unicode punctuation with standard ASCII
        """
        if not text:
            return text

        # Replace Unicode apostrophes and quotes with ASCII equivalents
        replacements = {
            "\u2018": "'",  # Left single quotation mark
            "\u2019": "'",  # Right single quotation mark (curly apostrophe)
            "\u201a": "'",  # Single low-9 quotation mark
            "\u201b": "'",  # Single high-reversed-9 quotation mark
            "\u201c": '"',  # Left double quotation mark
            "\u201d": '"',  # Right double quotation mark
            "\u201e": '"',  # Double low-9 quotation mark
            "\u201f": '"',  # Double high-reversed-9 quotation mark
            "\u2032": "'",  # Prime
            "\u2033": '"',  # Double prime
            "\u2013": "--",  # En dash
            "\u2014": "--",  # Em dash
            "\u2028": " ",  # Line Separator
            "\u2029": " ",  # Paragraph Separator
            "\ufe0f": "",  # Variation Selector-16 (emoji modifier)
        }

        for unicode_char, ascii_char in replacements.items():
            text = text.replace(unicode_char, ascii_char)

        return text

    def validate_export(self) -> bool:
        """Validate that export directory exists and contains media files"""
        if not self.export_path.exists():
            logger.error("Export path does not exist: %s", self.export_path)
            return False

        if not self.export_path.is_dir():
            logger.error("Export path is not a directory: %s", self.export_path)
            return False

        # Check if at least one media file exists
        media_files = []
        for ext in self.MEDIA_EXTENSIONS:
            media_files.extend(list(self.export_path.glob(f"*{ext}")))

        if not media_files:
            logger.error("No media files found in %s", self.export_path)
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
        import lzma

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
        Scan export directory and build catalog of posts with their media files
        Returns: Dict[base_name, {"media_files": [filenames], "txt": path, "json": path}]
        """
        catalog = {}

        logger.info("Scanning export directory...")

        # Scan all files in export directory
        import os

        with os.scandir(self.export_path) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue

                file_path = Path(entry.path)
                filename = entry.name
                suffix = file_path.suffix.lower()

                # Skip banned files (NAS system files, thumbnails, macOS files, etc.)
                if self.banned_filter.is_banned(file_path):
                    self.stats["banned_files_skipped"] += 1
                    self.log_message(
                        "BANNED_FILE_SKIPPED",
                        f"Skipped banned file: {filename}",
                    )
                    continue

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

                # Handle .json files (skip _comments.json)
                elif suffix == ".json":
                    if filename.endswith("_comments.json"):
                        continue  # Skip comments metadata

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
                        with open(entry["json"], "r", encoding="utf-8") as f:
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
            logger.info(f"Found {orphaned_count} orphaned metadata entries")

        # Filter out entries with no media files
        catalog = {k: v for k, v in catalog.items() if v["media_files"]}

        logger.info(f"   Found {len(catalog)} posts")
        logger.info(f"   Found {self.stats['total_media_files']} media files")

        return catalog

    def extract_caption_from_txt(self, txt_path: Path) -> str:
        """Extract caption from .txt file"""
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                caption = f.read().strip()
            return self.normalize_text(caption)
        except Exception as e:
            self.log_message(
                "TXT_READ_ERROR",
                f"Failed to read txt file: {txt_path.name}",
                str(e),
            )
            return ""

    def extract_metadata_from_json(self, json_path: Path) -> Dict:
        """
        Extract metadata from .json file
        Returns dict with: caption, timestamp, timestamp_raw, media_type
        """
        metadata = {
            "caption": "",
            "timestamp": None,
            "timestamp_raw": None,
            "media_type": None,
        }

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Extract data from nested structure
            node = data.get("node", {})

            # Extract caption
            caption_edges = node.get("edge_media_to_caption", {}).get("edges", [])
            if caption_edges and len(caption_edges) > 0:
                caption_text = caption_edges[0].get("node", {}).get("text", "")
                metadata["caption"] = self.normalize_text(caption_text)

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
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # Submit all post processing tasks
            future_to_post = {
                executor.submit(self._process_single_post, post_tuple): post_tuple[0]
                for post_tuple in catalog_items
            }

            # Collect results as they complete
            for future in futures_progress(
                future_to_post, PHASE_PREPROCESS, "Parsing posts", unit="post"
            ):
                try:
                    post_data = future.result()
                    all_posts.append(post_data)
                except Exception as e:
                    post_name = future_to_post[future]
                    self.log_message(
                        "POST_PROCESSING_ERROR",
                        f"Failed to process post {post_name}",
                        str(e),
                    )
                    logger.error(f"Failed to process post {post_name}: {e}")

        with self.stats_lock:
            self.stats["total_posts"] = len(all_posts)

        return all_posts

    def copy_media_files(self, metadata: List[Dict]) -> None:
        """Copy media files to output directory using copy_with_dedup."""
        # Create output media directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Collecting media files for copy...")
        logger.debug(
            f"Copying {self.stats['total_media_files']} media files to {self.media_output_dir}"
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
                    logger.warning(f"Media file not found: {filename}")
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

        # Phase 2: Copy files with deduplication
        logger.info("Copying %d media files with deduplication...", len(copy_tasks))
        results = self.copy_with_dedup(copy_tasks)

        # Build a map from source_path to dest_filename for updating posts
        source_to_dest: Dict[str, str] = {}

        for source_path, result in results.items():
            source_path_str = str(source_path)

            if result.is_duplicate:
                self.stats["duplicate_files"] += 1
            else:
                self.stats["media_copied"] += 1

            # Map this source path to the resulting destination filename
            if source_path_str and result.dest_filename:
                source_to_dest[source_path_str] = result.dest_filename

        # Phase 3: Update posts with resulting filenames (in case of dedup renames)
        for post_idx, post in enumerate(metadata):
            media_files = post.get("media_files", [])
            updated_files = []

            for filename in media_files:
                source_path = file_map.get((post_idx, filename))
                if source_path:
                    dest_filename = source_to_dest.get(str(source_path))
                    if dest_filename:
                        updated_files.append(dest_filename)

            post["media_files"] = updated_files

        logger.info(f"   Copied {self.stats['media_copied']} unique files")
        logger.info(f"   Deduplicated {self.stats['duplicate_files']} duplicate files")

    def save_metadata(self, metadata: List[Dict]) -> None:
        """Save cleaned metadata to metadata.json"""
        try:
            # Detect username from export
            detection_result = self.detect_username()
            export_username = detection_result.username or "unknown"

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
                    "export_username": export_username,
                    "username_source": detection_result.source,
                    "processed_date": datetime.now().isoformat(),
                    "total_posts": self.stats["total_posts"],
                    "total_media_files": self.stats["media_copied"],
                },
                "media": metadata,
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
        self.save_metadata(metadata)

        return True
