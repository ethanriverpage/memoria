#!/usr/bin/env python3
"""
Batch Exiftool Processing Module

This module provides batch processing functions for exiftool operations,
eliminating the need for locks and providing significant performance improvements
by processing multiple files in single exiftool invocations.

This is the mechanism layer - it provides the HOW of metadata writing.
Processors (policy layer) build MetadataWriteRequest objects specifying WHAT to write.
"""

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from common.output.progress import PHASE_EXIF, chunked_progress
from common.utils import get_media_type, get_gps_format
from common.files.utils import short_path

logger = logging.getLogger(__name__)


# =============================================================================
# Metadata Write Request Data Model
# =============================================================================


@dataclass
class GPSData:
    """GPS coordinate data for embedding in media files."""

    latitude: float
    longitude: float
    altitude: Optional[float] = None


@dataclass
class DescriptionWriteSpec:
    """Specification for writing description metadata."""

    default_description: str
    image_tags: List[str] = field(
        default_factory=lambda: ["ImageDescription", "IPTC:Caption-Abstract"]
    )
    video_tags: List[str] = field(default_factory=lambda: ["Comment", "Description"])


@dataclass
class MetadataWriteRequest:
    """A normalized request to write EXIF metadata to a file."""

    file_path: str
    arg_preamble: List[str] = field(default_factory=list)
    datetime_tags: List[Tuple[str, str]] = field(default_factory=list)
    gps: Optional[GPSData] = None
    description: Optional[DescriptionWriteSpec] = None
    processor_name: str = "unknown"


# Default preamble used by most processors
DEFAULT_PREAMBLE = ["-E", "-api", "largefilesupport=1", "-overwrite_original"]

# Preamble with ignoreMinorErrors (used by Google processors)
PREAMBLE_WITH_IGNORE_ERRORS = [
    "-E",
    "-api",
    "largefilesupport=1",
    "-overwrite_original",
    "-ignoreMinorErrors",
]


# =============================================================================
# Unified Batch Executor
# =============================================================================


def batch_write_metadata_requests(
    requests: List[MetadataWriteRequest],
    existing_metadata_map: Dict[str, Dict[str, bool]],
    dedup_manifest: Optional[Dict[str, Any]] = None,
    chunk_size: int = 500,
) -> None:
    """Write metadata for files in batches using exiftool argfiles."""
    if not requests:
        return

    for chunk in chunked_progress(requests, chunk_size, PHASE_EXIF, "Writing metadata"):
        argfile_path: Optional[str] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for request in chunk:
                    existing_fields = existing_metadata_map.get(request.file_path, {})

                    # Write preamble flags
                    for flag in request.arg_preamble:
                        argfile.write(f"{flag}\n")

                    # Write datetime tags (only if field is missing)
                    for tag_name, tag_value in request.datetime_tags:
                        if not existing_fields.get(tag_name, False):
                            argfile.write(f"-{tag_name}={tag_value}\n")

                    # Write GPS data if provided and not already present
                    if request.gps and not existing_fields.get("GPSLatitude", False):
                        lat = request.gps.latitude
                        lon = request.gps.longitude

                        gps_format = get_gps_format(request.file_path)

                        if gps_format == "absolute":
                            argfile.write(f"-GPSLatitude={abs(lat)}\n")
                            argfile.write(
                                f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}\n"
                            )
                            argfile.write(f"-GPSLongitude={abs(lon)}\n")
                            argfile.write(
                                f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}\n"
                            )
                        else:
                            argfile.write(f"-GPSLatitude={lat}\n")
                            argfile.write(f"-GPSLongitude={lon}\n")

                        if (
                            request.gps.altitude is not None
                            and request.gps.altitude != 0.0
                        ):
                            argfile.write(f"-GPSAltitude={request.gps.altitude}\n")

                    # Write description if provided (always overwrites existing)
                    if request.description:
                        # Apply dedup merged description override
                        final_description = _get_description_for_file(
                            request.file_path,
                            request.description.default_description,
                            dedup_manifest,
                        )

                        media_type = get_media_type(request.file_path)

                        if media_type == "image":
                            tags_to_write = request.description.image_tags
                        elif media_type == "video":
                            tags_to_write = request.description.video_tags
                        else:
                            tags_to_write = []

                        for tag in tags_to_write:
                            argfile.write(f"-{tag}={final_description}\n")

                    # Add file path and execute separator
                    argfile.write(f"{request.file_path}\n")
                    argfile.write("-execute\n")

            # Run exiftool with argfile
            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )

            # Clean up argfile
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(
                f"Failed to batch write metadata for {chunk[0].processor_name if chunk else 'unknown'} chunk: {e}"
            )
            try:
                if argfile_path is not None:
                    os.unlink(argfile_path)
            except Exception:
                pass


