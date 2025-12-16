#!/usr/bin/env python3
"""
Batch Exiftool Processing Module

This module provides batch processing functions for exiftool operations,
eliminating the need for locks and providing significant performance improvements
by processing multiple files in single exiftool invocations.
"""

import json
import logging
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from common.progress import PHASE_EXIF, chunked_progress
from common.utils import get_media_type, get_gps_format

logger = logging.getLogger(__name__)


def batch_validate_exif(file_paths: List[str]) -> Set[str]:
    """Validate EXIF structure for multiple files in one exiftool call

    Processes files in chunks to avoid command line length limits.
    File paths are passed directly on the command line (not via argfile),
    so chunk size is limited by ARG_MAX (~2MB on Linux).

    Args:
        file_paths: List of file paths to validate

    Returns:
        Set of file paths that have corrupted EXIF and need rebuilding
    """
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
                    corrupted_files.add(current_file)
                    logger.debug(f"Detected corrupted EXIF in {current_file}")

        except Exception as e:
            logger.warning(f"Failed to batch validate EXIF for chunk: {e}")

    return corrupted_files


def batch_rebuild_exif(file_paths: List[str]) -> None:
    """Rebuild EXIF structure for corrupted files using batch operations

    Processes files in chunks using argfile to avoid command line length limits.
    Uses exiftool's -execute flag for multiple independent operations.

    Args:
        file_paths: List of file paths to rebuild
    """
    if not file_paths:
        return

    # Process in chunks of 500 files for optimal throughput
    # Uses argfile approach so not limited by ARG_MAX
    chunk_size = 500
    for i in range(0, len(file_paths), chunk_size):
        chunk = file_paths[i : i + chunk_size]

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
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_read_existing_metadata(file_paths: List[str]) -> Dict[str, Dict[str, bool]]:
    """Read existing metadata from multiple files in one exiftool call

    Handles partial failures gracefully by:
    1. Filtering non-existent files before processing
    2. Processing in sub-batches to isolate problematic files
    3. Falling back to individual file processing if a sub-batch fails

    Args:
        file_paths: List of file paths to read metadata from

    Returns:
        Dict mapping file_path -> dict of field_name -> has_data (bool)
    """
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
                    logger.debug(f"Failed to read {file_path}: {individual_error}")
                    continue

    return metadata_map


