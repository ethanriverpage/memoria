#!/usr/bin/env python3
"""
Old Instagram Export Preprocessor

Organizes older Instagram export files (2021 and earlier) and creates cleaned metadata:
- Scans media files and groups carousel posts
- Extracts captions from .txt files
- Extracts metadata from .json files
- Creates metadata.json with essential information (captions, timestamps)
"""

import json
import logging
import shutil
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import multiprocessing
from common.progress import PHASE_PREPROCESS, futures_progress
from common.utils import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, ALL_MEDIA_EXTENSIONS
from common.filter_banned_files import BannedFilesFilter
from common.failure_tracker import FailureTracker

# Set up logging
logger = logging.getLogger(__name__)


class OldInstagramPreprocessor:
    """Preprocesses old Instagram export by organizing files and cleaning metadata"""

    # Use common media extensions (images and videos only)
    MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        self.export_path = Path(export_path)

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
            processor_name="Instagram Old Public Media",
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
            "posts_with_json": 0,
            "posts_with_txt": 0,
            "posts_with_only_media": 0,
            "carousel_posts": 0,
            "banned_files_skipped": 0,
        }

        # Log entries
        self.log_entries = []

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
                f.write("Old Instagram Export Preprocessing Log\n")
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
        """Validate that export directory exists and contains media files"""
        if not self.export_path.exists():
            print(f"ERROR: Export path does not exist: {self.export_path}")
            return False

        if not self.export_path.is_dir():
            print(f"ERROR: Export path is not a directory: {self.export_path}")
            return False

        # Check if at least one media file exists
        media_files = []
        for ext in self.MEDIA_EXTENSIONS:
            media_files.extend(list(self.export_path.glob(f"*{ext}")))

        if not media_files:
            print(f"ERROR: No media files found in {self.export_path}")
            return False

        return True

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

        print("\nScanning export directory...")

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

        print(f"\nProcessing posts (using {self.workers} workers)...")

        catalog_items = list(catalog.items())

        # Process posts in parallel
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # Submit all post processing tasks
            future_to_post = {
                executor.submit(self._process_single_post, post_tuple): post_tuple[0]
                for post_tuple in catalog_items
            }
            
            # Collect results as they complete
            for future in futures_progress(future_to_post, PHASE_PREPROCESS, "Parsing posts", unit="post"):
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
        """Copy media files to output directory"""
        # Create output media directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        logger.info("\nCopying media files...")
        logger.debug(f"Copying {self.stats['total_media_files']} media files to {self.media_output_dir}")

        for post in metadata:
            media_files = post.get("media_files", [])

            for filename in media_files:
                source_path = self.export_path / filename
                dest_path = self.media_output_dir / filename

                if not source_path.exists():
                    self.log_message(
                        "MISSING_FILE",
                        f"Media file not found: {filename}",
                    )
                    logger.warning(f"Media file not found: {filename}")
                    continue

                try:
                    logger.debug(f"Copying {filename} ({source_path.stat().st_size} bytes)")
                    shutil.copy2(source_path, dest_path)
                    self.stats["media_copied"] += 1
                except Exception as e:
                    self.log_message(
                        "COPY_ERROR",
                        f"Failed to copy {filename}",
                        str(e),
                    )
                    logger.error(f"Failed to copy {filename}: {e}")

        logger.info(f"   Copied {self.stats['media_copied']} files")

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
        print(f"Carousel posts:             {self.stats['carousel_posts']:>6}")
        print(f"Posts with JSON metadata:   {self.stats['posts_with_json']:>6}")
        print(f"Posts with TXT captions:    {self.stats['posts_with_txt']:>6}")
        print(f"Posts with only media:      {self.stats['posts_with_only_media']:>6}")
        print(f"Banned files skipped:       {self.stats['banned_files_skipped']:>6}")
        print("=" * 70)

    def process(self) -> None:
        """Main processing pipeline"""
        logger.info("Starting Old Instagram Export Preprocessing")
        print(f"Export path: {self.export_path}")
        print(f"Output path: {self.output_dir}\n")

        # Validate export structure
        if not self.validate_export():
            sys.exit(1)

        # Build file catalog
        catalog = self.build_file_catalog()

        # Create metadata
        metadata = self.create_metadata(catalog)

        # Copy media files to output directory
        self.copy_media_files(metadata)

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
        description="Preprocess old Instagram export: organize media and create cleaned metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process in-place (creates media/ and metadata.json in export directory)
  python preprocess_files.py instagram-username-2021-07-25

  # Process to a separate output directory
  python preprocess_files.py instagram-username-2021-07-25 -o processed/
  
  # Process with custom number of workers
  python preprocess_files.py instagram-username-2021-07-25 --workers 8
        """,
    )

    parser.add_argument(
        "export_directory",
        type=str,
        help="Path to old Instagram export directory",
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

    preprocessor = OldInstagramPreprocessor(export_path, output_path, workers=args.workers)
    preprocessor.process()


if __name__ == "__main__":
    main()
