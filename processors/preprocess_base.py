#!/usr/bin/env python3
"""
Base class for all media preprocessors.

Provides common initialization, logging, and utility methods that are
shared across all preprocessor implementations.

Template Enforcement:
    All preprocessors must inherit from PreprocessorBase and implement:
    - validate_export(): Validate export directory structure
    - detect_username(): Detect export owner username
    - process_export(): Platform-specific processing logic
    - print_statistics(): Display processing statistics
    - get_content_key(): Return content key for metadata.json

    The template is enforced at class definition time via __init_subclass__.
"""

import logging
import os
import re
from abc import ABC, abstractmethod
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, Union

import json

from common.dedup.config import get_dedup_config
from common.dedup.database import get_thread_db
from common.dedup.hashing import compute_content_hash
from common.detection import DetectionResult, detect_from_dirname
from common.failure_tracker import FailureTracker
from common.files.filter import BannedFilesFilter
from common.metadata.schema import validate_metadata, CONTENT_KEYS
from common.output.progress import PHASE_PREPROCESS, futures_progress
from common.utils import default_worker_count
from common.validation.invariants import (
    InvariantViolation,
    require_non_unknown_username,
)

logger = logging.getLogger(__name__)

# Minimal shared stats keys - preprocessors may add additional keys
SHARED_STATS_KEYS = {
    "files_total",
    "files_unique",
    "files_duplicates",
    "files_missing",
    "files_banned_skipped",
}


@dataclass
class CopyTask:
    """Task for hash_with_dedup() representing a file to hash.

    Attributes:
        source_path: Path to the source file.
        dest_filename: Desired destination filename (not full path).
        context: Metadata context for merging if duplicates found.
    """

    source_path: Path
    dest_filename: str
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CopyResult:
    """Result of register_downloaded_file() for a single file.

    Used by preprocessors that download files (e.g., Discord) and need
    to check for duplicates after download.

    Attributes:
        dest_filename: Actual filename to use.
        content_hash: xxHash64 of file content.
        is_duplicate: True if this was a duplicate (file deleted).
        primary_source: Source path of the primary file for this hash.
    """

    dest_filename: str
    content_hash: str
    is_duplicate: bool
    primary_source: Path


@dataclass
class HashResult:
    """Result of hash_with_dedup() for a single file.

    Used for hash-only preprocessing where files are not copied to a temp
    directory. The processor reads directly from source_path during processing.

    Attributes:
        source_path: Original file location (for direct processing).
        dest_filename: Generated unique filename for output.
        content_hash: xxHash64 of file content.
        is_duplicate: True if duplicate (skip processing).
        primary_source: Source path of the primary file for this hash.
    """

    source_path: Path
    dest_filename: str
    content_hash: str
    is_duplicate: bool
    primary_source: Path


def _hash_single_file(
    task: CopyTask,
    db_path: Optional[Path],
) -> Tuple[CopyTask, str, bool, Optional[Dict[str, Any]]]:
    """Hash a single file, using cache if available.

    Args:
        task: The CopyTask to hash.
        db_path: Optional path to database for hash cache.

    Returns:
        Tuple of (task, content_hash, cache_hit, cache_entry_to_write)
    """
    file_path = task.source_path

    # Get file stats for cache lookup
    stat = file_path.stat()
    file_size = stat.st_size
    mtime = stat.st_mtime

    content_hash = None
    cache_hit = False
    cache_entry_to_write = None

    # Try cache lookup if database path provided
    db = get_thread_db(db_path)
    if db is not None:
        cached = db.get_cached_hashes(str(file_path), file_size, mtime)
        if cached is not None:
            content_hash = cached["content_hash"]
            cache_hit = True

    # Compute hash if not cached
    if content_hash is None:
        content_hash = compute_content_hash(file_path)

        # Prepare cache entry for deferred batch write
        if db_path is not None:
            cache_entry_to_write = {
                "source_path": str(file_path),
                "file_size": file_size,
                "mtime": mtime,
                "content_hash": content_hash,
                "perceptual_hash": None,
                "dimensions": None,
            }

    return (task, content_hash, cache_hit, cache_entry_to_write)


