#!/usr/bin/env python3
"""
Deduplication Service for Cross-Processor Media Deduplication

Orchestrates the analysis of file catalogs to identify duplicates,
select best quality versions, determine primary sources, and merge
metadata across different export sources.
"""

import json
import logging
import math
import os
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.config import DedupConfig, get_dedup_config
from common.dedup.description import (
    SourceEntry,
    build_image_description,
    create_source_entry,
)
from common.dedup.database import HashDatabase
from common.dedup.hashing import compute_content_hash, hash_similarity
from common.output.progress import PHASE_DEDUP, progress_bar

logger = logging.getLogger(__name__)


def _compare_hash_chunk(
    args: Tuple[List[Tuple[int, int, str, str]], float],
) -> List[Tuple[int, int, float]]:
    """Compare a chunk of hash pairs for similarity (multiprocessing worker).

    This function is designed to be called by ProcessPoolExecutor for parallel
    perceptual hash comparison. It processes a batch of hash pairs and returns
    only those that meet the similarity threshold.

    Args:
        args: Tuple of (chunk, threshold) where:
            - chunk: List of (index_i, index_j, hash_i, hash_j) tuples
            - threshold: Minimum similarity to be considered a match

    Returns:
        List of (index_i, index_j, similarity) tuples for matches above threshold.
    """
    chunk, threshold = args
    matches = []

    for i, j, hash1, hash2 in chunk:
        try:
            similarity = hash_similarity(hash1, hash2)
            if similarity >= threshold:
                matches.append((i, j, similarity))
        except (ValueError, TypeError):
            # Skip pairs with incompatible hashes
            continue

    return matches