def _get_description_for_file(
    file_path: str,
    default_description: str,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> str:
    """Get merged description from dedup manifest if applicable, else default."""
    if dedup_manifest is None:
        return default_description

    # Import here to avoid circular imports
    from common.dedup.service import get_merged_description_for_file

    merged = get_merged_description_for_file(dedup_manifest, file_path)
    if merged:
        return merged
    return default_description


# =============================================================================
# EXIF Validation and Repair
# =============================================================================


def batch_validate_exif(file_paths: List[str]) -> Set[str]:
    """Return set of file paths with corrupted EXIF that need rebuilding."""
    if not file_paths:
        return set()

    corrupted_files = set()

    # Process in chunks of 500 files to stay well within ARG_MAX limits
    # At ~150 bytes per path, 500 files uses ~75KB (~3.5% of 2MB ARG_MAX)
    chunk_size = 500

    for i in range(0, len(file_paths), chunk_size):
        chunk = file_paths[i : i + chunk_size]
        try:
            # Use exiftool to validate files in this chunk
            cmd = ["exiftool", "-validate", "-warning"]
            cmd.extend([str(path) for path in chunk])

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )

            # Parse output to identify corrupted files
            # Format: "Warning: [minor] ... - {filename}"
            current_file = None
            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                # Check if line contains a filename (lines with "========")
                if "========" in line:
                    # Extract filename from header line
                    # Format: "======== {filename}"
                    current_file = line.replace("=", "").strip()
                    continue

                # Check for warnings indicating corruption
                if current_file and ("Warning" in line or "minor" in line.lower()):
                    if current_file not in corrupted_files:
                        corrupted_files.add(current_file)
                        logger.debug(f"Corrupted EXIF: {short_path(current_file)}")

        except Exception as e:
            logger.warning(f"Failed to batch validate EXIF for chunk: {e}")

    return corrupted_files


def batch_rebuild_exif(file_paths: List[str]) -> None:
    """Rebuild EXIF structure for corrupted files in batches."""
    if not file_paths:
        return

    # Process in chunks of 500 files for optimal throughput
    # Uses argfile approach so not limited by ARG_MAX
    chunk_size = 500
    for i in range(0, len(file_paths), chunk_size):
        chunk = file_paths[i : i + chunk_size]
        argfile_path: Optional[str] = None

        try:
            # Create temporary argfile for batch processing
            # Use process ID to ensure unique temp files per worker
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                # Write commands for each file using -execute separator
                for file_path in chunk:
                    argfile.write("-ignoreMinorErrors\n")
                    argfile.write("-overwrite_original\n")
                    argfile.write("-all=\n")
                    argfile.write("-tagsfromfile\n")
                    argfile.write("@\n")
                    argfile.write("-all:all\n")
                    argfile.write("-unsafe\n")
                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            # Run exiftool with argfile
            cmd = ["exiftool", "-@", argfile_path]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )

            # Count successes
            success_count = result.stdout.count("image files updated")
            if success_count > 0:
                logger.debug(
                    f"Successfully rebuilt EXIF for {success_count} files in chunk"
                )

            # Clean up argfile
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(f"Failed to batch rebuild EXIF for chunk: {e}")
            # Try to clean up argfile if it exists
            try:
                if argfile_path is not None:
                    os.unlink(argfile_path)
            except Exception:
                pass


