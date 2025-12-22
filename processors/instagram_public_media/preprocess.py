#!/usr/bin/env python3
"""
Instagram Export Preprocessor

Organizes Instagram export files and creates cleaned metadata:
- Parses HTML files containing post metadata
- Copies media files to organized output directory
- Creates metadata.json with essential information (captions, GPS, timestamps)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor

from common.detection import resolve_username, detect_from_dirname, DetectionResult
from common.preprocess.context_mergers import merge_first_occurrence
from common.processing import print_preprocessing_stats
from common.output.progress import PHASE_PREPROCESS, futures_progress
from processors.preprocess_base import CopyTask, PreprocessorBase

# Set up logging
logger = logging.getLogger(__name__)


class InstagramPreprocessor(PreprocessorBase):
    """Preprocesses Instagram export by organizing files and cleaning metadata"""

    # Media type mapping: HTML filename (without extension) -> output media_type
    MEDIA_TYPES = {
        "posts_1": "posts",
        "archived_posts": "archived_posts",
        "reels": "reels",
        "igtv_videos": "reels",  # Legacy format (pre-2023) uses igtv_videos.html
        "stories": "stories",
        "profile_photos": "profile",
        "other_content": "other",
    }

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        super().__init__(
            processor_name="Instagram Public Media",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        self.media_source_dir = self.export_path / "media"

        # Detect export format: new (2025+) vs legacy (2022)
        new_format_html_dir = self.export_path / "your_instagram_activity" / "media"
        legacy_format_html_dir = self.export_path / "content"

        if new_format_html_dir.exists():
            self.html_dir = new_format_html_dir
            self.is_legacy_format = False
        elif legacy_format_html_dir.exists():
            self.html_dir = legacy_format_html_dir
            self.is_legacy_format = True
        else:
            # Will fail validation with clear error
            self.html_dir = new_format_html_dir
            self.is_legacy_format = False

        # Statistics specific to this preprocessor
        self.stats = {
            "total_posts": 0,
            "total_media_files": 0,
            "media_copied": 0,
            "duplicate_files": 0,
            "orphaned_files": 0,
            "missing_files": 0,
            "banned_files_skipped": 0,
            "by_type": {},
        }

        # Set context merger for deduplication (first occurrence wins)
        self._context_merger = merge_first_occurrence

    def validate_export(self) -> bool:
        """Validate that export directory has required structure"""
        if not self.export_path.exists():
            logger.error("Export path does not exist: %s", self.export_path)
            return False

        if not self.media_source_dir.exists():
            logger.error("media directory not found: %s", self.media_source_dir)
            return False

        if not self.html_dir.exists():
            new_path = self.export_path / "your_instagram_activity" / "media"
            legacy_path = self.export_path / "content"
            logger.error("HTML metadata directory not found.")
            logger.error("  Checked: %s", new_path)
            logger.error("  Checked: %s", legacy_path)
            return False

        # Check if at least one HTML file exists
        html_files = list(self.html_dir.glob("*.html"))
        if not html_files:
            logger.error("No HTML files found in %s", self.html_dir)
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
          - New (2025+): "Oct 02, 2022 5:58 pm"
          - Legacy (2022): "Oct 02, 2022, 5:58 PM" (extra comma after year)
        Output format: "2022-10-02 17:58:00"
        """
        # Try both timestamp formats (new format first, then legacy with comma)
        formats = [
            "%b %d, %Y %I:%M %p",  # New format: "Oct 02, 2022 5:58 pm"
            "%b %d, %Y, %I:%M %p",  # Legacy format: "Oct 02, 2022, 5:58 PM"
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

    def extract_gps(self, post_element) -> Tuple[Optional[float], Optional[float]]:
        """
        Extract GPS coordinates from post element
        Returns: (latitude, longitude) or (None, None)
        """
        latitude = None
        longitude = None

        try:
            # Find all table rows
            tables = post_element.find_all("table")
            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    # Look for Latitude/Longitude labels
                    label_div = row.find("div", class_="_a6-q")
                    if label_div:
                        label_text = label_div.get_text(strip=True)
                        if label_text == "Latitude":
                            # Find the value in the next div
                            value_divs = row.find_all("div", class_="_a6-q")
                            if len(value_divs) >= 2:
                                try:
                                    latitude = float(value_divs[1].get_text(strip=True))
                                except ValueError:
                                    pass
                        elif label_text == "Longitude":
                            value_divs = row.find_all("div", class_="_a6-q")
                            if len(value_divs) >= 2:
                                try:
                                    longitude = float(
                                        value_divs[1].get_text(strip=True)
                                    )
                                except ValueError:
                                    pass
        except Exception as e:
            self.log_message("GPS_PARSE_ERROR", "Failed to extract GPS", str(e))

        return latitude, longitude

    def extract_additional_metadata(self, post_element) -> Dict:
        """Extract additional metadata fields from tables"""
        metadata = {}

        try:
            tables = post_element.find_all("table")
            for table in tables:
                rows = table.find_all("tr")
                for row in rows:
                    label_div = row.find("div", class_="_a6-q")
                    if label_div:
                        label_text = label_div.get_text(strip=True)
                        # Skip GPS fields (handled separately)
                        if label_text in [
                            "Latitude",
                            "Longitude",
                            "Has Camera Metadata",
                        ]:
                            continue

                        # Get value
                        value_divs = row.find_all("div", class_="_a6-q")
                        if len(value_divs) >= 2:
                            value_text = value_divs[1].get_text(strip=True)
                            if value_text:
                                # Convert field name to snake_case
                                field_name = label_text.lower().replace(" ", "_")
                                metadata[field_name] = value_text
        except Exception as e:
            self.log_message(
                "METADATA_PARSE_ERROR",
                "Failed to extract additional metadata",
                str(e),
            )

        return metadata

    def extract_media_paths(self, post_element) -> List[str]:
        """
        Extract all media file paths from post element
        Returns list of relative paths like: media/posts/202210/17948813480239445.jpg
        """
        media_paths = []

        try:
            # Find all <a> tags with href containing "media/"
            links = post_element.find_all("a", href=True)
            for link in links:
                href = link.get("href", "")
                if href.startswith("media/"):
                    media_paths.append(href)

            # Find all <video> tags with src containing "media/"
            videos = post_element.find_all("video", src=True)
            for video in videos:
                src = video.get("src", "")
                if src.startswith("media/"):
                    media_paths.append(src)

        except Exception as e:
            self.log_message(
                "MEDIA_PATH_EXTRACT_ERROR",
                "Failed to extract media paths",
                str(e),
            )

        return media_paths

    def parse_html_file(self, html_path: Path, media_type: str) -> List[Dict]:
        """
        Parse an HTML file and extract post metadata
        Returns list of post dictionaries
        """
        posts = []

        try:
            with open(html_path, "r", encoding="utf-8") as f:
                soup = BeautifulSoup(f, "html.parser")

            # Find all post containers
            post_containers = soup.find_all(
                "div", class_="pam _3-95 _2ph- _a6-g uiBoxWhite noborder"
            )

            logger.debug(
                "   Found %d posts in %s", len(post_containers), html_path.name
            )

            for idx, container in enumerate(post_containers):
                post_data: Dict[str, Any] = {"media_type": media_type}

                # Extract caption (optional)
                # New format uses <h2>, legacy format uses <div> with same class
                caption_elem = container.find("h2", class_="_3-95 _2pim _a6-h _a6-i")
                if not caption_elem:
                    caption_elem = container.find(
                        "div", class_="_3-95 _2pim _a6-h _a6-i"
                    )
                post_data["caption"] = (
                    caption_elem.get_text(strip=True) if caption_elem else ""
                )

                # Extract timestamp
                timestamp_elem = container.find("div", class_="_3-94 _a6-o")
                if timestamp_elem:
                    timestamp_str = timestamp_elem.get_text(strip=True)
                    post_data["timestamp"] = self.parse_timestamp(timestamp_str)
                    post_data["timestamp_raw"] = timestamp_str
                else:
                    post_data["timestamp"] = None
                    post_data["timestamp_raw"] = None

                # Extract media paths
                media_paths = self.extract_media_paths(container)
                post_data["media_paths"] = media_paths

                # Extract GPS
                latitude, longitude = self.extract_gps(container)
                post_data["latitude"] = latitude
                post_data["longitude"] = longitude

                # Extract additional metadata
                additional = self.extract_additional_metadata(container)
                if additional:
                    post_data["additional_metadata"] = additional

                # Log constructed metadata
                logger.debug(
                    f"Constructed metadata for post {idx + 1} in {html_path.name}:"
                )
                logger.debug(f"  Media type: {post_data['media_type']}")
                logger.debug(
                    f"  Timestamp: {post_data['timestamp']} (raw: {post_data['timestamp_raw']})"
                )
                logger.debug(
                    f"  Caption: {post_data['caption'][:100] if post_data['caption'] else '(none)'}..."
                )
                logger.debug(f"  GPS: lat={latitude}, lon={longitude}")
                logger.debug(f"  Media files: {len(media_paths)} file(s)")
                for media_path in media_paths:
                    logger.debug(f"    - {media_path}")
                if additional:
                    logger.debug(f"  Additional metadata: {additional}")

                posts.append(post_data)

        except Exception as e:
            self.log_message(
                "HTML_PARSE_ERROR",
                f"Failed to parse {html_path.name}",
                str(e),
            )
            logger.error("Failed to parse %s: %s", html_path.name, e)

        return posts

    def build_file_catalog(self) -> Dict[str, Path]:
        """
        Scan media directories and build catalog mapping filename -> full source path
        Returns: Dict[filename, source_path]
        """
        catalog = {}

        if not self.media_source_dir.exists():
            return catalog

        logger.info("Scanning media directories...")

        # Recursively find all media files
        for file_path in self.media_source_dir.rglob("*"):
            # Skip banned directories (check all parts of path)
            if any(
                self.banned_filter.is_banned(part_path)
                for part_path in file_path.parents
            ):
                continue

            if file_path.is_file():
                # Skip banned files
                if self.banned_filter.is_banned(file_path):
                    self.stats["banned_files_skipped"] += 1
                    self.log_message(
                        "BANNED_FILE_SKIPPED",
                        f"Skipped banned file: {file_path.name}",
                        f"path: {file_path.relative_to(self.media_source_dir)}",
                    )
                    continue

                # Map filename to source path
                catalog[file_path.name] = file_path
                self.stats["total_media_files"] += 1

        logger.info(f"   Found {len(catalog)} media files")

        return catalog

    def copy_media_files(
        self, metadata: List[Dict], file_catalog: Dict[str, Path]
    ) -> None:
        """Copy media files referenced in metadata to output directory using copy_with_dedup."""
        # Create output media directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Collecting media files for copy...")

        # Phase 1: Collect all files to copy
        copy_tasks: List[CopyTask] = []
        # Track which post/media_path pairs map to which source paths
        post_file_map: Dict[Tuple[int, str], Optional[Path]] = {}
        matched_files = set()

        for post_idx, post in enumerate(metadata):
            media_paths = post.get("media_paths", [])

            for media_path in media_paths:
                # Extract filename from path
                filename = Path(media_path).name

                if filename in file_catalog:
                    source_path = file_catalog[filename]
                    post_file_map[(post_idx, media_path)] = source_path
                    matched_files.add(filename)

                    # Build context for this file
                    context = {
                        "_source_path": str(source_path),
                        "media_type": post.get("media_type"),
                        "caption": post.get("caption", ""),
                        "timestamp": post.get("timestamp"),
                        "latitude": post.get("latitude"),
                        "longitude": post.get("longitude"),
                        "media_path": media_path,
                    }

                    copy_tasks.append(
                        CopyTask(
                            source_path=source_path,
                            dest_filename=filename,
                            context=context,
                        )
                    )
                else:
                    post_file_map[(post_idx, media_path)] = None
                    self.log_message(
                        "MISSING_FILE",
                        f"File referenced in HTML not found: {filename}",
                        f"from path: {media_path}",
                    )
                    self.stats["missing_files"] += 1

                    # Track orphaned metadata
                    self.failure_tracker.add_orphaned_metadata(
                        metadata_entry={
                            "media_type": post.get("media_type"),
                            "media_path": media_path,
                            "caption": post.get("caption", ""),
                            "timestamp": post.get("timestamp"),
                        },
                        reason="Media file not found in filesystem",
                        context={
                            "expected_filename": filename,
                        },
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

        # Phase 3: Update posts with resulting filenames
        for post_idx, post in enumerate(metadata):
            media_paths = post.get("media_paths", [])
            copied_files = []

            for media_path in media_paths:
                source_path = post_file_map.get((post_idx, media_path))
                if source_path:
                    dest_filename = source_to_dest.get(str(source_path))
                    if dest_filename:
                        copied_files.append(dest_filename)

            # Update post with just filenames (not full paths)
            post["media_files"] = copied_files
            # Remove media_paths as it's no longer needed
            if "media_paths" in post:
                del post["media_paths"]

        # Report orphaned files (in filesystem but not referenced in HTML)
        orphaned = set(file_catalog.keys()) - matched_files
        self.stats["orphaned_files"] = len(orphaned)
        for filename in orphaned:
            source_path = file_catalog[filename]
            self.failure_tracker.add_orphaned_media(
                media_path=source_path,
                reason="No matching metadata found",
                context={
                    "original_location": str(source_path),
                },
            )
        for filename in orphaned:
            self.log_message(
                "ORPHANED_FILE",
                f"File in filesystem not referenced in any HTML: {filename}",
            )

        logger.info("   Copied %d unique files", self.stats["media_copied"])
        logger.info("   Deduplicated %d duplicate files", self.stats["duplicate_files"])
        logger.info("   Missing files: %d", self.stats["missing_files"])
        logger.info("   Orphaned files: %d", self.stats["orphaned_files"])

    def _process_single_html_file(
        self, html_tuple: Tuple[str, str]
    ) -> Tuple[str, List[Dict]]:
        """
        Process a single HTML file (used by multithreaded processing)

        Args:
            html_tuple: Tuple of (html_basename, media_type)

        Returns:
            Tuple of (media_type, list of posts)
        """
        html_basename, media_type = html_tuple
        html_path = self.html_dir / f"{html_basename}.html"

        if not html_path.exists():
            return (media_type, [])

        posts = self.parse_html_file(html_path, media_type)

        # Update statistics
        with self.stats_lock:
            if media_type not in self.stats["by_type"]:
                self.stats["by_type"][media_type] = {"posts": 0, "media_files": 0}

            self.stats["by_type"][media_type]["posts"] = len(posts)
            for post in posts:
                self.stats["by_type"][media_type]["media_files"] += len(
                    post.get("media_paths", [])
                )

        return (media_type, posts)

    def create_metadata(self, _file_catalog: Dict[str, Path]) -> List[Dict]:
        """
        Main processing: parse all HTML files and create metadata
        Returns list of all posts with metadata (multithreaded)

        Note: _file_catalog is accepted for API consistency but not used here.
        """
        all_posts = []

        logger.info("Processing HTML files (using %d workers)...", self.workers)

        html_files = list(self.MEDIA_TYPES.items())

        # Process HTML files in parallel
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # Submit all HTML file processing tasks
            future_to_html = {
                executor.submit(self._process_single_html_file, html_tuple): html_tuple[
                    0
                ]
                for html_tuple in html_files
            }

            # Collect results as they complete
            for future in futures_progress(
                future_to_html, PHASE_PREPROCESS, "Parsing HTML files", unit="file"
            ):
                html_basename = future_to_html[future]
                try:
                    _, posts = future.result()
                    if posts:
                        logger.debug(
                            "   Processed %s.html: %d posts", html_basename, len(posts)
                        )
                        all_posts.extend(posts)
                except Exception as e:
                    self.log_message(
                        "HTML_PROCESSING_ERROR",
                        f"Failed to process {html_basename}.html",
                        str(e),
                    )
                    logger.error(f"Failed to process {html_basename}.html: {e}")

        with self.stats_lock:
            self.stats["total_posts"] = len(all_posts)

        return all_posts

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
            raise RuntimeError(f"Failed to save metadata: {e}")

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Posts processed": self.stats["total_posts"],
            "Media files found": self.stats["total_media_files"],
            "Files copied": self.stats["media_copied"],
            "Banned skipped": self.stats["banned_files_skipped"],
            "Missing files": self.stats["missing_files"],
            "Orphaned files": self.stats["orphaned_files"],
        }
        # Add media type breakdown
        for media_type, type_stats in self.stats["by_type"].items():
            stats[f"{media_type}"] = type_stats["media_files"]
        print_preprocessing_stats("Preprocessing Stats", stats)

    def process_export(self) -> bool:
        """Platform-specific preprocessing logic.

        Processes all posts, matching media files to metadata
        and performing deduplication.

        Returns:
            True if preprocessing succeeded, False otherwise
        """
        # Build file catalog
        file_catalog = self.build_file_catalog()

        # Create metadata by parsing HTML files
        metadata = self.create_metadata(file_catalog)

        # Copy media files to output directory
        self.copy_media_files(metadata, file_catalog)

        # Save metadata
        self.save_metadata(metadata)

        return True
