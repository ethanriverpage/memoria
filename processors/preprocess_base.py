#!/usr/bin/env python3
"""
Base class for all media preprocessors.

Provides common initialization, logging, and utility methods that are
shared across all preprocessor implementations.
"""

import logging
import os
import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, Union

from common.dedup.config import get_dedup_config
from common.dedup.hashing import compute_content_hash
from common.detection import DetectionResult
from common.failure_tracker import FailureTracker
from common.files.filter import BannedFilesFilter
from common.files.utils import copy_file
from common.output.progress import PHASE_PREPROCESS, futures_progress
from common.utils import default_worker_count

logger = logging.getLogger(__name__)

# Thread-local storage for database connections (one per thread for reads)
_thread_local = threading.local()


def _get_thread_db(db_path: Optional[Path]):
    """Get or create a thread-local database connection for cache reads.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        HashDatabase instance for this thread, or None if db_path is None.
    """
    if db_path is None:
        return None

    if not hasattr(_thread_local, "db") or _thread_local.db_path != str(db_path):
        from common.dedup.database import HashDatabase

        _thread_local.db = HashDatabase(db_path)
        _thread_local.db_path = str(db_path)

    return _thread_local.db


@dataclass
class CopyTask:
    """Task for copy_with_dedup() representing a file to copy.

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
    """Result of a copy_with_dedup() operation for a single file.

    Attributes:
        dest_filename: Actual filename in output directory.
        content_hash: xxHash64 of file content.
        is_duplicate: True if this was a duplicate (not copied).
        primary_source: Source path of the file that was actually copied.
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


def _copy_single_file(src_path: Path, dest_path: Path) -> None:
    """Copy a single file with reflink optimization on supported filesystems.

    Args:
        src_path: Source file path.
        dest_path: Destination file path.
    """
    copy_file(src_path, dest_path, preserve_metadata=True)


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
    db = _get_thread_db(db_path)
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
    - File hashing for deduplication
    - Failure tracking
    - Batch copy with deduplication

    Subclasses must implement:
    - validate_export(): Validate export directory structure
    - detect_username(): Detect export owner username
    - process_export(): Platform-specific processing logic
    - print_statistics(): Display processing statistics

    The run() method provides a standardized preprocessing pipeline that calls
    these methods in the correct order.
    """

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
        self.media_output_dir = output_base / "media"
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

        # Statistics - subclasses populate with specific keys
        self.stats: Dict[str, Any] = {}

        # Log entries
        self.log_entries = []

        # Deduplication state
        self._context_merger: Optional[Callable[[Dict, Dict], None]] = None
        self._hash_registry: Dict[str, Dict[str, Any]] = {}
        self._used_filenames: Dict[str, int] = {}

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

        Args:
            merger: Function with signature (existing: Dict, new: Dict) -> None
                    that modifies existing in-place to merge data from new.
                    Pass None for first-occurrence-wins behavior (no merging).
        """
        self._context_merger = merger

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

    def copy_with_dedup(
        self,
        tasks: List[CopyTask],
        workers: Optional[int] = None,
        show_progress: bool = True,
    ) -> Dict[Path, CopyResult]:
        """Parallel batch copy with deduplication.

        Hashes all files in parallel, groups by content hash to find duplicates,
        merges context for duplicate groups, and copies only one file per hash.

        Args:
            tasks: List of CopyTask objects describing files to copy.
            workers: Number of parallel workers (default: self.workers).
            show_progress: Whether to show progress bar.

        Returns:
            Dict mapping source_path to CopyResult for all input tasks.
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
                iterator = futures_progress(
                    futures, PHASE_PREPROCESS, "Hashing files", unit="file"
                )
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

        # Phase 3: Copy unique files and build results
        results: Dict[Path, CopyResult] = {}

        # Ensure output directory exists
        self.media_output_dir.mkdir(parents=True, exist_ok=True)

        # Pre-allocate unique filenames (deterministic, single-threaded)
        # This keeps filename generation stable regardless of copy order
        copy_plan: List[Tuple[str, Path, Path, str, List[CopyTask]]] = []
        for content_hash, group in hash_groups.items():
            primary = primaries[content_hash]
            unique_filename = self._get_unique_filename(primary.dest_filename)
            dest_path = self.media_output_dir / unique_filename

            # Update registry with actual filename used
            self._hash_registry[content_hash]["filename"] = unique_filename

            copy_plan.append(
                (content_hash, primary.source_path, dest_path, unique_filename, group)
            )

        # Parallel copy execution (I/O bound)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_copy_single_file, src_path, dest_path): (
                    content_hash,
                    src_path,
                    dest_path,
                    unique_filename,
                    group,
                )
                for content_hash, src_path, dest_path, unique_filename, group in copy_plan
            }

            if show_progress:
                iterator = futures_progress(
                    futures, PHASE_PREPROCESS, "Copying files", unit="file"
                )
            else:
                from concurrent.futures import as_completed

                iterator = as_completed(futures)

            for future in iterator:
                # Re-raise any exception from the copy operation
                future.result()

                # Retrieve plan details for this future
                (
                    content_hash,
                    src_path,
                    dest_path,
                    unique_filename,
                    group,
                ) = futures[future]

                # Build results for all tasks in this group
                for i, task in enumerate(group):
                    results[task.source_path] = CopyResult(
                        dest_filename=unique_filename,
                        content_hash=content_hash,
                        is_duplicate=(i > 0),  # First is primary, rest are duplicates
                        primary_source=primaries[content_hash].source_path,
                    )

        return results

    def hash_with_dedup(
        self,
        tasks: List[CopyTask],
        workers: Optional[int] = None,
        show_progress: bool = True,
    ) -> Dict[Path, HashResult]:
        """Parallel batch hashing with deduplication (no file copying).

        Hashes all files in parallel, groups by content hash to find duplicates,
        and merges context for duplicate groups. Unlike copy_with_dedup(), this
        method does NOT copy files - it only builds metadata with source paths.

        The processor phase will copy directly from source_path to final output,
        eliminating the intermediate temp/media/ copy.

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
                iterator = futures_progress(
                    futures, PHASE_PREPROCESS, "Hashing files", unit="file"
                )
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
        3. Run platform-specific processing
        4. Save log file
        5. Handle failures
        6. Print statistics
        7. Log completion

        Subclasses implement process_export() for platform-specific logic.

        Returns:
            True if preprocessing succeeded, False otherwise
        """
        logger.info("Starting %s Preprocessing", self.processor_name)
        logger.info("Export path: %s", self.export_path)
        logger.info("Output path: %s", self.output_dir)

        if not self.validate_export():
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
