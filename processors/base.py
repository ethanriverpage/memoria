#!/usr/bin/env python3
"""Base class for all media processors."""

import json
import logging
import os
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional, Tuple

if TYPE_CHECKING:
    from common.failures import FailureTracker
    from common.media.exiftool import BatchWriteOutcome

from common.config.dependencies import (
    check_exiftool,
    check_ffmpeg,
    check_pillow,
    print_exiftool_error,
    print_ffmpeg_error,
    print_pillow_error,
)
from common.files.utils import short_path
from common.metadata.schema import validate_metadata
from common.output.paths import OutputInfo
from common.output.progress import PHASE_PROCESS
from common.processing import (
    process_batches_parallel as _process_batches_parallel,
    process_items_parallel as _process_items_parallel,
)
from common.validation.invariants import (
    require_non_unknown_username,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MediaCopyTask:
    """A single media copy task for multiprocessing workers.

    This task format is processor-agnostic and supports the common 3-phase pipeline:
    1) Copy source file to destination
    2) Correct file extension based on magic bytes
    3) Update filesystem timestamps

    The worker returns (success, output_path, file_info_tuple) where file_info_tuple
    is (output_path, *info_rest). The exact contents of info_rest are processor-specific.
    """

    source_path: str
    dest_path: str
    info_rest: Tuple[Any, ...]
    timestamp_str: Optional[str] = None
    timestamp_format: Optional[str] = None


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

    def __init_subclass__(cls, **kwargs):
        """Validate subclass implementation at class definition time.

        Ensures that processors declaring certain capabilities actually
        implement the required methods.
        """
        super().__init_subclass__(**kwargs)
        # Skip validation for abstract classes
        if getattr(cls, "__abstractmethods__", None):
            return
        # Validate dedup capability
        if cls.supports_dedup() and cls.build_catalog is ProcessorBase.build_catalog:
            raise TypeError(
                f"{cls.__name__} declares supports_dedup=True "
                "but doesn't implement build_catalog()"
            )
        # Validate consolidation capability
        if (
            cls.supports_consolidation()
            and cls.process_consolidated is ProcessorBase.process_consolidated
        ):
            raise TypeError(
                f"{cls.__name__} declares supports_consolidation=True "
                "but doesn't implement process_consolidated()"
            )

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

    @classmethod
    @abstractmethod
    def process(
        cls,
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
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
            **kwargs: Additional arguments (workers).

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
        metadata.json (containing source_path entries). This method checks
        whether the input is already preprocessed or needs preprocessing first.

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
        metadata.json (containing source_path entries for all media). The
        result is written to the cache directory for reuse in subsequent runs.

        Args:
            input_dir: Path to the raw export directory.
            cache_dir: Path to the cache directory for storing preprocessed output.
            **kwargs: Additional arguments (workers).

        Returns:
            Path to the working directory (preprocessed output) on success,
            or None if preprocessing failed.
        """
        raise NotImplementedError(
            "Processor declares needs_preprocessing=True "
            "but does not implement preprocess()"
        )

    @staticmethod
    def check_preprocessed_source_path(
        input_path: Path,
        media_files_key: str = "media_files",
        container_key: Optional[str] = None,
    ) -> Optional[List[Dict[str, Any]]]:
        """Check if directory has valid preprocessed metadata.

        Checks for metadata.json and whether media entries contain source_path.
        Preprocessed directories must have source_path entries - this is the
        canonical format (no media/ directory in cache).

        Args:
            input_path: Path to the directory to check.
            media_files_key: Key containing media file entries in metadata.
                Default is "media_files" for flat structures (Google Photos).
            container_key: Optional key for nested structures (e.g., "conversations"
                for iMessage). If provided, looks for media_files inside messages.

        Returns:
            List of media file entries if valid preprocessed directory,
            None if not preprocessed or missing source_path entries.

        Example - Flat structure (Google Photos, Snapchat Memories):
            media_files = ProcessorBase.check_preprocessed_source_path(input_path)
            if media_files is not None:
                yield from catalog_from_preprocessed(...)
                return

        Example - Nested structure (iMessage, Google Chat):
            media_files = ProcessorBase.check_preprocessed_source_path(
                input_path, container_key="conversations"
            )
        """
        metadata_file = input_path / "metadata.json"
        if not metadata_file.exists():
            return None

        try:
            with open(metadata_file, "r", encoding="utf-8") as f:
                metadata = json.load(f)

            if container_key:
                # Nested structure: conversations/messages with media_files
                container = metadata.get(container_key, {})
                if isinstance(container, dict):
                    container_items = list(container.values())
                else:
                    container_items = container

                # Check for source_paths in messages (conversations format)
                for conv in container_items:
                    messages = conv.get("messages", [])
                    for msg in messages:
                        source_paths = msg.get("source_paths", {})
                        if source_paths:
                            # Found source_paths - extract all entries using
                            # unified extraction
                            all_entries = list(
                                ProcessorBase.extract_all_media_entries(metadata)
                            )
                            return all_entries if all_entries else None
                        # Also check for source_path on message level
                        if msg.get("source_path"):
                            all_entries = list(
                                ProcessorBase.extract_all_media_entries(metadata)
                            )
                            return all_entries if all_entries else None
            else:
                # Flat structure: media/media_files at top level
                media_files = metadata.get(media_files_key, [])
                if media_files:
                    sample = (
                        media_files[0] if isinstance(media_files[0], dict) else None
                    )
                    if sample:
                        # Check for source_path (media_files style)
                        if "source_path" in sample:
                            return media_files
                        # Check for source_paths dict (media/posts style like Instaloader)
                        if sample.get("source_paths"):
                            # Return all entries extracted from posts
                            all_entries = list(
                                ProcessorBase.extract_all_media_entries(metadata)
                            )
                            return all_entries if all_entries else None

        except Exception as e:
            logger.debug("Could not check preprocessed format: %s", e)

        return None

    @staticmethod
    def catalog_from_preprocessed_if_available(
        processor_name: str,
        input_path: Path,
        workers: int = 4,
        metadata_extractor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
        media_files_key: str = "media_files",
        container_key: Optional[str] = None,
    ) -> Optional[Iterator[Dict[str, Any]]]:
        """Build catalog from preprocessed metadata if available.

        Checks if directory has preprocessed metadata with source_path format
        and returns a catalog iterator if so. This is the recommended way to
        handle preprocessed directories in build_catalog() implementations.

        Args:
            processor_name: Name for the catalog (e.g., "Google Photos").
            input_path: Path to the directory to check.
            workers: Number of parallel workers for computing missing hashes.
            metadata_extractor: Optional function to transform metadata from each
                media_files entry. Signature: (entry: Dict) -> Dict[str, Any].
            media_files_key: Key containing media file entries in metadata.
            container_key: Optional key for nested structures.

        Returns:
            Iterator yielding catalog entries if preprocessed format found,
            None if directory is not preprocessed (no metadata.json or no
            source_path entries).

        Example:
            @staticmethod
            def build_catalog(input_dir: str, **kwargs):
                input_path = Path(input_dir)
                workers = kwargs.get("workers", 4)

                # Check for preprocessed format first
                catalog_iter = ProcessorBase.catalog_from_preprocessed_if_available(
                    processor_name="Google Photos",
                    input_path=input_path,
                    workers=workers,
                    metadata_extractor=lambda e: {"albums": e.get("albums", [])},
                )
                if catalog_iter is not None:
                    yield from catalog_iter
                    return

                # Fall back to raw export handling...
        """
        from common.dedup.catalog import catalog_from_preprocessed

        media_files = ProcessorBase.check_preprocessed_source_path(
            input_path, media_files_key, container_key
        )

        if media_files is None:
            return None

        logger.info(
            "Building catalog from preprocessed metadata (%d files): %s",
            len(media_files),
            input_path.name,
        )

        return catalog_from_preprocessed(
            processor_name=processor_name,
            input_path=input_path,
            workers=workers,
            metadata_extractor=metadata_extractor,
        )

    # =========================================================================
    # Shared Utility Methods
    # =========================================================================

    @classmethod
    def process_items_parallel(
        cls,
        tasks: List[Any],
        worker_fn: Callable[[Any], Any],
        num_workers: int,
        *,
        phase: str = PHASE_PROCESS,
        description: str = "Processing",
        initializer: Optional[Callable[..., None]] = None,
        initargs: tuple[Any, ...] = (),
    ) -> List[Any]:
        """Process tasks with a multiprocessing pool and shared progress handling.

        This is the standardized multiprocess mapping primitive for processors.
        It supports a pool initializer (e.g., worker logging setup) and also
        runs sequentially when num_workers <= 1.
        """
        return _process_items_parallel(
            tasks=tasks,
            worker_fn=worker_fn,
            num_workers=num_workers,
            phase=phase,
            description=description,
            initializer=initializer,
            initargs=initargs,
        )

    @classmethod
    def process_batches_parallel(
        cls,
        tasks: List[Any],
        worker_fn: Callable[[List[Any]], List[Any]],
        num_workers: int,
        *,
        batch_size: int = 100,
        phase: str = PHASE_PROCESS,
        description: str = "Processing",
    ) -> List[Any]:
        """Process tasks in batches with shared multiprocessing + progress handling."""
        return _process_batches_parallel(
            tasks=tasks,
            worker_fn=worker_fn,
            num_workers=num_workers,
            batch_size=batch_size,
            phase=phase,
            description=description,
        )

    @classmethod
    def get_processor(cls):
        """Return processor class for auto-discovery.

        This class method is used by memoria.py during automatic processor
        discovery. Subclasses inherit this automatically.

        Returns:
            The processor class itself.
        """
        return cls

    @classmethod
    def load_metadata(
        cls,
        metadata_path: Path,
        validate: bool = False,
        require_source_paths: bool = True,
        processor_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Load and optionally validate metadata.json.

        Args:
            metadata_path: Path to metadata.json file.
            validate: Whether to validate metadata structure.
            require_source_paths: If validating, whether source_paths are required.

        Returns:
            Parsed metadata dictionary.

        Raises:
            MetadataValidationError: If validation is enabled and fails.
            InvariantViolation: If export username is unknown.
        """
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        if validate:
            validate_metadata(metadata, require_source_paths=require_source_paths)
            # Enforce username invariant when validation is enabled
            export_info = metadata.get("export_info", {})
            username = export_info.get("export_username")
            username_source = export_info.get("username_source", "unknown")
            require_non_unknown_username(
                username=username,
                source=username_source,
                export_dir=metadata_path.parent,
                processor=processor_name
                or getattr(cls, "get_name", lambda: cls.__name__)(),
            )

        return metadata

    @classmethod
    def is_preprocessed_export_dir(
        cls,
        input_dir: str | Path,
        *,
        media_files_key: str = "media_files",
        container_key: Optional[str] = None,
        metadata_validator: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> bool:
        """Return True if input_dir is a valid preprocessed export directory.

        This is the single, canonical "already preprocessed?" check for processors.
        A preprocessed directory must contain a `metadata.json` that conforms to the
        standardized metadata schema and matches the expected content key.
        """
        input_path = Path(input_dir)
        metadata_path = input_path / "metadata.json"
        if not metadata_path.exists():
            return False

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception:
            return False

        try:
            validate_metadata(metadata, require_source_paths=True)
        except Exception:
            return False

        if metadata_validator is not None and not metadata_validator(metadata):
            return False

        expected_key = container_key or media_files_key
        return expected_key in metadata

    @staticmethod
    def is_preprocessed_for_platform(
        input_path: Path,
        platform: str,
        service: Optional[str] = None,
    ) -> bool:
        """Check if directory is preprocessed for a specific platform/service.

        This provides unambiguous detection of preprocessed directories by checking
        the platform and service markers in export_info. Use this in detect() methods
        instead of checking for generic keys like 'conversations'.

        Args:
            input_path: Path to directory to check.
            platform: Expected platform identifier (e.g., "Snapchat", "Discord").
            service: Expected service identifier (e.g., "messages", "memories").
                     If None, matches any service (including None).

        Returns:
            True if directory is preprocessed for the specified platform/service.

        Example:
            # In SnapchatMessagesProcessor.detect():
            if ProcessorBase.is_preprocessed_for_platform(
                input_path, platform="Snapchat", service="messages"
            ):
                return True
        """
        metadata_path = input_path / "metadata.json"
        if not metadata_path.exists():
            return False

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception:
            return False

        # Validate schema
        try:
            validate_metadata(metadata, require_source_paths=True)
        except Exception:
            return False

        # Check platform/service markers
        export_info = metadata.get("export_info", {})
        if export_info.get("platform") != platform:
            return False

        # If service is specified, check it matches
        if service is not None and export_info.get("service") != service:
            return False

        return True

    @staticmethod
    def validate_working_directory(
        working_dir: Path,
    ) -> Optional[Path]:
        """Validate working directory has required structure.

        Checks for existence of metadata.json. Preprocessed directories
        contain only metadata.json (with source_path entries); no media/
        directory is expected.

        Args:
            working_dir: Path to working directory to validate.

        Returns:
            Path to metadata.json if valid, or None if invalid.
        """
        working_dir = Path(working_dir)
        metadata_path = working_dir / "metadata.json"

        if not metadata_path.exists():
            logger.error("Metadata file not found: %s", metadata_path)
            return None

        return metadata_path

    @staticmethod
    def materialize_ambiguous_cases(
        metadata: Dict[str, Any],
        output_dir: Path,
    ) -> int:
        """Materialize ambiguous_cases from metadata into output_dir/issues/needs_matching/.

        Creates a folder structure for each ambiguous case with the media and overlay
        files copied from their source_path locations, plus a match_info.json manifest.

        Args:
            metadata: Parsed metadata.json dict (may contain 'ambiguous_cases')
            output_dir: Processor output directory (e.g., {base}/Snapchat Messages/)

        Returns:
            Number of cases materialized
        """
        from common.files.utils import copy_file

        ambiguous_cases = metadata.get("ambiguous_cases", [])
        if not ambiguous_cases:
            return 0

        needs_matching_dir = output_dir / "issues" / "needs_matching"
        needs_matching_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            "Materializing %d ambiguous cases to %s",
            len(ambiguous_cases),
            needs_matching_dir,
        )

        materialized = 0
        missing_media = 0
        missing_media_examples: list[str] = []
        missing_overlays = 0
        missing_overlay_examples: list[str] = []
        for case in ambiguous_cases:
            # Create timestamped subfolder (sanitized)
            timestamp = case.get("timestamp", "unknown")
            timestamp_safe = timestamp.replace(" ", "_").replace(":", "-")
            case_dir = needs_matching_dir / timestamp_safe
            case_dir.mkdir(exist_ok=True)

            # Create media and overlays subdirectories
            media_subdir = case_dir / "media"
            overlay_subdir = case_dir / "overlays"
            media_subdir.mkdir(exist_ok=True)
            overlay_subdir.mkdir(exist_ok=True)

            # Copy media files from source_path
            for media_info in case.get("media_files", []):
                filename = media_info.get("filename", "")
                source_path_str = media_info.get("source_path")
                if source_path_str:
                    source_path = Path(source_path_str)
                    if source_path.exists():
                        dest = media_subdir / filename
                        try:
                            copy_file(source_path, dest)
                        except Exception as e:
                            logger.warning("Failed to copy %s: %s", filename, e)
                    else:
                        missing_media += 1
                        if len(missing_media_examples) < 5:
                            missing_media_examples.append(short_path(source_path))

            # Copy overlay files from source_path
            for overlay_info in case.get("overlays", []):
                filename = overlay_info.get("filename", "")
                source_path_str = overlay_info.get("source_path")
                if source_path_str:
                    source_path = Path(source_path_str)
                    if source_path.exists():
                        dest = overlay_subdir / filename
                        try:
                            copy_file(source_path, dest)
                        except Exception as e:
                            logger.warning("Failed to copy %s: %s", filename, e)
                    else:
                        missing_overlays += 1
                        if len(missing_overlay_examples) < 5:
                            missing_overlay_examples.append(short_path(source_path))

            # Write match_info.json manifest
            match_info_path = case_dir / "match_info.json"
            try:
                with open(match_info_path, "w", encoding="utf-8") as f:
                    json.dump(case, f, indent=2, ensure_ascii=False)
                materialized += 1
            except Exception as e:
                logger.error("Failed to write %s: %s", match_info_path, e)

        if missing_media:
            logger.info(
                "Source media missing for %d files (e.g. %s)",
                missing_media,
                ", ".join(missing_media_examples),
            )
        if missing_overlays:
            logger.info(
                "Source overlays missing for %d files (e.g. %s)",
                missing_overlays,
                ", ".join(missing_overlay_examples),
            )

        logger.info(
            "Created %d matching case folders in %s", materialized, needs_matching_dir
        )
        return materialized

    @staticmethod
    def extract_all_media_entries(
        metadata: Dict[str, Any],
    ) -> Iterator[Dict[str, Any]]:
        """Extract all media entries from any metadata structure.

        Handles all three content types uniformly:
        - media_files: yields each entry directly
        - media: yields each post's media files with post context
        - conversations: yields each message's media files with context

        Also includes unmatched_media if present.

        This is useful for catalog building and cross-processor deduplication,
        providing a single interface to iterate over all media regardless of
        the underlying structure.

        Args:
            metadata: Parsed metadata.json dict

        Yields:
            Media entry dicts with at minimum: filename, source_path
        """
        from common.metadata.extraction import extract_all_media_entries

        yield from extract_all_media_entries(metadata)

    @staticmethod
    def should_skip_for_dedup(
        dedup_manifest: Optional[Dict[str, Any]],
        content_hash: str,
        processor_name: str,
        source_path: str,
    ) -> bool:
        """Check if file should be skipped due to deduplication.

        Uses the precomputed content hash from preprocessing metadata to check
        against the dedup manifest. Returns True if this file is a secondary
        copy that should be skipped.

        Args:
            dedup_manifest: Deduplication manifest dictionary (or None if dedup disabled).
            content_hash: Precomputed xxHash64 content hash from preprocessing.
            processor_name: Name of the processor for manifest lookup.
            source_path: Path to the source file (for within-processor dedup).

        Returns:
            True if file is a secondary (should be skipped), False if primary.
        """
        if not dedup_manifest:
            return False

        from common.dedup.service import is_file_primary_by_hash

        return not is_file_primary_by_hash(
            dedup_manifest, content_hash, processor_name, source_path
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
    ) -> Tuple[int, "BatchWriteOutcome", List[str]]:
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
            Tuple of (exif_rebuilt_count, write_outcome, missing_files).
        """
        from common.media.exiftool import (
            BatchWriteOutcome,
            batch_read_existing_metadata,
            batch_rebuild_exif,
            batch_validate_exif,
            batch_write_metadata_requests,
        )

        if not file_paths:
            return 0, BatchWriteOutcome(), []

        exif_rebuilt_count = 0

        # Run validation and metadata reading in parallel for better performance
        # Both are read-only operations that can safely execute concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            validate_future = executor.submit(batch_validate_exif, file_paths)
            metadata_future = executor.submit(batch_read_existing_metadata, file_paths)

            corrupted_files = validate_future.result()
            metadata_result = metadata_future.result()
            existing_metadata_map = metadata_result.metadata_map
            missing_files = metadata_result.missing_files

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
            existing_metadata_map.update(rebuilt_metadata.metadata_map)

        # Build and write metadata requests
        requests = metadata_request_builder(file_info)
        write_outcome = batch_write_metadata_requests(
            requests, existing_metadata_map, dedup_manifest
        )

        return exif_rebuilt_count, write_outcome, missing_files

    @classmethod
    def finalize_files(
        cls,
        file_paths: List[str],
        file_info: List[Tuple],
        metadata_builder: Callable[[List[Tuple]], List],
        dedup_manifest: Optional[Dict[str, Any]] = None,
        path_index: int = 0,
        failure_tracker: Optional["FailureTracker"] = None,
        metadata_for_file: Optional[Callable[[str], Optional[dict]]] = None,
    ) -> Tuple[List[str], List[Tuple], int]:
        """Correct extensions and batch process EXIF metadata.

        Combines correct_extensions_and_update_info() and batch_process_exif()
        into a single call for cleaner processor code.

        Args:
            file_paths: List of file path strings.
            file_info: List of tuples containing file metadata.
            metadata_builder: Callable that takes file_info and returns
                a list of MetadataWriteRequest objects.
            dedup_manifest: Optional deduplication manifest for merging.
            path_index: Index of the path element in each file_info tuple.
            failure_tracker: Optional FailureTracker for recording EXIF failures.
                When provided, EXIF failures are tracked as processing failures.
            metadata_for_file: Optional callable that returns metadata dict for
                a file path (used for tracking failures with context).

        Returns:
            Tuple of (updated_file_paths, updated_file_info, exif_rebuilt_count).
        """
        file_paths, file_info = cls.correct_extensions_and_update_info(
            file_paths, file_info, path_index=path_index
        )
        exif_rebuilt_count = 0
        if file_paths:
            exif_rebuilt_count, write_outcome, missing_files = cls.batch_process_exif(
                file_paths, file_info, metadata_builder, dedup_manifest
            )

            # Track EXIF failures if failure_tracker is provided
            if failure_tracker:
                # Track missing files (output files that don't exist at EXIF write time)
                for missing_path in missing_files:
                    metadata = (
                        metadata_for_file(missing_path) if metadata_for_file else None
                    )
                    failure_tracker.add_processing_failure(
                        media_path=Path(missing_path),
                        metadata=metadata,
                        reason="exif_target_missing",
                        error_details=f"Output file missing at EXIF write time: {missing_path}",
                    )

                # Track EXIF write failures
                for failure in write_outcome.failures:
                    metadata = (
                        metadata_for_file(failure.file_path)
                        if metadata_for_file
                        else None
                    )
                    failure_tracker.add_processing_failure(
                        media_path=Path(failure.file_path),
                        metadata=metadata,
                        reason=failure.reason_code,
                        error_details=failure.stderr_excerpt or "",
                    )

        return file_paths, file_info, exif_rebuilt_count

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
                Defaults to ["media_files"].
            include_orphaned: If True, also index "unmatched_media" entries.
            orphaned_prefer_existing: If True, skip orphaned entries that already
                exist in the index (prefer conversation entries over orphaned).
            context_builder: Optional callable(conversation, message) -> dict
                that returns the context to store in the index.
                If None, stores just the message dict.

        Returns:
            Dict mapping media_file -> context dict
        """
        if media_keys is None:
            media_keys = ["media_files"]

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

        # Handle unmatched media if requested
        if include_orphaned:
            unmatched = metadata.get("unmatched_media", [])
            for entry in unmatched:
                # Schema-compliant unmatched_media entries have:
                #   { "filename": "...", "source_path": "..." }
                # (see common/metadata/schema.py)
                filename = entry.get("filename") if isinstance(entry, dict) else None
                if filename:
                    if orphaned_prefer_existing and filename in filename_index:
                        continue
                    if context_builder:
                        context = context_builder({}, entry)
                    else:
                        context = entry
                    filename_index[filename] = context
                    continue

                # Fallback: some callers may treat unmatched_media as message-like entries
                # containing "media_files" (or similar). Index these as well.
                if not isinstance(entry, dict):
                    continue
                media_files: List[str] = []
                for key in media_keys:
                    value = entry.get(key)
                    if value:
                        if isinstance(value, list):
                            media_files.extend(value)
                        else:
                            media_files.append(value)

                for media_file in media_files:
                    if orphaned_prefer_existing and media_file in filename_index:
                        continue
                    if context_builder:
                        context = context_builder({}, entry)
                    else:
                        context = entry
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

    # =========================================================================
    # Shared worker functions (multiprocessing-safe)
    # =========================================================================

    @staticmethod
    def process_media_copy_batch(batch_tasks: List[MediaCopyTask]) -> List[Tuple]:
        """Copy files and update timestamps (multiprocessing worker).

        Consolidates the previously duplicated `process_media_batch()` helpers
        across multiple processors.

        Note: Extension correction is NOT done here. It is handled by
        finalize_files() immediately before EXIF metadata writing, ensuring
        files have correct extensions for proper metadata embedding.
        """
        from common.files.utils import copy_file
        from common.files.timestamps import update_file_timestamps

        results: List[Tuple] = []
        file_info: List[Tuple] = []
        timestamp_meta: List[Tuple[Optional[str], Optional[str]]] = []

        # Phase 1: Copy all files
        for task in batch_tasks:
            src = task.source_path
            dst = task.dest_path

            if not os.path.exists(src):
                logger.warning("Media file not found: %s", short_path(src))
                results.append((False, None, None))
                continue

            try:
                copy_file(src, dst)
                file_info.append((dst, *task.info_rest))
                timestamp_meta.append((task.timestamp_str, task.timestamp_format))
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to copy %s: %s", short_path(src), e)
                results.append((False, None, None))

        if not file_info:
            return results if results else [(False, None, None)] * len(batch_tasks)

        # Phase 2: Update timestamps and compile results
        for idx, info in enumerate(file_info):
            output_path = info[0]
            timestamp_str, timestamp_format = timestamp_meta[idx]
            if timestamp_str:
                if timestamp_format:
                    update_file_timestamps(output_path, timestamp_str, timestamp_format)
                else:
                    update_file_timestamps(output_path, timestamp_str)
            results.append((True, output_path, info))

        return results
