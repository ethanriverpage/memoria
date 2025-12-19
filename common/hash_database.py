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
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


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
                logger.debug(
                    f"Duplicate entry for {source_path} with hash {content_hash[:16]}..."
                )
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

    @staticmethod
    def _hex_to_bits(hex_string: str) -> str:
        """Convert hex string to binary string.

        Args:
            hex_string: Hexadecimal string.

        Returns:
            Binary string representation.
        """
        return bin(int(hex_string, 16))[2:].zfill(len(hex_string) * 4)
