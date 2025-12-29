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
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from common.output.progress import PHASE_EXIF, chunked_progress
from common.utils import get_media_type, get_gps_format
from common.files.utils import short_path
from common.validation.invariants import InvariantViolation, validate_gps

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
    content_hash: Optional[str] = None  # For dedup description lookup
    original_filename: Optional[str] = None  # For XMP:PreservedFileName embedding


# Default preamble used by most processors
DEFAULT_PREAMBLE = ["-E", "-api", "largefilesupport=1", "-overwrite_original"]

# Formats that support XMP:PreservedFileName for original filename embedding
# Excludes: .bmp (no metadata), .webm/.mkv (Matroska read-only in exiftool)
XMP_PRESERVED_FILENAME_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".tiff",
    ".tif",
    ".dng",
    ".avif",
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
}


# =============================================================================
# Datetime Tag Builder
# =============================================================================


def build_datetime_tags(
    timestamp_str: Optional[str],
    strip_utc: bool = False,
    date_only_default_time: str = "12:00:00",
) -> List[Tuple[str, str]]:
    """Build standard EXIF datetime tags from a timestamp string.

    Handles multiple timestamp formats:
    - ISO format: "2024-01-15T12:30:00Z" or "2024-01-15T12:30:00+00:00"
    - Standard: "YYYY-MM-DD HH:MM:SS" (with optional " UTC" suffix)
    - Date only: "YYYY-MM-DD" (uses date_only_default_time)

    Args:
        timestamp_str: Timestamp string in various formats.
        strip_utc: If True, strips " UTC" suffix before processing.
        date_only_default_time: Time to use for date-only strings (default "12:00:00").

    Returns:
        List of (tag_name, exif_date) tuples for DateTimeOriginal, CreateDate,
        ModifyDate. Returns empty list if timestamp_str is None or empty.
    """
    if not timestamp_str:
        return []

    # Strip UTC suffix if requested
    if strip_utc:
        timestamp_str = timestamp_str.replace(" UTC", "")

    # Handle ISO format timestamps (with T separator and Z or timezone)
    if "T" in timestamp_str:
        try:
            date_obj = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            exif_date = date_obj.strftime("%Y:%m:%d %H:%M:%S")
        except ValueError:
            # Fallback to simple replacement if parsing fails
            exif_date = timestamp_str.replace("T", " ").replace("Z", "")
            exif_date = exif_date.split("+")[0].replace("-", ":")
    # Handle date-only format (YYYY-MM-DD)
    elif len(timestamp_str) == 10 and timestamp_str.count("-") == 2:
        exif_date = timestamp_str.replace("-", ":") + " " + date_only_default_time
    # Handle standard format (YYYY-MM-DD HH:MM:SS)
    else:
        exif_date = timestamp_str.replace("-", ":")

    return [
        ("DateTimeOriginal", exif_date),
        ("CreateDate", exif_date),
        ("ModifyDate", exif_date),
    ]


# Preamble with ignoreMinorErrors (used by Google processors)
PREAMBLE_WITH_IGNORE_ERRORS = [
    "-E",
    "-api",
    "largefilesupport=1",
    "-overwrite_original",
    "-ignoreMinorErrors",
]


@dataclass(frozen=True)
class ExifWriteFailure:
    """Represents a single file's EXIF write failure."""

    file_path: str
    reason_code: str
    stderr_excerpt: str = ""


@dataclass
class BatchWriteOutcome:
    """Outcome for a batch EXIF write invocation."""

    successes: Set[str] = field(default_factory=set)
    failures: List[ExifWriteFailure] = field(default_factory=list)


@dataclass
class BatchReadResult:
    """Result of batch_read_existing_metadata."""

    metadata_map: Dict[str, Dict[str, bool]]
    missing_files: List[str]


# =============================================================================
# Per-File Error Parsing
# =============================================================================

# Pattern to extract file paths from exiftool error/warning messages
# Matches lines like:
#   "Error: File not found - /path/to/file.jpg"
#   "Error: Not a valid JPG (looks more like a TXT) - /path/to/file.jpg"
#   "Warning: Bad EXIF data - /path/to/file.jpg"
_EXIFTOOL_ERROR_PATTERN = re.compile(
    r"^(?:Error|Warning):\s+(.+?)\s+-\s+(.+)$", re.MULTILINE
)


