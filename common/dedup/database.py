#!/usr/bin/env python3
"""
Hash Database for Cross-Processor Deduplication

SQLite persistence layer for storing file hashes and metadata across
processing runs. Supports both exact (xxHash64) and perceptual (dhash)
hash matching.
"""

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class HashDatabaseProtocol(Protocol):
    """Protocol defining the interface for hash database operations.

    This protocol enables:
    - Easy mocking for unit tests
    - Alternative implementations (e.g., PostgreSQL, in-memory)
    - Clear contract for database operations
    """

    @property
    def db_path(self) -> Path:
        """Path to the database file."""
        ...

    def register(
        self,
        content_hash: str,
        processor: str,
        source_path: str,
        perceptual_hash: Optional[str] = None,
        file_size: Optional[int] = None,
        dimensions: Optional[tuple] = None,
        metadata: Optional[Dict[str, Any]] = None,
        is_primary: bool = False,
    ) -> bool:
        """Register a file in the database."""
        ...

    def get_duplicates(self, content_hash: str) -> List[Dict[str, Any]]:
        """Get all files with the same content hash."""
        ...

    def get_similar(
        self,
        perceptual_hash: str,
        threshold: float = 0.92,
    ) -> List[Dict[str, Any]]:
        """Get files with similar perceptual hashes."""
        ...

    def get_by_processor(self, processor: str) -> List[Dict[str, Any]]:
        """Get all files from a specific processor."""
        ...

    def get_all_content_hashes(self) -> List[str]:
        """Get all unique content hashes in the database."""
        ...

    def set_primary(self, content_hash: str, source_path: str) -> bool:
        """Set a file as the primary copy for its content hash."""
        ...

    def update_metadata(
        self,
        content_hash: str,
        source_path: str,
        metadata: Dict[str, Any],
    ) -> bool:
        """Update metadata for a specific file."""
        ...

    def get_statistics(self) -> Dict[str, Any]:
        """Get database statistics."""
        ...

    def clear(self) -> None:
        """Clear all data from the database."""
        ...

    def clear_processor(self, processor: str) -> int:
        """Clear all entries for a specific processor."""
        ...

    def write_catalog_entry(
        self,
        run_id: str,
        export_name: str,
        entry: Dict[str, Any],
    ) -> bool:
        """Write a single catalog entry to the database."""
        ...

    def write_catalog_entries_batch(
        self,
        run_id: str,
        export_name: str,
        entries: List[Dict[str, Any]],
    ) -> int:
        """Write multiple catalog entries in a single transaction."""
        ...

    def get_duplicate_content_hashes(self, run_id: str) -> List[str]:
        """Get content hashes that appear more than once in a run."""
        ...

    def get_entries_by_hash(
        self,
        run_id: str,
        content_hash: str,
    ) -> List[Dict[str, Any]]:
        """Get all catalog entries with a specific content hash."""
        ...

    def iter_all_entries(
        self,
        run_id: str,
        batch_size: int = 1000,
    ) -> Iterator[Dict[str, Any]]:
        """Iterate over all catalog entries for a run."""
        ...

    def iter_entries_with_perceptual_hash(
        self,
        run_id: str,
        exclude_hashes: Optional[set] = None,
        batch_size: int = 1000,
    ) -> Iterator[Dict[str, Any]]:
        """Iterate over entries with perceptual hashes."""
        ...

    def get_run_statistics(self, run_id: str) -> Dict[str, Any]:
        """Get statistics for a specific processing run."""
        ...

    def clear_run(self, run_id: str) -> int:
        """Clear all catalog entries for a specific run."""
        ...

    def get_cached_hashes(
        self,
        source_path: str,
        file_size: int,
        mtime: float,
    ) -> Optional[Dict[str, Any]]:
        """Look up cached hashes for a file."""
        ...

    def cache_hashes(
        self,
        source_path: str,
        file_size: int,
        mtime: float,
        content_hash: str,
        perceptual_hash: Optional[str] = None,
        dimensions: Optional[tuple] = None,
    ) -> bool:
        """Store computed hashes in the cache."""
        ...

    def cache_hashes_batch(
        self,
        entries: List[Dict[str, Any]],
    ) -> int:
        """Store multiple hash entries in the cache."""
        ...

    def get_hash_cache_statistics(self) -> Dict[str, Any]:
        """Get statistics about the hash cache."""
        ...

    def clear_hash_cache(self) -> int:
        """Clear all entries from the hash cache."""
        ...

    def prune_hash_cache(self, valid_paths: Optional[set] = None) -> int:
        """Remove cache entries for files that no longer exist."""
        ...

    def close(self) -> None:
        """Close the database."""
        ...

    def __enter__(self) -> "HashDatabaseProtocol":
        """Enter context manager."""
        ...

    def __exit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        """Exit context manager."""
        ...