class DeduplicationService:
    """Service for analyzing and deduplicating files across processors.

    Orchestrates the deduplication analysis phase:
    1. Groups files by exact content hash
    2. Groups remaining files by perceptual hash similarity
    3. Validates candidate duplicates using metadata
    4. Selects best quality file for each group
    5. Determines primary source based on timestamp
    6. Merges metadata from all sources

    Attributes:
        db: HashDatabase for persistence.
        config: DedupConfig with thresholds and settings.
    """

    def __init__(
        self,
        db: Optional[HashDatabase] = None,
        config: Optional[DedupConfig] = None,
    ):
        """Initialize the deduplication service.

        Args:
            db: Optional HashDatabase instance. Creates new one if not provided.
            config: Optional DedupConfig. Uses environment config if not provided.
        """
        self.config = config or get_dedup_config()
        self.db = db or HashDatabase(self.config.database_path)

    def analyze(self, catalogs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze catalogs to identify duplicates and build manifest.

        Main entry point for deduplication analysis. Processes all files
        from all catalogs and produces a manifest describing duplicates
        and their relationships.

        Args:
            catalogs: List of catalog dictionaries from processor.build_catalog()

        Returns:
            Deduplication manifest with structure:
            {
                "version": "1.0",
                "created": ISO timestamp,
                "statistics": {...},
                "duplicates": {
                    content_hash: {
                        "primary": {...},
                        "secondary": [...],
                        "merged_metadata": {...}
                    }
                }
            }
        """
        logger.info(f"Analyzing {len(catalogs)} catalog(s) for duplicates")

        # Collect all files from all catalogs
        all_files = []
        for catalog in catalogs:
            processor = catalog.get("processor", "Unknown")
            for file_entry in catalog.get("files", []):
                file_entry["_processor"] = processor
                all_files.append(file_entry)

        logger.info(f"Total files to analyze: {len(all_files)}")

        # Register files in database
        self._register_files(all_files)

        # Group by exact hash
        exact_groups = self._group_by_exact_hash(all_files)
        logger.info(f"Exact hash groups with duplicates: {len(exact_groups)}")

        # Group remaining by perceptual hash (if enabled)
        perceptual_groups = {}
        if self.config.use_perceptual:
            ungrouped = self._get_ungrouped_files(all_files, exact_groups)
            perceptual_groups = self._group_by_perceptual_hash(ungrouped)
            logger.info(
                f"Perceptual hash groups with duplicates: {len(perceptual_groups)}"
            )

        # Merge groups and build manifest
        manifest = self._build_manifest(all_files, exact_groups, perceptual_groups)

        return manifest

    def _register_files(self, files: List[Dict[str, Any]]) -> None:
        """Register all files in the hash database.

        Args:
            files: List of file entries to register.
        """
        for file_entry in progress_bar(
            files, PHASE_DEDUP, "Registering files", unit="file"
        ):
            self.db.register(
                content_hash=file_entry.get("content_hash", ""),
                processor=file_entry.get("_processor", "Unknown"),
                source_path=file_entry.get("source_path", ""),
                perceptual_hash=file_entry.get("perceptual_hash"),
                file_size=file_entry.get("file_size"),
                dimensions=file_entry.get("dimensions"),
                metadata=file_entry.get("metadata"),
            )

    def _group_by_exact_hash(
        self,
        files: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group files by exact content hash.

        Args:
            files: List of file entries.

        Returns:
            Dict mapping content hash to list of file entries.
            Only includes hashes with 2+ files.
        """
        groups = defaultdict(list)

        for file_entry in progress_bar(
            files, PHASE_DEDUP, "Grouping by exact hash", unit="file"
        ):
            content_hash = file_entry.get("content_hash")
            if content_hash:
                groups[content_hash].append(file_entry)

        # Filter to groups with duplicates
        return {h: files for h, files in groups.items() if len(files) > 1}

    def _group_by_perceptual_hash(
        self,
        files: List[Dict[str, Any]],
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group files by perceptual hash similarity.

        Uses clustering to find groups of similar images. When PARALLEL_DEDUP
        is enabled (default), comparison is parallelized across multiple processes.

        Args:
            files: List of file entries without exact hash matches.

        Returns:
            Dict mapping group ID to list of similar file entries.
        """
        if not files:
            return {}

        threshold = self.config.perceptual_threshold

        # Build list of files with perceptual hashes
        hashable = [f for f in files if f.get("perceptual_hash")]

        if not hashable:
            return {}

        use_parallel = os.environ.get("PARALLEL_DEDUP", "true").lower() == "true"
        num_pairs = len(hashable) * (len(hashable) - 1) // 2

        # Use parallel comparison for large datasets
        if use_parallel and num_pairs > 1000:
            return self._group_by_perceptual_hash_parallel(hashable, threshold)
        else:
            return self._group_by_perceptual_hash_sequential(hashable, threshold)

    def _group_by_perceptual_hash_sequential(
        self,
        hashable: List[Dict[str, Any]],
        threshold: float,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Sequential perceptual hash grouping (original implementation).

        Args:
            hashable: List of file entries with perceptual hashes.
            threshold: Minimum similarity threshold.

        Returns:
            Dict mapping group ID to list of similar file entries.
        """
        groups = {}
        group_id = 0
        assigned = set()

        for i, file1 in progress_bar(
            list(enumerate(hashable)),
            PHASE_DEDUP,
            "Comparing perceptual hashes",
            unit="file",
        ):
            if i in assigned:
                continue

            hash1 = file1.get("perceptual_hash")
            if not hash1:
                continue

            # Find all similar files
            similar = [file1]
            assigned.add(i)

            for j, file2 in enumerate(hashable[i + 1 :], start=i + 1):
                if j in assigned:
                    continue

                hash2 = file2.get("perceptual_hash")
                if not hash2:
                    continue

                try:
                    similarity = hash_similarity(hash1, hash2)
                    if similarity >= threshold:
                        # Validate with metadata
                        if self._validate_duplicate_pair(file1, file2):
                            similar.append(file2)
                            assigned.add(j)
                except (ValueError, TypeError):
                    continue

            # Only keep groups with duplicates
            if len(similar) > 1:
                groups[f"perceptual_{group_id}"] = similar
                group_id += 1

        return groups

    def _group_by_perceptual_hash_parallel(
        self,
        hashable: List[Dict[str, Any]],
        threshold: float,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Parallel perceptual hash grouping using ProcessPoolExecutor.

        Distributes hash comparisons across multiple processes for better
        CPU utilization on large datasets.

        Args:
            hashable: List of file entries with perceptual hashes.
            threshold: Minimum similarity threshold.

        Returns:
            Dict mapping group ID to list of similar file entries.
        """
        import multiprocessing

        num_files = len(hashable)
        num_pairs = num_files * (num_files - 1) // 2

        logger.info(
            f"Parallel perceptual hash comparison: {num_files} files, "
            f"{num_pairs} pairs"
        )

        # Build all comparison pairs (indices and hashes only for pickling)
        pairs = []
        for i in range(num_files):
            hash1 = hashable[i].get("perceptual_hash")
            for j in range(i + 1, num_files):
                hash2 = hashable[j].get("perceptual_hash")
                if hash1 and hash2:
                    pairs.append((i, j, hash1, hash2))

        if not pairs:
            return {}

        # Chunk pairs for parallel processing
        # Use smaller chunks for better load balancing
        num_workers = min(multiprocessing.cpu_count(), 12)
        chunk_size = max(1000, len(pairs) // (num_workers * 4))
        chunks = [pairs[i : i + chunk_size] for i in range(0, len(pairs), chunk_size)]

        logger.info(f"Processing {len(chunks)} chunks with {num_workers} workers")

        # Process chunks in parallel
        all_matches = []
        with ProcessPoolExecutor(max_workers=num_workers) as executor:
            chunk_args = [(chunk, threshold) for chunk in chunks]
            for chunk_matches in progress_bar(
                executor.map(_compare_hash_chunk, chunk_args),
                PHASE_DEDUP,
                "Comparing perceptual hashes",
                total=len(chunks),
                unit="chunk",
            ):
                all_matches.extend(chunk_matches)

        if not all_matches:
            return {}

        logger.info(f"Found {len(all_matches)} potential matches, building groups")

        # Build groups using Union-Find for clustering
        parent = list(range(num_files))

        def find(x: int) -> int:
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x: int, y: int) -> None:
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # First pass: union all matching pairs (validated by metadata)
        for i, j, _similarity in all_matches:
            if self._validate_duplicate_pair(hashable[i], hashable[j]):
                union(i, j)

        # Build groups from union-find structure
        group_members: Dict[int, List[int]] = defaultdict(list)
        for i in range(num_files):
            root = find(i)
            group_members[root].append(i)

        # Convert to output format, only keeping groups with 2+ members
        groups = {}
        group_id = 0
        for members in group_members.values():
            if len(members) > 1:
                groups[f"perceptual_{group_id}"] = [hashable[i] for i in members]
                group_id += 1

        logger.info(f"Created {len(groups)} perceptual duplicate groups")
        return groups

    def _get_ungrouped_files(
        self,
        all_files: List[Dict[str, Any]],
        exact_groups: Dict[str, List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Get files not in any exact hash group.

        Args:
            all_files: All file entries.
            exact_groups: Exact hash groups.

        Returns:
            List of files without exact hash matches.
        """
        grouped_hashes = set(exact_groups.keys())
        return [f for f in all_files if f.get("content_hash") not in grouped_hashes]

    def _validate_duplicate_pair(
        self,
        file1: Dict[str, Any],
        file2: Dict[str, Any],
    ) -> bool:
        """Validate that two files are likely true duplicates.

        Uses metadata consistency checks: GPS distance, timestamp delta,
        and aspect ratio similarity.

        Args:
            file1: First file entry.
            file2: Second file entry.

        Returns:
            True if files pass validation checks.
        """
        # Check GPS distance
        gps1 = self._extract_gps(file1)
        gps2 = self._extract_gps(file2)

        if gps1 and gps2:
            distance = self._haversine_distance(gps1, gps2)
            if distance > self.config.validate_gps_distance:
                logger.debug(
                    f"GPS distance too large: {distance:.0f}m > "
                    f"{self.config.validate_gps_distance}m"
                )
                return False

        # Check timestamp delta
        ts1 = self._extract_timestamp(file1)
        ts2 = self._extract_timestamp(file2)

        if ts1 and ts2:
            delta = abs((ts1 - ts2).total_seconds())
            if delta > self.config.validate_timestamp_delta:
                logger.debug(
                    f"Timestamp delta too large: {delta:.0f}s > "
                    f"{self.config.validate_timestamp_delta}s"
                )
                return False

        # Check aspect ratio
        dims1 = file1.get("dimensions")
        dims2 = file2.get("dimensions")

        if dims1 and dims2 and len(dims1) == 2 and len(dims2) == 2:
            ratio1 = dims1[0] / dims1[1] if dims1[1] > 0 else 0
            ratio2 = dims2[0] / dims2[1] if dims2[1] > 0 else 0

            if ratio1 > 0 and ratio2 > 0:
                ratio_diff = abs(ratio1 - ratio2) / max(ratio1, ratio2)
                if ratio_diff > self.config.validate_aspect_ratio:
                    logger.debug(
                        f"Aspect ratio difference too large: {ratio_diff:.2f} > "
                        f"{self.config.validate_aspect_ratio}"
                    )
                    return False

        return True

    def _extract_gps(
        self,
        file_entry: Dict[str, Any],
    ) -> Optional[Tuple[float, float]]:
        """Extract GPS coordinates from file entry.

        Args:
            file_entry: File entry with metadata.

        Returns:
            Tuple of (latitude, longitude) or None.
        """
        metadata = file_entry.get("metadata", {})
        gps = metadata.get("gps", {})

        lat = gps.get("latitude", 0)
        lon = gps.get("longitude", 0)

        if lat != 0 or lon != 0:
            return (lat, lon)

        return None

    def _extract_timestamp(
        self,
        file_entry: Dict[str, Any],
    ) -> Optional[datetime]:
        """Extract timestamp from file entry.

        Args:
            file_entry: File entry with metadata.

        Returns:
            datetime object or None.
        """
        # Try direct timestamp field
        ts = file_entry.get("timestamp")
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        # Try metadata
        metadata = file_entry.get("metadata", {})
        ts = metadata.get("capture_timestamp") or metadata.get("timestamp")
        if isinstance(ts, datetime):
            return ts
        if isinstance(ts, str):
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                pass

        return None

    @staticmethod
    def _haversine_distance(
        coord1: Tuple[float, float],
        coord2: Tuple[float, float],
    ) -> float:
        """Calculate distance between two GPS coordinates in meters.

        Uses Haversine formula for great-circle distance.

        Args:
            coord1: (latitude, longitude) of first point.
            coord2: (latitude, longitude) of second point.

        Returns:
            Distance in meters.
        """
        R = 6371000  # Earth's radius in meters

        lat1, lon1 = math.radians(coord1[0]), math.radians(coord1[1])
        lat2, lon2 = math.radians(coord2[0]), math.radians(coord2[1])

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = (
            math.sin(dlat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        )
        c = 2 * math.asin(math.sqrt(a))

        return R * c

    def _build_manifest(
        self,
        all_files: List[Dict[str, Any]],
        exact_groups: Dict[str, List[Dict[str, Any]]],
        perceptual_groups: Dict[str, List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Build the deduplication manifest.

        Args:
            all_files: All analyzed files.
            exact_groups: Groups from exact hash matching.
            perceptual_groups: Groups from perceptual hash matching.

        Returns:
            Complete manifest dictionary.
        """
        duplicates = {}
        total_duplicates = 0
        space_saved = 0

        # Process exact hash groups
        for content_hash, files in progress_bar(
            exact_groups.items(),
            PHASE_DEDUP,
            "Building manifest (exact)",
            total=len(exact_groups),
            unit="group",
        ):
            best = self._select_best_file(files)
            primary = self._determine_primary_source(files)
            merged = self._merge_metadata(files)

            duplicates[content_hash] = {
                "match_type": "exact",
                "primary": {
                    "processor": primary.get("_processor"),
                    "source_path": primary.get("source_path"),
                    "content_hash": content_hash,
                    "file_size": primary.get("file_size"),
                },
                "best_quality": {
                    "processor": best.get("_processor"),
                    "source_path": best.get("source_path"),
                    "content_hash": content_hash,
                    "file_size": best.get("file_size"),
                },
                "secondary": [
                    {
                        "processor": f.get("_processor"),
                        "source_path": f.get("source_path"),
                        "content_hash": f.get("content_hash"),
                        "context": f.get("metadata", {}),
                    }
                    for f in files
                    if f.get("source_path") != primary.get("source_path")
                ],
                "merged_metadata": merged,
            }

            total_duplicates += len(files) - 1
            file_size = best.get("file_size", 0)
            space_saved += file_size * (len(files) - 1)

        # Process perceptual hash groups
        for group_id, files in progress_bar(
            perceptual_groups.items(),
            PHASE_DEDUP,
            "Building manifest (perceptual)",
            total=len(perceptual_groups),
            unit="group",
        ):
            best = self._select_best_file(files)
            primary = self._determine_primary_source(files)
            merged = self._merge_metadata(files)

            # Use best file's content hash as key
            key = f"perceptual_{best.get('content_hash', group_id)}"

            duplicates[key] = {
                "match_type": "perceptual",
                "primary": {
                    "processor": primary.get("_processor"),
                    "source_path": primary.get("source_path"),
                    "content_hash": primary.get("content_hash"),
                    "file_size": primary.get("file_size"),
                },
                "best_quality": {
                    "processor": best.get("_processor"),
                    "source_path": best.get("source_path"),
                    "content_hash": best.get("content_hash"),
                    "file_size": best.get("file_size"),
                },
                "secondary": [
                    {
                        "processor": f.get("_processor"),
                        "source_path": f.get("source_path"),
                        "content_hash": f.get("content_hash"),
                        "context": f.get("metadata", {}),
                    }
                    for f in files
                    if f.get("source_path") != primary.get("source_path")
                ],
                "merged_metadata": merged,
            }

            total_duplicates += len(files) - 1
            file_size = best.get("file_size", 0)
            space_saved += file_size * (len(files) - 1)

        # Build statistics
        unique_files = len(all_files) - total_duplicates

        manifest = {
            "version": "1.0",
            "created": datetime.now(timezone.utc).isoformat(),
            "statistics": {
                "total_files_scanned": len(all_files),
                "unique_files": unique_files,
                "duplicate_groups": len(duplicates),
                "duplicates_found": total_duplicates,
                "space_saved_bytes": space_saved,
            },
            "duplicates": duplicates,
        }

        logger.info(
            f"Manifest built: {unique_files} unique, "
            f"{total_duplicates} duplicates, "
            f"{space_saved / (1024 * 1024):.1f} MB saved"
        )

        return manifest

    def _select_best_file(
        self,
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Select the best quality file from candidates.

        Priority: file_size > dimensions > has_exif

        Args:
            candidates: List of file entries.

        Returns:
            Best quality file entry.
        """

        def quality_score(entry: Dict[str, Any]) -> Tuple[int, int, int]:
            file_size = entry.get("file_size", 0)

            dims = entry.get("dimensions")
            if dims and len(dims) == 2:
                pixels = dims[0] * dims[1]
            else:
                pixels = 0

            metadata = entry.get("metadata", {})
            has_exif = 1 if metadata.get("has_exif") else 0

            return (file_size, pixels, has_exif)

        return max(candidates, key=quality_score)

    def _determine_primary_source(
        self,
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Determine the primary source based on earliest timestamp.

        Args:
            candidates: List of file entries.

        Returns:
            Primary source file entry.
        """
        entries_with_ts = []

        for entry in candidates:
            ts = self._extract_timestamp(entry)
            if ts:
                entries_with_ts.append((ts, entry))

        if entries_with_ts:
            # Sort by timestamp, prefer timezone-aware
            def sort_key(item: Tuple[datetime, Dict]) -> Tuple[int, datetime]:
                ts, _ = item
                # Prefer timezone-aware (0) over naive (1)
                tz_priority = 0 if ts.tzinfo else 1
                return (tz_priority, ts)

            entries_with_ts.sort(key=sort_key)
            return entries_with_ts[0][1]

        # Fallback to first entry
        return candidates[0]

    def _merge_metadata(
        self,
        candidates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Merge metadata from all candidate files.

        Merge strategies per field:
        - timestamp: Earliest with timezone preference
        - gps: First non-zero
        - camera_info: Most detailed
        - people: Union
        - albums: All with source attribution

        Args:
            candidates: List of file entries.

        Returns:
            Merged metadata dictionary.
        """
        merged = {
            "timestamp": None,
            "gps": None,
            "camera_info": {},
            "people": [],
            "albums": [],
            "provenance": [],
        }

        # Collect all timestamps
        timestamps = []
        for entry in candidates:
            ts = self._extract_timestamp(entry)
            if ts:
                timestamps.append(ts)

        # Use earliest timestamp, prefer timezone-aware
        if timestamps:
            tz_aware = [ts for ts in timestamps if ts.tzinfo]
            tz_naive = [ts for ts in timestamps if not ts.tzinfo]

            if tz_aware:
                merged["timestamp"] = min(tz_aware).isoformat()
            elif tz_naive:
                merged["timestamp"] = min(tz_naive).isoformat()

        # Collect GPS (first non-zero)
        for entry in candidates:
            gps = self._extract_gps(entry)
            if gps:
                merged["gps"] = {"latitude": gps[0], "longitude": gps[1]}
                break

        # Collect camera info (most detailed)
        best_camera = {}
        for entry in candidates:
            metadata = entry.get("metadata", {})
            camera = metadata.get("camera_info", {})
            if len(camera) > len(best_camera):
                best_camera = camera
        merged["camera_info"] = best_camera

        # Collect people (union)
        people_set = set()
        for entry in candidates:
            metadata = entry.get("metadata", {})
            people = metadata.get("people", [])
            people_set.update(people)
        merged["people"] = sorted(list(people_set))

        # Collect albums with source attribution
        for entry in candidates:
            processor = entry.get("_processor", "Unknown")
            metadata = entry.get("metadata", {})
            albums = metadata.get("albums", [])
            for album in albums:
                merged["albums"].append(
                    {
                        "name": album,
                        "source": processor,
                    }
                )

        # Record provenance from all sources
        for entry in candidates:
            merged["provenance"].append(
                {
                    "processor": entry.get("_processor"),
                    "source_path": entry.get("source_path"),
                }
            )

        return merged


def save_manifest(manifest: Dict[str, Any], output_path: Path) -> None:
    """Save deduplication manifest to JSON file.

    Args:
        manifest: Manifest dictionary.
        output_path: Path to output JSON file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved deduplication manifest to {output_path}")


def load_manifest(manifest_path: Path) -> Dict[str, Any]:
    """Load deduplication manifest from JSON file.

    Args:
        manifest_path: Path to manifest JSON file.

    Returns:
        Manifest dictionary.

    Raises:
        FileNotFoundError: If manifest file doesn't exist.
        json.JSONDecodeError: If manifest is invalid JSON.
    """
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_file_primary(
    manifest: Dict[str, Any],
    source_path: str,
) -> bool:
    """Check if a file is marked as primary in the manifest.

    Args:
        manifest: Deduplication manifest.
        source_path: Path to check.

    Returns:
        True if file is primary or not in manifest.
    """
    for group in manifest.get("duplicates", {}).values():
        primary = group.get("primary", {})
        if primary.get("source_path") == source_path:
            return True

        for secondary in group.get("secondary", []):
            if secondary.get("source_path") == source_path:
                return False

    # Not in manifest = treat as primary
    return True


def is_file_primary_by_hash(
    manifest: Dict[str, Any],
    content_hash: str,
    processor_name: str,
) -> bool:
    """Check if a file with given hash should be copied by this processor.

    This function enables hash-based deduplication lookup, which works correctly
    even when files are preprocessed to temporary directories (where path-based
    matching would fail due to path mismatch between catalog and process phases).

    Args:
        manifest: Deduplication manifest from analyze().
        content_hash: xxHash64 content hash of the file to check.
        processor_name: Name of the processor checking the file.

    Returns:
        True if this processor should copy the file (primary or not in manifest).
        False if another processor is the primary for this file.
    """
    duplicates = manifest.get("duplicates", {})

    # For exact hash matches, the content_hash is the dictionary key
    if content_hash in duplicates:
        group = duplicates[content_hash]
        primary = group.get("primary", {})
        # Check if this processor owns the primary
        return primary.get("processor") == processor_name

    # For perceptual hash groups, we need to search by content_hash in entries
    for key, group in duplicates.items():
        if not key.startswith("perceptual_"):
            continue

        # Check if primary has this content_hash
        primary = group.get("primary", {})
        if primary.get("content_hash") == content_hash:
            return primary.get("processor") == processor_name

        # Check if any secondary has this content_hash
        for secondary in group.get("secondary", []):
            if secondary.get("content_hash") == content_hash:
                # This file is a secondary - another processor has the primary
                return False

    # Not in manifest = treat as primary (not a duplicate)
    return True


def filter_primary_files(
    files: List[Path],
    dedup_manifest: Optional[Dict[str, Any]],
    processor_name: str,
) -> Tuple[List[Path], int]:
    """Filter files to only include primary copies based on dedup manifest.

    Convenience function that wraps is_file_primary_by_hash() for batch filtering.
    Computes content hash for each file and checks against the manifest.

    Args:
        files: List of file paths to filter.
        dedup_manifest: Deduplication manifest from analyze(). If None, all files
            are returned as primary.
        processor_name: Name of the calling processor (e.g., "Google Chat").

    Returns:
        Tuple of (filtered files list, count of skipped secondary files).

    Example:
        >>> primary_files, skipped = filter_primary_files(
        ...     media_files, manifest, "Google Photos"
        ... )
        >>> logger.info(f"Processing {len(primary_files)} files, skipped {skipped}")
    """
    if not dedup_manifest:
        return files, 0

    filtered = []
    for f in files:
        content_hash = compute_content_hash(f)
        if is_file_primary_by_hash(dedup_manifest, content_hash, processor_name):
            filtered.append(f)

    skipped = len(files) - len(filtered)
    if skipped > 0:
        logger.info(f"Skipped {skipped} secondary files (dedup)")

    return filtered, skipped


def get_primary_for_file(
    manifest: Dict[str, Any],
    source_path: str,
) -> Optional[str]:
    """Get the primary source path for a file.

    Args:
        manifest: Deduplication manifest.
        source_path: Path to check.

    Returns:
        Primary source path, or None if not in manifest.
    """
    for group in manifest.get("duplicates", {}).values():
        primary = group.get("primary", {})
        primary_path = primary.get("source_path")

        if primary_path == source_path:
            return primary_path

        for secondary in group.get("secondary", []):
            if secondary.get("source_path") == source_path:
                return primary_path

    return None


def get_merged_description_for_file(
    manifest: Dict[str, Any],
    source_path: str,
) -> Optional[str]:
    """Get merged ImageDescription for a primary file.

    Looks up the file in the manifest and builds a formatted description
    that includes provenance information from all sources (primary and
    secondary) where this file appeared.

    Args:
        manifest: Deduplication manifest.
        source_path: Path to check.

    Returns:
        Formatted ImageDescription string if file is primary in a duplicate
        group, None if file is not in manifest or is a secondary file.
    """
    for group in manifest.get("duplicates", {}).values():
        primary = group.get("primary", {})
        primary_path = primary.get("source_path")

        # Only generate description for primary files
        if primary_path != source_path:
            continue

        # Build primary source entry
        merged_metadata = group.get("merged_metadata", {})
        primary_processor = primary.get("processor", "Unknown")

        # Extract albums for primary
        primary_albums = []
        for album_info in merged_metadata.get("albums", []):
            if album_info.get("source") == primary_processor:
                primary_albums.append(album_info.get("name", ""))

        primary_entry = SourceEntry(
            processor=primary_processor,
            is_primary=True,
            albums=primary_albums,
        )

        # Build secondary source entries
        secondary_entries = []
        for sec in group.get("secondary", []):
            sec_processor = sec.get("processor", "Unknown")
            sec_context = sec.get("context", {})

            # Create source entry from context
            entry = create_source_entry(
                processor=sec_processor,
                metadata=sec_context,
                is_primary=False,
            )
            secondary_entries.append(entry)

        # Build the formatted description
        return build_image_description(
            primary=primary_entry,
            secondary=secondary_entries,
        )

    # Not in manifest or not a primary file
    return None


def get_duplicate_group_for_file(
    manifest: Dict[str, Any],
    source_path: str,
) -> Optional[Dict[str, Any]]:
    """Get the duplicate group containing a file.

    Args:
        manifest: Deduplication manifest.
        source_path: Path to check.

    Returns:
        Duplicate group dictionary if file is in a group, None otherwise.
    """
    for group in manifest.get("duplicates", {}).values():
        primary = group.get("primary", {})
        if primary.get("source_path") == source_path:
            return group

        for secondary in group.get("secondary", []):
            if secondary.get("source_path") == source_path:
                return group

    return None


def get_secondary_reference(
    manifest: Dict[str, Any],
    source_path: str,
    output_base: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Build reference dict for a secondary file.

    When a file is skipped as a secondary duplicate, this function builds
    a reference to its canonical location for inclusion in metadata.json.

    Args:
        manifest: Deduplication manifest.
        source_path: Path to the secondary file.
        output_base: Optional base output path for computing relative paths.

    Returns:
        Reference dictionary with canonical location info, or None if file
        is not a secondary in any duplicate group.

    Example return value:
        {
            "original_filename": "story_123.jpg",
            "deduplicated": True,
            "canonical_location": "../google_photos/media/IMG_001.jpg",
            "canonical_processor": "Google Photos",
            "original_context": {...}
        }
    """
    for group in manifest.get("duplicates", {}).values():
        primary = group.get("primary", {})
        primary_path = primary.get("source_path")
        primary_processor = primary.get("processor", "Unknown")

        for secondary in group.get("secondary", []):
            if secondary.get("source_path") == source_path:
                # This file is a secondary duplicate
                ref = {
                    "original_filename": Path(source_path).name,
                    "deduplicated": True,
                    "canonical_location": primary_path,
                    "canonical_processor": primary_processor,
                    "original_context": secondary.get("context", {}),
                }

                # Compute relative path if output_base provided
                if output_base and primary_path:
                    try:
                        primary_as_path = Path(primary_path)
                        # If primary is also relative to output base, compute relative path
                        rel_path = primary_as_path.relative_to(output_base)
                        ref["canonical_location"] = str(rel_path)
                    except ValueError:
                        # Not relative, keep absolute path
                        pass

                return ref

    # Not a secondary file
    return None


def build_dedup_references(
    manifest: Dict[str, Any],
    skipped_files: List[str],
    processor_name: str,
    output_base: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Build list of dedup references for skipped secondary files.

    Creates reference entries for all files that were skipped during
    processing because they are secondary duplicates.

    Args:
        manifest: Deduplication manifest.
        skipped_files: List of source paths that were skipped.
        processor_name: Name of the processor for context.
        output_base: Optional base output path for relative paths.

    Returns:
        List of reference dictionaries for each skipped file.
    """
    references = []

    for source_path in skipped_files:
        ref = get_secondary_reference(manifest, source_path, output_base)
        if ref:
            # Add processor context
            ref["skipped_by_processor"] = processor_name
            references.append(ref)

    return references


def get_skipped_secondary_files(
    manifest: Dict[str, Any],
    processor_name: str,
) -> List[str]:
    """Get list of secondary file paths for a specific processor.

    Args:
        manifest: Deduplication manifest.
        processor_name: Name of the processor to filter by.

    Returns:
        List of source paths that are secondaries for this processor.
    """
    skipped = []

    for group in manifest.get("duplicates", {}).values():
        for secondary in group.get("secondary", []):
            if secondary.get("processor") == processor_name:
                source_path = secondary.get("source_path")
                if source_path:
                    skipped.append(source_path)

    return skipped


def save_dedup_metadata(
    manifest: Dict[str, Any],
    skipped_files: List[str],
    processor_name: str,
    output_dir: Path,
    metadata_filename: str = "dedup_references.json",
) -> None:
    """Save deduplication reference metadata to the output directory.

    Creates a JSON file containing references for all skipped secondary files,
    allowing users to trace where the canonical copies are located.

    Args:
        manifest: Deduplication manifest.
        skipped_files: List of source paths that were skipped.
        processor_name: Name of the processor.
        output_dir: Output directory where references file will be saved.
        metadata_filename: Name of the output JSON file.
    """
    if not skipped_files:
        return

    references = build_dedup_references(
        manifest=manifest,
        skipped_files=skipped_files,
        processor_name=processor_name,
        output_base=output_dir,
    )

    if not references:
        return

    output_path = Path(output_dir) / metadata_filename
    output_path.parent.mkdir(parents=True, exist_ok=True)

    dedup_data = {
        "version": "1.0",
        "processor": processor_name,
        "total_skipped": len(references),
        "deduplicated_files": references,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dedup_data, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved {len(references)} dedup references to {output_path}")
