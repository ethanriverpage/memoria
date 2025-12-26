#!/usr/bin/env python3
"""Base class for all media processors."""

import json
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from common.dependency_checker import (
    check_exiftool,
    check_ffmpeg,
    check_pillow,
    print_exiftool_error,
    print_ffmpeg_error,
    print_pillow_error,
)
from common.output.paths import OutputInfo

logger = logging.getLogger(__name__)


@dataclass
class ProcessingResults:
    """Container for results from parallel file processing.

    Collects successful and failed processing outcomes along with
    file paths and metadata for downstream EXIF processing.

    Attributes:
        success_count: Number of successfully processed files.
        failed_count: Number of failed processing attempts.
        file_paths: List of output file paths for successful files.
        file_info: List of metadata tuples for successful files.
    """

    success_count: int = 0
    failed_count: int = 0
    file_paths: List[str] = field(default_factory=list)
    file_info: List[Tuple] = field(default_factory=list)


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
    def get_required_dependencies() -> List[str]:
        """Return list of required system dependencies.

        Override in subclasses that need additional dependencies beyond exiftool.
        Valid dependency names: "exiftool", "ffmpeg", "pillow"

        Returns:
            List of dependency names required by this processor.
        """
        return ["exiftool"]

    # Registry of dependency checkers: (check_function, error_function)
    _DEPENDENCY_CHECKERS: Dict[str, Tuple[Callable[[], bool], Callable[[], None]]] = {
        "exiftool": (check_exiftool, print_exiftool_error),
        "ffmpeg": (check_ffmpeg, print_ffmpeg_error),
        "pillow": (check_pillow, print_pillow_error),
    }

    @classmethod
    def check_dependencies(cls) -> bool:
        """Check all required dependencies are available.

        Iterates through dependencies declared by get_required_dependencies()
        and verifies each is available. Prints appropriate error messages for
        any missing dependencies.

        Returns:
            True if all dependencies are available, False otherwise.
        """
        for dep in cls.get_required_dependencies():
            if dep not in cls._DEPENDENCY_CHECKERS:
                logger.error("Unknown dependency: %s", dep)
                return False
            check_fn, error_fn = cls._DEPENDENCY_CHECKERS[dep]
            if not check_fn():
                error_fn()
                return False
        return True

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

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if input directory requires preprocessing before cataloging.

        Preprocessing converts raw exports into a standardized format with
        metadata.json and a media/ directory. This method checks whether the
        input is already preprocessed or needs preprocessing first.

        Args:
            input_dir: Path to the input directory to check.

        Returns:
            True if preprocessing is needed, False if already preprocessed
            or if this processor doesn't require preprocessing.
        """
        return False  # Default: no preprocessing needed

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on a raw export directory.

        Converts a raw export into a standardized preprocessed format with
        metadata.json and a media/ directory. The result is written to the
        cache directory for reuse in subsequent runs.

        Args:
            input_dir: Path to the raw export directory.
            cache_dir: Path to the cache directory for storing preprocessed output.
            **kwargs: Additional arguments (workers, verbose).

        Returns:
            Path to the working directory (preprocessed output) on success,
            or None if preprocessing failed.
        """
        raise NotImplementedError(
            "Processor declares needs_preprocessing=True "
            "but does not implement preprocess()"
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
        # Pass source_path for within-processor dedup
        return not is_file_primary_by_hash(
            dedup_manifest, content_hash, processor_name, file_path
        )

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
        dedup_manifest: Optional[Dict[str, Any]] = None,
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
            dedup_manifest: Optional deduplication manifest for merging descriptions
                from cross-processor duplicates.

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

        # Run validation and metadata reading in parallel for better performance
        # Both are read-only operations that can safely execute concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            validate_future = executor.submit(batch_validate_exif, file_paths)
            metadata_future = executor.submit(batch_read_existing_metadata, file_paths)

            corrupted_files = validate_future.result()
            existing_metadata_map = metadata_future.result()

        # Rebuild corrupted EXIF structures
        if corrupted_files:
            logger.info(
                "Rebuilding %d corrupted EXIF structures...", len(corrupted_files)
            )
            batch_rebuild_exif(list(corrupted_files))
            exif_rebuilt_count = len(corrupted_files)

            # Re-read metadata for rebuilt files to ensure accuracy
            # The initial parallel read may have gotten incomplete data from corrupted files
            rebuilt_metadata = batch_read_existing_metadata(list(corrupted_files))
            existing_metadata_map.update(rebuilt_metadata)

        # Build and write metadata requests
        requests = metadata_request_builder(file_info)
        batch_write_metadata_requests(requests, existing_metadata_map, dedup_manifest)

        return exif_rebuilt_count

    @staticmethod
    def build_media_filename_index(
        metadata: dict,
        conversations_key: str = "conversations",
        messages_key: str = "messages",
        media_keys: Optional[List[str]] = None,
        include_orphaned: bool = False,
        orphaned_prefer_existing: bool = False,
        context_builder: Optional[Callable[[dict, dict], dict]] = None,
    ) -> Dict[str, dict]:
        """Build index mapping media filename to metadata context.

        Generic utility for building filename indexes from metadata.json.
        Handles both dict and list conversation formats, singular and plural
        media fields, and optional orphaned media entries.

        Args:
            metadata: Parsed metadata.json content.
            conversations_key: Key for conversations in metadata.
            messages_key: Key for messages within each conversation.
            media_keys: List of keys to check for media files in messages.
                Defaults to ["media_files", "media_file"].
            include_orphaned: If True, also index "orphaned_media" entries.
            orphaned_prefer_existing: If True, skip orphaned entries that already
                exist in the index (prefer conversation entries over orphaned).
            context_builder: Optional callable(conversation, message) -> dict
                that returns the context to store in the index.
                If None, stores just the message dict.

        Returns:
            Dict mapping media_file -> context dict
        """
        if media_keys is None:
            media_keys = ["media_files", "media_file"]

        filename_index: Dict[str, dict] = {}
        conversations = metadata.get(conversations_key, {})

        # Handle both dict and list conversation formats
        if isinstance(conversations, dict):
            conv_items = conversations.values()
        else:
            conv_items = conversations

        for conversation in conv_items:
            for message in conversation.get(messages_key, []):
                # Collect media files from all configured keys
                media_files = []
                for key in media_keys:
                    value = message.get(key)
                    if value:
                        if isinstance(value, list):
                            media_files.extend(value)
                        else:
                            media_files.append(value)

                for media_file in media_files:
                    if context_builder:
                        context = context_builder(conversation, message)
                    else:
                        context = message
                    filename_index[media_file] = context

        # Handle orphaned media if requested
        if include_orphaned:
            for message in metadata.get("orphaned_media", []):
                media_files = []
                for key in media_keys:
                    value = message.get(key)
                    if value:
                        if isinstance(value, list):
                            media_files.extend(value)
                        else:
                            media_files.append(value)

                for media_file in media_files:
                    if orphaned_prefer_existing and media_file in filename_index:
                        continue
                    if context_builder:
                        context = context_builder({}, message)
                    else:
                        context = message
                    filename_index[media_file] = context

        return filename_index

    @staticmethod
    def build_flat_filename_index(
        items: List[Dict[str, Any]],
        filename_key: str = "media_filename",
    ) -> Dict[str, Dict[str, Any]]:
        """Build index mapping media filename to item metadata.

        Simple version for flat list structures without conversation nesting
        (e.g., snapchat_memories).

        Args:
            items: List of item dictionaries.
            filename_key: Key containing the media filename.

        Returns:
            Dict mapping filename -> item dict
        """
        filename_index: Dict[str, Dict[str, Any]] = {}
        for item in items:
            filename = item.get(filename_key)
            if filename:
                filename_index[filename] = item
        return filename_index

    @staticmethod
    def collect_processing_results(
        results: List[Tuple],
        info_extractor: Optional[Callable[[Tuple], Optional[Tuple]]] = None,
    ) -> "ProcessingResults":
        """Collect processing results into a unified structure.

        Iterates through processing results, counting successes and failures,
        and collecting file paths and metadata for successful files.

        Args:
            results: List of result tuples. Each tuple must have at least:
                - results[0]: success (bool) - whether processing succeeded
                - results[1]: output_path (Optional[str]) - path to output file
                The remaining elements are processor-specific metadata.
            info_extractor: Optional callable that extracts the info tuple from
                a result tuple. If provided, should return the info tuple for
                successful results, or None to skip adding to file_info.
                If not provided, uses result[2] as the info tuple (standard case).

        Returns:
            ProcessingResults containing counts and collected file data.

        Examples:
            Standard 3-element tuples (success, output_path, info_tuple):
                results = processor.collect_processing_results(results)

            Custom extraction for 5-element tuples:
                def extract_info(r):
                    # r = (success, output_path, message, username, hash)
                    return (r[1], r[2], r[3], r[4])  # (path, msg, user, hash)
                results = processor.collect_processing_results(results, extract_info)

            Filtering (e.g., skip MKV files):
                def extract_non_mkv(r):
                    # r = (success, output_path, is_mkv, message, username, hash)
                    if r[2]:  # is_mkv
                        return None  # Skip MKV files
                    return (r[1], r[3], r[4], r[5])
                results = processor.collect_processing_results(results, extract_non_mkv)
        """
        collected = ProcessingResults()

        for result in results:
            success = result[0]
            output_path = result[1]

            if success and output_path:
                collected.success_count += 1
                collected.file_paths.append(output_path)

                if info_extractor:
                    info = info_extractor(result)
                    if info is not None:
                        collected.file_info.append(info)
                else:
                    # Default: use third element as info_tuple
                    if len(result) > 2:
                        collected.file_info.append(result[2])
            else:
                collected.failed_count += 1

        return collected

    @staticmethod
    def collect_batch_processing_results(
        batch_results: List[List[Tuple]],
        info_extractor: Optional[Callable[[Tuple], Optional[Tuple]]] = None,
    ) -> "ProcessingResults":
        """Collect processing results from batched/nested result lists.

        Flattens nested batch results and collects into unified structure.
        Used when processing is done in batches that each return a list of results.

        Args:
            batch_results: List of result batches, where each batch is a list
                of result tuples.
            info_extractor: Optional callable for custom info extraction.
                See collect_processing_results for details.

        Returns:
            ProcessingResults containing counts and collected file data.
        """
        # Flatten batch results and delegate to standard collector
        flattened = [result for batch in batch_results for result in batch]
        return ProcessorBase.collect_processing_results(flattened, info_extractor)
