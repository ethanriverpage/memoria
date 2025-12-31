#!/usr/bin/env python3
"""
Snapchat Memories Preprocessor

Preprocesses Snapchat Memories exports with hash-only deduplication:
- Hashes media files and builds source_path references (no file copying)
- Stores overlay associations from metadata (explicit, unlike Messages)
- Creates metadata.json with standardized structure and source_path entries
- Tracks orphaned files (media without metadata, metadata without media)
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.detection import DetectionResult
from common.files.scanner import iter_files
from common.files.types import get_media_type
from common.output.logging import get_processor_logger
from common.output.summary import print_preprocessing_stats
from processors.preprocess_base import PreprocessorBase, CopyTask

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


def _check_raw_memories_structure(base_path: Path) -> bool:
    """Check if a path contains valid raw Snapchat Memories structure.

    Raw exports have:
    - media/ directory
    - overlays/ directory
    - metadata.json (list format)
    """
    media_dir = base_path / "media"
    overlays_dir = base_path / "overlays"
    metadata_file = base_path / "metadata.json"

    if not media_dir.exists() or not media_dir.is_dir():
        return False

    if not overlays_dir.exists() or not overlays_dir.is_dir():
        return False

    if not metadata_file.exists() or not metadata_file.is_file():
        return False

    # Check metadata.json is list format (raw) not dict format (preprocessed)
    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        # Raw format is a list; preprocessed format is a dict with media_files key
        if not isinstance(metadata, list):
            return False

        if len(metadata) == 0:
            return False

        # Check first memory has expected raw fields
        first_memory = metadata[0]
        required_fields = ["date", "media_type", "media_filename"]

        for field in required_fields:
            if field not in first_memory:
                return False

        return True

    except (json.JSONDecodeError, KeyError, IndexError):
        return False


class SnapchatMemoriesPreprocessor(PreprocessorBase):
    """Preprocesses Snapchat Memories export by hashing files and building metadata."""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path] = None,
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
        username_override: Optional[str] = None,
    ):
        super().__init__(
            processor_name="Snapchat Memories",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
            final_output_dir=final_output_dir,
        )

        # Resolve actual paths (may be in memories/ subdirectory)
        self.memories_base = self._resolve_memories_base()
        self.media_dir = self.memories_base / "media"
        self.overlays_dir = self.memories_base / "overlays"
        self.raw_metadata_file = self.memories_base / "metadata.json"

        # Store username override for consolidated exports
        self.username_override = username_override

        # No context merger needed - each memory is independent
        # (unlike Messages where same file can appear in multiple conversations)

        # Statistics specific to this preprocessor
        self.stats.update(
            {
                "total_memories": 0,
                "media_files_found": 0,
                "overlay_files_found": 0,
                "memories_with_overlays": 0,
                "orphaned_media": 0,
                "orphaned_metadata": 0,
            }
        )

    def _resolve_memories_base(self) -> Path:
        """Resolve the base directory for memories content.

        Supports both:
        - Direct structure: export_path contains media/, overlays/, metadata.json
        - Consolidated structure: export_path/memories/ contains the above
        """
        # Check for consolidated structure first
        memories_subdir = self.export_path / "memories"
        if memories_subdir.exists() and _check_raw_memories_structure(memories_subdir):
            logger.info(
                "Detected consolidated export structure, using: %s", memories_subdir
            )
            return memories_subdir

        # Use direct structure
        return self.export_path

    def validate_export(self) -> bool:
        """Validate that export directory has required structure."""
        if not self.require_exists(self.memories_base, label="Memories base"):
            return False

        if not self.require_exists(self.media_dir, label="media directory"):
            return False

        if not self.require_exists(self.overlays_dir, label="overlays directory"):
            return False

        if not self.require_exists(self.raw_metadata_file, label="metadata.json"):
            return False

        return True

    def detect_username(self) -> DetectionResult:
        """Detect export username from directory name or override.

        Returns:
            DetectionResult with username and source
        """
        # Use override if provided (for consolidated exports)
        if self.username_override:
            return DetectionResult(username=self.username_override, source="override")

        # From directory name using regex
        # Pattern: snapchat-{username}-YYYY-MM-DD or snapchat-{username}-YYYYMMDD
        import re

        dir_name = self.export_path.name
        match = re.match(r"snapchat-(.+?)-\d{4}-?\d{2}-?\d{2}", dir_name)
        if match:
            return DetectionResult(username=match.group(1), source="directory_name")

        return DetectionResult(username="unknown", source="none")

    def get_content_key(self) -> str:
        """Return the content key for Snapchat Memories metadata."""
        return "media_files"

    @staticmethod
    def get_platform() -> str:
        """Return the platform identifier for Snapchat Memories."""
        return "Snapchat"

    @staticmethod
    def get_service() -> str:
        """Return the service identifier for Snapchat Memories."""
        return "memories"

    def load_raw_metadata(self) -> Optional[List[Dict[str, Any]]]:
        """Load and parse Snapchat's raw metadata.json (list format).

        Returns:
            List of memory dictionaries, or None if loading fails.
        """
        try:
            with open(self.raw_metadata_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            if not isinstance(metadata, list):
                logger.error("metadata.json is not a list (expected raw format)")
                return None

            logger.info("Loaded %d memories from metadata.json", len(metadata))
            return metadata

        except json.JSONDecodeError as e:
            logger.error("Failed to parse metadata.json: %s", e)
            return None
        except OSError as e:
            logger.error("Error loading metadata.json: %s", e)
            return None

    def build_file_catalog(self) -> Dict[str, Path]:
        """Scan media directory and build catalog of files.

        Returns:
            Dict mapping filename -> Path
        """
        catalog: Dict[str, Path] = {}

        for file_path in iter_files(
            self.media_dir,
            banned_filter=self.banned_filter,
            extensions=None,  # Accept all media files
            skip_hidden=True,
        ):
            # Only include actual media files
            if get_media_type(file_path):
                catalog[file_path.name] = file_path
                self.stats["media_files_found"] += 1
            else:
                self.bump_stat("files_banned_skipped")

        return catalog

    def build_overlay_catalog(self) -> Dict[str, Path]:
        """Scan overlays directory and build catalog.

        Returns:
            Dict mapping filename -> Path
        """
        catalog: Dict[str, Path] = {}

        for file_path in iter_files(
            self.overlays_dir,
            banned_filter=self.banned_filter,
            extensions=None,
            skip_hidden=True,
        ):
            catalog[file_path.name] = file_path
            self.stats["overlay_files_found"] += 1

        return catalog

    def collect_copy_tasks(
        self,
        raw_metadata: List[Dict[str, Any]],
        file_catalog: Dict[str, Path],
        overlay_catalog: Dict[str, Path],
    ) -> List[CopyTask]:
        """Build CopyTask entries from raw metadata.

        Args:
            raw_metadata: List of memory objects from Snapchat's metadata.json
            file_catalog: Dict mapping media filename -> Path
            overlay_catalog: Dict mapping overlay filename -> Path

        Returns:
            List of CopyTask entries for hashing
        """
        copy_tasks: List[CopyTask] = []
        matched_media: set = set()

        for memory in raw_metadata:
            self.stats["total_memories"] += 1

            media_filename = memory.get("media_filename")
            if not media_filename:
                continue

            # Check if media file exists
            if media_filename not in file_catalog:
                # Orphaned metadata - media file not found
                self.stats["orphaned_metadata"] += 1
                self.failure_tracker.add_orphaned_metadata(
                    metadata_entry=memory,
                    reason="Media file not found in media/ directory",
                    context={
                        "expected_file": media_filename,
                        "date": memory.get("date"),
                        "media_type": memory.get("media_type"),
                    },
                )
                continue

            source_path = file_catalog[media_filename]
            matched_media.add(media_filename)

            # Build context with memory data
            context: Dict[str, Any] = {
                "date": memory.get("date"),
                "media_type": memory.get("media_type"),
            }

            # Add optional fields if present
            if "latitude" in memory:
                context["latitude"] = memory["latitude"]
            if "longitude" in memory:
                context["longitude"] = memory["longitude"]

            # Handle overlay (explicit association in Snapchat Memories)
            overlay_filename = memory.get("overlay_filename")
            if overlay_filename:
                if overlay_filename in overlay_catalog:
                    overlay_path = overlay_catalog[overlay_filename]
                    context["overlay_filename"] = overlay_filename
                    context["overlay_source_path"] = str(overlay_path)
                    self.stats["memories_with_overlays"] += 1
                else:
                    # Overlay referenced but not found
                    self.log_message(
                        "MISSING_OVERLAY",
                        f"Overlay not found for {media_filename}",
                        f"expected={overlay_filename}",
                    )

            copy_tasks.append(
                CopyTask(
                    source_path=source_path,
                    dest_filename=media_filename,
                    context=context,
                )
            )

        # Track orphaned media files (in media/ but not in metadata)
        for filename, path in file_catalog.items():
            if filename not in matched_media:
                self.stats["orphaned_media"] += 1
                self.failure_tracker.add_orphaned_media(
                    media_path=path,
                    reason="No matching entry in metadata.json",
                    context={"original_location": str(path)},
                )

        return copy_tasks

    def build_metadata_from_results(self) -> List[Dict[str, Any]]:
        """Build media_files list from hash_with_dedup results.

        Returns:
            List of media file entries for metadata.json
        """
        media_files: List[Dict[str, Any]] = []

        for _content_hash, registry_entry in self._hash_registry.items():
            filename = registry_entry.get("filename", "")
            source_path = registry_entry.get("source_path", "")
            context = registry_entry.get("context", {})

            entry: Dict[str, Any] = {
                "filename": filename,
                "source_path": source_path,
                "date": context.get("date"),
                "media_type": context.get("media_type"),
            }

            # Add optional fields
            if context.get("latitude") is not None:
                entry["latitude"] = context["latitude"]
            if context.get("longitude") is not None:
                entry["longitude"] = context["longitude"]
            if context.get("overlay_filename"):
                entry["overlay_filename"] = context["overlay_filename"]
            if context.get("overlay_source_path"):
                entry["overlay_source_path"] = context["overlay_source_path"]

            media_files.append(entry)

        return media_files

    def print_statistics(self) -> None:
        """Print processing statistics as a compact table."""
        stats = {
            "Memories in metadata": self.stats["total_memories"],
            "Media files found": self.stats["media_files_found"],
            "Overlay files found": self.stats["overlay_files_found"],
            "Memories with overlays": self.stats["memories_with_overlays"],
            "Unique files": self.stats["files_unique"],
            "Duplicate files": self.stats["files_duplicates"],
            "Orphaned media": self.stats["orphaned_media"],
            "Orphaned metadata": self.stats["orphaned_metadata"],
            "Banned skipped": self.stats["files_banned_skipped"],
        }
        print_preprocessing_stats("Preprocessing Stats", stats)

    def process_export(self) -> bool:
        """Platform-specific preprocessing logic.

        Loads raw Snapchat Memories metadata, scans files, performs
        deduplication, and builds standardized metadata.json.

        Returns:
            True if processing succeeded, False otherwise
        """
        # Load raw metadata (list format)
        raw_metadata = self.load_raw_metadata()
        if raw_metadata is None:
            return False

        # Build file catalogs
        logger.info("Scanning media directory...")
        file_catalog = self.build_file_catalog()

        logger.info("Scanning overlays directory...")
        overlay_catalog = self.build_overlay_catalog()

        # Collect copy tasks
        logger.info("Matching memories to files...")
        copy_tasks = self.collect_copy_tasks(
            raw_metadata, file_catalog, overlay_catalog
        )

        # Update total files stat
        self.stats["files_total"] = len(copy_tasks)

        # Hash media files with deduplication (no file copying)
        if copy_tasks:
            logger.info("Hashing %d media files with deduplication...", len(copy_tasks))
            results = self.hash_with_dedup(copy_tasks)

            # Summarize results
            unique, duplicates = self.summarize_dedup_results(results)
            logger.info("   %d unique files, %d duplicates", unique, duplicates)

        # Build metadata from results
        logger.info("Building metadata...")
        media_files = self.build_metadata_from_results()

        # Save standardized metadata
        self.save_metadata(
            media_files,
            extra_export_info={
                "total_memories": self.stats["total_memories"],
                "memories_with_overlays": self.stats["memories_with_overlays"],
            },
        )

        return True
