#!/usr/bin/env python3
"""Media file cataloging utilities for cross-processor deduplication."""

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from common.dedup.config import get_dedup_config
from common.dedup.hashing import (
    compute_content_hash,
    compute_perceptual_hash_with_dims,
    get_media_dimensions,
)
from common.output.progress import PHASE_CATALOG, futures_progress

logger = logging.getLogger(__name__)


def catalog_media_files(
    processor_name: str,
    input_path: Path,
    media_files: List[Path],
    workers: int = 4,
    metadata_extractor: Optional[Callable[[Path], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build a catalog of media files with hashes and metadata.

    Handles the common pattern across all processor build_catalog() methods:
    - Compute content hash (xxHash64)
    - Compute perceptual hash (if enabled in config)
    - Extract dimensions
    - Run in parallel with progress bar

    This function is designed to be called by processor build_catalog() methods
    after they have discovered their media files. Each processor handles file
    discovery differently (different directory structures), but the hashing
    and cataloging logic is identical.

    Args:
        processor_name: Name for the catalog (e.g., "Google Chat", "Discord").
            This is stored in the catalog and used for deduplication decisions.
        input_path: Base input path for relative path calculation. All file
            paths in the catalog will be stored relative to this path.
        media_files: List of media file paths to catalog. These should be
            absolute paths or paths that can be resolved.
        workers: Number of parallel workers for hashing (default: 4).
        metadata_extractor: Optional function to extract additional metadata
            from each file. Signature: (file_path: Path) -> Dict[str, Any].
            The returned dict is stored in the entry's "metadata" field.

    Returns:
        Catalog dictionary with structure:
        {
            "processor": str,
            "input_dir": str,
            "files": [
                {
                    "source_path": str,
                    "relative_path": str,
                    "content_hash": str,
                    "perceptual_hash": str | None,
                    "file_size": int,
                    "dimensions": tuple | None,
                    "timestamp": None,
                    "metadata": dict,
                },
                ...
            ]
        }

    Example:
        >>> from common.dedup.catalog import catalog_media_files
        >>> media_files = list(input_path.rglob("*.jpg"))
        >>> catalog = catalog_media_files(
        ...     processor_name="Google Photos",
        ...     input_path=input_path,
        ...     media_files=media_files,
        ...     workers=8,
        ... )
        >>> print(f"Cataloged {len(catalog['files'])} files")
    """
    config = get_dedup_config()

    catalog: Dict[str, Any] = {
        "processor": processor_name,
        "input_dir": str(input_path),
        "files": [],
    }

    if not media_files:
        logger.info(f"No media files to catalog for {processor_name}")
        return catalog

    logger.info(f"Found {len(media_files)} media files to catalog")

    def process_file(file_path: Path) -> Optional[Dict[str, Any]]:
        """Process a single file for cataloging."""
        try:
            content_hash = compute_content_hash(file_path)

            # Get perceptual hash and dimensions in a single file open
            # This avoids opening the image/video twice (once for hash, once for dims)
            perceptual_hash = None
            dimensions = None
            if config.use_perceptual:
                perceptual_hash, dimensions = compute_perceptual_hash_with_dims(
                    file_path
                )
            else:
                # Still need dimensions even without perceptual hash
                dimensions = get_media_dimensions(file_path)

            entry: Dict[str, Any] = {
                "source_path": str(file_path),
                "relative_path": str(file_path.relative_to(input_path)),
                "content_hash": content_hash,
                "perceptual_hash": perceptual_hash,
                "file_size": file_path.stat().st_size,
                "dimensions": dimensions,
                "timestamp": None,
                "metadata": {},
            }

            if metadata_extractor:
                try:
                    entry["metadata"] = metadata_extractor(file_path)
                except Exception as e:
                    logger.debug(f"Metadata extraction failed for {file_path}: {e}")

            return entry

        except Exception as e:
            logger.warning(f"Failed to catalog {file_path}: {e}")
            return None

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_file, fp): fp for fp in media_files}
        for future in futures_progress(
            futures, PHASE_CATALOG, "Hashing files", unit="file"
        ):
            result = future.result()
            if result:
                catalog["files"].append(result)

    logger.info(f"Cataloged {len(catalog['files'])} files for {processor_name}")
    return catalog