# =============================================================================
# Metadata Reading
# =============================================================================


def batch_read_existing_metadata(file_paths: List[str]) -> Dict[str, Dict[str, bool]]:
    """Read existing metadata fields from files, returning presence map."""
    if not file_paths:
        return {}

    # Filter to only existing files
    existing_files = [fp for fp in file_paths if Path(fp).exists()]
    missing_files = [fp for fp in file_paths if not Path(fp).exists()]

    if missing_files:
        logger.warning(
            f"Skipping {len(missing_files)} non-existent files in metadata batch"
        )
        for missing in missing_files[:5]:  # Log first 5 as examples
            logger.debug(f"  Missing file: {missing}")
        if len(missing_files) > 5:
            logger.debug(f"  ... and {len(missing_files) - 5} more")

    if not existing_files:
        return {}

    # Process in sub-batches to isolate problematic files while maximizing throughput
    # File paths are passed directly on command line, so limited by ARG_MAX
    # At ~150 bytes per path, 500 files uses ~75KB (~3.5% of 2MB ARG_MAX)
    metadata_map = {}
    batch_size = 500

    for i in range(0, len(existing_files), batch_size):
        sub_batch = existing_files[i : i + batch_size]

        try:
            # Try this sub-batch
            sub_result = _read_metadata_batch(sub_batch)
            metadata_map.update(sub_result)
        except Exception as e:
            logger.warning(f"Sub-batch failed, processing files individually: {e}")

            # Process files one at a time as fallback
            for file_path in sub_batch:
                try:
                    individual_result = _read_metadata_batch([file_path])
                    metadata_map.update(individual_result)
                except Exception as individual_error:
                    logger.debug(
                        f"Read failed: {short_path(file_path)}: {individual_error}"
                    )
                    continue

    return metadata_map


def _read_metadata_batch(file_paths: List[str]) -> Dict[str, Dict[str, bool]]:
    """Read metadata from a batch of files using exiftool JSON output."""
    if not file_paths:
        return {}

    metadata_map = {}

    # Use exiftool with JSON output for easy parsing
    cmd = [
        "exiftool",
        "-ignoreMinorErrors",
        "-json",
        "-s",
        "-DateTimeOriginal",
        "-CreateDate",
        "-ModifyDate",
        "-ImageDescription",
        "-Comment",
        "-Description",
        "-GPSLatitude",
        "-GPSLongitude",
        "-GPSAltitude",
    ]
    cmd.extend([str(path) for path in file_paths])

    # Don't use check=True - handle errors manually
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )

    # Check for serious errors (not just missing files)
    if result.returncode != 0:
        logger.debug(f"Exiftool returned non-zero exit code {result.returncode}")
        if result.stderr:
            logger.debug(f"Exiftool stderr: {result.stderr[:500]}")

        # Still try to parse whatever output we got
        if not result.stdout:
            raise Exception(f"No output from exiftool (exit code {result.returncode})")

    # Parse JSON output
    metadata_list = json.loads(result.stdout)

    for item in metadata_list:
        source_file = item.get("SourceFile")
        if not source_file:
            continue

        # Build field presence map
        fields = {}
        for field_name in [
            "DateTimeOriginal",
            "CreateDate",
            "ModifyDate",
            "ImageDescription",
            "Comment",
            "Description",
            "GPSLatitude",
            "GPSLongitude",
            "GPSAltitude",
        ]:
            value = item.get(field_name)
            # Consider field as having data if it's not empty, not just "-",
            # and not an invalid date like "0000:00:00 00:00:00"
            has_data = bool(value and value != "-")
            if has_data and field_name in [
                "DateTimeOriginal",
                "CreateDate",
                "ModifyDate",
            ]:
                # Check for invalid date patterns
                if value.startswith("0000:00:00"):
                    has_data = False
            fields[field_name] = has_data

        metadata_map[source_file] = fields

    return metadata_map