logger = logging.getLogger(__name__)

# Thread-local storage for database connections (one per thread for reads)
_thread_local = threading.local()


def get_thread_db(db_path: Optional[Path]) -> Optional[HashDatabaseProtocol]:
    """Get or create a thread-local database connection for cache reads.

    This is useful for parallel processing where each worker thread needs
    its own database connection for thread-safe reads.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        HashDatabase instance for this thread, or None if db_path is None.
    """
    if db_path is None:
        return None

    if not hasattr(_thread_local, "db") or _thread_local.db_path != str(db_path):
        _thread_local.db = HashDatabase(db_path)
        _thread_local.db_path = str(db_path)

    return _thread_local.db


class HashDatabase:
    """SQLite-based hash storage for cross-processor deduplication.

    Stores content hashes, perceptual hashes, and metadata for media files
    to enable duplicate detection across different export sources and
    processing runs.

    Attributes:
        db_path: Path to the SQLite database file.
    """

    # Database schema version for migrations
    SCHEMA_VERSION = 1

    def __init__(self, db_path: Optional[Path] = None):
        """Initialize the hash database.

        Args:
            db_path: Path to SQLite database file. Defaults to ~/.memoria/dedup.db
        """
        if db_path is None:
            db_path = Path.home() / ".memoria" / "dedup.db"

        self.db_path = Path(db_path).expanduser()
        self._ensure_directory()
        self._init_database()

    def _ensure_directory(self) -> None:
        """Create parent directory if it doesn't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _get_connection(self):
        """Context manager for database connections.

        Yields:
            sqlite3.Connection with row factory set to sqlite3.Row
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_database(self) -> None:
        """Initialize database schema if not exists."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Enable WAL mode for better concurrent read/write performance
            # WAL provides:
            # - Readers don't block writers and writers don't block readers
            # - Better crash recovery
            # - Faster writes for most workloads
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute(
                "PRAGMA synchronous=NORMAL"
            )  # Safe with WAL, faster than FULL
            cursor.execute("PRAGMA cache_size=-64000")  # 64MB cache (negative = KB)
            cursor.execute("PRAGMA temp_store=MEMORY")  # Store temp tables in memory
            cursor.execute("PRAGMA mmap_size=268435456")  # 256MB memory-mapped I/O

            # Create files table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id INTEGER PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    perceptual_hash TEXT,
                    processor TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    is_primary BOOLEAN DEFAULT FALSE,
                    file_size INTEGER,
                    dimensions TEXT,
                    metadata JSON,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(content_hash, source_path)
                )
            """
            )

            # Create indexes for fast lookups
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_content_hash 
                ON files(content_hash)
            """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_perceptual_hash 
                ON files(perceptual_hash)
            """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_processor 
                ON files(processor)
            """
            )

            # Create catalog_entries table for streaming catalog writes
            # This table stores catalog entries during a processing run,
            # enabling memory-efficient deduplication across large export sets
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS catalog_entries (
                    id INTEGER PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    export_name TEXT NOT NULL,
                    processor TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    relative_path TEXT,
                    content_hash TEXT NOT NULL,
                    perceptual_hash TEXT,
                    file_size INTEGER,
                    dimensions TEXT,
                    timestamp TEXT,
                    metadata JSON,
                    UNIQUE(run_id, source_path)
                )
            """
            )

            # Create indexes for catalog queries
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_catalog_run_hash 
                ON catalog_entries(run_id, content_hash)
            """
            )
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_catalog_run 
                ON catalog_entries(run_id)
            """
            )

            # Create hash_cache table for caching computed hashes across runs
            # Cache key is (source_path, file_size, mtime) to detect file changes
            # This avoids recomputing expensive hashes for unchanged files
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS hash_cache (
                    id INTEGER PRIMARY KEY,
                    source_path TEXT NOT NULL UNIQUE,
                    file_size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    content_hash TEXT NOT NULL,
                    perceptual_hash TEXT,
                    dimensions TEXT,
                    cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """
            )

            # Create index for fast cache lookups by path
            cursor.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_hash_cache_path 
                ON hash_cache(source_path)
            """
            )

            # Create schema version table
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY
                )
            """
            )

            # Insert schema version if not present
            cursor.execute("SELECT version FROM schema_version LIMIT 1")
            if cursor.fetchone() is None:
                cursor.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (self.SCHEMA_VERSION,),
                )

            conn.commit()

    def register(
        self,
        content_hash: str,
        processor: str,
        source_path: str,
        perceptual_hash: Optional[str] = None,
        file_size: Optional[int] = None,
        dimensions: Optional[tuple] = None,
        metadata: Optional[Dict[str, Any]] = None,
        is_primary: bool = False,
    ) -> bool:
        """Register a file in the database.

        Args:
            content_hash: xxHash64 hex digest of file content.
            processor: Name of the processor that found this file.
            source_path: Path to the source file.
            perceptual_hash: Optional dhash hex string for images/videos.
            file_size: Optional file size in bytes.
            dimensions: Optional (width, height) tuple.
            metadata: Optional dict of additional metadata.
            is_primary: Whether this is the primary copy for deduplication.

        Returns:
            True if registered successfully, False if duplicate entry exists.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Serialize dimensions as JSON string
            dimensions_str = json.dumps(dimensions) if dimensions else None
            metadata_str = json.dumps(metadata) if metadata else None

            try:
                cursor.execute(
                    """
                    INSERT INTO files (
                        content_hash, perceptual_hash, processor, source_path,
                        is_primary, file_size, dimensions, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        content_hash,
                        perceptual_hash,
                        processor,
                        source_path,
                        is_primary,
                        file_size,
                        dimensions_str,
                        metadata_str,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.IntegrityError:
                # Duplicate entry (same content_hash + source_path)
                # Note: Caller should track duplicate counts if needed
                return False

    def get_duplicates(self, content_hash: str) -> List[Dict[str, Any]]:
        """Get all files with the same content hash.

        Args:
            content_hash: xxHash64 hex digest to search for.

        Returns:
            List of dicts with file information.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, content_hash, perceptual_hash, processor, source_path,
                       is_primary, file_size, dimensions, metadata, first_seen
                FROM files
                WHERE content_hash = ?
                """,
                (content_hash,),
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_similar(
        self,
        perceptual_hash: str,
        threshold: float = 0.92,
    ) -> List[Dict[str, Any]]:
        """Get files with similar perceptual hashes.

        Uses Hamming distance to find perceptually similar images.
        Threshold of 0.92 means at least 92% of bits must match.

        Args:
            perceptual_hash: dhash hex string to compare against.
            threshold: Minimum similarity ratio (0.0-1.0).

        Returns:
            List of dicts with file information and similarity scores.
        """
        if not perceptual_hash:
            return []

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Get all files with perceptual hashes
            cursor.execute(
                """
                SELECT id, content_hash, perceptual_hash, processor, source_path,
                       is_primary, file_size, dimensions, metadata, first_seen
                FROM files
                WHERE perceptual_hash IS NOT NULL
                """
            )

            similar = []
            query_bits = self._hex_to_bits(perceptual_hash)
            query_len = len(query_bits)

            for row in cursor.fetchall():
                row_hash = row["perceptual_hash"]
                if not row_hash:
                    continue

                row_bits = self._hex_to_bits(row_hash)

                # Calculate Hamming similarity
                if len(row_bits) != query_len:
                    continue

                matching_bits = sum(a == b for a, b in zip(query_bits, row_bits))
                similarity = matching_bits / query_len

                if similarity >= threshold:
                    result = self._row_to_dict(row)
                    result["similarity"] = similarity
                    similar.append(result)

            # Sort by similarity descending
            similar.sort(key=lambda x: x["similarity"], reverse=True)
            return similar

    def get_by_processor(self, processor: str) -> List[Dict[str, Any]]:
        """Get all files from a specific processor.

        Args:
            processor: Processor name to filter by.

        Returns:
            List of dicts with file information.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, content_hash, perceptual_hash, processor, source_path,
                       is_primary, file_size, dimensions, metadata, first_seen
                FROM files
                WHERE processor = ?
                """,
                (processor,),
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_all_content_hashes(self) -> List[str]:
        """Get all unique content hashes in the database.

        Returns:
            List of unique content hash strings.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT content_hash FROM files")
            return [row[0] for row in cursor.fetchall()]

    def set_primary(self, content_hash: str, source_path: str) -> bool:
        """Set a file as the primary copy for its content hash.

        Clears primary flag from other files with same content hash.

        Args:
            content_hash: Content hash of the duplicate group.
            source_path: Source path of the file to mark as primary.

        Returns:
            True if updated successfully, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Clear existing primary flags for this content hash
            cursor.execute(
                "UPDATE files SET is_primary = FALSE WHERE content_hash = ?",
                (content_hash,),
            )

            # Set the specified file as primary
            cursor.execute(
                """
                UPDATE files SET is_primary = TRUE 
                WHERE content_hash = ? AND source_path = ?
                """,
                (content_hash, source_path),
            )
            conn.commit()
            return cursor.rowcount > 0

    def update_metadata(
        self,
        content_hash: str,
        source_path: str,
        metadata: Dict[str, Any],
    ) -> bool:
        """Update metadata for a specific file.

        Args:
            content_hash: Content hash of the file.
            source_path: Source path of the file.
            metadata: New metadata dict to store.

        Returns:
            True if updated successfully, False otherwise.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE files SET metadata = ?
                WHERE content_hash = ? AND source_path = ?
                """,
                (json.dumps(metadata), content_hash, source_path),
            )
            conn.commit()
            return cursor.rowcount > 0

    def get_statistics(self) -> Dict[str, Any]:
        """Get database statistics.

        Returns:
            Dict with total files, unique hashes, processors, etc.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM files")
            total_files = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT content_hash) FROM files")
            unique_hashes = cursor.fetchone()[0]

            cursor.execute("SELECT DISTINCT processor FROM files")
            processors = [row[0] for row in cursor.fetchall()]

            cursor.execute(
                "SELECT COUNT(*) FROM files WHERE perceptual_hash IS NOT NULL"
            )
            with_perceptual = cursor.fetchone()[0]

            # Count duplicates (hashes with more than one file)
            cursor.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT content_hash FROM files 
                    GROUP BY content_hash HAVING COUNT(*) > 1
                )
                """
            )
            duplicate_groups = cursor.fetchone()[0]

            return {
                "total_files": total_files,
                "unique_content_hashes": unique_hashes,
                "duplicate_groups": duplicate_groups,
                "files_with_perceptual_hash": with_perceptual,
                "processors": processors,
            }

    def clear(self) -> None:
        """Clear all data from the database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM files")
            conn.commit()
            logger.info("Hash database cleared")

    def clear_processor(self, processor: str) -> int:
        """Clear all entries for a specific processor.

        Args:
            processor: Processor name to clear.

        Returns:
            Number of rows deleted.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM files WHERE processor = ?", (processor,))
            conn.commit()
            deleted = cursor.rowcount
            logger.info(f"Cleared {deleted} entries for processor: {processor}")
            return deleted

    # =========================================================================
    # Catalog Entry Methods (Streaming Architecture)
    # =========================================================================

    def write_catalog_entry(
        self,
        run_id: str,
        export_name: str,
        entry: Dict[str, Any],
    ) -> bool:
        """Write a single catalog entry to the database.

        Called from worker processes during streaming catalog building.
        Uses INSERT OR REPLACE to handle duplicate source paths.

        Args:
            run_id: Unique identifier for this processing run.
            export_name: Name of the export directory.
            entry: Catalog entry dict with file information.

        Returns:
            True if written successfully, False on error.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Extract and serialize fields
            dimensions = entry.get("dimensions")
            dimensions_str = json.dumps(dimensions) if dimensions else None
            metadata = entry.get("metadata")
            metadata_str = json.dumps(metadata) if metadata else None

            try:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO catalog_entries (
                        run_id, export_name, processor, source_path, relative_path,
                        content_hash, perceptual_hash, file_size, dimensions,
                        timestamp, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        export_name,
                        entry.get("_processor", "Unknown"),
                        entry.get("source_path", ""),
                        entry.get("relative_path", ""),
                        entry.get("content_hash", ""),
                        entry.get("perceptual_hash"),
                        entry.get("file_size"),
                        dimensions_str,
                        entry.get("timestamp"),
                        metadata_str,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                logger.error(f"Failed to write catalog entry: {e}")
                return False

    def write_catalog_entries_batch(
        self,
        run_id: str,
        export_name: str,
        entries: List[Dict[str, Any]],
    ) -> int:
        """Write multiple catalog entries in a single transaction.

        More efficient than individual writes for bulk operations.

        Args:
            run_id: Unique identifier for this processing run.
            export_name: Name of the export directory.
            entries: List of catalog entry dicts.

        Returns:
            Number of entries written successfully.
        """
        if not entries:
            return 0

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Build all rows upfront for executemany
            rows = []
            for entry in entries:
                dimensions = entry.get("dimensions")
                dimensions_str = json.dumps(dimensions) if dimensions else None
                metadata = entry.get("metadata")
                metadata_str = json.dumps(metadata) if metadata else None

                rows.append(
                    (
                        run_id,
                        export_name,
                        entry.get("_processor", "Unknown"),
                        entry.get("source_path", ""),
                        entry.get("relative_path", ""),
                        entry.get("content_hash", ""),
                        entry.get("perceptual_hash"),
                        entry.get("file_size"),
                        dimensions_str,
                        entry.get("timestamp"),
                        metadata_str,
                    )
                )

            try:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO catalog_entries (
                        run_id, export_name, processor, source_path, relative_path,
                        content_hash, perceptual_hash, file_size, dimensions,
                        timestamp, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.commit()
                return len(rows)
            except sqlite3.Error as e:
                logger.error(f"Failed to write catalog entries batch: {e}")
                conn.rollback()
                return 0

    def get_duplicate_content_hashes(self, run_id: str) -> List[str]:
        """Get content hashes that appear more than once in a run.

        Uses SQL GROUP BY for memory-efficient duplicate detection.

        Args:
            run_id: Processing run identifier.

        Returns:
            List of content hash strings with duplicates.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT content_hash FROM catalog_entries
                WHERE run_id = ?
                GROUP BY content_hash
                HAVING COUNT(*) > 1
                """,
                (run_id,),
            )
            return [row[0] for row in cursor.fetchall()]

    def get_entries_by_hash(
        self,
        run_id: str,
        content_hash: str,
    ) -> List[Dict[str, Any]]:
        """Get all catalog entries with a specific content hash.

        Args:
            run_id: Processing run identifier.
            content_hash: Content hash to search for.

        Returns:
            List of catalog entry dicts.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, run_id, export_name, processor, source_path, relative_path,
                       content_hash, perceptual_hash, file_size, dimensions,
                       timestamp, metadata
                FROM catalog_entries
                WHERE run_id = ? AND content_hash = ?
                """,
                (run_id, content_hash),
            )
            return [self._catalog_row_to_dict(row) for row in cursor.fetchall()]

    def iter_all_entries(
        self,
        run_id: str,
        batch_size: int = 1000,
    ) -> Iterator[Dict[str, Any]]:
        """Iterate over all catalog entries for a run.

        Memory-efficient generator that fetches entries in batches.

        Args:
            run_id: Processing run identifier.
            batch_size: Number of entries to fetch per batch.

        Yields:
            Catalog entry dicts one at a time.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, run_id, export_name, processor, source_path, relative_path,
                       content_hash, perceptual_hash, file_size, dimensions,
                       timestamp, metadata
                FROM catalog_entries
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            )

            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    yield self._catalog_row_to_dict(row)

    def iter_entries_with_perceptual_hash(
        self,
        run_id: str,
        exclude_hashes: Optional[set] = None,
        batch_size: int = 1000,
    ) -> Iterator[Dict[str, Any]]:
        """Iterate over entries with perceptual hashes, optionally excluding some content hashes.

        Used for perceptual duplicate detection after exact hash grouping.

        Args:
            run_id: Processing run identifier.
            exclude_hashes: Set of content hashes to exclude (already grouped).
            batch_size: Number of entries to fetch per batch.

        Yields:
            Catalog entry dicts with perceptual hashes.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            if exclude_hashes:
                # Build placeholders for IN clause - this is safe as placeholders
                # are generated programmatically (not from user input) and values
                # are passed as parameters
                placeholders = ",".join("?" * len(exclude_hashes))
                query = f"""
                    SELECT id, run_id, export_name, processor, source_path, relative_path,
                           content_hash, perceptual_hash, file_size, dimensions,
                           timestamp, metadata
                    FROM catalog_entries
                    WHERE run_id = ? 
                      AND perceptual_hash IS NOT NULL
                      AND content_hash NOT IN ({placeholders})
                    ORDER BY id
                    """  # nosec B608 - placeholders are safe (generated ? marks)
                cursor.execute(query, (run_id, *exclude_hashes))
            else:
                cursor.execute(
                    """
                    SELECT id, run_id, export_name, processor, source_path, relative_path,
                           content_hash, perceptual_hash, file_size, dimensions,
                           timestamp, metadata
                    FROM catalog_entries
                    WHERE run_id = ? AND perceptual_hash IS NOT NULL
                    ORDER BY id
                    """,
                    (run_id,),
                )

            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                for row in rows:
                    yield self._catalog_row_to_dict(row)

    def get_run_statistics(self, run_id: str) -> Dict[str, Any]:
        """Get statistics for a specific processing run.

        Args:
            run_id: Processing run identifier.

        Returns:
            Dict with total files, unique hashes, duplicate groups, etc.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT COUNT(*) FROM catalog_entries WHERE run_id = ?",
                (run_id,),
            )
            total_files = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(DISTINCT content_hash) FROM catalog_entries WHERE run_id = ?",
                (run_id,),
            )
            unique_hashes = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT content_hash FROM catalog_entries
                    WHERE run_id = ?
                    GROUP BY content_hash HAVING COUNT(*) > 1
                )
                """,
                (run_id,),
            )
            duplicate_groups = cursor.fetchone()[0]

            cursor.execute(
                """
                SELECT COUNT(*) FROM catalog_entries 
                WHERE run_id = ? AND perceptual_hash IS NOT NULL
                """,
                (run_id,),
            )
            with_perceptual = cursor.fetchone()[0]

            cursor.execute(
                "SELECT DISTINCT processor FROM catalog_entries WHERE run_id = ?",
                (run_id,),
            )
            processors = [row[0] for row in cursor.fetchall()]

            cursor.execute(
                "SELECT DISTINCT export_name FROM catalog_entries WHERE run_id = ?",
                (run_id,),
            )
            exports = [row[0] for row in cursor.fetchall()]

            return {
                "run_id": run_id,
                "total_files": total_files,
                "unique_content_hashes": unique_hashes,
                "duplicate_groups": duplicate_groups,
                "files_with_perceptual_hash": with_perceptual,
                "processors": processors,
                "exports": exports,
            }

    def clear_run(self, run_id: str) -> int:
        """Clear all catalog entries for a specific run.

        Args:
            run_id: Processing run identifier to clear.

        Returns:
            Number of entries deleted.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "DELETE FROM catalog_entries WHERE run_id = ?",
                (run_id,),
            )
            conn.commit()
            deleted = cursor.rowcount
            logger.debug(f"Cleared {deleted} catalog entries for run: {run_id}")
            return deleted

    def _catalog_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a catalog_entries row to a dictionary.

        Args:
            row: sqlite3.Row object from catalog_entries table.

        Returns:
            Dict with catalog entry information, formatted for dedup service.
        """
        result = {
            "source_path": row["source_path"],
            "relative_path": row["relative_path"],
            "content_hash": row["content_hash"],
            "perceptual_hash": row["perceptual_hash"],
            "file_size": row["file_size"],
            "timestamp": row["timestamp"],
            "_processor": row["processor"],
            "_export_name": row["export_name"],
        }

        # Parse JSON fields
        if row["dimensions"]:
            result["dimensions"] = json.loads(row["dimensions"])
        else:
            result["dimensions"] = None

        if row["metadata"]:
            result["metadata"] = json.loads(row["metadata"])
        else:
            result["metadata"] = {}

        # Extract live_photo_video from metadata if present (for Live Photo dedup)
        if result["metadata"].get("live_photo_video"):
            result["live_photo_video"] = result["metadata"]["live_photo_video"]

        return result

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a database row to a dictionary.

        Args:
            row: sqlite3.Row object.

        Returns:
            Dict with file information.
        """
        result = dict(row)

        # Parse JSON fields
        if result.get("dimensions"):
            result["dimensions"] = json.loads(result["dimensions"])
        if result.get("metadata"):
            result["metadata"] = json.loads(result["metadata"])

        return result

    # =========================================================================
    # Hash Cache Methods (Caching for Repeated Runs)
    # =========================================================================

    def get_cached_hashes(
        self,
        source_path: str,
        file_size: int,
        mtime: float,
    ) -> Optional[Dict[str, Any]]:
        """Look up cached hashes for a file.

        Returns cached hashes only if the file hasn't changed (same size and mtime).
        This avoids recomputing expensive hashes for unchanged files.

        Args:
            source_path: Absolute path to the file.
            file_size: Current file size in bytes.
            mtime: Current file modification time (from os.stat().st_mtime).

        Returns:
            Dict with cached hash data if found and valid, None otherwise.
            Structure: {
                "content_hash": str,
                "perceptual_hash": str | None,
                "dimensions": tuple | None,
            }
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT content_hash, perceptual_hash, dimensions
                FROM hash_cache
                WHERE source_path = ? AND file_size = ? AND mtime = ?
                """,
                (source_path, file_size, mtime),
            )
            row = cursor.fetchone()

            if row is None:
                return None

            result = {
                "content_hash": row["content_hash"],
                "perceptual_hash": row["perceptual_hash"],
                "dimensions": None,
            }

            if row["dimensions"]:
                result["dimensions"] = json.loads(row["dimensions"])

            return result

    def cache_hashes(
        self,
        source_path: str,
        file_size: int,
        mtime: float,
        content_hash: str,
        perceptual_hash: Optional[str] = None,
        dimensions: Optional[tuple] = None,
    ) -> bool:
        """Store computed hashes in the cache.

        Uses INSERT OR REPLACE to update existing entries when file changes.

        Args:
            source_path: Absolute path to the file.
            file_size: File size in bytes.
            mtime: File modification time (from os.stat().st_mtime).
            content_hash: Computed xxHash64 hex digest.
            perceptual_hash: Optional computed perceptual hash.
            dimensions: Optional (width, height) tuple.

        Returns:
            True if cached successfully, False on error.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            dimensions_str = json.dumps(dimensions) if dimensions else None

            try:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO hash_cache (
                        source_path, file_size, mtime, content_hash,
                        perceptual_hash, dimensions, cached_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (
                        source_path,
                        file_size,
                        mtime,
                        content_hash,
                        perceptual_hash,
                        dimensions_str,
                    ),
                )
                conn.commit()
                return True
            except sqlite3.Error as e:
                logger.error(f"Failed to cache hashes for {source_path}: {e}")
                return False

    def cache_hashes_batch(
        self,
        entries: List[Dict[str, Any]],
    ) -> int:
        """Store multiple hash entries in the cache in a single transaction.

        More efficient than individual cache_hashes() calls for bulk operations.

        Args:
            entries: List of dicts with keys:
                - source_path: str
                - file_size: int
                - mtime: float
                - content_hash: str
                - perceptual_hash: str | None
                - dimensions: tuple | None

        Returns:
            Number of entries cached successfully.
        """
        if not entries:
            return 0

        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Build all rows upfront for executemany
            rows = []
            for entry in entries:
                dimensions = entry.get("dimensions")
                dimensions_str = json.dumps(dimensions) if dimensions else None

                rows.append(
                    (
                        entry["source_path"],
                        entry["file_size"],
                        entry["mtime"],
                        entry["content_hash"],
                        entry.get("perceptual_hash"),
                        dimensions_str,
                    )
                )

            try:
                cursor.executemany(
                    """
                    INSERT OR REPLACE INTO hash_cache (
                        source_path, file_size, mtime, content_hash,
                        perceptual_hash, dimensions, cached_at
                    ) VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    rows,
                )
                conn.commit()
                return len(rows)
            except sqlite3.Error as e:
                logger.error(f"Failed to cache hashes batch: {e}")
                conn.rollback()
                return 0

    def get_hash_cache_statistics(self) -> Dict[str, Any]:
        """Get statistics about the hash cache.

        Returns:
            Dict with cache statistics:
            - total_entries: Total cached files
            - with_perceptual: Files with perceptual hashes
            - cache_size_bytes: Approximate database size for cache
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM hash_cache")
            total_entries = cursor.fetchone()[0]

            cursor.execute(
                "SELECT COUNT(*) FROM hash_cache WHERE perceptual_hash IS NOT NULL"
            )
            with_perceptual = cursor.fetchone()[0]

            return {
                "total_entries": total_entries,
                "with_perceptual_hash": with_perceptual,
            }

    def clear_hash_cache(self) -> int:
        """Clear all entries from the hash cache.

        Returns:
            Number of entries deleted.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM hash_cache")
            conn.commit()
            deleted = cursor.rowcount
            logger.info(f"Cleared {deleted} entries from hash cache")
            return deleted

    def prune_hash_cache(self, valid_paths: Optional[set] = None) -> int:
        """Remove cache entries for files that no longer exist.

        Args:
            valid_paths: Optional set of valid paths to keep. If None,
                         checks filesystem for each cached path.

        Returns:
            Number of stale entries removed.
        """
        import os

        with self._get_connection() as conn:
            cursor = conn.cursor()

            if valid_paths is not None:
                # Remove entries not in valid_paths set
                cursor.execute("SELECT id, source_path FROM hash_cache")
                to_delete = []
                for row in cursor.fetchall():
                    if row["source_path"] not in valid_paths:
                        to_delete.append(row["id"])

                if to_delete:
                    # placeholders are safe - generated "?" marks for parameterized query
                    placeholders = ",".join("?" * len(to_delete))
                    cursor.execute(
                        f"DELETE FROM hash_cache WHERE id IN ({placeholders})",  # nosec B608
                        to_delete,
                    )
                    conn.commit()
                    return len(to_delete)
                return 0
            else:
                # Check filesystem for each path
                cursor.execute("SELECT id, source_path FROM hash_cache")
                to_delete = []
                for row in cursor.fetchall():
                    if not os.path.exists(row["source_path"]):
                        to_delete.append(row["id"])

                if to_delete:
                    # placeholders are safe - generated "?" marks for parameterized query
                    placeholders = ",".join("?" * len(to_delete))
                    cursor.execute(
                        f"DELETE FROM hash_cache WHERE id IN ({placeholders})",  # nosec B608
                        to_delete,
                    )
                    conn.commit()
                    logger.info(f"Pruned {len(to_delete)} stale cache entries")
                    return len(to_delete)
                return 0

    @staticmethod
    def _hex_to_bits(hex_string: str) -> str:
        """Convert hex string to binary string.

        Args:
            hex_string: Hexadecimal string.

        Returns:
            Binary string representation.
        """
        return bin(int(hex_string, 16))[2:].zfill(len(hex_string) * 4)

    # =========================================================================
    # Context Manager Protocol
    # =========================================================================

    def __enter__(self) -> "HashDatabase":
        """Enter context manager."""
        return self

    def __exit__(self, _exc_type, _exc_val, _exc_tb) -> None:
        """Exit context manager.

        Currently a no-op since connections are created per-operation.
        This enables future connection reuse optimization (see refactor #4).
        """
        pass

    def close(self) -> None:
        """Close the database.

        Currently a no-op since connections are created per-operation.
        This enables future connection reuse optimization (see refactor #4).
        """
        pass