def _read_metadata_batch(file_paths: List[str]) -> Dict[str, Dict[str, bool]]:
    """Internal helper to read metadata from a batch of files

    Args:
        file_paths: List of file paths to read metadata from

    Returns:
        Dict mapping file_path -> dict of field_name -> has_data (bool)

    Raises:
        Exception: If exiftool fails or JSON parsing fails
    """
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
        check=False,  # Changed from True to handle errors gracefully
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
        for field in [
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
            value = item.get(field)
            # Consider field as having data if it's not empty, not just "-",
            # and not an invalid date like "0000:00:00 00:00:00"
            has_data = bool(value and value != "-")
            if has_data and field in ["DateTimeOriginal", "CreateDate", "ModifyDate"]:
                # Check for invalid date patterns
                if value.startswith("0000:00:00"):
                    has_data = False
            fields[field] = has_data

        metadata_map[source_file] = fields

    return metadata_map


# ============================================================================
# Processor-Specific Batch Metadata Writers
# ============================================================================


def batch_write_metadata_google_photos(
    file_info: List[Tuple[str, dict, str, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for Google Photos files in batch

    Args:
        file_info: List of (file_path, media_data, album_name, export_username) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Process in chunks of 500 files for optimal throughput
    # Uses argfile approach so not limited by ARG_MAX
    chunk_size = 500
    for chunk in chunked_progress(
        file_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            # Create temporary argfile for batch processing
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                # Write commands for each file
                for file_path, media_data, album_name, export_username in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    # Start command for this file
                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")
                    argfile.write("-ignoreMinorErrors\n")

                    # Add date/time metadata if available and field is empty
                    timestamp_str = media_data.get("capture_timestamp")
                    if timestamp_str:
                        date_obj = datetime.fromisoformat(
                            timestamp_str.replace("Z", "+00:00")
                        )
                        exif_date = date_obj.strftime("%Y:%m:%d %H:%M:%S")

                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")

                    # Add GPS metadata if available and not already present
                    gps_data = media_data.get("gps", {})
                    latitude = gps_data.get("latitude", 0.0)
                    longitude = gps_data.get("longitude", 0.0)
                    altitude = gps_data.get("altitude", 0.0)

                    if (
                        latitude != 0.0 or longitude != 0.0
                    ) and not existing_fields.get("GPSLatitude", False):
                        lat = float(latitude)
                        lon = float(longitude)

                        gps_format = get_gps_format(file_path)

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

                        if altitude != 0.0:
                            argfile.write(f"-GPSAltitude={altitude}\n")

                    # Build source description
                    source_description = f"Source: Google Photos/{export_username}"

                    # Determine file type for appropriate metadata tags
                    media_type = get_media_type(file_path)

                    if media_type == "image":
                        if not existing_fields.get("ImageDescription", False):
                            argfile.write(f"-ImageDescription={source_description}\n")
                            argfile.write(
                                f"-IPTC:Caption-Abstract={source_description}\n"
                            )
                    elif media_type == "video":
                        if not existing_fields.get("Comment", False):
                            argfile.write(f"-Comment={source_description}\n")
                        if not existing_fields.get("Description", False):
                            argfile.write(f"-Description={source_description}\n")

                    # Add file path and execute separator
                    argfile.write(f"{file_path}\n")
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
                f"Failed to batch write metadata for google_photos chunk: {e}"
            )
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_write_metadata_google_chat(
    file_info: List[Tuple[str, dict, str, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for Google Chat files in batch

    Args:
        file_info: List of (file_path, message_data, conversation_name, export_username) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Process in chunks of 100 files
    chunk_size = 500
    for chunk in chunked_progress(
        file_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for (
                    file_path,
                    message_data,
                    conversation_name,
                    export_username,
                ) in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")
                    argfile.write("-ignoreMinorErrors\n")

                    # Add date/time metadata
                    date_str = message_data["timestamp"]
                    if date_str:
                        exif_date = date_str.replace("-", ":")

                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")

                    # Build source description
                    source_description = f"Source: Google Chat/{export_username}&#xa;Conversation: \"{conversation_name}\"&#xa;Sender: \"{message_data['sender']}\""

                    media_type = get_media_type(file_path)

                    if media_type == "image":
                        if not existing_fields.get("ImageDescription", False):
                            argfile.write(f"-ImageDescription={source_description}\n")
                            argfile.write(
                                f"-IPTC:Caption-Abstract={source_description}\n"
                            )
                    elif media_type == "video":
                        if not existing_fields.get("Comment", False):
                            argfile.write(f"-Comment={source_description}\n")
                        if not existing_fields.get("Description", False):
                            argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(f"Failed to batch write metadata for google_chat chunk: {e}")
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_write_metadata_google_voice(
    file_info: List[Tuple[str, dict, str, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for Google Voice files in batch

    Args:
        file_info: List of (file_path, message_data, conversation_name, export_username) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Process in chunks of 100 files
    chunk_size = 500
    for chunk in chunked_progress(
        file_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for (
                    file_path,
                    message_data,
                    conversation_name,
                    export_username,
                ) in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")
                    argfile.write("-ignoreMinorErrors\n")

                    # Add date/time metadata
                    date_str = message_data["timestamp"]
                    if date_str:
                        exif_date = date_str.replace("-", ":")

                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")

                    # Build source description
                    source_description = f"Source: Google Voice/{export_username}&#xa;Conversation: \"{conversation_name}\"&#xa;Sender: \"{message_data['sender']}\""

                    media_type = get_media_type(file_path)

                    if media_type == "image":
                        if not existing_fields.get("ImageDescription", False):
                            argfile.write(f"-ImageDescription={source_description}\n")
                            argfile.write(
                                f"-IPTC:Caption-Abstract={source_description}\n"
                            )
                    elif media_type == "video":
                        if not existing_fields.get("Comment", False):
                            argfile.write(f"-Comment={source_description}\n")
                        if not existing_fields.get("Description", False):
                            argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(
                f"Failed to batch write metadata for google_voice chunk: {e}"
            )
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_write_metadata_instagram_messages(
    file_info: List[Tuple[str, dict, str, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for Instagram Messages files in batch.

    Preserves existing date/time EXIF data, but always writes description fields.

    Args:
        file_info: List of (file_path, message_data, conversation_title, export_username) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Process in chunks of 500 files
    chunk_size = 500
    for chunk in chunked_progress(
        file_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for (
                    file_path,
                    message_data,
                    conversation_title,
                    export_username,
                ) in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")

                    # Log what metadata is being embedded
                    logger.debug(f"Embedding metadata for: {Path(file_path).name}")

                    # Add date/time metadata only if not already present
                    date_str = message_data["timestamp"]
                    if date_str:
                        exif_date = date_str.replace("-", ":")
                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")
                        logger.debug(f"  DateTime: {exif_date}")

                    # Build source description
                    source_description = f"Source: Instagram/{export_username}/messages&#xa;Conversation: \"{conversation_title}\"&#xa;Sender: \"{message_data['sender']}\""

                    media_type = get_media_type(file_path)
                    logger.debug(f"  Media type: {media_type}")
                    logger.debug(f"  Conversation: {conversation_title}")
                    logger.debug(f"  Sender: {message_data['sender']}")
                    logger.debug(f"  Source description: {source_description}")

                    # Always write description fields (source context is valuable)
                    if media_type == "image":
                        argfile.write(f"-ImageDescription={source_description}\n")
                        argfile.write(f"-IPTC:Caption-Abstract={source_description}\n")
                    elif media_type == "video":
                        argfile.write(f"-Comment={source_description}\n")
                        argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(
                f"Failed to batch write metadata for instagram_messages chunk: {e}"
            )
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_write_metadata_instagram_public(
    file_info: List[Tuple[str, dict, str, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for Instagram Public Media files in batch.

    Preserves existing date/time and GPS EXIF data, but always writes description fields.

    Args:
        file_info: List of (file_path, post_data, export_username, media_type) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Process in chunks of 500 files
    chunk_size = 500
    for chunk in chunked_progress(
        file_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for file_path, post_data, export_username, media_type_category in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")

                    # Log what metadata is being embedded
                    logger.debug(f"Embedding metadata for: {Path(file_path).name}")

                    # Add date/time metadata only if not already present
                    date_str = post_data["timestamp"]
                    if date_str:
                        exif_date = date_str.replace("-", ":")
                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")
                        logger.debug(f"  DateTime: {exif_date}")

                    # Add GPS coordinates if available and not already present
                    if (
                        post_data.get("latitude") is not None
                        and post_data.get("longitude") is not None
                        and not existing_fields.get("GPSLatitude", False)
                    ):
                        lat = float(post_data["latitude"])
                        lon = float(post_data["longitude"])

                        gps_format = get_gps_format(file_path)
                        logger.debug(
                            f"  GPS: lat={lat}, lon={lon} (format={gps_format})"
                        )

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

                    # Build source description with caption
                    source_description = (
                        f"Source: Instagram/{export_username}/{media_type_category}"
                    )

                    if post_data.get("caption"):
                        # Replace newlines in caption with HTML entity to avoid breaking argfile
                        caption = post_data["caption"].replace("\n", "&#xa;")
                        source_description += f'&#xa;Caption: "{caption}"'
                        logger.debug(f"  Caption: {caption[:100]}...")

                    file_media_type = get_media_type(file_path)
                    logger.debug(f"  Media type: {file_media_type}")
                    logger.debug(f"  Source description: {source_description[:150]}...")

                    # Always write description fields (source context is valuable)
                    if file_media_type == "image":
                        argfile.write(f"-ImageDescription={source_description}\n")
                        argfile.write(f"-IPTC:Caption-Abstract={source_description}\n")
                    elif file_media_type == "video":
                        argfile.write(f"-Comment={source_description}\n")
                        argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(
                f"Failed to batch write metadata for instagram_public chunk: {e}"
            )
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_write_metadata_instagram_old_public(
    file_info: List[Tuple[str, dict, str, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for Instagram Old Public Media files in batch

    Args:
        file_info: List of (file_path, post_data, export_username, media_type) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Process in chunks of 100 files
    chunk_size = 500
    for chunk in chunked_progress(
        file_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for file_path, post_data, export_username, _media_type in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")

                    # Get media type to apply correct date tags
                    file_media_type = get_media_type(file_path)

                    # Add date/time metadata if available and field is empty
                    date_str = post_data["timestamp"]
                    if date_str:
                        exif_date = date_str.replace("-", ":")

                        if file_media_type == "video":
                            # For videos, write QuickTime-specific date tags
                            if not existing_fields.get("DateTimeOriginal", False):
                                argfile.write(f"-DateTimeOriginal={exif_date}\n")
                            if not existing_fields.get("CreateDate", False):
                                argfile.write(f"-QuickTime:CreateDate={exif_date}\n")
                            if not existing_fields.get("ModifyDate", False):
                                argfile.write(f"-QuickTime:ModifyDate={exif_date}\n")
                        else:
                            # For images, use standard EXIF tags
                            if not existing_fields.get("DateTimeOriginal", False):
                                argfile.write(f"-DateTimeOriginal={exif_date}\n")
                            if not existing_fields.get("CreateDate", False):
                                argfile.write(f"-CreateDate={exif_date}\n")
                            if not existing_fields.get("ModifyDate", False):
                                argfile.write(f"-ModifyDate={exif_date}\n")

                    # Add GPS coordinates if available and not already present
                    if (
                        post_data.get("latitude") is not None
                        and post_data.get("longitude") is not None
                    ):
                        if not existing_fields.get("GPSLatitude", False):
                            lat = float(post_data["latitude"])
                            lon = float(post_data["longitude"])

                            gps_format = get_gps_format(file_path)

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

                    # Build source description with caption
                    source_description = f"Source: Instagram/{export_username}/posts"

                    if post_data.get("caption"):
                        # Replace newlines in caption with HTML entity to avoid breaking argfile
                        caption = post_data["caption"].replace("\n", "&#xa;")
                        source_description += f'&#xa;Caption: "{caption}"'

                    if file_media_type == "image":
                        if not existing_fields.get("ImageDescription", False):
                            argfile.write(f"-ImageDescription={source_description}\n")
                            argfile.write(
                                f"-IPTC:Caption-Abstract={source_description}\n"
                            )
                    elif file_media_type == "video":
                        if not existing_fields.get("Comment", False):
                            argfile.write(f"-Comment={source_description}\n")
                        if not existing_fields.get("Description", False):
                            argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(
                f"Failed to batch write metadata for instagram_old_public chunk: {e}"
            )
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_write_metadata_snapchat_memories(
    file_info: List[Tuple[str, dict, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for Snapchat Memories files in batch.

    Skips MKV files as they already have metadata embedded during overlay creation.
    Preserves existing date/time and GPS EXIF data, but always writes description fields.

    Args:
        file_info: List of (file_path, memory_data, export_username) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Filter out MKV files (they already have metadata from overlay creation)
    filtered_info = [
        (fp, md, eu) for fp, md, eu in file_info if not fp.endswith(".mkv")
    ]

    if not filtered_info:
        return

    # Process in chunks of 500 files
    chunk_size = 500
    for chunk in chunked_progress(
        filtered_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for file_path, memory_data, export_username in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")

                    # Add date/time metadata only if not already present
                    date_str = memory_data["date"]
                    if date_str:
                        exif_date = date_str.replace("-", ":").replace(" UTC", "")
                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")

                    # Add GPS coordinates if available and not already present
                    if (
                        "latitude" in memory_data
                        and "longitude" in memory_data
                        and not existing_fields.get("GPSLatitude", False)
                    ):
                        lat = float(memory_data["latitude"])
                        lon = float(memory_data["longitude"])

                        gps_format = get_gps_format(file_path)

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

                    # Build source description
                    source_description = f"Source: Snapchat/{export_username}/memories"

                    media_type = get_media_type(file_path)

                    # Always write description fields (source context is valuable)
                    if media_type == "image":
                        argfile.write(f"-ImageDescription={source_description}\n")
                        argfile.write(f"-IPTC:Caption-Abstract={source_description}\n")
                    elif media_type == "video":
                        argfile.write(f"-Comment={source_description}\n")
                        argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(
                f"Failed to batch write metadata for snapchat_memories chunk: {e}"
            )
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_write_metadata_snapchat_messages(
    file_info: List[Tuple[str, dict, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for Snapchat Messages files in batch.

    Skips MKV files as they already have metadata embedded during overlay creation.
    Preserves existing date/time EXIF data, but always writes description fields.

    Args:
        file_info: List of (file_path, message, export_username) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Filter out MKV files (they already have metadata from overlay creation)
    filtered_info = [
        (fp, msg, eu) for fp, msg, eu in file_info if not fp.endswith(".mkv")
    ]

    if not filtered_info:
        return

    # Process in chunks of 500 files
    chunk_size = 500
    for chunk in chunked_progress(
        filtered_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for file_path, message, export_username in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")

                    # Check if this is a merged message (duplicate across conversations)
                    is_merged = "messages" in message and isinstance(
                        message["messages"], list
                    )

                    # Add date/time metadata only if not already present
                    if is_merged:
                        date_str = message.get("primary_created")
                    else:
                        date_str = message.get("created")

                    if date_str:
                        # Handle both legacy date-only and standard timestamp formats
                        if len(date_str) == 10 and date_str.count(":") == 0:
                            exif_date = date_str.replace("-", ":") + " 12:00:00"
                        else:
                            exif_date = date_str.replace("-", ":").replace(" UTC", "")

                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")

                    # Build source description with unified format
                    description_parts = [f"Source: Snapchat/{export_username}/messages"]

                    if is_merged:
                        # Merged message - list all conversations in unified format
                        for msg in message["messages"]:
                            conv_type = msg.get("conversation_type")
                            conv_id = msg.get("conversation_id")
                            sender = msg.get("sender", "unknown")
                            content = msg.get("content", "")

                            if conv_id:
                                if conv_type == "dm":
                                    conv_name = f"DM with {conv_id}"
                                else:
                                    conv_name = msg.get(
                                        "conversation_title", "Unknown Group"
                                    )

                                # Replace newlines in content with space
                                if content:
                                    content = content.replace("\n", " ")
                                    description_parts.append(
                                        f'  - {sender} in "{conv_name}": "{content}"'
                                    )
                                else:
                                    description_parts.append(
                                        f'  - {sender} in "{conv_name}"'
                                    )

                        source_description = "&#xa;".join(description_parts)
                    else:
                        # Single message - use same unified format
                        conv_type = message.get("conversation_type")
                        conv_id = message.get("conversation_id")

                        if conv_id is None or conv_type is None:
                            # Orphaned media - minimal metadata
                            source_description = (
                                f"Source: Snapchat/{export_username}/messages"
                            )
                        else:
                            # Normal message with unified format
                            if conv_type == "dm":
                                conv_name = f"DM with {conv_id}"
                            else:
                                conv_name = message.get(
                                    "conversation_title", "Unknown Group"
                                )

                            sender = message.get("sender", "unknown")
                            content = message.get("content", "")

                            # Replace newlines in content with space
                            if content:
                                content = content.replace("\n", " ")
                                description_parts.append(
                                    f'  - {sender} in "{conv_name}": "{content}"'
                                )
                            else:
                                description_parts.append(
                                    f'  - {sender} in "{conv_name}"'
                                )

                            source_description = "&#xa;".join(description_parts)

                    media_type = get_media_type(file_path)

                    # Always write description fields (source context is valuable)
                    if media_type == "image":
                        argfile.write(f"-ImageDescription={source_description}\n")
                        argfile.write(f"-IPTC:Caption-Abstract={source_description}\n")
                    elif media_type == "video":
                        argfile.write(f"-Comment={source_description}\n")
                        argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(
                f"Failed to batch write metadata for snapchat_messages chunk: {e}"
            )
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_write_metadata_snapchat(
    file_info: List[Tuple[str, dict, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for unified Snapchat files in batch (memories + messages).

    Skips MKV files as they already have metadata embedded during overlay creation.
    Handles both memories-sourced and messages-sourced media based on 'source' field.
    Preserves existing date/time and GPS EXIF data, but always writes description fields.

    Args:
        file_info: List of (file_path, media_entry, export_username) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Filter out MKV files (they already have metadata from overlay creation)
    filtered_info = [
        (fp, entry, eu) for fp, entry, eu in file_info if not fp.endswith(".mkv")
    ]

    if not filtered_info:
        return

    # Process in chunks of 500 files
    chunk_size = 500
    for chunk in chunked_progress(
        filtered_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for file_path, media_entry, export_username in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")

                    # Add date/time metadata only if not already present
                    date_str = media_entry.get("created", "")
                    if date_str:
                        # Handle both legacy date-only and standard timestamp formats
                        if len(date_str) == 10 and date_str.count(":") == 0:
                            exif_date = date_str.replace("-", ":") + " 12:00:00"
                        else:
                            exif_date = date_str.replace("-", ":").replace(" UTC", "")

                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")

                    # Check source type
                    source = media_entry.get("source", "unknown")

                    if source == "memories":
                        # Memories-sourced metadata - GPS only if not present
                        location = media_entry.get("location")
                        if location and not existing_fields.get("GPSLatitude", False):
                            lat = location.get("lat")
                            lon = location.get("lon")

                            if lat is not None and lon is not None:
                                argfile.write(f"-GPSLatitude={abs(lat)}\n")
                                argfile.write(
                                    f"-GPSLatitudeRef={'N' if lat >= 0 else 'S'}\n"
                                )
                                argfile.write(f"-GPSLongitude={abs(lon)}\n")
                                argfile.write(
                                    f"-GPSLongitudeRef={'E' if lon >= 0 else 'W'}\n"
                                )

                        # Description field may include consolidated message metadata
                        description = media_entry.get("description", "")
                        if not description:
                            description = f"Source: Snapchat/{export_username}/memories"

                        media_type = get_media_type(file_path)

                        # Always write description fields (source context is valuable)
                        if media_type == "image":
                            argfile.write(f"-ImageDescription={description}\n")
                            argfile.write(f"-IPTC:Caption-Abstract={description}\n")
                        elif media_type == "video":
                            argfile.write(f"-Comment={description}\n")
                            argfile.write(f"-Description={description}\n")

                    elif source == "messages":
                        # Messages-sourced metadata
                        conv_type = media_entry.get("conversation_type")
                        conv_id = media_entry.get("conversation_id")

                        if conv_id is None or conv_type is None:
                            # Orphaned media - minimal metadata
                            source_description = (
                                f"Source: Snapchat/{export_username}/messages"
                            )
                        else:
                            # Normal message with full context
                            if conv_type == "dm":
                                conversation_context = f"DM with {conv_id}"
                            else:
                                conversation_context = media_entry.get(
                                    "conversation_title", "Unknown Group"
                                )

                            # Build description with sender information
                            description_parts = [
                                f"Source: Snapchat/{export_username}/messages",
                                f'Conversation: "{conversation_context}"',
                            ]

                            # Add sender information (may be multiple for duplicate messages)
                            senders = media_entry.get("senders", [])
                            if senders:
                                for sender_info in senders:
                                    sender = sender_info.get("sender", "unknown")
                                    content = sender_info.get("content", "")

                                    if content:
                                        # Replace newlines in content with HTML entity to avoid breaking argfile
                                        content = content.replace("\n", "&#xa;")
                                        description_parts.append(
                                            f'From {sender}: "{content}"'
                                        )
                                    else:
                                        description_parts.append(f"From {sender}")

                            source_description = "&#xa;".join(description_parts)

                        media_type = get_media_type(file_path)

                        # Always write description fields (source context is valuable)
                        if media_type == "image":
                            argfile.write(f"-ImageDescription={source_description}\n")
                            argfile.write(
                                f"-IPTC:Caption-Abstract={source_description}\n"
                            )
                        elif media_type == "video":
                            argfile.write(f"-Comment={source_description}\n")
                            argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(
                f"Failed to batch write metadata for snapchat unified chunk: {e}"
            )
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def _extract_device_from_source_export(source_export: str) -> str:
    """Extract device identifier from source_export directory name.
    
    Examples:
        "iph13p-messages-20220426" -> "iph13p"
        "mac-messages-20240601" -> "mac"
        "iphone14-messages-20241001" -> "iphone14"
    
    Args:
        source_export: Directory name like "{device}-messages-YYYYMMDD"
    
    Returns:
        Device identifier, or "unknown" if pattern doesn't match
    """
    if not source_export:
        return "unknown"
    
    # Pattern: {device}-messages-YYYYMMDD
    if "-messages-" in source_export:
        return source_export.split("-messages-")[0]
    
    return "unknown"


def _extract_export_date_from_source_export(source_export: str) -> Optional[datetime]:
    """Extract export date from source_export directory name.
    
    Examples:
        "iph13p-messages-20220426" -> datetime(2022, 4, 26)
        "mac-messages-20240601" -> datetime(2024, 6, 1)
    
    Args:
        source_export: Directory name like "{device}-messages-YYYYMMDD"
    
    Returns:
        datetime object for the export date, or None if pattern doesn't match
    """
    if not source_export or "-messages-" not in source_export:
        return None
    
    try:
        # Extract date part after "-messages-"
        date_str = source_export.split("-messages-")[1]
        # Parse YYYYMMDD format
        return datetime.strptime(date_str, "%Y%m%d")
    except (ValueError, IndexError):
        return None


def batch_write_metadata_imessage(
    file_info: List[Tuple[str, dict, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for iMessage files in batch.

    Embeds source information and timestamps into media files from iMessage exports.
    Preserves existing date/time EXIF data, but always writes description fields.

    Args:
        file_info: List of (file_path, message, export_username) tuples
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Process in chunks of 500 files
    chunk_size = 500
    for chunk in chunked_progress(
        file_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for file_path, message, export_username in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")

                    # Check if this is a merged message (duplicate across conversations)
                    is_merged = "messages" in message and isinstance(
                        message["messages"], list
                    )

                    # Add date/time metadata only if not already present
                    if is_merged:
                        date_str = message.get("primary_created")
                    else:
                        date_str = message.get("created")

                    if date_str:
                        # Convert "YYYY-MM-DD HH:MM:SS UTC" to EXIF format
                        exif_date = date_str.replace("-", ":").replace(" UTC", "")

                        # Only write date fields if they don't already exist
                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")

                    # Determine the correct source device identifier
                    if is_merged:
                        # For merged messages, find the oldest export by export date
                        oldest_msg = None
                        oldest_export_date = None
                        for msg in message["messages"]:
                            source_export = msg.get("source_export")
                            if source_export:
                                export_date = _extract_export_date_from_source_export(
                                    source_export
                                )
                                if export_date:
                                    if (
                                        oldest_export_date is None
                                        or export_date < oldest_export_date
                                    ):
                                        oldest_export_date = export_date
                                        oldest_msg = msg
                        
                        # Extract device from oldest export's source_export
                        if oldest_msg and oldest_msg.get("source_export"):
                            source_device = _extract_device_from_source_export(
                                oldest_msg["source_export"]
                            )
                        else:
                            # Fallback: try to find any source_export in messages
                            source_device = export_username
                            for msg in message["messages"]:
                                if msg.get("source_export"):
                                    source_device = _extract_device_from_source_export(
                                        msg["source_export"]
                                    )
                                    break
                    else:
                        # For non-merged messages, use the message's source_export if available
                        if message.get("source_export"):
                            source_device = _extract_device_from_source_export(
                                message["source_export"]
                            )
                        else:
                            # Fallback to export_username
                            source_device = export_username

                    # Build source description
                    description_parts = [f"Source: iMessage/{source_device}"]

                    if is_merged:
                        # Merged message - list all conversations, deduplicating identical lines
                        seen_lines = set()
                        for msg in message["messages"]:
                            conv_type = msg.get("conversation_type")
                            conv_title = msg.get("conversation_title")
                            conv_id = msg.get("conversation_id", "Unknown")
                            sender = msg.get("sender", "unknown")
                            content = msg.get("content", "")

                            if conv_type == "dm":
                                # For DMs, use title if available, otherwise conversation_id
                                display_name = conv_title if conv_title else conv_id
                                conv_name = f"DM with {display_name}"
                            else:
                                conv_name = conv_title or "Unknown"

                            # Build message line (without source export)
                            msg_line = f'  - {sender} in "{conv_name}"'
                            if content:
                                # Replace newlines in content with space
                                content = content.replace("\n", " ")
                                # Truncate long messages
                                if len(content) > 100:
                                    content = content[:97] + "..."
                                msg_line += f': "{content}"'

                            # Deduplicate identical message lines
                            if msg_line not in seen_lines:
                                seen_lines.add(msg_line)
                                description_parts.append(msg_line)

                        source_description = "&#xa;".join(description_parts)
                    else:
                        # Single message
                        conv_type = message.get("conversation_type")
                        conv_title = message.get("conversation_title")
                        conv_id = message.get("conversation_id", "Unknown")
                        sender = message.get("sender", "unknown")
                        content = message.get("content", "")

                        if conv_type == "dm":
                            # For DMs, use title if available, otherwise conversation_id
                            display_name = conv_title if conv_title else conv_id
                            conv_name = f"DM with {display_name}"
                        else:
                            conv_name = conv_title or "Unknown"

                        msg_line = f'  - {sender} in "{conv_name}"'
                        if content:
                            content = content.replace("\n", " ")
                            if len(content) > 100:
                                content = content[:97] + "..."
                            msg_line += f': "{content}"'

                        description_parts.append(msg_line)
                        source_description = "&#xa;".join(description_parts)

                    # Add Live Photo marker if applicable
                    if message.get("is_live_photo_video"):
                        source_description += "&#xa;[Live Photo Video]"

                    media_type = get_media_type(file_path)

                    # Always write description fields (source context is valuable)
                    if media_type == "image":
                        argfile.write(f"-ImageDescription={source_description}\n")
                        argfile.write(f"-IPTC:Caption-Abstract={source_description}\n")
                    elif media_type == "video":
                        argfile.write(f"-Comment={source_description}\n")
                        argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(f"Failed to batch write metadata for imessage chunk: {e}")
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass


def batch_write_metadata_discord(
    file_info: List[Tuple[str, dict, str]],
    existing_metadata_map: Dict[str, Dict[str, bool]],
) -> None:
    """Write metadata for Discord files in batch.

    Note: Discord exports only contain messages sent by the exporting user,
    so there is no sender field - the export user is always the sender.
    Preserves existing date/time EXIF data, but always writes description fields.

    Args:
        file_info: List of (file_path, message_info, export_username) tuples
            where message_info contains: message, channel_id, channel_type,
            channel_title, guild_name
        existing_metadata_map: Dict mapping file_path -> field presence map
    """
    if not file_info:
        return

    # Process in chunks of 500 files
    chunk_size = 500
    for chunk in chunked_progress(
        file_info, chunk_size, PHASE_EXIF, "Writing metadata"
    ):
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"exiftool_{os.getpid()}_",
                suffix=".args",
                delete=False,
            ) as argfile:
                argfile_path = argfile.name

                for file_path, message_info, export_username in chunk:
                    existing_fields = existing_metadata_map.get(file_path, {})

                    argfile.write("-E\n")  # Enable HTML character entities
                    argfile.write("-api\n")
                    argfile.write("largefilesupport=1\n")
                    argfile.write("-overwrite_original\n")

                    # Extract message and channel context
                    message = message_info.get("message", {})
                    channel_type = message_info.get("channel_type", "unknown")
                    channel_title = message_info.get("channel_title", "Unknown")
                    guild_name = message_info.get("guild_name")

                    # Add date/time metadata only if not already present
                    timestamp = message.get("timestamp", "")
                    if timestamp:
                        # Discord format: "YYYY-MM-DD HH:MM:SS UTC"
                        # Convert to EXIF format
                        date_str = timestamp.replace(" UTC", "").strip()
                        exif_date = date_str.replace("-", ":")

                        # Only write date fields if they don't already exist
                        if not existing_fields.get("DateTimeOriginal", False):
                            argfile.write(f"-DateTimeOriginal={exif_date}\n")
                        if not existing_fields.get("CreateDate", False):
                            argfile.write(f"-CreateDate={exif_date}\n")
                        if not existing_fields.get("ModifyDate", False):
                            argfile.write(f"-ModifyDate={exif_date}\n")

                    # Build source description matching Snapchat/iMessage style
                    description_parts = [f"Source: Discord/{export_username}"]

                    # Build conversation name based on channel type
                    if channel_type == "dm":
                        # Extract recipient from title like "Direct Message with username#0"
                        if "Direct Message with" in channel_title:
                            recipient = channel_title.replace("Direct Message with ", "")
                            conv_name = f"DM with {recipient}"
                        else:
                            conv_name = f"DM with {channel_title}"
                    elif channel_type == "group_dm":
                        conv_name = "Group DM"
                    elif channel_type == "server":
                        # Extract channel name from title
                        if " in " in channel_title:
                            channel_name = channel_title.split(" in ")[0]
                        else:
                            channel_name = channel_title
                        if guild_name:
                            conv_name = f"#{channel_name} in {guild_name}"
                        else:
                            conv_name = f"#{channel_name}"
                    else:
                        conv_name = channel_title

                    # Add conversation context with message content
                    content = message.get("content", "")
                    if content:
                        # Replace newlines with spaces and truncate
                        content = content.replace("\n", " ")
                        if len(content) > 100:
                            content = content[:97] + "..."
                        description_parts.append(f'  - in "{conv_name}": "{content}"')
                    else:
                        description_parts.append(f'  - in "{conv_name}"')

                    source_description = "&#xa;".join(description_parts)

                    media_type = get_media_type(file_path)

                    # Always write description fields (source context is valuable)
                    if media_type == "image":
                        argfile.write(f"-ImageDescription={source_description}\n")
                        argfile.write(f"-IPTC:Caption-Abstract={source_description}\n")
                    elif media_type == "video":
                        argfile.write(f"-Comment={source_description}\n")
                        argfile.write(f"-Description={source_description}\n")

                    argfile.write(f"{file_path}\n")
                    argfile.write("-execute\n")

            cmd = ["exiftool", "-@", argfile_path]
            subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            os.unlink(argfile_path)

        except Exception as e:
            logger.warning(f"Failed to batch write metadata for discord chunk: {e}")
            try:
                if "argfile_path" in locals():
                    os.unlink(argfile_path)
            except Exception:
                pass
