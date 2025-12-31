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
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

from common.detection import resolve_username, detect_from_dirname, DetectionResult
from common.files.scanner import iter_subdirs, iter_files
from common.files.types import ALL_MEDIA_EXTENSIONS
from common.output.logging import get_processor_logger
from common.output.progress import PHASE_PREPROCESS
from common.output.summary import print_preprocessing_stats
from common.preprocess.context_mergers import merge_albums
from common.processing import process_parallel_threaded
from processors.preprocess_base import PreprocessorBase, CopyTask

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class GooglePhotosPreprocessor(PreprocessorBase):
    """Preprocesses Google Photos export by organizing files and cleaning metadata"""

    # Compiled regex patterns for better performance
    SUPPLEMENTAL_WITH_DUP_PATTERN = re.compile(r"(\.[^.]+)\.supp[^.]*\((\d+)\)\.json$")
    SUPPLEMENTAL_PATTERN = re.compile(r"\.supp[^.]*\.json$")
    FILE_INDEX_PATTERN = re.compile(r"\((\d+)\)(\.[^.]+)$")
    # Pattern to match Google Photos edit suffixes (e.g., -edited, -effects, -collage)
    # These are appended by Google Photos when saving edited versions
    EDIT_SUFFIX_PATTERN = re.compile(
        r"-(?:edited|effect[s]?|collage|animation|movie)$", re.IGNORECASE
    )
    # Used to build fast candidate sets for "long common prefix" matchers without O(n) scans
    _PREFIX_INDEX_LEN_STRONG = 40
    _PREFIX_INDEX_LEN_WEAK = 30

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        super().__init__(
            processor_name="Google Photos",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        # Support both raw and Takeout layouts
        self.photos_dir = self._resolve_google_photos_dir(self.export_path)

        # Set up context merger for album accumulation
        self.set_context_merger(merge_albums)

        # Statistics specific to this preprocessor (preserves shared keys from base)
        self.stats.update(
            {
                "total_albums": 0,
                "total_media_files": 0,
                "unique_files": 0,
                "matched_files": 0,
                "unmatched_files": 0,
                "fuzzy_matches": 0,
                "deduplicated_files": 0,
                "skipped_banned": 0,
            }
        )

        # Track destination filenames to avoid collisions (used by base class)
        self.destination_files = {}  # maps destination_filename -> source_path

        # Storage for parsed metadata keyed by source path
        self._parsed_metadata: Dict[str, Dict] = {}

    @staticmethod
    def _resolve_google_photos_dir(export_path: Path) -> Path:
        """Resolve the Google Photos directory for both raw and Takeout exports."""
        raw = export_path / "Google Photos"
        if raw.exists() and raw.is_dir():
            return raw
        takeout = export_path / "Takeout" / "Google Photos"
        if takeout.exists() and takeout.is_dir():
            return takeout
        # Default (for error messages)
        return raw

    def is_banned(self, path: Path) -> bool:
        """
        Check if a file or directory should be skipped based on banned patterns

        Args:
            path: Path object to check

        Returns:
            True if the path matches any banned pattern, False otherwise
        """
        return self.banned_filter.is_banned(path)

    def validate_export(self) -> bool:
        """Validate that export directory has required structure."""
        if not self.require_exists(self.export_path, label="Export path"):
            return False

        # Re-resolve in case export_path was populated after init (tests/generators)
        self.photos_dir = self._resolve_google_photos_dir(self.export_path)

        if not self.require_exists(self.photos_dir, label="Google Photos directory"):
            return False

        # Check if at least one album folder exists
        if not self.require_any_dirs(self.photos_dir, label="album"):
            return False

        return True

    def detect_username(self) -> DetectionResult:
        """Detect export username using available sources.

        Priority:
        1. Profile/Profile.json - Google account profile
        2. Google Chat user_info.json - if Chat is in same export
        3. Directory name pattern (google-username-YYYYMMDD)

        Returns:
            DetectionResult with username and source
        """

        def detect_from_profile_json(path: Path) -> Optional[str]:
            """Extract username from Profile/Profile.json."""
            profile_path = path / "Profile" / "Profile.json"
            if not profile_path.exists():
                return None
            try:
                with open(profile_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Get first email and strip @gmail.com
                emails = data.get("emails", [])
                if emails:
                    email = emails[0].get("value", "")
                    if "@gmail.com" in email:
                        return email.replace("@gmail.com", "")
                    # For non-gmail, use the part before @
                    if "@" in email:
                        return email.split("@")[0]
                return None
            except Exception:
                return None

        def detect_from_google_chat_user_info(path: Path) -> Optional[str]:
            """Extract username from Google Chat user_info.json if present."""
            users_dir = path / "Google Chat" / "Users"
            if not users_dir.exists():
                return None
            try:
                user_dirs = [d for d in users_dir.iterdir() if d.is_dir()]
                if not user_dirs:
                    return None
                user_info_path = user_dirs[0] / "user_info.json"
                if not user_info_path.exists():
                    return None
                with open(user_info_path, "r", encoding="utf-8") as f:
                    user_data = json.load(f)
                email = user_data.get("user", {}).get("email")
                if email:
                    return email.replace("@gmail.com", "")
                return None
            except Exception:
                return None

        def detect_from_dirname_google(path: Path) -> Optional[str]:
            """Extract username from directory name."""
            return detect_from_dirname(path, "google")

        return resolve_username(
            self.export_path,
            [
                ("Profile.json", detect_from_profile_json),
                ("Google Chat user_info.json", detect_from_google_chat_user_info),
                ("dirname", detect_from_dirname_google),
            ],
        )

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
        Scan Google Photos directory for all album folders.

        Returns list of (album_name, folder_path) tuples.

        Uses shared iter_subdirs utility for consistent directory scanning
        with automatic hidden file and banned pattern filtering.
        """
        albums = []

        def has_files(album_dir: Path) -> bool:
            """Check if directory contains at least one file."""
            try:
                for item in album_dir.iterdir():
                    if item.is_file():
                        return True
            except OSError:
                pass
            return False

        for album_dir in iter_subdirs(
            self.photos_dir,
            banned_filter=self.banned_filter,
            skip_hidden=True,
            sorted_=True,
        ):
            if has_files(album_dir):
                albums.append((album_dir.name, album_dir))

        return albums

    def build_album_catalog(self, album_folder: Path) -> Dict:
        """
        Build file catalog for a specific album folder.

        Returns: Dict with 'media' (filename -> path) and 'metadata' (filename -> json_path)
        """
        catalog: Dict[str, Any] = {
            "media": {},
            "metadata": {},
        }

        # Use common media extensions
        media_extensions = ALL_MEDIA_EXTENSIONS

        try:
            # Use shared scanner for consistent filtering of hidden and banned files
            for file_path in iter_files(
                album_folder,
                banned_filter=self.banned_filter,
                extensions=None,  # Accept all files, filter below
                skip_hidden=True,
            ):
                filename = file_path.name

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
                        # Example: RenderedImage.JPG.supplemental-metadata(1).json -> RenderedImage(1).JPG
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

                            # Some Google Photos exports store media sidecars as "uuid__hash.json"
                            # (no media extension) but the JSON "title" contains the real media
                            # filename (including extension). Index by title too to improve matching.
                            #
                            # This prevents cases where media exists on disk with a long common prefix
                            # but the metadata sidecar name is truncated or lacks the extension.
                            try:
                                base_suffix = Path(base_name).suffix.lower()
                                if base_suffix not in ALL_MEDIA_EXTENSIONS:
                                    with open(file_path, "r", encoding="utf-8") as f:
                                        metadata_json = json.load(f)
                                    title = (metadata_json.get("title") or "").strip()
                                    if (
                                        title
                                        and Path(title).suffix.lower()
                                        in ALL_MEDIA_EXTENSIONS
                                    ):
                                        catalog["metadata"].setdefault(title, file_path)
                            except (
                                OSError,
                                UnicodeDecodeError,
                                json.JSONDecodeError,
                                ValueError,
                                TypeError,
                            ):
                                # Title indexing is a best-effort optimization; never fail cataloging.
                                pass
                    continue

                # Check if it's a media file
                if file_path.suffix.lower() in media_extensions:
                    catalog["media"][filename] = file_path

            # Build stem index for O(1) candidate lookup
            catalog["metadata_by_stem"] = {}
            # Build prefix index to enable fast “common prefix” candidate lookup.
            # Keys are first N characters of the metadata stem (lowercased).
            catalog["metadata_by_prefix"] = {
                self._PREFIX_INDEX_LEN_STRONG: {},
                self._PREFIX_INDEX_LEN_WEAK: {},
            }
            for metadata_name, metadata_path in catalog["metadata"].items():
                stem = Path(metadata_name).stem.lower()
                # Also index without trailing punctuation for trailing_chars matcher
                stem_clean = stem.rstrip("-_.")

                catalog["metadata_by_stem"].setdefault(stem, []).append(
                    (metadata_name, metadata_path)
                )
                if stem_clean != stem:
                    catalog["metadata_by_stem"].setdefault(stem_clean, []).append(
                        (metadata_name, metadata_path)
                    )

                # Index base name without duplicate index for duplicate matching
                # e.g., "IMG_0004(1)" -> also index under "IMG_0004"
                base, idx = self.get_file_index(metadata_name)
                if idx:
                    base_stem = Path(base).stem.lower()
                    catalog["metadata_by_stem"].setdefault(base_stem, []).append(
                        (metadata_name, metadata_path)
                    )

                # Prefix indexes (strong/weak) for truncated/suffix-variant matching
                for n in (self._PREFIX_INDEX_LEN_STRONG, self._PREFIX_INDEX_LEN_WEAK):
                    if len(stem) >= n:
                        key = stem[:n]
                        catalog["metadata_by_prefix"][n].setdefault(key, []).append(
                            (metadata_name, metadata_path)
                        )

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

        Examples:
        - JSON: PXL_20220405_090123740.PORTRAIT.jpg.json
        - metadata_name: PXL_20220405_090123740.PORTRAIT.jpg
        - media_name: PXL_20220405_090123740.PORTRAIT-modifie.jpg

        Also handles edited duplicates:
        - media: IMG_3661-edited(1).JPG -> metadata: IMG_3661(1).JPG
        - media: 26 (3)-edited.jpg -> metadata: 26 (3).jpg

        This matcher checks if the metadata name contains a media extension,
        strips it, and checks if the media file name (without extension) starts
        with the metadata base (without media extension).
        """
        # Define media extensions
        media_extensions = ALL_MEDIA_EXTENSIONS

        # Get the extension from metadata_name (which has .json already stripped)
        metadata_ext = Path(metadata_name).suffix.lower()

        # Check if metadata_name has a media extension as its last extension
        if metadata_ext and metadata_ext not in media_extensions:
            return False

        # Strip the media extension from both
        metadata_base = Path(metadata_name).stem
        media_base = Path(media_name).stem

        # Handle files with duplicate indices (e.g., IMG_3661-edited(1).JPG)
        media_base_no_idx, media_idx = self.get_file_index(media_name)
        metadata_base_no_idx, metadata_idx = self.get_file_index(metadata_name)

        # Special case: media has edit suffix AFTER an internal duplicate index
        # e.g., "26 (3)-edited.jpg" - get_file_index returns no idx because -edited is at end
        # but "26 (3).jpg" - get_file_index returns idx='3'
        # Check if stripping the edit suffix reveals a matching duplicate index
        if not media_idx and metadata_idx:
            edit_match = self.EDIT_SUFFIX_PATTERN.search(media_base)
            if edit_match:
                media_base_stripped = media_base[: edit_match.start()]
                # Try to extract duplicate index from stripped media base
                internal_match = self.FILE_INDEX_PATTERN.search(
                    media_base_stripped + Path(media_name).suffix
                )
                if internal_match and internal_match.group(1) == metadata_idx:
                    # Both have same duplicate index - compare base names
                    media_base = media_base_stripped[: -len(f"({metadata_idx})")]
                    media_base = media_base.rstrip(" ")  # Handle space before (N)
                    metadata_base = Path(metadata_base_no_idx).stem.rstrip(" ")
                    # Since we're comparing base names and they're both stripped,
                    # they should be equal (not startswith) for this case
                    return media_base == metadata_base

        if media_idx or metadata_idx:
            # If both have indices at the end, they must match
            if media_idx and metadata_idx and media_idx != metadata_idx:
                return False
            # If only metadata has an index (and we didn't handle internal idx above),
            # they can't match via this matcher
            if not media_idx and metadata_idx:
                return False
            # Both have the same index - compare base names without index
            media_base = Path(media_base_no_idx).stem
            metadata_base = Path(metadata_base_no_idx).stem

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
        self, media_name: str, catalog: Dict[str, Any]
    ) -> Optional[Path]:
        """
        Find matching metadata for a media file using various matching strategies
        Returns metadata file path or None

        Uses stem-based index for O(1) candidate lookup instead of iterating all metadata.

        When multiple matches exist, applies tie-breaking logic to select the best match:
        1. Prioritize exact matches
        2. Prefer matches with same duplicate index
        3. Fall back to first match found
        """
        metadata_catalog = catalog["metadata"]
        stem_index = catalog.get("metadata_by_stem", {})
        prefix_index = catalog.get("metadata_by_prefix", {})

        # Fast path: exact match (O(1) dictionary lookup)
        if media_name in metadata_catalog:
            self.log_message(
                "EXACT_MATCH",
                f"{media_name} -> {metadata_catalog[media_name].name} (exact)",
            )
            return metadata_catalog[media_name]

        # Build candidate set from stem index (O(1) average per lookup)
        media_stem = Path(media_name).stem.lower()
        candidates: Dict[str, Path] = {}

        # Add candidates matching the media stem
        for metadata_name, metadata_path in stem_index.get(media_stem, []):
            candidates[metadata_name] = metadata_path

        # Also check base stem without duplicate index
        base, idx = self.get_file_index(media_name)
        if idx:
            base_stem = Path(base).stem.lower()
            for metadata_name, metadata_path in stem_index.get(base_stem, []):
                candidates[metadata_name] = metadata_path

        # Also check cleaned stem (without trailing punctuation)
        stem_clean = media_stem.rstrip("-_.")
        if stem_clean != media_stem:
            for metadata_name, metadata_path in stem_index.get(stem_clean, []):
                candidates[metadata_name] = metadata_path

        # Check for Google Photos edit suffixes (e.g., IMG_6205-edited -> IMG_6205)
        # This handles cases where edited photos have no dedicated metadata
        # and should inherit metadata from the original file
        edit_match = self.EDIT_SUFFIX_PATTERN.search(media_stem)
        if edit_match:
            original_stem = media_stem[: edit_match.start()]
            for metadata_name, metadata_path in stem_index.get(original_stem, []):
                candidates[metadata_name] = metadata_path

        # Also handle edited files with duplicate indices: IMG_3661-edited(1).JPG
        # Strip the duplicate index first, check for edit suffix, then search with both
        # the base stem and the base stem + duplicate index
        if idx:
            # base is already the name without the duplicate index (e.g., IMG_3661-edited.JPG)
            base_stem_only = Path(base).stem.lower()
            edit_match_base = self.EDIT_SUFFIX_PATTERN.search(base_stem_only)
            if edit_match_base:
                # Found edit suffix in the base name (without duplicate index)
                original_base = base_stem_only[: edit_match_base.start()]
                # Look for original stem (IMG_3661)
                for metadata_name, metadata_path in stem_index.get(original_base, []):
                    candidates[metadata_name] = metadata_path

        # Handle files with INTERNAL duplicate indices followed by edit suffix:
        # e.g., "26 (3)-edited.jpg" -> should match "26 (3).jpg"
        # These have the duplicate number BEFORE the edit suffix, not at the end
        # The edit suffix is at the end of the stem (no idx extracted)
        if not idx and edit_match:
            # Already stripped the -edited suffix above; now check if what remains
            # has a duplicate index pattern: "26 (3)" -> base="26", idx="3"
            original_stem_with_idx = media_stem[: edit_match.start()]
            # Try to extract duplicate index from the remaining stem
            # Pattern: match "name (N)" at end of stem
            import re

            internal_idx_match = re.search(
                r"^(.*?)\s*\((\d+)\)$", original_stem_with_idx
            )
            if internal_idx_match:
                # Found internal duplicate index: "26 (3)" -> stem_with_idx = "26 (3)"
                # Look for metadata with the same stem including the index
                for metadata_name, metadata_path in stem_index.get(
                    original_stem_with_idx, []
                ):
                    candidates[metadata_name] = metadata_path

        # If no candidates from stem index, fall back to prefix-index candidate generation.
        # This enables match_truncated and match_live_photo_variants for cases where the
        # metadata stem and media stem share a long prefix but differ in trailing chars.
        if not candidates and prefix_index:
            for n in (self._PREFIX_INDEX_LEN_STRONG, self._PREFIX_INDEX_LEN_WEAK):
                if len(media_stem) >= n:
                    key = media_stem[:n]
                    for metadata_name, metadata_path in prefix_index.get(n, {}).get(
                        key, []
                    ):
                        candidates[metadata_name] = metadata_path
                if candidates:
                    break

        # Still no candidates means no match possible without expensive scanning.
        if not candidates:
            return None

        # Try matchers in priority order (based on immich-go)
        # Only run matchers on the small candidate set, not all metadata
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
            for metadata_name, metadata_path in candidates.items():
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

    def _collect_album_tasks(
        self, album_tuple: Tuple[str, Path]
    ) -> Tuple[List[CopyTask], Dict, List[str], Optional[Dict]]:
        """
        Collect copy tasks for a single album (used by multithreaded processing).

        Args:
            album_tuple: Tuple of (album_name, album_path)

        Returns:
            Tuple of (copy_tasks, path_to_metadata, unmatched_files, album_metadata)
        """
        album_name, album_path = album_tuple

        # Build catalog for this album
        catalog = self.build_album_catalog(album_path)

        # Parse album metadata if present
        album_metadata = None
        if "album_metadata" in catalog:
            album_metadata = self.parse_album_metadata(catalog["album_metadata"])

        copy_tasks: List[CopyTask] = []
        path_to_metadata: Dict[str, Dict] = {}
        album_unmatched: List[str] = []
        matched_metadata_paths: set = set()

        # Process each media file
        for media_name, media_path in catalog["media"].items():
            with self.stats_lock:
                self.stats["total_media_files"] += 1

            # Find matching metadata (uses stem index for O(1) candidate lookup)
            metadata_path = self.find_metadata_for_media(media_name, catalog)

            if metadata_path:
                matched_metadata_paths.add(str(metadata_path))

                # Parse metadata
                media_metadata = self.parse_media_metadata(metadata_path)
                if media_metadata:
                    with self.stats_lock:
                        self.stats["matched_files"] += 1

                    # Create copy task with album context
                    context = {
                        "albums": [album_name],
                        "metadata": media_metadata,
                    }
                    copy_tasks.append(
                        CopyTask(
                            source_path=media_path,
                            dest_filename=media_name,
                            context=context,
                        )
                    )
                    path_to_metadata[str(media_path)] = media_metadata
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
                    logger.debug(
                        f"Failed to parse orphaned metadata {metadata_path}: {e}"
                    )
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

        return copy_tasks, path_to_metadata, album_unmatched, album_metadata

    def process_albums(self) -> List[Dict]:
        """
        Main processing: scan albums, match files with metadata, copy media
        Returns list of all albums with media files (multithreaded)
        """
        logger.info("Scanning albums...")
        album_list = self.scan_albums()
        logger.info("   Found %d albums", len(album_list))

        logger.info(
            "Processing albums and media files (using %d workers)...", self.workers
        )

        # Phase 1: Collect all copy tasks from all albums in parallel
        all_copy_tasks: List[CopyTask] = []
        album_data_list: List[Dict] = []

        # Process albums in parallel - each returns (album_tuple, result_data)
        def process_album_with_context(album_tuple: Tuple) -> Tuple[Tuple, Tuple]:
            """Wrapper to include album_tuple in result for post-processing."""
            return (album_tuple, self._collect_album_tasks(album_tuple))

        results = process_parallel_threaded(
            tasks=album_list,
            worker_fn=process_album_with_context,
            workers=self.workers,
            phase=PHASE_PREPROCESS,
            description="Parsing albums",
            task_id_fn=lambda t: t[0],  # album_name
            on_error=lambda tid, e: (
                self.log_message(
                    "ALBUM_PROCESSING_ERROR",
                    f"Failed to process album {tid}",
                    str(e),
                ),
                logger.error("Failed to process album %s: %s", tid, e),
            ),
        )

        # Post-process results
        for album_tuple, result_data in results:
            album_name = album_tuple[0]
            (
                copy_tasks,
                path_to_metadata,
                album_unmatched,
                album_metadata,
            ) = result_data

            # Store metadata for later use
            self._parsed_metadata.update(path_to_metadata)

            # Add to all copy tasks
            all_copy_tasks.extend(copy_tasks)

            # Store album info for later
            album_title = (
                album_metadata.get("title", album_name)
                if album_metadata
                else album_name
            )
            album_data = {
                "album_name": album_name,
                "album_title": album_title,
                "unmatched_media": album_unmatched,
                "album_metadata": album_metadata,
            }
            album_data_list.append(album_data)

        # Phase 2: Hash all files with deduplication (no file copying)
        # Files will be copied directly from source to final output during processing
        if all_copy_tasks:
            logger.info("Hashing %d files with deduplication...", len(all_copy_tasks))
            results = self.hash_with_dedup(all_copy_tasks)

            # Update stats
            duplicates = sum(1 for r in results.values() if r.is_duplicate)
            with self.stats_lock:
                self.stats["unique_files"] = len(all_copy_tasks) - duplicates
                self.stats["deduplicated_files"] = duplicates

        with self.stats_lock:
            self.stats["total_albums"] = len(
                [a for a in album_data_list if a.get("unmatched_media") is not None]
            )

        logger.info("   Processed %d albums", self.stats["total_albums"])
        logger.info("   Found %d media files", self.stats["total_media_files"])
        logger.info("   Matched %d files with metadata", self.stats["matched_files"])
        logger.info("   Unmatched %d files", self.stats["unmatched_files"])

        return album_data_list

    def get_content_key(self) -> str:
        """Return the content key for Google Photos metadata."""
        return "media_files"

    @staticmethod
    def get_platform() -> str:
        """Return the platform identifier for Google Photos."""
        return "Google"

    @staticmethod
    def get_service() -> str:
        """Return the service identifier for Google Photos."""
        return "photos"

    def _build_media_files_from_contexts(self) -> List[Dict]:
        """Build file-centric media_files list from merged contexts.

        Returns:
            List of media file dicts sorted by filename.
        """
        media_files = []

        merged_contexts = self.get_merged_contexts()
        for content_hash, context in merged_contexts.items():
            # Get the stored registry entry for filename and source_path
            registry_entry = self._hash_registry.get(content_hash, {})
            filename = registry_entry.get("filename", "")
            source_path = registry_entry.get("source_path", "")

            # Build file metadata
            file_metadata = context.get("metadata", {}).copy()
            file_metadata["albums"] = sorted(context.get("albums", []))
            file_metadata["filename"] = filename
            file_metadata["source_path"] = source_path
            file_metadata["content_hash"] = content_hash

            media_files.append(file_metadata)

        # Sort by filename for consistency
        media_files.sort(key=lambda x: x.get("filename", ""))
        return media_files

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Albums processed": self.stats["total_albums"],
            "Media files found": self.stats["total_media_files"],
            "Files matched": self.stats["matched_files"],
            "Unmatched files": self.stats["unmatched_files"],
            "Unique files": self.stats["unique_files"],
            "Deduplicated": self.stats["deduplicated_files"],
            "Fuzzy matches": self.stats["fuzzy_matches"],
            "Banned skipped": self.stats["skipped_banned"],
        }
        print_preprocessing_stats("Preprocessing Stats", stats)

    def process_export(self) -> bool:
        """Platform-specific preprocessing logic.

        Processes all albums and media files, performing metadata matching
        and deduplication.

        Returns:
            True if processing succeeded, False otherwise
        """
        # Process all albums and media
        self.process_albums()

        # Build file-centric media_files from merged contexts
        media_files = self._build_media_files_from_contexts()

        # Save consolidated metadata
        self.save_metadata(
            media_files,
            extra_export_info={
                "total_albums": self.stats["total_albums"],
                "total_media_files": self.stats["total_media_files"],
                "matched_files": self.stats["matched_files"],
                "unmatched_files": self.stats["unmatched_files"],
                "unique_files": self.stats["unique_files"],
                "deduplicated_files": self.stats["deduplicated_files"],
            },
        )

        return True
