#!/usr/bin/env python3
"""Base class for all media processors."""

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from common.output.paths import OutputInfo

logger = logging.getLogger(__name__)


class ProcessorBase(ABC):
    """Base class for all media processors."""

    @staticmethod
    @abstractmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory."""
        pass

    @staticmethod
    @abstractmethod
    def get_name() -> str:
        """Return human-readable processor name."""
        pass

    @staticmethod
    @abstractmethod
    def get_priority() -> int:
        """Return execution priority (higher = run first, range 1-100)."""
        pass

    @staticmethod
    @abstractmethod
    def get_output_info() -> OutputInfo:
        """Return output configuration for Platform/User/Service structure.

        Returns:
            OutputInfo with platform, service, and username_prefix.

        Example:
            return OutputInfo(
                platform="Google",
                service="photos",
                username_prefix="google",
            )
        """
        pass

    @staticmethod
    @abstractmethod
    def process(
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> bool:
        """Process the input directory."""
        pass

    @staticmethod
    def supports_consolidation() -> bool:
        """Return True if processor supports multi-export consolidation."""
        return False

    @staticmethod
    def process_consolidated(
        input_dirs: List[str], output_dir: Optional[str] = None, **kwargs
    ) -> bool:
        """Process multiple exports as a single consolidated unit."""
        raise NotImplementedError(
            "Processor declares supports_consolidation=True "
            "but does not implement process_consolidated()"
        )

    @staticmethod
    def supports_dedup() -> bool:
        """Return True if processor supports cross-processor deduplication."""
        return False

    @staticmethod
    def build_catalog(input_dir: str, **kwargs) -> Iterator[Dict[str, Any]]:
        """Build catalog by yielding entries one at a time (memory-efficient).

        Yields catalog entries as they are computed, allowing the caller to
        write them to a database incrementally without holding all entries
        in memory.

        Args:
            input_dir: Path to input directory.
            **kwargs: Additional arguments (verbose, workers).

        Yields:
            Catalog entry dictionaries with hashes and metadata.
        """
        raise NotImplementedError(
            "Processor declares supports_dedup=True "
            "but does not implement build_catalog()"
        )

    # =========================================================================
    # Shared Utility Methods
    # =========================================================================

    @classmethod
    def get_processor(cls):
        """Return processor class for auto-discovery.

        This class method is used by memoria.py during automatic processor
        discovery. Subclasses inherit this automatically.

        Returns:
            The processor class itself.
        """
        return cls

    @staticmethod
    def load_metadata(metadata_path: Path) -> Dict[str, Any]:
        """Load and parse metadata.json.

        Args:
            metadata_path: Path to metadata.json file.

        Returns:
            Parsed metadata dictionary.
        """
        with open(metadata_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def validate_working_directory(
        working_dir: Path,
    ) -> Tuple[Optional[Path], Optional[Path]]:
        """Validate working directory has required structure.

        Checks for existence of metadata.json and media/ directory.

        Args:
            working_dir: Path to working directory to validate.

        Returns:
            Tuple of (metadata_path, media_dir) if valid,
            or (None, None) if invalid.
        """
        working_dir = Path(working_dir)
        metadata_path = working_dir / "metadata.json"
        media_dir = working_dir / "media"

        if not metadata_path.exists():
            logger.error("Metadata file not found: %s", metadata_path)
            return None, None

        if not media_dir.exists():
            logger.error("Media directory not found: %s", media_dir)
            return None, None

        return metadata_path, media_dir

    @staticmethod
    def should_skip_for_dedup(
        file_path: str,
        dedup_manifest: Optional[Dict[str, Any]],
        processor_name: str,
    ) -> bool:
        """Check if file should be skipped due to deduplication.

        Computes content hash and checks against dedup manifest to determine
        if this file is a secondary copy that should be skipped.

        Args:
            file_path: Path to the file to check.
            dedup_manifest: Optional deduplication manifest dictionary.
            processor_name: Name of the processor for manifest lookup.

        Returns:
            True if file is a secondary (should be skipped), False if primary.
        """
        if not dedup_manifest:
            return False

        from common.dedup.hashing import compute_content_hash
        from common.dedup.service import is_file_primary_by_hash

        content_hash = compute_content_hash(file_path)
        return not is_file_primary_by_hash(dedup_manifest, content_hash, processor_name)

    @staticmethod
    def correct_extensions_and_update_info(
        file_paths: List[str],
        file_info: List[Tuple],
        path_index: int = 0,
    ) -> Tuple[List[str], List[Tuple]]:
        """Correct file extensions and update paths in file_info tuples.

        Uses magic bytes detection to correct file extensions based on actual
        content, then updates the file_paths list and file_info tuples with
        the corrected paths.

        Args:
            file_paths: List of file path strings.
            file_info: List of tuples where path_index indicates the path position.
            path_index: Index of the path element in each file_info tuple.

        Returns:
            Tuple of (updated_file_paths, updated_file_info).
        """
        from common.files.utils import batch_correct_extensions

        if not file_paths:
            return file_paths, file_info

        corrections = batch_correct_extensions([Path(p) for p in file_paths])
        if not corrections:
            return file_paths, file_info

        # Update file_paths with corrected paths
        updated_paths = [corrections.get(p, p) for p in file_paths]

        # Update file_info tuples with corrected paths at the specified index
        updated_info = []
        for t in file_info:
            t_list = list(t)
            old_path = t_list[path_index]
            t_list[path_index] = corrections.get(old_path, old_path)
            updated_info.append(tuple(t_list))

        return updated_paths, updated_info

    @staticmethod
    def batch_process_exif(
        file_paths: List[str],
        file_info: List[Tuple],
        metadata_request_builder: Callable[[List[Tuple]], List],
    ) -> int:
        """Batch validate, rebuild, and write EXIF metadata.

        Performs the standard EXIF processing pipeline:
        1. Validate EXIF structures
        2. Rebuild corrupted EXIF structures
        3. Read existing metadata
        4. Build and write new metadata requests

        Args:
            file_paths: List of file paths to process.
            file_info: List of tuples containing file metadata for building requests.
            metadata_request_builder: Callable that takes file_info and returns
                a list of MetadataWriteRequest objects.

        Returns:
            Count of files with rebuilt EXIF structures.
        """
        from common.media.exiftool import (
            batch_read_existing_metadata,
            batch_rebuild_exif,
            batch_validate_exif,
            batch_write_metadata_requests,
        )

        if not file_paths:
            return 0

        exif_rebuilt_count = 0

        # Validate and rebuild corrupted EXIF
        corrupted_files = batch_validate_exif(file_paths)
        if corrupted_files:
            logger.info(
                "Rebuilding %d corrupted EXIF structures...", len(corrupted_files)
            )
            batch_rebuild_exif(list(corrupted_files))
            exif_rebuilt_count = len(corrupted_files)

        # Batch read and write metadata
        existing_metadata_map = batch_read_existing_metadata(file_paths)
        requests = metadata_request_builder(file_info)
        batch_write_metadata_requests(requests, existing_metadata_map)

        return exif_rebuilt_count
