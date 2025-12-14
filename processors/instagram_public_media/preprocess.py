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
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import sys
import argparse
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import multiprocessing
from tqdm import tqdm

from common.filter_banned_files import BannedFilesFilter
from common.failure_tracker import FailureTracker

# Set up logging
logger = logging.getLogger(__name__)



class InstagramPreprocessor:
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
        self.export_path = Path(export_path)
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
            processor_name="Instagram Public Media",
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
            "total_posts": 0,
            "total_media_files": 0,
            "media_copied": 0,
            "orphaned_files": 0,
            "missing_files": 0,
            "banned_files_skipped": 0,
            "by_type": {},
        }

        # Log entries
        self.log_entries = []

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
                f.write("Instagram Export Preprocessing Log\n")
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

        if not self.media_source_dir.exists():
            print(f"ERROR: media directory not found: {self.media_source_dir}")
            return False

        if not self.html_dir.exists():
            new_path = self.export_path / "your_instagram_activity" / "media"
            legacy_path = self.export_path / "content"
            print(f"ERROR: HTML metadata directory not found.")
            print(f"  Checked: {new_path}")
            print(f"  Checked: {legacy_path}")
            return False

        # Check if at least one HTML file exists
        html_files = list(self.html_dir.glob("*.html"))
        if not html_files:
            print(f"ERROR: No HTML files found in {self.html_dir}")
            return False

        return True

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
            "%b %d, %Y %I:%M %p",   # New format: "Oct 02, 2022 5:58 pm"
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

            print(f"   Found {len(post_containers)} posts in {html_path.name}")

            for idx, container in enumerate(post_containers):
                post_data = {"media_type": media_type}

                # Extract caption (optional)
                # New format uses <h2>, legacy format uses <div> with same class
                caption_elem = container.find("h2", class_="_3-95 _2pim _a6-h _a6-i")
                if not caption_elem:
                    caption_elem = container.find("div", class_="_3-95 _2pim _a6-h _a6-i")
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
                logger.debug(f"Constructed metadata for post {idx + 1} in {html_path.name}:")
                logger.debug(f"  Media type: {post_data['media_type']}")
                logger.debug(f"  Timestamp: {post_data['timestamp']} (raw: {post_data['timestamp_raw']})")
                logger.debug(f"  Caption: {post_data['caption'][:100] if post_data['caption'] else '(none)'}...")
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
            print(f"ERROR: Failed to parse {html_path.name}: {e}")

        return posts

    def build_file_catalog(self) -> Dict[str, Path]:
        """
        Scan media directories and build catalog mapping filename -> full source path
        Returns: Dict[filename, source_path]
        """
        catalog = {}

        if not self.media_source_dir.exists():
            return catalog

        print("\nScanning media directories...")

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
        """Copy media files referenced in metadata to output directory"""
        # Create output media directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        print("\nCopying media files...")

        matched_files = set()

        for post in metadata:
            media_paths = post.get("media_paths", [])
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

            # Update post with just filenames (not full paths)
            post["media_files"] = copied_files
            # Remove media_paths as it's no longer needed
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

        logger.info(f"   Copied {self.stats['media_copied']} files")
        print(f"   Missing files: {self.stats['missing_files']}")
        print(f"   Orphaned files: {self.stats['orphaned_files']}")

    def _process_single_html_file(self, html_tuple: Tuple[str, str]) -> Tuple[str, List[Dict]]:
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

    def create_metadata(self, file_catalog: Dict[str, Path]) -> List[Dict]:
        """
        Main processing: parse all HTML files and create metadata
        Returns list of all posts with metadata (multithreaded)
        """
        all_posts = []

        print(f"\nProcessing HTML files (using {self.workers} workers)...")

        html_files = list(self.MEDIA_TYPES.items())

        # Process HTML files in parallel
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # Submit all HTML file processing tasks
            future_to_html = {
                executor.submit(self._process_single_html_file, html_tuple): html_tuple[0]
                for html_tuple in html_files
            }
            
            # Collect results as they complete
            for future in tqdm(as_completed(future_to_html), total=len(future_to_html), desc="Processing HTML files", unit="file"):
                html_basename = future_to_html[future]
                try:
                    media_type, posts = future.result()
                    if posts:
                        print(f"   Processed {html_basename}.html: {len(posts)} posts")
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
            # Add export info
            output = {
                "export_info": {
                    "export_path": str(self.export_path),
                    "export_name": self.export_path.name,
                    "processed_date": datetime.now().isoformat(),
                    "total_posts": self.stats["total_posts"],
                    "total_media_files": self.stats["media_copied"],
                },
                "media": metadata,
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
        print(f"Total posts processed:      {self.stats['total_posts']:>6}")
        print(f"Total media files found:    {self.stats['total_media_files']:>6}")
        print(f"Media files copied:         {self.stats['media_copied']:>6}")
        print(f"Banned files skipped:       {self.stats['banned_files_skipped']:>6}")
        print(f"Missing files:              {self.stats['missing_files']:>6}")
        print(f"Orphaned files:             {self.stats['orphaned_files']:>6}")
        print()
        print("By Media Type:")
        for media_type, type_stats in self.stats["by_type"].items():
            print(
                f"  {media_type:20} {type_stats['posts']:>4} posts, {type_stats['media_files']:>4} files"
            )
        print("=" * 70)

    def process(self) -> None:
        """Main processing pipeline"""
        logger.info("Starting Instagram Export Preprocessing")
        print(f"Export path: {self.export_path}")
        print(f"Output path: {self.output_dir}\n")

        # Validate export structure
        if not self.validate_export():
            sys.exit(1)

        # Build file catalog
        file_catalog = self.build_file_catalog()

        # Create metadata by parsing HTML files
        metadata = self.create_metadata(file_catalog)

        # Copy media files to output directory
        self.copy_media_files(metadata, file_catalog)

        # Save metadata
        self.save_metadata(metadata)

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
        description="Preprocess Instagram export: organize media and create cleaned metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process in-place (creates media/ in export directory)
  python preprocess_files.py instagram-username-2025-10-07

  # Process to a separate output directory
  python preprocess_files.py instagram-username-2025-10-07 -o processed/
  
  # Process with custom number of workers
  python preprocess_files.py instagram-username-2025-10-07 --workers 8
        """,
    )

    parser.add_argument(
        "export_directory",
        type=str,
        help="Path to Instagram export directory (contains media/ and your_instagram_activity/)",
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

    preprocessor = InstagramPreprocessor(export_path, output_path, workers=args.workers)
    preprocessor.process()


if __name__ == "__main__":
    main()
