#!/usr/bin/env python3
"""
Google Photos Preprocessor

Organizes Google Photos export files and creates cleaned metadata:
- Parses JSON files containing photo/video metadata
- Copies media files to organized output directory
- Creates metadata.json with essential information (timestamps, GPS, albums, people tags)
"""

import json
import logging
import shutil
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
import sys
import argparse
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import multiprocessing
import xxhash

from common.progress import PHASE_PREPROCESS, futures_progress

from common.file_utils import detect_and_correct_extension
from common.utils import ALL_MEDIA_EXTENSIONS
from common.filter_banned_files import BannedFilesFilter
from common.failure_tracker import FailureTracker

# Set up logging
logger = logging.getLogger(__name__)


class GooglePhotosPreprocessor:
    """Preprocesses Google Photos export by organizing files and cleaning metadata"""

    # Compiled regex patterns for better performance
    SUPPLEMENTAL_WITH_DUP_PATTERN = re.compile(r"(\.[^.]+)\.supp[^.]*\((\d+)\)\.json$")
    SUPPLEMENTAL_PATTERN = re.compile(r"\.supp[^.]*\.json$")
    FILE_INDEX_PATTERN = re.compile(r"\((\d+)\)(\.[^.]+)$")

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        self.export_path = Path(export_path)
        self.photos_dir = self.export_path / "Google Photos"

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
            processor_name="Google Photos",
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
        self.hash_lock = Lock()

        # Statistics
        self.stats = {
            "total_albums": 0,
            "total_media_files": 0,
            "media_copied": 0,
            "matched_files": 0,
            "unmatched_files": 0,
            "fuzzy_matches": 0,
            "extensions_corrected": 0,
            "duplicate_files": 0,
            "deduplicated_files": 0,
            "skipped_banned": 0,
        }

        # Log entries
        self.log_entries = []

        # Track copied files to avoid duplicates
        self.copied_files = {}  # maps (size, hash) -> destination_filename
        self.destination_files = {}  # maps destination_filename -> source_path
        
        # Track file hashes for deduplication across albums
        # Maps hash -> {"filename": str, "albums": [str], "metadata": Dict}
        self.file_hashes = {}

    def is_banned(self, path: Path) -> bool:
        """
        Check if a file or directory should be skipped based on banned patterns

        Args:
            path: Path object to check

        Returns:
            True if the path matches any banned pattern, False otherwise
        """
        return self.banned_filter.is_banned(path)

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
                f.write("Google Photos Preprocessing Log\n")
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

        if not self.photos_dir.exists():
            print(f"ERROR: Google Photos directory not found: {self.photos_dir}")
            return False

        # Check if at least one album folder exists
        album_folders = [d for d in self.photos_dir.iterdir() if d.is_dir()]
        if not album_folders:
            print(f"ERROR: No album folders found in {self.photos_dir}")
            return False

        return True

    def parse_timestamp(self, timestamp_str: str) -> Optional[str]:
        """
        Convert epoch timestamp to ISO format
        Input: "1523204744"
        Output: "2018-04-08T16:25:44Z"
        """
        try:
            ts = int(timestamp_str)
            dt = datetime.fromtimestamp(ts, timezone.utc)
            return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError) as e:
            self.log_message(
                "TIMESTAMP_PARSE_ERROR",
                f"Failed to parse timestamp: {timestamp_str}",
                str(e),
            )
            return None

    def scan_albums(self) -> List[Tuple[str, Path]]:
        """
        Scan Google Photos directory for all album folders
        Returns list of (album_name, folder_path) tuples
        """
        albums = []

        try:
            import os
            with os.scandir(self.photos_dir) as entries:
                for entry in sorted(entries, key=lambda e: e.name):
                    if not entry.is_dir():
                        continue

                    # Skip system directories early
                    if entry.name.startswith((".", "_")):
                        continue

                    album_dir = Path(entry.path)

                    # Skip banned directories
                    if self.is_banned(album_dir):
                        with self.stats_lock:
                            self.stats["skipped_banned"] += 1
                        self.log_message(
                            "SKIPPED_BANNED",
                            f"Skipped banned directory: {entry.name}",
                        )
                        continue

                    album_name = entry.name

                    # Check if folder has media or metadata
                    has_content = False
                    with os.scandir(album_dir) as items:
                        for item in items:
                            if item.is_file():
                                has_content = True
                                break

                    if has_content:
                        albums.append((album_name, album_dir))

        except Exception as e:
            self.log_message(
                "SCAN_ERROR",
                "Failed to scan album folders",
                str(e),
            )
            logger.error(f"Failed to scan albums: {e}")

        return albums

    def build_album_catalog(self, album_folder: Path) -> Dict:
        """
        Build file catalog for a specific album folder
        Returns: Dict with 'media' (filename -> path) and 'metadata' (filename -> json_path)
        """
        catalog = {
            "media": {},
            "metadata": {},
        }

        # Use common media extensions
        media_extensions = ALL_MEDIA_EXTENSIONS

        try:
            import os
            with os.scandir(album_folder) as entries:
                for entry in entries:
                    if not entry.is_file():
                        continue

                    filename = entry.name

                    # Skip system files early (before Path creation)
                    if filename.startswith((".", "_")):
                        continue

                    # Create Path object only once when needed
                    file_path = Path(entry.path)

                    # Skip banned files
                    if self.is_banned(file_path):
                        with self.stats_lock:
                            self.stats["skipped_banned"] += 1
                        continue

                    # Check for metadata JSON files
                    if filename.endswith(".json"):
                        # Album metadata file
                        if filename == "metadata.json":
                            catalog["album_metadata"] = file_path
                        # Media metadata file - handle multiple patterns
                        else:
                            # Extract the base media filename
                            base_name = None

                            # Pattern 1: file.ext.supplemental*(N).json (with duplicate number)
                            # Example: RenderedImage.JPG.supplemental-metadata(1).json → RenderedImage(1).JPG
                            # Must check this first before the general supplemental pattern
                            # Matches ANY truncation: .supplemental-metadata(1), .supplemental-meta(1),
                            # .supplemental(1), .suppl(1), etc.
                            # Changed to .supp[^.]* to handle truncation within the word itself
                            match = self.SUPPLEMENTAL_WITH_DUP_PATTERN.search(filename)
                            if match:
                                # Extract extension and duplicate index to reconstruct correct base name
                                ext = match.group(1)  # e.g., ".JPG"
                                idx = match.group(2)  # e.g., "1"
                                base_without_ext = filename[
                                    : match.start()
                                ]  # e.g., "RenderedImage"
                                # Reconstruct with duplicate number in correct position
                                base_name = f"{base_without_ext}({idx}){ext}"
                            else:
                                # Pattern 2: file.ext.supplemental*.json (any truncation)
                                # Matches .supplemental-metadata.json, .supplemental-meta.json,
                                # .supplemental.json, .supple.json, .suppl.json, etc.
                                # Uses regex HasPrefix logic like immich-go to handle all truncations
                                # Changed to .supp[^.]* to handle truncation within the word itself
                                match = self.SUPPLEMENTAL_PATTERN.search(filename)
                                if match:
                                    base_name = filename[: match.start()]
                                # Pattern 3: file.ext.json (has 2+ dots, likely media metadata)
                                elif filename.count(".") >= 2:
                                    base_name = filename[:-5]  # Remove .json
                                # Pattern 4: single extension .json that's not metadata.json
                                # These are direct metadata files like "uuid__hash.json"
                                else:
                                    # This is likely a media metadata file without double extension
                                    # Store without .json extension
                                    base_name = filename[:-5]  # Remove .json

                            if base_name:
                                catalog["metadata"][base_name] = file_path
                        continue

                    # Check if it's a media file
                    if file_path.suffix.lower() in media_extensions:
                        catalog["media"][filename] = file_path

        except Exception as e:
            self.log_message(
                "CATALOG_ERROR",
                f"Failed to catalog album {album_folder.name}",
                str(e),
            )

        return catalog

    def get_file_index(self, name: str) -> Tuple[str, str]:
        """
        Extract index from filename like IMG_0004(1).PNG
        Returns: (base_name, index) e.g., ("IMG_0004.PNG", "1")
        """
        match = self.FILE_INDEX_PATTERN.search(name)
        if match:
            index = match.group(1)
            ext = match.group(2)
            base = name[: match.start()] + ext
            return base, index
        return name, ""

    def match_exact(self, media_name: str, metadata_name: str) -> bool:
        """Direct filename match"""
        return media_name == metadata_name

    def match_with_duplicates(self, media_name: str, metadata_name: str) -> bool:
        """
        Handle numbered duplicates: IMG_0004(1).PNG matches IMG_0004.PNG metadata
        Also handles Live Photo pairs where base names match but extensions differ.

        Based on immich-go's matchNormal logic.
        """
        # Extract indices from both
        media_base, media_idx = self.get_file_index(media_name)
        metadata_base, metadata_idx = self.get_file_index(metadata_name)

        # Case 1: Both have indices - they must match exactly
        if media_idx and metadata_idx:
            if media_idx != metadata_idx:
                return False
            # Indices match, compare base names
            media_stem = Path(media_base).stem
            metadata_stem = Path(metadata_base).stem
            return media_stem == metadata_stem

        # Case 2: Media has index but metadata doesn't (duplicate file case)
        # IMG_0004(1).PNG matches IMG_0004.PNG metadata
        if media_idx and not metadata_idx:
            media_stem = Path(media_base).stem
            metadata_stem = Path(metadata_name).stem
            return media_stem == metadata_stem

        # Case 3: Neither has index (standard Live Photo case)
        # IMG_0354.MP4 matches IMG_0354.JPG
        if not media_idx and not metadata_idx:
            media_stem = Path(media_name).stem
            metadata_stem = Path(metadata_name).stem
            return media_stem == metadata_stem

        return False

    def match_truncated(self, media_name: str, metadata_name: str) -> bool:
        """
        Handle truncated filenames (>46 chars)
        """
        # Check if one is a prefix of the other
        if len(media_name) < 30 and len(metadata_name) < 30:
            return False

        # Compare extensions first
        media_ext = Path(media_name).suffix.lower()
        metadata_ext = Path(metadata_name).suffix.lower()
        if media_ext != metadata_ext:
            return False

        # Compare base names
        media_base = Path(media_name).stem
        metadata_base = Path(metadata_name).stem

        # Check if one is a prefix of the other (truncation)
        min_prefix_len = 30
        if (
            media_base.startswith(metadata_base)
            and len(metadata_base) >= min_prefix_len
        ):
            return True
        if metadata_base.startswith(media_base) and len(media_base) >= min_prefix_len:
            return True

        return False

    def match_edited_names(self, media_name: str, metadata_name: str) -> bool:
        """
        Handle edited versions: PXL_20220405_090123740.PORTRAIT-modified.jpg
        matches PXL_20220405_090123740.PORTRAIT.jpg metadata

        Based on immich-go's matchEditedName logic.

        Example:
        - JSON: PXL_20220405_090123740.PORTRAIT.jpg.json
        - metadata_name: PXL_20220405_090123740.PORTRAIT.jpg
        - media_name: PXL_20220405_090123740.PORTRAIT-modifié.jpg

        This matcher checks if the metadata name contains a media extension,
        strips it, and checks if the media file name (without extension) starts
        with the metadata base (without media extension).
        """
        # Don't match if media has numbered duplicates like IMG_0001(1).JPG
        _, media_idx = self.get_file_index(media_name)
        if media_idx:
            return False

        # Define media extensions
        media_extensions = ALL_MEDIA_EXTENSIONS

        # Get the extension from metadata_name (which has .json already stripped)
        metadata_ext = Path(metadata_name).suffix.lower()

        # Check if metadata_name has a media extension as its last extension
        # This is the key difference from matchForgottenDuplicates
        if metadata_ext and metadata_ext in media_extensions:
            # Strip the media extension from both
            metadata_base = Path(metadata_name).stem
            media_base = Path(media_name).stem

            # Check if media base starts with metadata base
            # AND that media base is actually different (longer) than metadata base
            # This prevents matching regular Live Photos where bases are identical
            if media_base.startswith(metadata_base) and media_base != metadata_base:
                return True

        return False

    def match_trailing_chars(self, media_name: str, metadata_name: str) -> bool:
        """
        Handle trailing character differences like:
        - Media: "uuid__hash-.jpg"
        - Metadata: "uuid__hash" (without trailing dash)
        """
        # Check extensions match
        media_ext = Path(media_name).suffix.lower()
        metadata_ext = Path(metadata_name).suffix.lower()
        if media_ext != metadata_ext:
            return False

        media_base = Path(media_name).stem
        metadata_base = Path(metadata_name).stem

        # Check if one is the other with trailing punctuation removed
        # Remove common trailing characters: dash, underscore, period
        media_stripped = media_base.rstrip("-_.")
        metadata_stripped = metadata_base.rstrip("-_.")

        return media_stripped == metadata_stripped

    def match_live_photo_duplicates(self, media_name: str, metadata_name: str) -> bool:
        """
        Handle Live Photo video components with duplicate numbers:
        - Media: RenderedImage(1).MP4
        - Metadata: RenderedImage(1).JPG (from RenderedImage.JPG.supplemental-metadata(1).json)

        Both files must have the SAME duplicate index to match.
        """
        # Only for media with duplicate indices
        media_base, media_idx = self.get_file_index(media_name)
        if not media_idx:
            return False

        # Check if metadata also has a duplicate index
        metadata_base, metadata_idx = self.get_file_index(metadata_name)

        # Both must have the SAME duplicate index
        if media_idx != metadata_idx:
            return False

        # If indices match, check if the base names (without numbers) match
        media_stem = Path(media_base).stem
        metadata_stem = Path(metadata_base).stem

        # Compare stems: RenderedImage.MP4 vs RenderedImage.JPG
        return media_stem == metadata_stem

    def match_live_photo_variants(self, media_name: str, metadata_name: str) -> bool:
        """
        Handle Live Photo pairs where files are truncated at different points.
        Example:
        - JSON: 70391126464__72D07F3A-468D-4FD6-A9D1-2D368E323.json
        - HEIC: 70391126464__72D07F3A-468D-4FD6-A9D1-2D368E323.HEIC (matches exactly)
        - MP4:  70391126464__72D07F3A-468D-4FD6-A9D1-2D368E3231.MP4 (different truncation)

        Match if they share a long common prefix (indicating same original name)
        """
        # Strip extensions
        media_base = Path(media_name).stem
        metadata_base = Path(metadata_name).stem

        # Need at least 40 chars to consider this matcher
        if len(media_base) < 40 or len(metadata_base) < 40:
            return False

        # Find common prefix length
        common_len = 0
        min_len = min(len(media_base), len(metadata_base))
        for i in range(min_len):
            if media_base[i] == metadata_base[i]:
                common_len += 1
            else:
                break

        # Match if they share at least 95% of the shorter name
        # and at least 40 characters
        shorter_len = min(len(media_base), len(metadata_base))
        if common_len >= 40 and common_len >= shorter_len * 0.95:
            return True

        return False

    def find_metadata_for_media(
        self, media_name: str, metadata_catalog: Dict[str, Path]
    ) -> Optional[Path]:
        """
        Find matching metadata for a media file using various matching strategies
        Returns metadata file path or None

        When multiple matches exist, applies tie-breaking logic to select the best match:
        1. Prioritize exact matches
        2. Prefer matches with same duplicate index
        3. Fall back to first match found
        """
        # Try matchers in priority order (based on immich-go)
        matchers = [
            ("exact", self.match_exact),
            (
                "normal",
                self.match_with_duplicates,
            ),  # Handles base name matches + duplicates
            ("live_photo_dups", self.match_live_photo_duplicates),
            ("trailing_chars", self.match_trailing_chars),
            ("truncated", self.match_truncated),
            ("edited", self.match_edited_names),
            ("live_photo_variants", self.match_live_photo_variants),
        ]

        # Collect ALL matches instead of returning first
        all_matches = []

        for matcher_name, matcher_fn in matchers:
            for metadata_name, metadata_path in metadata_catalog.items():
                if matcher_fn(media_name, metadata_name):
                    all_matches.append((metadata_name, metadata_path, matcher_name))

        # No matches found
        if not all_matches:
            return None

        # Single match - return it
        if len(all_matches) == 1:
            match = all_matches[0]
            match_type = "EXACT_MATCH" if match[2] == "exact" else "FUZZY_MATCH"
            if match[2] != "exact":
                with self.stats_lock:
                    self.stats["fuzzy_matches"] += 1
            self.log_message(
                match_type,
                f"{media_name} -> {match[1].name} ({match[2]})",
            )
            return match[1]

        # Multiple matches - apply tie-breaking logic
        selected = self._resolve_ambiguous_match(media_name, all_matches)

        # Log the selected match and the ambiguity
        match_type = "EXACT_MATCH" if selected[2] == "exact" else "FUZZY_MATCH"
        if selected[2] != "exact":
            with self.stats_lock:
                self.stats["fuzzy_matches"] += 1

        self.log_message(
            "AMBIGUOUS_MATCH",
            f"Multiple matches for {media_name}",
            f"Candidates: {[m[0] for m in all_matches]}, Selected: {selected[0]} ({selected[2]})",
        )
        self.log_message(
            match_type,
            f"{media_name} -> {selected[1].name} ({selected[2]})",
        )

        return selected[1]

    def _resolve_ambiguous_match(
        self, media_name: str, matches: List[Tuple[str, Path, str]]
    ) -> Tuple[str, Path, str]:
        """
        Tie-breaking logic when multiple matches exist

        Args:
            media_name: The media filename being matched
            matches: List of (metadata_name, metadata_path, matcher_name) tuples

        Returns:
            Selected (metadata_name, metadata_path, matcher_name) tuple
        """
        # Priority 1: Exact match always wins
        exact_matches = [m for m in matches if m[2] == "exact"]
        if exact_matches:
            return exact_matches[0]

        # Priority 2: Same duplicate index
        _, media_idx = self.get_file_index(media_name)
        if media_idx:
            # Prefer metadata with matching duplicate index
            same_idx_matches = [m for m in matches if f"({media_idx})" in m[0]]
            if same_idx_matches:
                return same_idx_matches[0]

        # Default: return first match (existing behavior)
        return matches[0]

    def parse_media_metadata(self, json_path: Path) -> Optional[Dict]:
        """
        Parse media metadata JSON file
        Returns dict with cleaned metadata or None
        """
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            metadata = {
                "title": data.get("title", ""),
                "description": data.get("description", ""),
            }

            # Parse timestamp
            photo_taken = data.get("photoTakenTime", {})
            if photo_taken and photo_taken.get("timestamp"):
                metadata["capture_timestamp"] = self.parse_timestamp(
                    photo_taken["timestamp"]
                )
            else:
                creation_time = data.get("creationTime", {})
                if creation_time and creation_time.get("timestamp"):
                    metadata["capture_timestamp"] = self.parse_timestamp(
                        creation_time["timestamp"]
                    )

            # GPS data
            geo_data = data.get("geoData") or data.get("geoDataExif")
            if geo_data:
                metadata["gps"] = {
                    "latitude": geo_data.get("latitude", 0.0),
                    "longitude": geo_data.get("longitude", 0.0),
                    "altitude": geo_data.get("altitude", 0.0),
                }
            else:
                metadata["gps"] = {"latitude": 0.0, "longitude": 0.0, "altitude": 0.0}

            # Boolean flags
            metadata["archived"] = data.get("archived", False)
            metadata["favorited"] = data.get("favorited", False)
            metadata["trashed"] = data.get("trashed", False)

            # People tags
            people = data.get("people", [])
            metadata["people"] = [p.get("name", "") for p in people if p.get("name")]

            # Origin information
            origin = data.get("googlePhotosOrigin", {})
            metadata["from_partner"] = bool(origin.get("fromPartnerSharing"))
            metadata["upload_source"] = {}
            if "mobileUpload" in origin:
                metadata["upload_source"]["mobile_upload"] = origin["mobileUpload"]

            return metadata

        except Exception as e:
            self.log_message(
                "JSON_PARSE_ERROR",
                f"Failed to parse {json_path.name}",
                str(e),
            )
            return None

    def parse_album_metadata(self, json_path: Path) -> Optional[Dict]:
        """
        Parse album-level metadata.json
        Returns dict with album info or None
        """
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            album_info = {
                "title": data.get("title", ""),
                "description": data.get("description", ""),
            }

            # Album enrichments (location, narrative)
            enrichments = data.get("enrichments", [])
            if enrichments:
                for enrichment in enrichments:
                    if "narrativeEnrichment" in enrichment:
                        text = enrichment["narrativeEnrichment"].get("text", "")
                        if text:
                            album_info["description"] = (
                                album_info.get("description", "") + "\n" + text
                            )
                    if "locationEnrichment" in enrichment:
                        locations = enrichment["locationEnrichment"].get("location", [])
                        if locations:
                            loc = locations[0]
                            album_info["location"] = {
                                "latitude": loc.get("latitudeE7", 0) / 10e6,
                                "longitude": loc.get("longitudeE7", 0) / 10e6,
                                "name": loc.get("name", ""),
                            }

            return album_info

        except Exception as e:
            self.log_message(
                "ALBUM_JSON_PARSE_ERROR",
                f"Failed to parse album metadata {json_path.name}",
                str(e),
            )
            return None

    def _compute_file_hash(self, file_path: Path) -> str:
        """
        Compute xxHash64 of file for deduplication (fast, non-cryptographic)
        
        Args:
            file_path: Path to file to hash
            
        Returns:
            str: Hexadecimal hash digest
        """
        hasher = xxhash.xxh64()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):  # 64KB chunks
                hasher.update(chunk)
        return hasher.hexdigest()

    def copy_media_file(self, source_path: Path, filename: str, album_name: str) -> Optional[str]:
        """
        Copy media file to output directory with hash-based deduplication
        Returns destination filename or None
        
        Args:
            source_path: Path to source media file
            filename: Original filename
            album_name: Name of album this file belongs to
            
        Returns:
            Destination filename if successful, None otherwise
        """
        # Define image and video extension categories
        image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
        video_extensions = {".mov", ".mp4", ".avi", ".webm", ".mkv"}

        # Detect and correct extension if needed using shared utility
        correct_ext = detect_and_correct_extension(
            source_path,
            filename,
            log_callback=lambda msg, details: self.log_message("EXTENSION_CORRECTED", msg, details),
        )
        original_ext = Path(filename).suffix.lower()

        if correct_ext != original_ext:
            # Check if conversion is valid (same category)
            original_is_image = original_ext in image_extensions
            original_is_video = original_ext in video_extensions
            correct_is_image = correct_ext in image_extensions
            correct_is_video = correct_ext in video_extensions

            # Only apply correction if both are same type (image->image or video->video)
            if (original_is_image and correct_is_image) or (
                original_is_video and correct_is_video
            ):
                base_name = Path(filename).stem
                corrected_filename = base_name + correct_ext
                self.log_message(
                    "EXTENSION_CORRECTED",
                    f"Fixed extension: {filename} -> {corrected_filename}",
                    f"Actual format: {correct_ext}",
                )
                with self.stats_lock:
                    self.stats["extensions_corrected"] += 1
                filename = corrected_filename
            elif (original_is_image and correct_is_video) or (
                original_is_video and correct_is_image
            ):
                # Skip cross-category conversions
                self.log_message(
                    "EXTENSION_CROSS_CATEGORY_SKIPPED",
                    f"Skipped cross-category conversion: {filename}",
                    f"Original: {original_ext}, Detected: {correct_ext}",
                )

        # Compute file hash for content-based deduplication
        try:
            file_hash = self._compute_file_hash(source_path)
        except Exception as e:
            self.log_message(
                "HASH_ERROR",
                f"Failed to compute hash for {filename}",
                str(e),
            )
            # If hash computation fails, fall back to old behavior
            file_hash = None

        dest_filename = filename
        
        # Check if this file content already exists (thread-safe)
        if file_hash:
            with self.hash_lock:
                if file_hash in self.file_hashes:
                    # Duplicate content found - reuse existing file
                    existing_entry = self.file_hashes[file_hash]
                    existing_filename = existing_entry["filename"]
                    
                    # Add this album to the list if not already present
                    if album_name not in existing_entry["albums"]:
                        existing_entry["albums"].append(album_name)
                    
                    with self.stats_lock:
                        self.stats["deduplicated_files"] += 1
                    
                    self.log_message(
                        "DEDUPLICATED",
                        f"Duplicate content: {filename} in album '{album_name}'",
                        f"Using existing file: {existing_filename}, Hash: {file_hash[:16]}...",
                    )
                    
                    # Return existing filename (file already copied)
                    return existing_filename

        # Not a duplicate - proceed with copy
        dest_path = self.media_output_dir / dest_filename

        # Handle filename collisions (thread-safe)
        with self.destination_lock:
            if dest_filename in self.destination_files:
                # Different file with same name - create unique name
                base, ext = filename.rsplit(".", 1) if "." in filename else (filename, "")
                counter = 1
                while dest_filename in self.destination_files:
                    dest_filename = (
                        f"{base}_dup{counter}.{ext}" if ext else f"{base}_dup{counter}"
                    )
                    counter += 1
                dest_path = self.media_output_dir / dest_filename

            try:
                shutil.copy2(source_path, dest_path)
                self.destination_files[dest_filename] = source_path
                with self.stats_lock:
                    self.stats["media_copied"] += 1
                
                # Record hash for future deduplication
                if file_hash:
                    with self.hash_lock:
                        self.file_hashes[file_hash] = {
                            "filename": dest_filename,
                            "albums": [album_name],
                            "metadata": {}  # Will be populated later
                        }
                
                return dest_filename

            except Exception as e:
                self.log_message(
                    "COPY_ERROR",
                    f"Failed to copy {filename}",
                    str(e),
                )
                return None

    def _process_single_album(self, album_tuple: Tuple[str, Path]) -> Optional[Dict]:
        """
        Process a single album (used by multithreaded processing)
        
        Args:
            album_tuple: Tuple of (album_name, album_path)
            
        Returns:
            Album data dict or None if album has no media
        """
        album_name, album_path = album_tuple
        
        # Build catalog for this album
        catalog = self.build_album_catalog(album_path)

        # Parse album metadata if present
        album_metadata = None
        if "album_metadata" in catalog:
            album_metadata = self.parse_album_metadata(catalog["album_metadata"])

        album_title = (
            album_metadata.get("title", album_name)
            if album_metadata
            else album_name
        )

        media_files = []
        album_unmatched = []
        matched_metadata_paths = set()

        # Process each media file
        for media_name, media_path in catalog["media"].items():
            with self.stats_lock:
                self.stats["total_media_files"] += 1

            # Find matching metadata
            metadata_path = self.find_metadata_for_media(
                media_name, catalog["metadata"]
            )

            if metadata_path:
                matched_metadata_paths.add(str(metadata_path))
                
                # Parse metadata
                media_metadata = self.parse_media_metadata(metadata_path)
                if media_metadata:
                    with self.stats_lock:
                        self.stats["matched_files"] += 1

                    # Copy media file (handles deduplication)
                    dest_filename = self.copy_media_file(media_path, media_name, album_name)
                    if dest_filename:
                        media_metadata["filename"] = dest_filename
                        media_metadata["original_filename"] = media_metadata.get(
                            "title", media_name
                        )
                        media_files.append(media_metadata)
                        
                        # Update metadata in file_hashes for consolidated output
                        # Need to find the hash entry for this file
                        try:
                            file_hash = self._compute_file_hash(media_path)
                            with self.hash_lock:
                                if file_hash in self.file_hashes:
                                    # Store/merge metadata (prefer non-empty values)
                                    stored_metadata = self.file_hashes[file_hash]["metadata"]
                                    if not stored_metadata:
                                        self.file_hashes[file_hash]["metadata"] = media_metadata.copy()
                                    else:
                                        # Merge metadata, preferring non-empty values
                                        for key, value in media_metadata.items():
                                            if key not in stored_metadata or not stored_metadata[key]:
                                                stored_metadata[key] = value
                        except Exception as e:
                            logger.debug(f"Failed to update hash metadata for {media_name}: {e}")
                else:
                    with self.stats_lock:
                        self.stats["unmatched_files"] += 1
                    album_unmatched.append(media_name)
            else:
                with self.stats_lock:
                    self.stats["unmatched_files"] += 1
                self.log_message(
                    "UNMATCHED_MEDIA",
                    f"No metadata found for {media_name}",
                    f"album: {album_name}",
                )
                album_unmatched.append(media_name)
                
                # Track orphaned media file
                self.failure_tracker.add_orphaned_media(
                    media_path=media_path,
                    reason="No matching metadata found",
                    context={
                        "album": album_name,
                        "original_location": str(media_path),
                    },
                )
        
        # Track orphaned metadata (metadata files that didn't match any media)
        for metadata_name, metadata_path in catalog["metadata"].items():
            if str(metadata_path) not in matched_metadata_paths:
                # Parse the metadata to include in the report
                try:
                    with open(metadata_path, "r", encoding="utf-8") as f:
                        metadata_content = json.load(f)
                except Exception as e:
                    logger.debug(f"Failed to parse orphaned metadata {metadata_path}: {e}")
                    metadata_content = {"filename": metadata_name}
                
                self.failure_tracker.add_orphaned_metadata(
                    metadata_entry=metadata_content,
                    reason="Media file not found in filesystem",
                    context={
                        "album": album_name,
                        "metadata_filename": metadata_name,
                        "expected_media": metadata_content.get("title", metadata_name),
                    },
                )

        # Only include albums that have media files
        if media_files or album_unmatched:
            album_data = {
                "album_name": album_name,
                "album_title": album_title,
                "media_files": media_files,
            }

            if album_metadata:
                if album_metadata.get("description"):
                    album_data["description"] = album_metadata["description"]
                if album_metadata.get("location"):
                    album_data["location"] = album_metadata["location"]

            if album_unmatched:
                album_data["unmatched_media"] = album_unmatched

            return album_data
        
        return None

    def process_albums(self) -> List[Dict]:
        """
        Main processing: scan albums, match files with metadata, copy media
        Returns list of all albums with media files (multithreaded)
        """
        all_albums = []

        print("\nScanning albums...")
        album_list = self.scan_albums()
        logger.info(f"   Found {len(album_list)} albums")

        # Create output media directory
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        print(f"\nProcessing albums and media files (using {self.workers} workers)...")

        # Process albums in parallel
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            # Submit all album processing tasks
            future_to_album = {
                executor.submit(self._process_single_album, album_tuple): album_tuple[0]
                for album_tuple in album_list
            }
            
            # Collect results as they complete
            for future in futures_progress(future_to_album, PHASE_PREPROCESS, "Parsing albums", unit="album"):
                album_name = future_to_album[future]
                try:
                    album_data = future.result()
                    if album_data:
                        all_albums.append(album_data)
                except Exception as e:
                    self.log_message(
                        "ALBUM_PROCESSING_ERROR",
                        f"Failed to process album {album_name}",
                        str(e),
                    )
                    logger.error(f"Failed to process album {album_name}: {e}")

        with self.stats_lock:
            self.stats["total_albums"] = len(all_albums)

        print(f"   Processed {self.stats['total_albums']} albums")
        logger.info(f"   Found {self.stats['total_media_files']} media files")
        print(f"   Matched {self.stats['matched_files']} files with metadata")
        print(f"   Unmatched {self.stats['unmatched_files']} files")

        return all_albums

    def save_metadata(self, albums: List[Dict]) -> None:
        """Save consolidated metadata to metadata.json (file-centric structure)"""
        try:
            # Build file-centric structure from file_hashes
            media_files = []
            
            with self.hash_lock:
                for file_hash, entry in self.file_hashes.items():
                    # Get metadata for this file
                    file_metadata = entry["metadata"].copy()
                    
                    # Add albums list and filename
                    file_metadata["albums"] = sorted(entry["albums"])
                    file_metadata["filename"] = entry["filename"]
                    
                    media_files.append(file_metadata)
            
            # Sort by filename for consistency
            media_files.sort(key=lambda x: x.get("filename", ""))
            
            output = {
                "export_info": {
                    "export_path": str(self.export_path),
                    "export_name": self.export_path.name,
                    "processed_date": datetime.now().isoformat(),
                    "total_albums": self.stats["total_albums"],
                    "total_media_files": self.stats["total_media_files"],
                    "matched_files": self.stats["matched_files"],
                    "unmatched_files": self.stats["unmatched_files"],
                    "media_copied": self.stats["media_copied"],
                    "deduplicated_files": self.stats["deduplicated_files"],
                    "unique_files": len(media_files),
                },
                "media_files": media_files,
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
        print(f"Total albums processed:           {self.stats['total_albums']:>6}")
        print(f"Total media files found:          {self.stats['total_media_files']:>6}")
        print(f"Files matched with metadata:      {self.stats['matched_files']:>6}")
        print(f"Files without metadata:           {self.stats['unmatched_files']:>6}")
        print(f"Media files copied:               {self.stats['media_copied']:>6}")
        print(f"Deduplicated files (not copied):  {self.stats['deduplicated_files']:>6}")
        print(f"Fuzzy matches applied:            {self.stats['fuzzy_matches']:>6}")
        print(
            f"Extensions corrected:             {self.stats['extensions_corrected']:>6}"
        )
        print(f"Banned items skipped:             {self.stats['skipped_banned']:>6}")
        print("=" * 70)
        
        # Calculate and display space savings
        if self.stats['deduplicated_files'] > 0:
            unique_files = len(self.file_hashes)
            print("\nDEDUPLICATION SUMMARY:")
            print(f"  Unique media files:             {unique_files:>6}")
            print(f"  Duplicate instances avoided:    {self.stats['deduplicated_files']:>6}")
            print(f"  Space savings: Deduplicated {self.stats['deduplicated_files']} files")
            print("=" * 70)

    def process(self) -> None:
        """Main processing pipeline"""
        logger.info("Starting Google Photos Preprocessing")
        print(f"Export path: {self.export_path}")
        print(f"Output path: {self.output_dir}\n")

        # Validate export structure
        if not self.validate_export():
            sys.exit(1)

        # Process all albums and media
        albums = self.process_albums()

        # Save consolidated metadata
        self.save_metadata(albums)

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
        description="Preprocess Google Photos export: organize media and create cleaned metadata",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process in-place (creates media/ and metadata.json in export directory)
  python modules/preprocess_files.py originals/google-username-2025-10-07

  # Process to a separate output directory
  python modules/preprocess_files.py originals/google-username-2025-10-07 -o processed/
  
  # Process with custom number of workers
  python modules/preprocess_files.py originals/google-username-2025-10-07 --workers 8
        """,
    )

    parser.add_argument(
        "export_directory",
        type=str,
        help="Path to Google Photos export directory (contains Google Photos/)",
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

    preprocessor = GooglePhotosPreprocessor(export_path, output_path, workers=args.workers)
    preprocessor.process()


if __name__ == "__main__":
    main()
