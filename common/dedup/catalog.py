#!/usr/bin/env python3
"""Media file cataloging utilities for cross-processor deduplication."""

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

from common.dedup.config import get_dedup_config
from common.dedup.database import get_thread_db
from common.dedup.hashing import (
    compute_content_hash,
    compute_perceptual_hash_with_dims,
    get_media_dimensions,
)
from common.output.progress import PHASE_CATALOG, futures_progress
from common.files.utils import short_path, get_export_name

logger = logging.getLogger(__name__)


def _extract_media_entries(metadata: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Extract all media entries from any metadata structure.

    This is a wrapper around ProcessorBase.extract_all_media_entries() to avoid
    circular imports. The actual implementation is in ProcessorBase.

    Args:
        metadata: Parsed metadata.json dict

    Yields:
        Media entry dicts with at minimum: filename, source_path, content_hash
    """
    # Import here to avoid circular import
    from processors.base import ProcessorBase

    yield from ProcessorBase.extract_all_media_entries(metadata)


def catalog_from_preprocessed(
    processor_name: str,
    input_path: Path,
    workers: int = 4,
    metadata_extractor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Iterator[Dict[str, Any]]:
    """Build catalog from preprocessed metadata.json.

    This function reads catalog entries from a preprocessed directory's metadata.json
    file. Since preprocessing already computes content_hash and stores source_path,
    this avoids re-hashing files that were already processed.

    For perceptual hashes and dimensions, we check the hash cache database.
    If not cached, we compute them from the source files.

    Args:
        processor_name: Name for the catalog (e.g., "Google Photos").
        input_path: Path to preprocessed directory containing metadata.json.
        workers: Number of parallel workers for computing missing hashes.
        metadata_extractor: Optional function to transform metadata from each
            media_files entry. Signature: (entry: Dict) -> Dict[str, Any].
            If not provided, uses entry's existing metadata fields.

    Yields:
        Catalog entry dictionaries with structure matching catalog_media_files().
    """
    metadata_file = input_path / "metadata.json"
    if not metadata_file.exists():
        logger.warning(f"No metadata.json found in: {input_path}")
        return

    # Load metadata
    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load metadata.json: {e}")
        return

    # Extract media entries using unified extraction
    media_entries = list(_extract_media_entries(metadata))
    if not media_entries:
        logger.info(f"No files to catalog: {get_export_name(input_path)}")
        return

    # Check if this uses source_path format (new) vs media/ directory (legacy)
    sample = media_entries[0] if media_entries else {}
    if "source_path" not in sample or not sample.get("source_path"):
        # Legacy format - caller should use catalog_media_files instead
        logger.debug(
            f"Legacy format detected in {input_path}, "
            "caller should use catalog_media_files"
        )
        return

    export_name = get_export_name(input_path)
    logger.info(f"Cataloging {len(media_entries)} files from metadata: {export_name}")

    config = get_dedup_config()
    use_perceptual = config.use_perceptual
    db_path = config.database_path

    # Separate entries that need hash computation from those fully cached
    entries_needing_hashes: List[Tuple[Dict[str, Any], Path]] = []
    cache_hits = 0

    for entry in media_entries:
        source_path_str = entry.get("source_path", "")
        if not source_path_str:
            continue

        source_path = Path(source_path_str)
        if not source_path.exists():
            logger.debug(f"Source file not found: {source_path}")
            continue

        content_hash = entry.get("content_hash")
        if not content_hash:
            # No content hash in metadata - need to compute
            entries_needing_hashes.append((entry, source_path))
            continue

        # Check cache for perceptual hash and dimensions
        perceptual_hash = None
        dimensions = None

        if db_path:
            try:
                stat = source_path.stat()
                db = get_thread_db(db_path)
                if db is not None:
                    cached = db.get_cached_hashes(
                        str(source_path), stat.st_size, stat.st_mtime
                    )
                    if cached:
                        perceptual_hash = cached.get("perceptual_hash")
                        dimensions = cached.get("dimensions")
            except Exception:
                pass

        # If we need perceptual hash but don't have it, queue for computation
        if use_perceptual and perceptual_hash is None:
            entries_needing_hashes.append((entry, source_path))
            continue

        # Build catalog entry from metadata
        cache_hits += 1
        catalog_entry = _build_catalog_entry_from_metadata(
            entry=entry,
            source_path=source_path,
            input_path=input_path,
            processor_name=processor_name,
            content_hash=content_hash,
            perceptual_hash=perceptual_hash,
            dimensions=dimensions,
            metadata_extractor=metadata_extractor,
        )
        yield catalog_entry

    # Process entries that need hash computation in parallel
    if entries_needing_hashes:
        logger.debug(
            f"Computing hashes for {len(entries_needing_hashes)} files: {export_name}"
        )
        cache_entries_to_write: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(
                    _compute_missing_hashes,
                    entry,
                    source_path,
                    input_path,
                    processor_name,
                    use_perceptual,
                    db_path,
                    metadata_extractor,
                ): (entry, source_path)
                for entry, source_path in entries_needing_hashes
            }
            for future in futures_progress(
                futures, PHASE_CATALOG, "Computing hashes", unit="file"
            ):
                result = future.result()
                if result:
                    catalog_entry, cache_entry = result
                    if cache_entry:
                        cache_entries_to_write.append(cache_entry)
                    yield catalog_entry

        # Batch write cache entries
        if cache_entries_to_write and db_path:
            from common.dedup.database import HashDatabase

            db = HashDatabase(db_path)
            written = 0
            chunk_size = 1000
            for i in range(0, len(cache_entries_to_write), chunk_size):
                chunk = cache_entries_to_write[i : i + chunk_size]
                written += db.cache_hashes_batch(chunk)
            logger.debug(f"Cached {written} hashes: {export_name}")

    total = cache_hits + len(entries_needing_hashes)
    cache_pct = (cache_hits / total) * 100 if total > 0 else 0
    logger.info(
        f"Cataloged {total}: {export_name} "
        f"({cache_hits} from metadata, {len(entries_needing_hashes)} computed, "
        f"{cache_pct:.1f}% hit)"
    )


def _build_catalog_entry_from_metadata(
    entry: Dict[str, Any],
    source_path: Path,
    input_path: Path,
    processor_name: str,
    content_hash: str,
    perceptual_hash: Optional[str],
    dimensions: Optional[Tuple[int, int]],
    metadata_extractor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a catalog entry from preprocessed metadata."""
    # Extract metadata using custom extractor or use entry's metadata fields
    if metadata_extractor:
        metadata = metadata_extractor(entry)
    else:
        # Default: extract common metadata fields
        metadata = {}
        for key in ["albums", "sender", "conversation", "timestamp", "content", "gps"]:
            if key in entry:
                metadata[key] = entry[key]

    # Get file size
    try:
        file_size = source_path.stat().st_size
    except Exception:
        file_size = 0

    return {
        "source_path": str(source_path),
        "relative_path": entry.get("filename", source_path.name),
        "content_hash": content_hash,
        "perceptual_hash": perceptual_hash,
        "file_size": file_size,
        "dimensions": dimensions,
        "timestamp": entry.get("capture_timestamp") or entry.get("timestamp"),
        "metadata": metadata,
        "_processor": processor_name,
    }


def _compute_missing_hashes(
    entry: Dict[str, Any],
    source_path: Path,
    input_path: Path,
    processor_name: str,
    use_perceptual: bool,
    db_path: Optional[Path],
    metadata_extractor: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
) -> Optional[Tuple[Dict[str, Any], Optional[Dict[str, Any]]]]:
    """Compute missing hashes for an entry from preprocessed metadata."""
    try:
        stat = source_path.stat()
        file_size = stat.st_size
        mtime = stat.st_mtime

        # Use content_hash from metadata if available
        content_hash = entry.get("content_hash")
        if not content_hash:
            content_hash = compute_content_hash(source_path)

        # Compute perceptual hash and dimensions
        perceptual_hash = None
        dimensions = None
        if use_perceptual:
            perceptual_hash, dimensions = compute_perceptual_hash_with_dims(source_path)
        else:
            dimensions = get_media_dimensions(source_path)

        # Prepare cache entry
        cache_entry = None
        if db_path:
            cache_entry = {
                "source_path": str(source_path),
                "file_size": file_size,
                "mtime": mtime,
                "content_hash": content_hash,
                "perceptual_hash": perceptual_hash,
                "dimensions": dimensions,
            }

        catalog_entry = _build_catalog_entry_from_metadata(
            entry=entry,
            source_path=source_path,
            input_path=input_path,
            processor_name=processor_name,
            content_hash=content_hash,
            perceptual_hash=perceptual_hash,
            dimensions=dimensions,
            metadata_extractor=metadata_extractor,
        )

        return catalog_entry, cache_entry

    except Exception as e:
        logger.warning(f"Failed to compute hashes for {short_path(source_path)}: {e}")
        return None

# Environment variable to override catalog workers (for memory-constrained systems)
# Large images (HEIC) can use 36-50MB each during perceptual hashing
# Example: CATALOG_WORKERS=2 limits to 2 concurrent file processes
CATALOG_WORKERS_ENV = "CATALOG_WORKERS"

# Environment variable to disable hash caching (for debugging or fresh runs)
# Example: DISABLE_HASH_CACHE=1
DISABLE_HASH_CACHE_ENV = "DISABLE_HASH_CACHE"


def _process_single_file(
    file_path: Path,
    input_path: Path,
    processor_name: str,
    use_perceptual: bool,
    metadata_extractor: Optional[Callable[[Path], Dict[str, Any]]] = None,
    db_path: Optional[Path] = None,
) -> Optional[Tuple[Dict[str, Any], bool, Optional[Dict[str, Any]]]]:
    """Process a single file for cataloging, with optional hash caching.

    Cache reads happen in worker threads (safe for concurrent access).
    Cache writes are deferred - this function returns cache data to be
    written in a batch after parallel processing completes.

    Args:
        file_path: Path to the file to process.
        input_path: Base input path for relative path calculation.
        processor_name: Name of the processor for the _processor field.
        use_perceptual: Whether to compute perceptual hash.
        metadata_extractor: Optional function to extract additional metadata.
        db_path: Optional path to database for hash cache reads.

    Returns:
        Tuple of (catalog_entry, cache_hit, cache_entry_to_write) or None if failed.
        - catalog_entry: The catalog entry dict
        - cache_hit: True if hashes were retrieved from cache
        - cache_entry_to_write: Dict with cache data if needs writing, None if cache hit
    """
    try:
        # Get file stats for cache lookup
        stat = file_path.stat()
        file_size = stat.st_size
        mtime = stat.st_mtime

        content_hash = None
        perceptual_hash = None
        dimensions = None
        cache_hit = False
        cache_entry_to_write = None

        # Try cache lookup if database path provided (reads are thread-safe)
        db = get_thread_db(db_path)
        if db is not None:
            cached = db.get_cached_hashes(str(file_path), file_size, mtime)
            if cached is not None:
                content_hash = cached["content_hash"]
                perceptual_hash = cached.get("perceptual_hash")
                dimensions = cached.get("dimensions")
                cache_hit = True

                # If perceptual hash is requested but not in cache, we need to compute it
                if use_perceptual and perceptual_hash is None:
                    cache_hit = False

        # Compute hashes if not cached (or cache miss)
        if not cache_hit:
            content_hash = compute_content_hash(file_path)

            # Get perceptual hash and dimensions in a single file open
            # This avoids opening the image/video twice (once for hash, once for dims)
            if use_perceptual:
                perceptual_hash, dimensions = compute_perceptual_hash_with_dims(
                    file_path
                )
            else:
                # Still need dimensions even without perceptual hash
                dimensions = get_media_dimensions(file_path)

            # Prepare cache entry for deferred batch write (avoids SQLite lock contention)
            if db_path is not None:
                cache_entry_to_write = {
                    "source_path": str(file_path),
                    "file_size": file_size,
                    "mtime": mtime,
                    "content_hash": content_hash,
                    "perceptual_hash": perceptual_hash,
                    "dimensions": dimensions,
                }

        entry: Dict[str, Any] = {
            "source_path": str(file_path),
            "relative_path": str(file_path.relative_to(input_path)),
            "content_hash": content_hash,
            "perceptual_hash": perceptual_hash,
            "file_size": file_size,
            "dimensions": dimensions,
            "timestamp": None,
            "metadata": {},
            "_processor": processor_name,
        }

        if metadata_extractor:
            try:
                entry["metadata"] = metadata_extractor(file_path)
            except Exception as e:
                logger.debug(f"Metadata failed: {short_path(file_path)}: {e}")

        return entry, cache_hit, cache_entry_to_write

    except Exception as e:
        logger.warning(f"Failed to catalog {short_path(file_path)}: {e}")
        return None


def catalog_media_files(
    processor_name: str,
    input_path: Path,
    media_files: List[Path],
    workers: int = 4,
    metadata_extractor: Optional[Callable[[Path], Dict[str, Any]]] = None,
    db_path: Optional[Path] = None,
) -> Iterator[Dict[str, Any]]:
    """Build a catalog of media files with hashes and metadata.

    This is a generator function that yields catalog entries one at a time,
    enabling memory-efficient streaming to a database rather than accumulating
    all entries in memory.

    Handles the common pattern across all processor build_catalog() methods:
    - Compute content hash (xxHash64)
    - Compute perceptual hash (if enabled in config)
    - Extract dimensions
    - Run in parallel with progress bar
    - Cache hashes for future runs (if db_path provided)

    This function is designed to be called by processor build_catalog() methods
    after they have discovered their media files. Each processor handles file
    discovery differently (different directory structures), but the hashing
    and cataloging logic is identical.

    Args:
        processor_name: Name for the catalog (e.g., "Google Chat", "Discord").
            This is stored in each entry's _processor field.
        input_path: Base input path for relative path calculation. All file
            paths in the catalog will be stored relative to this path.
        media_files: List of media file paths to catalog. These should be
            absolute paths or paths that can be resolved.
        workers: Number of parallel workers for hashing (default: 4).
        metadata_extractor: Optional function to extract additional metadata
            from each file. Signature: (file_path: Path) -> Dict[str, Any].
            The returned dict is stored in the entry's "metadata" field.
        db_path: Optional path to database for hash caching. When provided,
            computed hashes are cached and reused on subsequent runs for
            unchanged files (same path, size, and mtime).

    Yields:
        Catalog entry dictionaries with structure:
        {
            "source_path": str,
            "relative_path": str,
            "content_hash": str,
            "perceptual_hash": str | None,
            "file_size": int,
            "dimensions": tuple | None,
            "timestamp": None,
            "metadata": dict,
            "_processor": str,
        }

    Example:
        >>> from common.dedup.catalog import catalog_media_files
        >>> media_files = list(input_path.rglob("*.jpg"))
        >>> for entry in catalog_media_files(
        ...     processor_name="Google Photos",
        ...     input_path=input_path,
        ...     media_files=media_files,
        ...     workers=8,
        ...     db_path=Path("~/.memoria/dedup.db"),
        ... ):
        ...     db.write_catalog_entry(run_id, export_name, entry)
    """
    # Extract export name from path for logging
    export_name = get_export_name(input_path)

    if not media_files:
        logger.info(f"No files to catalog: {export_name}")
        return

    logger.info(f"Cataloging {len(media_files)} files: {export_name}")

    # Allow environment variable to override workers for memory-constrained systems
    # Large HEIC images can use 36-50MB each during perceptual hashing
    env_workers = os.environ.get(CATALOG_WORKERS_ENV)
    if env_workers:
        try:
            workers = int(env_workers)
            logger.debug(f"Using {workers} catalog workers from {CATALOG_WORKERS_ENV}")
        except ValueError:
            logger.warning(
                f"Invalid {CATALOG_WORKERS_ENV}={env_workers}, using {workers}"
            )

    # Check if hash caching is disabled
    disable_cache = os.environ.get(DISABLE_HASH_CACHE_ENV, "").lower() in (
        "1",
        "true",
        "yes",
    )
    if disable_cache:
        db_path = None
        logger.debug("Hash caching disabled via environment variable")

    config = get_dedup_config()
    use_perceptual = config.use_perceptual

    # Use database path from config if not provided
    if db_path is None and not disable_cache:
        db_path = config.database_path

    count = 0
    cache_hits = 0
    cache_entries_to_write: List[Dict[str, Any]] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _process_single_file,
                fp,
                input_path,
                processor_name,
                use_perceptual,
                metadata_extractor,
                db_path,
            ): fp
            for fp in media_files
        }
        for future in futures_progress(
            futures, PHASE_CATALOG, "Hashing files", unit="file"
        ):
            result = future.result()
            if result:
                entry, was_cached, cache_entry = result
                count += 1
                if was_cached:
                    cache_hits += 1
                elif cache_entry is not None:
                    cache_entries_to_write.append(cache_entry)
                yield entry

    # Batch write cache entries after parallel processing (avoids lock contention)
    # Write in chunks of 1000 to bound memory usage during executemany
    if cache_entries_to_write and db_path is not None:
        from common.dedup.database import HashDatabase

        db = HashDatabase(db_path)
        written = 0
        chunk_size = 1000
        for i in range(0, len(cache_entries_to_write), chunk_size):
            chunk = cache_entries_to_write[i : i + chunk_size]
            written += db.cache_hashes_batch(chunk)
        logger.debug(f"Cached {written} hashes: {export_name}")

    # Log cache statistics
    if db_path is not None and count > 0:
        cache_pct = (cache_hits / count) * 100 if count > 0 else 0
        logger.info(
            f"Cataloged {count}: {export_name} "
            f"({cache_hits} cached, {count - cache_hits} computed, {cache_pct:.1f}% hit)"
        )
    else:
        logger.info(f"Cataloged {count}: {export_name}")