class PreprocessorBase(ABC):
    """Abstract base class for all media preprocessors.

    Provides common functionality:
    - Output directory validation and setup
    - Thread-safe logging
    - File hashing for deduplication (hash_with_dedup)
    - Failure tracking
    - Cached username detection
    - Standardized metadata saving

    Subclasses must implement:
    - validate_export(): Validate export directory structure
    - detect_username(): Detect export owner username
    - process_export(): Platform-specific processing logic
    - print_statistics(): Display processing statistics
    - get_content_key(): Return content key for metadata.json

    Template enforcement:
    - Subclasses must NOT assign _context_merger directly; use set_context_merger()
    - get_content_key() must return one of: 'conversations', 'media', 'media_files'

    The run() method provides a standardized preprocessing pipeline that calls
    these methods in the correct order.
    """

    # Guard attribute for detecting direct _context_merger assignment
    _context_merger_guard_active: bool = False

    # Required hooks that subclasses must implement
    _REQUIRED_HOOKS = frozenset(
        {
            "validate_export",
            "detect_username",
            "process_export",
            "print_statistics",
            "get_content_key",
            "get_platform",
            "get_service",
        }
    )

    def __init_subclass__(cls, **kwargs):
        """Validate subclass implementation at class definition time.

        Enforces that preprocessor subclasses follow the required template:
        - All required hooks must be implemented (not just inherited from base)
        - get_content_key() must be overridden (enforced via @abstractmethod)

        Runtime enforcement (via __setattr__ and __init__):
        - _context_merger cannot be assigned directly (use set_context_merger())
        - get_content_key() must return a value in CONTENT_KEYS

        Raises:
            TypeError: If subclass violates template requirements.
        """
        super().__init_subclass__(**kwargs)

        # Skip validation for abstract classes (they can't be instantiated anyway)
        if getattr(cls, "__abstractmethods__", None):
            return

        # Verify all required hooks are implemented (not just inherited stubs)
        for hook_name in cls._REQUIRED_HOOKS:
            method = getattr(cls, hook_name, None)
            if method is None:
                raise TypeError(f"{cls.__name__} must implement {hook_name}()")
            # Check it's not the abstract base version
            base_method = getattr(PreprocessorBase, hook_name, None)
            if method is base_method:
                raise TypeError(
                    f"{cls.__name__} must override {hook_name}() - cannot use base class stub"
                )

    def __setattr__(self, name: str, value: Any) -> None:
        """Guard against direct _context_merger assignment.

        Subclasses must use set_context_merger() instead of directly assigning
        to _context_merger. This ensures consistent behavior and allows for
        future validation or logging of context merger changes.

        Raises:
            TypeError: If attempting to directly set _context_merger after init.
        """
        if name == "_context_merger" and self._context_merger_guard_active:
            raise TypeError(
                f"{self.__class__.__name__}: Direct assignment to _context_merger is "
                "not allowed. Use set_context_merger() instead."
            )
        super().__setattr__(name, value)

    def __init__(
        self,
        processor_name: str,
        export_path: Path,
        output_dir: Optional[Path],
        workers: Optional[int] = None,
        final_output_dir: Optional[Path] = None,
    ):
        """Initialize preprocessor with common setup.

        Args:
            processor_name: Human-readable name for logging and tracking
            export_path: Path to export directory to process
            output_dir: Output directory for processed files (required)
            workers: Number of parallel workers (default: CPU count - 1)
            final_output_dir: Final output directory for failure tracking
        """
        self.processor_name = processor_name
        self.export_path = Path(export_path)

        # Output directories - require explicit output_dir to prevent writing to exports
        if output_dir is None:
            raise ValueError(
                "output_dir is required. Preprocessors must not write to the export "
                "directory. Use a temp directory or specify an explicit output path."
            )
        output_base = Path(output_dir)

        # Safety check: never write to the export directory
        if output_base.resolve() == self.export_path.resolve():
            raise ValueError(
                f"output_dir cannot be the same as export_path ({self.export_path}). "
                "This would corrupt the original export."
            )

        self.output_dir = output_base
        self.metadata_file = output_base / "metadata.json"
        self.log_file = output_base / "preprocessing.log"

        # Final output directory for processor (for failure tracking)
        self.final_output_dir = (
            Path(final_output_dir) if final_output_dir else output_base
        )

        # Initialize banned files filter
        self.banned_filter = BannedFilesFilter()

        # Initialize failure tracker
        self.failure_tracker = FailureTracker(
            processor_name=processor_name,
            export_directory=str(export_path),
        )

        # Threading configuration
        self.workers = workers if workers is not None else default_worker_count()

        # Thread-safe locks for shared data structures
        self.stats_lock = Lock()
        self.log_lock = Lock()
        self.destination_lock = Lock()
        self.dedup_lock = Lock()

        # Statistics - initialize with minimal shared keys, subclasses add more
        self.stats: Dict[str, Any] = {
            "files_total": 0,
            "files_unique": 0,
            "files_duplicates": 0,
            "files_missing": 0,
            "files_banned_skipped": 0,
        }

        # Log entries
        self.log_entries = []

        # Deduplication state
        self._context_merger: Optional[Callable[[Dict, Dict], None]] = None
        self._hash_registry: Dict[str, Dict[str, Any]] = {}
        self._used_filenames: Dict[str, int] = {}

        # Unmatched media tracking (files processable but lacking full context)
        self._unmatched_media: List[Dict[str, Any]] = []

        # Ambiguous cases tracking (e.g., overlay matching ambiguity)
        self._ambiguous_cases: List[Dict[str, Any]] = []

        # Cached username detection result
        self._detection_result: Optional[DetectionResult] = None

        # Validate get_content_key() returns a valid value
        content_key = self.get_content_key()
        if content_key not in CONTENT_KEYS:
            raise TypeError(
                f"{self.__class__.__name__}.get_content_key() returned '{content_key}', "
                f"but must return one of: {sorted(CONTENT_KEYS)}"
            )

        # Activate guard after initialization to prevent direct _context_merger assignment
        self._context_merger_guard_active = True

    @classmethod
    @contextmanager
    def temp_output_dir(
        cls, base_dir: Union[str, Path], prefix: str
    ) -> Generator[Path, None, None]:
        """Create a temporary preprocessing output directory (with cleanup).

        This standardizes the temp directory lifecycle across processors.
        Directories are created under `base_dir` with a unique suffix and are
        automatically cleaned up on context exit unless temp cleanup is disabled.

        Cleanup behavior is controlled by `DISABLE_TEMP_CLEANUP` via
        `common.utils.should_cleanup_temp()`.

        Args:
            base_dir: Base directory under which to create the temp directory.
            prefix: Prefix for the temp directory name (e.g., "gvoice", "instaloader").

        Yields:
            Path to the created temporary directory.
        """
        # Import here to avoid circular import chains in some execution paths.
        from common.processing import temp_processing_directory

        with temp_processing_directory(str(base_dir), prefix=prefix) as temp_dir:
            yield temp_dir

    def set_context_merger(
        self, merger: Optional[Callable[[Dict, Dict], None]]
    ) -> None:
        """Set the context merger function for deduplication.

        The merger function is called when duplicates are found, allowing
        metadata from duplicate files to be merged into the primary's context.

        This is the ONLY way to set the context merger - direct assignment to
        _context_merger is blocked and will raise TypeError.

        Args:
            merger: Function with signature (existing: Dict, new: Dict) -> None
                    that modifies existing in-place to merge data from new.
                    Pass None for first-occurrence-wins behavior (no merging).
        """
        # Temporarily disable guard for controlled assignment
        self._context_merger_guard_active = False
        self._context_merger = merger
        self._context_merger_guard_active = True

    def get_merged_contexts(self) -> Dict[str, Dict[str, Any]]:
        """Get all merged contexts after deduplication.

        Returns:
            Dict mapping content_hash to merged context dict.
        """
        return {h: entry["context"] for h, entry in self._hash_registry.items()}

    def get_merged_context_for_file(
        self, dest_filename: str
    ) -> Optional[Dict[str, Any]]:
        """Get merged context for a specific destination filename.

        Args:
            dest_filename: The destination filename to look up.

        Returns:
            Merged context dict for the file, or None if not found.
        """
        for entry in self._hash_registry.values():
            if entry.get("filename") == dest_filename:
                return entry.get("context")
        return None

    def get_contexts_list_for_file(self, dest_filename: str) -> List[Dict[str, Any]]:
        """Get list of all original contexts that were merged for a destination file.

        For preprocessors that accumulate contexts (like iMessage's messages list),
        this returns the accumulated contexts from the merged context.

        Args:
            dest_filename: The destination filename to look up.

        Returns:
            List of context dicts. If the merged context has a 'messages' list,
            returns that. Otherwise returns a single-element list with the context,
            or empty list if not found.
        """
        merged_context = self.get_merged_context_for_file(dest_filename)
        if merged_context is None:
            return []

        # Check if this context has accumulated messages (iMessage pattern)
        if "messages" in merged_context and isinstance(
            merged_context["messages"], list
        ):
            # Return the original context plus the accumulated messages
            # The first entry is the primary, messages are the duplicates
            all_contexts = [merged_context]  # Primary context
            all_contexts.extend(merged_context.get("messages", []))
            return all_contexts

        # For non-accumulating patterns, just return the single merged context
        return [merged_context]

    # =========================================================================
    # Cached Username Detection
    # =========================================================================

    def _extract_directory_username(self) -> Optional[str]:
        """Extract the user-chosen export username from the export directory name.

        This project treats the directory username as the canonical export identity
        (user-controlled), and it must be used consistently for:
        - export_info.export_username
        - filenames and descriptions (via downstream processors reading export_info)

        We support two common directory naming styles:
        - prefix-username-<date...> (e.g. "imessage-iphxr-20210621_...")
        - username-service-<date...> (e.g. "iphxr-messages-20251121") for some iMessage-style exports
        """
        dir_name = self.export_path.name

        # Style 2: username-service-date (only if the second token is a known service marker)
        # Example: iphxr-messages-20251121
        parts = dir_name.split("-")
        if len(parts) >= 3:
            service_token = parts[1].lower()
            date_token = parts[2]
            known_services = {
                "messages",
                "photos",
                "memories",
                "voice",
                "chat",
                "public",
                "public_media",
                "instaloader",
            }
            if service_token in known_services and re.match(
                r"^\d{8}($|[-_])", date_token
            ):
                return parts[0]

        # Style 1: prefix-username-date (standard)
        m = re.match(r"^([a-z0-9]+)[-_]", dir_name, flags=re.IGNORECASE)
        if not m:
            return None
        prefix = m.group(1)
        return detect_from_dirname(self.export_path, prefix)

    def ensure_detection_result(self) -> DetectionResult:
        """Ensure username detection has been performed and return the result.

        This method caches the result of detect_username() so it's only called
        once per preprocessor lifecycle. Subsequent calls return the cached result.

        Returns:
            DetectionResult with username and source
        """
        if self._detection_result is None:
            detected = self.detect_username()

            # Directory username is canonical across the pipeline.
            directory_username = self._extract_directory_username()
            if directory_username:
                self._detection_result = DetectionResult(
                    username=directory_username,
                    source="directory",
                )
            else:
                self._detection_result = detected
        return self._detection_result

    def get_detection_result(self) -> Optional[DetectionResult]:
        """Get the cached detection result, or None if not yet computed.

        Unlike ensure_detection_result(), this does not trigger detection.
        Use this to check if detection has already been performed.

        Returns:
            Cached DetectionResult or None
        """
        return self._detection_result

    # =========================================================================
    # Export Layout Resolution + Validation Helpers
    # =========================================================================

    def resolve_first_existing_dir(self, candidates: List[Path], *, label: str) -> Path:
        """Resolve the first existing directory from a list of candidates.

        Useful for supporting multiple export layouts (e.g., raw vs Takeout).

        Args:
            candidates: List of directory paths to check, in priority order.
            label: Human-readable label for error messages (e.g., "Google Photos").

        Returns:
            First existing directory Path.

        Raises:
            FileNotFoundError: If no candidate directory exists.
        """
        for candidate in candidates:
            if candidate.exists() and candidate.is_dir():
                logger.debug("Resolved %s directory: %s", label, candidate)
                return candidate

        checked_paths = ", ".join(str(c) for c in candidates)
        raise FileNotFoundError(
            f"{label} directory not found. Checked: {checked_paths}"
        )

    def require_exists(self, path: Path, *, label: str) -> bool:
        """Require that a path exists (file or directory).

        Logs an error if the path does not exist.

        Args:
            path: Path to check.
            label: Human-readable label for error messages.

        Returns:
            True if exists, False otherwise.
        """
        if not path.exists():
            logger.error("%s not found: %s", label, path)
            return False
        return True

    def require_any_files(self, directory: Path, pattern: str, *, label: str) -> bool:
        """Require that at least one file matching pattern exists in directory.

        Args:
            directory: Directory to search.
            pattern: Glob pattern (e.g., "*.html", "*.json").
            label: Human-readable label for error messages.

        Returns:
            True if at least one matching file exists, False otherwise.
        """
        if not directory.exists():
            logger.error("%s directory not found: %s", label, directory)
            return False

        matching = list(directory.glob(pattern))
        if not matching:
            logger.error("No %s files found in %s", label, directory)
            return False
        return True

    def require_any_dirs(
        self,
        directory: Path,
        *,
        label: str,
        filter_fn: Optional[Callable[[Path], bool]] = None,
    ) -> bool:
        """Require that at least one subdirectory exists.

        Args:
            directory: Directory to search.
            label: Human-readable label for error messages.
            filter_fn: Optional filter function to apply to subdirectories.
                       If provided, only directories where filter_fn returns True count.

        Returns:
            True if at least one matching subdirectory exists, False otherwise.
        """
        if not directory.exists():
            logger.error("%s directory not found: %s", label, directory)
            return False

        subdirs = [d for d in directory.iterdir() if d.is_dir()]
        if filter_fn:
            subdirs = [d for d in subdirs if filter_fn(d)]

        if not subdirs:
            logger.error("No %s subdirectories found in %s", label, directory)
            return False
        return True

    # =========================================================================
    # Stats Helpers
    # =========================================================================

    def bump_stat(self, key: str, amount: int = 1) -> None:
        """Thread-safe increment of a stat key.

        Args:
            key: Stat key to increment.
            amount: Amount to add (default 1).
        """
        with self.stats_lock:
            self.stats[key] = self.stats.get(key, 0) + amount

    def summarize_dedup_results(
        self, results: Dict[Path, Union["HashResult", "CopyResult"]]
    ) -> Tuple[int, int]:
        """Summarize deduplication results and update shared stats.

        Computes unique and duplicate counts from results and updates
        the shared stats keys (files_unique, files_duplicates).

        Args:
            results: Results dict from hash_with_dedup.

        Returns:
            Tuple of (unique_count, duplicate_count).
        """
        unique = sum(1 for r in results.values() if not r.is_duplicate)
        duplicates = sum(1 for r in results.values() if r.is_duplicate)

        with self.stats_lock:
            self.stats["files_unique"] = unique
            self.stats["files_duplicates"] = duplicates

        return unique, duplicates

    def log_message(self, category: str, message: str, details: str = "") -> None:
        """Add a log entry (thread-safe).

        Args:
            category: Log category (e.g., "DOWNLOAD_ERROR", "MISSING_FILE")
            message: Log message
            details: Optional additional details
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] {category}: {message}"
        if details:
            entry += f" ({details})"
        with self.log_lock:
            self.log_entries.append(entry)

    def save_log(self) -> None:
        """Save log entries to log file."""
        if not self.log_entries:
            return

        try:
            with open(self.log_file, "w", encoding="utf-8") as f:
                f.write(f"{self.processor_name} Preprocessing Log\n")
                f.write("=" * 80 + "\n")
                f.write(f"Export: {self.export_path}\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 80 + "\n\n")

                for entry in self.log_entries:
                    f.write(entry + "\n")

                f.write(f"\nTotal logged issues: {len(self.log_entries)}\n")

        except OSError as e:
            logger.warning("Failed to save log file: %s", e)

    def compute_file_hash(self, file_path: Path) -> str:
        """Compute xxHash64 of file for deduplication.

        Args:
            file_path: Path to file to hash

        Returns:
            Hexadecimal hash digest
        """
        return compute_content_hash(file_path)

    def _get_unique_filename(self, filename: str) -> str:
        """Get a unique filename, handling collisions.

        Thread-safe method that tracks used filenames and appends
        a counter suffix if the filename is already in use.

        Args:
            filename: Desired filename.

        Returns:
            Unique filename (may have counter suffix).
        """
        with self.destination_lock:
            if filename not in self._used_filenames:
                self._used_filenames[filename] = 1
                return filename

            # Handle collision
            base = Path(filename)
            stem = base.stem
            suffix = base.suffix
            counter = self._used_filenames[filename]
            self._used_filenames[filename] = counter + 1

            unique_name = f"{stem}_{counter}{suffix}"
            return unique_name

    def hash_with_dedup(
        self,
        tasks: List[CopyTask],
        workers: Optional[int] = None,
        show_progress: bool = True,
    ) -> Dict[Path, HashResult]:
        """Parallel batch hashing with deduplication (no file copying).

        Hashes all files in parallel, groups by content hash to find duplicates,
        and merges context for duplicate groups. Does NOT copy files - only builds
        metadata with source paths. The processor phase copies directly from
        source_path to final output.

        Args:
            tasks: List of CopyTask objects describing files to process.
            workers: Number of parallel workers (default: self.workers).
            show_progress: Whether to show progress bar.

        Returns:
            Dict mapping source_path to HashResult for all input tasks.
        """
        if not tasks:
            return {}

        if workers is None:
            workers = self.workers

        # Get database path for hash caching
        config = get_dedup_config()
        db_path = config.database_path

        # Check if hash caching is disabled
        disable_cache = os.environ.get("DISABLE_HASH_CACHE", "").lower() in (
            "1",
            "true",
            "yes",
        )
        if disable_cache:
            db_path = None

        # Phase 1: Parallel hashing
        hashed_tasks: List[Tuple[CopyTask, str]] = []
        cache_entries_to_write: List[Dict[str, Any]] = []
        cache_hits = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_hash_single_file, task, db_path): task
                for task in tasks
            }

            if show_progress:
                iterator = futures_progress(futures, PHASE_PREPROCESS, "Hashing files")
            else:
                from concurrent.futures import as_completed

                iterator = as_completed(futures)

            for future in iterator:
                task, content_hash, cache_hit, cache_entry = future.result()
                hashed_tasks.append((task, content_hash))
                if cache_hit:
                    cache_hits += 1
                elif cache_entry is not None:
                    cache_entries_to_write.append(cache_entry)

        # Batch write cache entries after parallel processing
        if cache_entries_to_write and db_path is not None:
            from common.dedup.database import HashDatabase

            db = HashDatabase(db_path)
            chunk_size = 1000
            for i in range(0, len(cache_entries_to_write), chunk_size):
                chunk = cache_entries_to_write[i : i + chunk_size]
                db.cache_hashes_batch(chunk)

        logger.debug(
            "Hashed %d files (%d cache hits, %d computed)",
            len(tasks),
            cache_hits,
            len(tasks) - cache_hits,
        )

        # Phase 2: Group by content hash and merge contexts
        hash_groups: Dict[str, List[CopyTask]] = {}
        for task, content_hash in hashed_tasks:
            if content_hash not in hash_groups:
                hash_groups[content_hash] = []
            hash_groups[content_hash].append(task)

        # Determine primary for each group and merge contexts
        primaries: Dict[str, CopyTask] = {}
        for content_hash, group in hash_groups.items():
            # First task is primary (first seen wins)
            primary = group[0]
            primaries[content_hash] = primary

            # Store in registry with merged context
            merged_context = dict(primary.context)
            for duplicate in group[1:]:
                if self._context_merger is not None:
                    self._context_merger(merged_context, duplicate.context)

            self._hash_registry[content_hash] = {
                "filename": primary.dest_filename,
                "context": merged_context,
                "source_path": str(primary.source_path),
            }

        # Phase 3: Build results WITHOUT copying files
        # Pre-allocate unique filenames (deterministic, single-threaded)
        results: Dict[Path, HashResult] = {}

        for content_hash, group in hash_groups.items():
            primary = primaries[content_hash]
            unique_filename = self._get_unique_filename(primary.dest_filename)

            # Update registry with actual filename used
            self._hash_registry[content_hash]["filename"] = unique_filename

            # Build results for all tasks in this group
            for i, task in enumerate(group):
                results[task.source_path] = HashResult(
                    source_path=task.source_path,
                    dest_filename=unique_filename,
                    content_hash=content_hash,
                    is_duplicate=(i > 0),  # First is primary, rest are duplicates
                    primary_source=primary.source_path,
                )

        return results

    def register_downloaded_file(
        self,
        file_path: Path,
        dest_filename: str,
        context: Dict[str, Any],
    ) -> CopyResult:
        """Register a file that was already downloaded (Discord pattern).

        Used by preprocessors that download files first, then check for duplicates.
        If duplicate, deletes the file and returns existing filename.
        If unique, keeps the file and registers it.

        Args:
            file_path: Path to the downloaded file.
            dest_filename: Desired destination filename.
            context: Metadata context for merging.

        Returns:
            CopyResult indicating whether file was kept or deduplicated.
        """
        # Get database path for hash caching
        config = get_dedup_config()
        db_path = config.database_path

        # Check if hash caching is disabled
        disable_cache = os.environ.get("DISABLE_HASH_CACHE", "").lower() in (
            "1",
            "true",
            "yes",
        )
        if disable_cache:
            db_path = None

        # Compute hash with caching
        stat = file_path.stat()
        file_size = stat.st_size
        mtime = stat.st_mtime

        content_hash = None

        # Try cache lookup
        if db_path is not None:
            from common.dedup.database import HashDatabase

            db = HashDatabase(db_path)
            cached = db.get_cached_hashes(str(file_path), file_size, mtime)
            if cached is not None:
                content_hash = cached["content_hash"]

        # Compute if not cached
        if content_hash is None:
            content_hash = compute_content_hash(file_path)

            # Cache the hash
            if db_path is not None:
                from common.dedup.database import HashDatabase

                db = HashDatabase(db_path)
                db.cache_hashes(
                    source_path=str(file_path),
                    file_size=file_size,
                    mtime=mtime,
                    content_hash=content_hash,
                )

        # Check for duplicate
        with self.dedup_lock:
            if content_hash in self._hash_registry:
                # Duplicate - delete downloaded file, return existing
                existing = self._hash_registry[content_hash]
                file_path.unlink()

                if self._context_merger is not None:
                    self._context_merger(existing["context"], context)

                return CopyResult(
                    dest_filename=existing["filename"],
                    content_hash=content_hash,
                    is_duplicate=True,
                    primary_source=Path(existing["source_path"]),
                )

            # New unique file - register it
            unique_filename = self._get_unique_filename(dest_filename)
            self._hash_registry[content_hash] = {
                "filename": unique_filename,
                "context": dict(context),
                "source_path": str(file_path),
            }

            return CopyResult(
                dest_filename=unique_filename,
                content_hash=content_hash,
                is_duplicate=False,
                primary_source=file_path,
            )

    def run(self) -> bool:
        """Run the standard preprocessing pipeline (Template Method).

        This method provides the common preprocessing flow:
        1. Log start information
        2. Validate export structure
        3. Detect username (cached for reuse)
        4. Run platform-specific processing
        5. Save log file
        6. Handle failures
        7. Print statistics
        8. Log completion

        Subclasses implement process_export() for platform-specific logic.

        Returns:
            True if preprocessing succeeded, False otherwise
        """
        logger.info("Starting %s Preprocessing", self.processor_name)
        logger.info("Export path: %s", self.export_path)
        logger.info("Output path: %s", self.output_dir)

        if not self.validate_export():
            return False

        # Cache username detection result for use in build_export_info() and subclasses
        detection = self.ensure_detection_result()
        logger.info(
            "Detected username: %s (via %s)",
            detection.username or "unknown",
            detection.source,
        )

        try:
            require_non_unknown_username(
                detection.username,
                source=detection.source,
                export_dir=self.export_path,
                processor=self.processor_name,
            )
        except InvariantViolation as e:
            logger.error(str(e))
            return False

        try:
            if not self.process_export():
                return False
        except Exception as e:
            logger.error("%s preprocessing failed: %s", self.processor_name, e)
            return False

        self.save_log()
        self.failure_tracker.handle_failures(self.final_output_dir)
        self.print_statistics()
        logger.info("Preprocessing complete!")
        return True

    # =========================================================================
    # Standardized Metadata Methods
    # =========================================================================

    @abstractmethod
    def get_content_key(self) -> str:
        """Return the content key for this processor's metadata.json.

        Must return one of: 'conversations', 'media', 'media_files'

        This method is abstract and must be implemented by all preprocessor
        subclasses. The implementation is validated at class definition time.

        Returns:
            Content key string (one of CONTENT_KEYS)
        """

    @staticmethod
    @abstractmethod
    def get_platform() -> str:
        """Return the platform identifier for this preprocessor.

        This is used to disambiguate preprocessed directories from different
        platforms that may share similar structures (e.g., 'conversations' key).

        Examples: "Snapchat", "Discord", "iMessage", "Google", "Instagram"

        Returns:
            Platform identifier string
        """

    @staticmethod
    @abstractmethod
    def get_service() -> Optional[str]:
        """Return the service identifier for this preprocessor.

        For platforms with multiple services (e.g., Snapchat Memories vs Messages),
        this distinguishes between them. For single-service platforms, return None.

        Examples: "messages", "memories", "photos", "chat", None

        Returns:
            Service identifier string, or None for single-service platforms
        """

    def add_unmatched_media(
        self,
        filename: str,
        source_path: Path,
        content_hash: Optional[str] = None,
        timestamp: Optional[str] = None,
        **metadata: Any,
    ) -> None:
        """Track media file that can be processed but lacks full context.

        Unlike FailureTracker.add_orphaned_media (which copies to issues/),
        these files ARE included in metadata.json for processing.

        Args:
            filename: Output filename
            source_path: Absolute path to source file
            content_hash: Optional xxHash64 of file content
            timestamp: Optional ISO format timestamp
            **metadata: Additional metadata fields
        """
        entry: Dict[str, Any] = {
            "filename": filename,
            "source_path": str(source_path),
        }
        if content_hash:
            entry["content_hash"] = content_hash
        if timestamp:
            entry["timestamp"] = timestamp
        if metadata:
            entry["metadata"] = metadata

        self._unmatched_media.append(entry)

    def build_file_hashes(self) -> Dict[str, str]:
        """Build file_hashes lookup from hash registry.

        Returns:
            Dict mapping filename -> content_hash
        """
        file_hashes: Dict[str, str] = {}
        for content_hash, registry_entry in self._hash_registry.items():
            filename = registry_entry.get("filename", "")
            if filename:
                file_hashes[filename] = content_hash
        return file_hashes

    def build_export_info(
        self,
        extra_fields: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build standardized export_info.

        Uses the cached detection result from ensure_detection_result().
        If detection hasn't been performed yet, this will trigger it.

        Includes platform and service fields for unambiguous processor detection
        of preprocessed directories.

        Args:
            extra_fields: Processor-specific fields to add

        Returns:
            export_info dict with required fields
        """
        # Use cached result (ensure_detection_result caches on first call)
        detection_result = self.ensure_detection_result()

        # Note: detection_result.username is guaranteed non-None by invariant
        # enforcement in run() - the hard-fail happens before this code is reached
        info: Dict[str, Any] = {
            "export_path": str(self.export_path),
            "export_name": self.export_path.name,
            "export_username": detection_result.username,
            "username_source": detection_result.source,
            "processed_date": datetime.now().isoformat(),
            # Platform/service markers for unambiguous detection
            "platform": self.get_platform(),
            "service": self.get_service(),
        }

        if extra_fields:
            info.update(extra_fields)

        return info

    def build_metadata(
        self,
        content_data: Any,
        content_key: Optional[str] = None,
        extra_export_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build complete metadata.json structure.

        Args:
            content_data: Data for the content key (conversations/media/media_files)
            content_key: Override content key (uses get_content_key() if None)
            extra_export_info: Processor-specific stats for export_info

        Returns:
            Complete metadata dict ready for validation and saving
        """
        key = content_key or self.get_content_key()
        if key not in CONTENT_KEYS:
            raise ValueError(
                f"Invalid content_key '{key}', must be one of: {CONTENT_KEYS}"
            )

        metadata: Dict[str, Any] = {
            "export_info": self.build_export_info(extra_export_info),
            key: content_data,
            "file_hashes": self.build_file_hashes(),
        }

        # Include unmatched_media if present
        if self._unmatched_media:
            metadata["unmatched_media"] = self._unmatched_media

        # Include ambiguous_cases if present (for processor materialization)
        if self._ambiguous_cases:
            metadata["ambiguous_cases"] = self._ambiguous_cases

        return metadata

    def save_metadata_standardized(
        self,
        content_data: Any,
        content_key: Optional[str] = None,
        extra_export_info: Optional[Dict[str, Any]] = None,
        validate: bool = True,
    ) -> None:
        """Build, validate, and save metadata.json using standardized structure.

        This is the preferred method for saving metadata. It ensures:
        - Required fields are present (export_info, file_hashes)
        - Content key is valid
        - All media entries have source_path for cross-processor dedup
        - unmatched_media is included if present

        Args:
            content_data: Data for the content key
            content_key: Override content key (uses get_content_key() if None)
            extra_export_info: Processor-specific export_info fields
            validate: Whether to validate structure (default True)

        Raises:
            MetadataValidationError: If validation fails
        """
        metadata = self.build_metadata(content_data, content_key, extra_export_info)

        if validate:
            validate_metadata(metadata)

        self.output_dir.mkdir(parents=True, exist_ok=True)

        with open(self.metadata_file, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

        logger.info("Saved metadata to %s", self.metadata_file)

    def save_metadata(
        self,
        content_data: Any,
        *,
        extra_export_info: Optional[Dict[str, Any]] = None,
        validate: bool = True,
    ) -> None:
        """Standardized metadata save wrapper.

        This is the preferred method for saving metadata from preprocessors.
        It wraps save_metadata_standardized() with consistent logging and
        error handling.

        Uses get_content_key() automatically - subclasses just pass content_data.

        Args:
            content_data: Data for the content key (conversations/media/media_files).
            extra_export_info: Optional processor-specific export_info fields.
            validate: Whether to validate structure (default True).

        Raises:
            RuntimeError: If metadata save fails.
        """
        try:
            self.save_metadata_standardized(
                content_data=content_data,
                content_key=None,  # Uses get_content_key()
                extra_export_info=extra_export_info,
                validate=validate,
            )
        except Exception as e:
            logger.error("Failed to save metadata: %s", e)
            raise RuntimeError(f"Failed to save metadata: {e}") from e

    def set_unmatched_media(self, unmatched: List[Dict[str, Any]]) -> None:
        """Set the unmatched media list for inclusion in metadata.json.

        Use this instead of directly modifying _unmatched_media.

        Args:
            unmatched: List of unmatched media entries.
        """
        self._unmatched_media = unmatched

    def set_ambiguous_cases(self, cases: List[Dict[str, Any]]) -> None:
        """Set the ambiguous cases list for inclusion in metadata.json.

        Ambiguous cases (e.g., overlay matching) are recorded during preprocessing
        and materialized into output_dir/issues/needs_matching/ during processing.

        Each case should include source_path for any files that need to be copied.

        Args:
            cases: List of ambiguous case entries with source_path fields.
        """
        self._ambiguous_cases = cases

    @abstractmethod
    def validate_export(self) -> bool:
        """Validate that export directory has required structure.

        Returns:
            True if valid, False otherwise
        """

    @abstractmethod
    def detect_username(self) -> DetectionResult:
        """Detect export username using available sources.

        Returns:
            DetectionResult with username and source
        """

    @abstractmethod
    def process_export(self) -> bool:
        """Run platform-specific preprocessing logic.

        This method should:
        - Perform the main processing (scanning, copying, organizing)
        - Call save_metadata() to write metadata.json

        The template method run() handles:
        - Logging start/end messages
        - Calling validate_export()
        - Calling save_log()
        - Calling failure_tracker.handle_failures()
        - Calling print_statistics()

        Returns:
            True if processing succeeded, False otherwise
        """

    @abstractmethod
    def print_statistics(self) -> None:
        """Print processing statistics."""