def _parse_exiftool_failed_files(stderr: str) -> Dict[str, str]:
    """Parse exiftool stderr to extract per-file error messages.

    Args:
        stderr: The stderr output from exiftool.

    Returns:
        Dict mapping file paths to their error messages.
    """
    failed_files: Dict[str, str] = {}

    for match in _EXIFTOOL_ERROR_PATTERN.finditer(stderr):
        error_msg = match.group(1).strip()
        file_path = match.group(2).strip()
        # If same file has multiple errors, concatenate them
        if file_path in failed_files:
            failed_files[file_path] += f"; {error_msg}"
        else:
            failed_files[file_path] = error_msg

    return failed_files


# =============================================================================
# Unified Batch Executor
# =============================================================================


def batch_write_metadata_requests(
    requests: List[MetadataWriteRequest],
    existing_metadata_map: Dict[str, Dict[str, bool]],
    dedup_manifest: Optional[Dict[str, Any]] = None,
    chunk_size: int = 500,
) -> BatchWriteOutcome:
    """Write metadata for files in batches using exiftool argfiles.

    When a dedup_manifest is provided, merged metadata from all sources
    is applied to create a "super-image" with:
    - Earliest timestamp from all sources (primary attribution)
    - GPS coordinates from any source that has them
    - Description with cross-processor provenance
    """
    outcome = BatchWriteOutcome()
    if not requests:
        return outcome

    for chunk in chunked_progress(requests, chunk_size, PHASE_EXIF, "Writing metadata"):
        argfile_path: Optional[str] = None
        filtered_chunk: List[MetadataWriteRequest] = []

        # Pre-validate chunk (missing files, GPS invariants)
        for request in chunk:
            file_path = Path(request.file_path)
            if not file_path.exists():
                outcome.failures.append(
                    ExifWriteFailure(
                        file_path=request.file_path,
                        reason_code="file_missing",
                        stderr_excerpt="File missing before EXIF write",
                    )
                )
                continue

            # Validate GPS if present or merged later
            merged_meta = _get_merged_metadata_for_request(
                dedup_manifest, request.content_hash
            )
            gps_to_write = _get_gps_with_merged(request.gps, merged_meta)
            if gps_to_write is not None:
                validate_gps(
                    gps_to_write.latitude,
                    gps_to_write.longitude,
                    processor=request.processor_name,
                    file_path=request.file_path,
                )

            filtered_chunk.append(request)

        if not filtered_chunk:
            continue

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for request in filtered_chunk:
                    existing_fields = existing_metadata_map.get(request.file_path, {})

                    merged_meta = _get_merged_metadata_for_request(
                        dedup_manifest, request.content_hash
                    )

                    for flag in request.arg_preamble:
                        argfile.write(f"{flag}\n")

                    datetime_tags = _get_datetime_tags_with_merged(
                        request.datetime_tags, merged_meta
                    )
                    for tag_name, tag_value in datetime_tags:
                        if not existing_fields.get(tag_name, False):
                            argfile.write(f"-{tag_name}={tag_value}\n")

                    gps_to_write = _get_gps_with_merged(request.gps, merged_meta)
                    if gps_to_write and not existing_fields.get("GPSLatitude", False):
                        lat = gps_to_write.latitude
                        lon = gps_to_write.longitude

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
                            gps_to_write.altitude is not None
                            and gps_to_write.altitude != 0.0
                        ):
                            argfile.write(f"-GPSAltitude={gps_to_write.altitude}\n")

                    if request.original_filename:
                        ext = Path(request.file_path).suffix.lower()
                        if ext in XMP_PRESERVED_FILENAME_EXTENSIONS:
                            argfile.write(
                                f"-XMP:PreservedFileName={request.original_filename}\n"
                            )

                    if request.description:
                        final_description = _get_description_for_file(
                            request.description.default_description,
                            dedup_manifest,
                            request.content_hash,
                            request.processor_name,
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

                    argfile.write(f"{request.file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )

            # Parse per-file errors from stderr for precise failure attribution
            stderr_text = (result.stderr or "").strip()
            failed_files = _parse_exiftool_failed_files(stderr_text)

            # Classify each file individually based on whether it appears in errors
            for request in filtered_chunk:
                if request.file_path in failed_files:
                    # This specific file failed - determine reason code
                    error_msg = failed_files[request.file_path]
                    reason_code = "exiftool_write_failed"
                    if (
                        "format error" in error_msg.lower()
                        or "not a valid" in error_msg.lower()
                    ):
                        reason_code = "format_error"
                    elif "moov atom not found" in error_msg.lower():
                        reason_code = "probe_error"
                    elif "file not found" in error_msg.lower():
                        reason_code = "file_missing"

                    outcome.failures.append(
                        ExifWriteFailure(
                            file_path=request.file_path,
                            reason_code=reason_code,
                            stderr_excerpt=error_msg[:500],
                        )
                    )
                else:
                    # File not in error list - it succeeded
                    outcome.successes.add(request.file_path)

            os.unlink(argfile_path)

        except InvariantViolation:
            # Hard-fail invariants bubble up
            raise
        except Exception as e:
            logger.warning(
                f"Failed to batch write metadata for {filtered_chunk[0].processor_name if filtered_chunk else 'unknown'} chunk: {e}"
            )
            try:
                if argfile_path is not None:
                    os.unlink(argfile_path)
            except Exception:
                pass

    return outcome


def _get_merged_metadata_for_request(
    dedup_manifest: Optional[Dict[str, Any]],
    content_hash: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Get merged metadata from dedup manifest for a file.

    Args:
        dedup_manifest: Deduplication manifest.
        content_hash: Content hash of the file.

    Returns:
        Merged metadata dict if found, None otherwise.
    """
    if dedup_manifest is None or content_hash is None:
        return None

    # Import here to avoid circular imports
    from common.dedup.service import get_merged_metadata_for_hash

    return get_merged_metadata_for_hash(dedup_manifest, content_hash)


def _get_datetime_tags_with_merged(
    original_tags: List[Tuple[str, str]],
    merged_meta: Optional[Dict[str, Any]],
) -> List[Tuple[str, str]]:
    """Get datetime tags, using merged timestamp if earlier.

    The merged timestamp represents the earliest occurrence of this image
    across all sources (primary attribution). If the merged timestamp is
    earlier than the processor's timestamp, use the merged one.

    Args:
        original_tags: Original datetime tags from the request.
        merged_meta: Merged metadata from dedup manifest.

    Returns:
        Datetime tags to write, potentially using merged timestamp.
    """
    if not merged_meta:
        return original_tags

    merged_ts_str = merged_meta.get("timestamp")
    if not merged_ts_str:
        return original_tags

    # Parse merged timestamp (ISO format)
    try:
        merged_ts = datetime.fromisoformat(merged_ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return original_tags

    # Format merged timestamp for EXIF (YYYY:MM:DD HH:MM:SS)
    merged_exif_value = merged_ts.strftime("%Y:%m:%d %H:%M:%S")

    # If original tags exist, compare timestamps and use earliest
    if original_tags:
        # Parse the first original tag value to compare
        try:
            original_value = original_tags[0][1]
            # Handle various formats
            if ":" in original_value and len(original_value) >= 19:
                # EXIF format: YYYY:MM:DD HH:MM:SS
                original_ts = datetime.strptime(
                    original_value[:19], "%Y:%m:%d %H:%M:%S"
                )
                if merged_ts.replace(tzinfo=None) < original_ts:
                    # Merged is earlier - use it for all tags
                    return [
                        (tag_name, merged_exif_value) for tag_name, _ in original_tags
                    ]
        except (ValueError, IndexError):
            pass
        return original_tags

    # No original tags - provide merged timestamp with standard EXIF tags
    return [
        ("DateTimeOriginal", merged_exif_value),
        ("CreateDate", merged_exif_value),
        ("ModifyDate", merged_exif_value),
    ]


def _get_gps_with_merged(
    original_gps: Optional[GPSData],
    merged_meta: Optional[Dict[str, Any]],
) -> Optional[GPSData]:
    """Get GPS data, using merged GPS if original is missing.

    If the current file doesn't have GPS coordinates but another source
    in the duplicate group does, use the merged GPS data.

    Args:
        original_gps: Original GPS data from the request.
        merged_meta: Merged metadata from dedup manifest.

    Returns:
        GPS data to write (original if present, merged as fallback).
    """
    # If original has GPS, use it
    if original_gps is not None:
        return original_gps

    # Check for merged GPS
    if merged_meta is None:
        return None

    merged_gps = merged_meta.get("gps")
    if not merged_gps:
        return None

    lat = merged_gps.get("latitude")
    lon = merged_gps.get("longitude")

    if lat is None or lon is None:
        return None

    # Return merged GPS as GPSData
    return GPSData(latitude=lat, longitude=lon, altitude=None)


def _get_description_for_file(
    default_description: str,
    dedup_manifest: Optional[Dict[str, Any]] = None,
    content_hash: Optional[str] = None,
    processor_name: str = "unknown",
) -> str:
    """Enhance description with secondary sources from dedup manifest.

    Rather than replacing the processor's rich description, this function
    appends secondary source lines for cross-processor duplicates. This
    preserves the processor's context (sender, conversation, content) while
    adding provenance from other platforms.

    Args:
        default_description: The processor's rich description to enhance.
        dedup_manifest: Optional deduplication manifest.
        content_hash: Content hash for manifest lookup (more reliable than path).
        processor_name: Name of the processor that owns this file.

    Returns:
        Enhanced description with secondary sources appended, or default
        if no manifest or no cross-processor duplicates found.
    """
    if dedup_manifest is None or content_hash is None:
        return default_description

    # Import here to avoid circular imports
    from common.dedup.service import get_secondary_sources_for_hash
    from common.dedup.description import format_secondary_source

    # Get deduplicated secondary sources (excludes same-processor duplicates)
    secondary_sources = get_secondary_sources_for_hash(
        dedup_manifest, content_hash, processor_name
    )

    if not secondary_sources:
        return default_description

    # Append secondary source lines to processor's description
    lines = [default_description]
    for entry in secondary_sources:
        lines.append(format_secondary_source(entry))

    return "\n".join(lines)


# =============================================================================
# EXIF Validation and Repair
# =============================================================================


def batch_validate_exif(file_paths: Sequence[Optional[str]]) -> Set[str]:
    from common.utils import EXIF_BEARING_EXTENSIONS

    # Filter out None values
    valid_paths = [p for p in file_paths if p is not None]
    if not valid_paths:
        return set()

    # Skip HEIC files entirely - the EXIF rebuild process corrupts their complex
    # multi-image container structure (depth maps, auxiliary images, etc.)
    skip_extensions = {".heic"}

    # Filter to only EXIF-bearing formats (PNG/video/GIF don't have EXIF structure)
    # Also exclude formats where rebuild causes corruption
    exif_paths = [
        p
        for p in valid_paths
        if Path(p).suffix.lower() in EXIF_BEARING_EXTENSIONS
        and Path(p).suffix.lower() not in skip_extensions
    ]
    if not exif_paths:
        return set()

    corrupted_files = set()

    # Process in chunks of 500 files to stay well within ARG_MAX limits
    # At ~150 bytes per path, 500 files uses ~75KB (~3.5% of 2MB ARG_MAX)
    chunk_size = 500

    for i in range(0, len(exif_paths), chunk_size):
        chunk = exif_paths[i : i + chunk_size]
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
            # Format: "======== {filename}" followed by "Warning: ..."
            current_file = None
            has_warning = False

            for line in result.stdout.strip().split("\n"):
                if not line:
                    continue

                # Check if line contains a filename (lines with "========")
                if "========" in line:
                    # Before moving to next file, check if current file had warnings
                    if current_file and has_warning:
                        corrupted_files.add(current_file)
                    # Extract filename from header line
                    current_file = line.replace("=", "").strip()
                    has_warning = False
                    continue

                # Skip the summary line "Validate: N Warning(s)" - not an actual warning
                if line.strip().startswith("Validate"):
                    continue

                # Check for actual warning lines (start with "Warning")
                if current_file and line.strip().startswith("Warning"):
                    has_warning = True

            # Don't forget the last file
            if current_file and has_warning:
                corrupted_files.add(current_file)

        except Exception as e:
            logger.warning(f"Failed to batch validate EXIF for chunk: {e}")

    if corrupted_files:
        logger.debug(f"Files with corrupted EXIF: {len(corrupted_files)}")

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


def batch_read_existing_metadata(
    file_paths: Sequence[Optional[str]],
) -> BatchReadResult:
    """Read existing metadata fields from files, returning presence map."""
    # Filter out None values first
    valid_paths = [p for p in file_paths if p is not None]
    if not valid_paths:
        return BatchReadResult(metadata_map={}, missing_files=[])

    # Filter to only existing files
    existing_files = [fp for fp in valid_paths if Path(fp).exists()]
    missing_files = [fp for fp in valid_paths if not Path(fp).exists()]

    if missing_files:
        logger.warning(
            f"Skipping {len(missing_files)} non-existent files in metadata batch"
        )
        for missing in missing_files[:5]:  # Log first 5 as examples
            logger.debug(f"  Missing file: {missing}")
        if len(missing_files) > 5:
            logger.debug(f"  ... and {len(missing_files) - 5} more")

    if not existing_files:
        return BatchReadResult(metadata_map={}, missing_files=missing_files)

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

    return BatchReadResult(metadata_map=metadata_map, missing_files=missing_files)


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
