#!/usr/bin/env python3
"""iMazing iMessage Processor - handles iMazing-format exports with filename metadata."""

import json
import logging
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.description import (
    SourceEntry,
    build_source_line,
    build_message_context,
    PLATFORM_IMESSAGE,
)
from common.dedup.service import is_file_primary_by_hash, save_dedup_metadata
from common.files.utils import copy_file
from common.media.exiftool import (
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    MetadataWriteRequest,
    build_datetime_tags,
)
from common.output.logging import get_processor_logger
from common.processing import print_processing_summary
from common.output.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    ALL_MEDIA_EXTENSIONS,
    default_worker_count,
    FilenameGenerator,
    sanitize_filename,
    update_file_timestamps,
)
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.imessage_imazing.preprocess import ImazingPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class ImazingProcessor(ProcessorBase):
    """Processor for iMazing-format iMessage exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this is an iMazing iMessage export."""
        try:
            # Check for Device-Info.txt with iMazing reference
            device_info = input_path / "Device-Info.txt"
            if device_info.exists():
                try:
                    content = device_info.read_text(encoding="utf-8")
                    # iMazing exports typically contain "iMazing" in the content
                    if "iMazing" in content or "DigiDNA" in content:
                        return True
                except Exception:
                    pass

            # Check for iMazing-style vCard files (contain iMazing PRODID)
            vcf_files = list(input_path.glob("Contact - *.vcf"))
            if vcf_files:
                try:
                    content = vcf_files[0].read_text(encoding="utf-8")
                    if "iMazing" in content or "DigiDNA" in content:
                        return True
                except Exception:
                    pass

            # Check for pattern: Messages CSV + media files with timestamp prefix
            message_csvs = list(input_path.glob("Messages - *.csv"))
            if message_csvs:
                # Check for media files with iMazing timestamp pattern
                # Pattern: "YYYY-MM-DD HH MM SS - Name - filename.ext"
                import re

                pattern = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2} \d{2} \d{2} - .+ - .+$")

                for f in input_path.iterdir():
                    if f.is_file() and f.suffix.lower() in ALL_MEDIA_EXTENSIONS:
                        if pattern.match(f.stem + f.suffix):
                            return True

            # Check for preprocessed iMazing export
            metadata_file = input_path / "metadata.json"

            if metadata_file.exists():
                try:
                    with open(metadata_file, "r", encoding="utf-8") as f:
                        metadata = json.load(f)

                    # iMazing preprocessed exports have export_format: "imazing"
                    if isinstance(metadata, dict) and "conversations" in metadata:
                        export_info = metadata.get("export_info", {})
                        if export_info.get("export_format") == "imazing":
                            return True
                except (json.JSONDecodeError, KeyError):
                    pass

            return False

        except Exception as e:
            log.detection_failed("iMazing", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "iMessage-iMazing"

    @staticmethod
    def get_priority() -> int:
        return 85

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="iMessage",
            service=None,
            username_prefix="imessage",
        )

    @staticmethod
    def supports_consolidation() -> bool:
        return False

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if iMazing export needs preprocessing."""
        input_path = Path(input_dir)

        # Check if already preprocessed (has metadata.json)
        metadata_file = input_path / "metadata.json"
        if metadata_file.exists():
            return False

        # Check for raw export structure (Device-Info.txt)
        device_info = input_path / "Device-Info.txt"
        if device_info.exists():
            return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw iMazing export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        log.logger.info("Preprocessing iMazing export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = ImazingPreprocessor(
            export_path=input_path,
            output_dir=cache_path,
            workers=workers,
        )

        if preprocessor.run():
            return str(cache_path)
        return None

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata.

        Requires preprocessed directory with metadata.json and source_path fields.

        Args:
            input_dir: Path to preprocessed input directory
            **kwargs: Additional arguments (verbose, workers)

        Yields:
            Catalog entries with processor name and file data.
        """
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        def extract_preprocessed_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
            """Extract metadata from preprocessed entry."""
            result: Dict[str, Any] = {}
            if "sender" in entry:
                result["sender"] = entry["sender"]
            if "conversation_title" in entry:
                result["conversation"] = entry["conversation_title"]
            if "created" in entry:
                result["timestamp"] = entry["created"]
            return result

        catalog_iter = ImazingProcessor.catalog_from_preprocessed_if_available(
            processor_name="iMessage-iMazing",
            input_path=input_path,
            workers=workers,
            metadata_extractor=extract_preprocessed_metadata,
            container_key="conversations",
        )
        if catalog_iter is not None:
            yield from catalog_iter
            return

        logger.warning("Directory not preprocessed, skipping catalog: %s", input_path)

    @classmethod
    def process(
        cls,
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process an iMazing iMessage export."""
        return process_logic(
            input_dir=input_dir,
            output_dir=output_dir,
            workers=kwargs.get("workers"),
            dedup_manifest=dedup_manifest,
        )


def _extract_date_from_source_export(source_export: str) -> Optional[str]:
    """Extract date string from source_export directory name.

    Examples:
        "imessage-iph13p-20220426" -> "20220426"
        "imessage-m3_mbp-20251202" -> "20251202"

    Returns:
        Date string (YYYYMMDD), or None if pattern doesn't match
    """
    if not source_export or not source_export.startswith("imessage-"):
        return None

    # Format: imessage-{device}-YYYYMMDD
    # The date is always the last 8 characters after the final hyphen
    parts = source_export.rsplit("-", 1)
    if len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit():
        return parts[1]

    return None


def _extract_device_from_source_export(source_export: str) -> str:
    """Extract device identifier from source_export directory name.

    Examples:
        "imessage-iph13p-20220426" -> "iph13p"
        "imessage-m3_mbp-20251202" -> "m3_mbp"
    """
    if not source_export or not source_export.startswith("imessage-"):
        return "unknown"

    # Format: imessage-{device}-YYYYMMDD
    # Remove "imessage-" prefix, then extract device before the date suffix
    remainder = source_export[9:]  # len("imessage-") = 9
    # Find the last hyphen followed by 8 digits (date)
    parts = remainder.rsplit("-", 1)
    if len(parts) == 2 and len(parts[1]) == 8 and parts[1].isdigit():
        return parts[0]

    return "unknown"


def _build_description(message: dict, export_username: str) -> str:
    """Build the description string for an iMessage using unified format."""
    is_merged = "messages" in message and isinstance(message["messages"], list)

    # Determine the correct source device identifier
    if is_merged:
        # For merged messages, find the oldest export by export date
        oldest_msg = None
        oldest_export_date = None
        for msg in message["messages"]:
            source_export = msg.get("source_export")
            if source_export:
                try:
                    date_str = _extract_date_from_source_export(source_export)
                    if date_str:
                        export_date = datetime.strptime(date_str, "%Y%m%d")
                        if (
                            oldest_export_date is None
                            or export_date < oldest_export_date
                        ):
                            oldest_export_date = export_date
                            oldest_msg = msg
                except (ValueError, IndexError):
                    pass

        if oldest_msg and oldest_msg.get("source_export"):
            source_device = _extract_device_from_source_export(
                oldest_msg["source_export"]
            )
        else:
            source_device = export_username
            for msg in message["messages"]:
                if msg.get("source_export"):
                    source_device = _extract_device_from_source_export(
                        msg["source_export"]
                    )
                    break
    else:
        if message.get("source_export"):
            source_device = _extract_device_from_source_export(message["source_export"])
        else:
            source_device = export_username

    # Build source line using unified format
    source_entry = SourceEntry(
        processor=PLATFORM_IMESSAGE,
        username=source_device,
    )
    source_line = build_source_line(source_entry)
    description_parts = [source_line]

    if is_merged:
        seen_lines: set = set()
        for msg in message["messages"]:
            conv_type = msg.get("conversation_type")
            conv_title = msg.get("conversation_title")
            conv_id = msg.get("conversation_id", "Unknown")
            sender = msg.get("sender", "unknown")
            content = msg.get("content", "")

            if conv_type == "dm":
                display_name = conv_title if conv_title else conv_id
                conv_name = f"DM with {display_name}"
            else:
                conv_name = conv_title or "Unknown"

            # Use unified message context format
            msg_entry = SourceEntry(
                processor=PLATFORM_IMESSAGE,
                sender=sender,
                conversation=conv_name,
                content=content,
            )
            msg_line = build_message_context(msg_entry)

            if msg_line and msg_line not in seen_lines:
                seen_lines.add(msg_line)
                description_parts.append(msg_line)

        source_description = "&#xa;".join(description_parts)
    else:
        conv_type = message.get("conversation_type")
        conv_title = message.get("conversation_title")
        conv_id = message.get("conversation_id", "Unknown")
        sender = message.get("sender", "unknown")
        content = message.get("content", "")

        if conv_type == "dm":
            display_name = conv_title if conv_title else conv_id
            conv_name = f"DM with {display_name}"
        else:
            conv_name = conv_title or "Unknown"

        # Use unified message context format
        msg_entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            sender=sender,
            conversation=conv_name,
            content=content,
        )
        msg_line = build_message_context(msg_entry)
        if msg_line:
            description_parts.append(msg_line)

        source_description = "&#xa;".join(description_parts)

    if message.get("is_live_photo_video"):
        source_description += "&#xa;[Live Photo Video]"

    return source_description


def _build_metadata_requests(
    file_info: List[Tuple[Optional[str], dict, str, Optional[str], Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for iMessage files.

    Args:
        file_info: List of (file_path, message, export_username, content_hash,
                   original_filename) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for (
        file_path,
        message,
        export_username,
        content_hash,
        original_filename,
    ) in file_info:
        if file_path is None:
            continue
        is_merged = "messages" in message and isinstance(message["messages"], list)

        # Build datetime tags (iMessage format: "YYYY-MM-DD HH:MM:SS UTC")
        date_str = (
            message.get("primary_created") if is_merged else message.get("created")
        )
        datetime_tags = build_datetime_tags(date_str, strip_utc=True)

        default_description = _build_description(message, export_username)

        requests.append(
            MetadataWriteRequest(
                file_path=file_path,
                arg_preamble=DEFAULT_PREAMBLE,
                datetime_tags=datetime_tags,
                gps=None,
                description=DescriptionWriteSpec(
                    default_description=default_description,
                ),
                processor_name="imessage_imazing",
                content_hash=content_hash,
                original_filename=original_filename,
            )
        )

    return requests


def is_preprocessed(input_path: Path) -> bool:
    metadata_file = input_path / "metadata.json"
    media_dir = input_path / "media"

    if not metadata_file.exists():
        return False

    # Verify it's an iMazing preprocessed export
    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        # Basic structure checks
        if "conversations" not in metadata or "export_info" not in metadata:
            return False
        if metadata.get("export_info", {}).get("export_format") != "imazing":
            return False

        # New format: file_hashes present (source_path-based, no media/ dir needed)
        # Old format: media/ directory must exist
        has_file_hashes = "file_hashes" in metadata
        has_media_dir = media_dir.exists()

        return has_file_hashes or has_media_dir
    except (json.JSONDecodeError, KeyError):
        return False


def build_filename_index(metadata: dict) -> dict:
    """Build index mapping media filename to message metadata."""
    return ProcessorBase.build_media_filename_index(
        metadata,
        media_keys=["media_files"],
        include_orphaned=True,
    )


def _build_imessage_identifier(message: dict) -> str:
    """Build identifier for iMessage filename based on conversation type."""
    is_merged = "messages" in message and isinstance(message["messages"], list)

    if is_merged:
        return "merged"

    conv_type = message.get("conversation_type")
    conv_id = message.get("conversation_id", "")
    conv_title = message.get("conversation_title")

    if conv_type == "group" and conv_title:
        conv_prefix = sanitize_filename(conv_title)
    elif conv_id:
        # For DMs, use the contact identifier
        conv_prefix = sanitize_filename(conv_id)
    else:
        conv_prefix = "unknown"

    # Truncate if too long
    if len(conv_prefix) > 30:
        conv_prefix = conv_prefix[:30]

    return conv_prefix


def _extract_message_timestamp(message: dict) -> str | None:
    """Extract timestamp string from iMazing message metadata."""
    if "primary_created" in message:
        return message["primary_created"]
    elif "messages" in message and message["messages"]:
        return message["messages"][0].get("created")
    else:
        return message.get("created")


def _process_file_worker(args_tuple):
    """Worker function for processing files (copy and rename).

    Args:
        args_tuple: Tuple of (media_path, message, export_username, output_dir,
                    output_filename, content_hash, original_filename)

    Returns:
        Tuple of (success, output_path, message, export_username, content_hash,
        original_filename)
    """
    (
        media_path,
        message,
        export_username,
        output_dir,
        output_filename,
        content_hash,
        original_filename,
    ) = args_tuple

    try:
        output_path = output_dir / output_filename

        # Copy file
        copy_file(media_path, output_path)

        # Update timestamps
        timestamp_str = _extract_message_timestamp(message)
        update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")

        return (
            True,
            str(output_path),
            message,
            export_username,
            content_hash,
            original_filename,
        )

    except Exception as e:
        log.processing_error(media_path.name, e)
        return (
            False,
            None,
            message,
            export_username,
            content_hash,
            original_filename,
        )


def process_logic(
    input_dir: str,
    output_dir: Optional[str] = None,
    workers: Optional[int] = None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Core processing logic for iMazing iMessage exports.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling ImazingProcessor.preprocess() directly).
    """
    # Check for required tools
    if not ImazingProcessor.check_dependencies():
        return False

    log.processor_start("iMazing iMessage Processor")

    input_path = Path(input_dir)

    # Determine output directory
    if output_dir:
        output_path = Path(output_dir)
    else:
        output_path = Path("final_imessage_imazing")

    # Create output subdirectory for messages
    messages_output_dir = output_path / "messages"
    messages_output_dir.mkdir(parents=True, exist_ok=True)

    def _process_working_dir(working_dir: Path) -> bool:
        # Load metadata
        metadata_file = working_dir / "metadata.json"

        if not metadata_file.exists():
            log.file_not_found(str(metadata_file), "Metadata file")
            return False

        log.loading_metadata(str(metadata_file))
        metadata = ImazingProcessor.load_metadata(
            metadata_file, validate=True, require_source_paths=True
        )

        export_username = metadata.get("export_info", {}).get(
            "export_username", "unknown"
        )
        log.export_username(export_username)

        # Build filename index
        filename_index = build_filename_index(metadata)
        log.found_items(len(filename_index), "media files to process")

        # Filter out secondary files based on dedup manifest (using hash-based lookup)
        # Use cached file_hashes from preprocessing when available to avoid re-hashing
        file_hashes = metadata.get("file_hashes", {})
        skipped_secondary_files = []
        skipped_dedup = 0

        # Match files to messages and pre-generate filenames
        log.logger.info("Matching files and generating filenames...")
        filename_gen = FilenameGenerator(
            "imessage", timestamp_format="%Y-%m-%d %H:%M:%S UTC"
        )
        matched_files = []

        # Read source_path from metadata
        for media_filename, message in filename_index.items():
            source_path_str = message.get("source_path")
            if not source_path_str:
                log.logger.warning(f"No source_path for file: {media_filename}")
                continue

            source_path = Path(source_path_str)
            if not source_path.exists():
                log.logger.warning(f"Source file not found: {source_path}")
                continue

            # Dedup filtering
            if dedup_manifest:
                content_hash = file_hashes.get(media_filename)
                if content_hash:
                    # Pass source_path for within-processor dedup
                    if not is_file_primary_by_hash(
                        dedup_manifest,
                        content_hash,
                        "iMessage iMazing",
                        source_path_str,
                    ):
                        skipped_secondary_files.append(str(source_path))
                        skipped_dedup += 1
                        continue
                else:
                    if ImazingProcessor.should_skip_for_dedup(
                        str(source_path), dedup_manifest, "iMessage iMazing"
                    ):
                        skipped_secondary_files.append(str(source_path))
                        skipped_dedup += 1
                        continue

            file_ext = source_path.suffix.lower()
            identifier = _build_imessage_identifier(message)
            timestamp = _extract_message_timestamp(message)
            output_filename = filename_gen.generate(timestamp, identifier, file_ext)
            # Get content_hash for EXIF metadata
            content_hash = file_hashes.get(media_filename)
            matched_files.append((source_path, message, output_filename, content_hash))

        if skipped_dedup > 0:
            log.skipped_secondary(skipped_dedup)

        log.matched_files(len(matched_files))

        # Process files
        log.processing_start(str(messages_output_dir))

        num_workers = workers if workers is not None else default_worker_count()
        log.using_workers(num_workers)

        # Prepare arguments (include content_hash and original_filename for EXIF)
        process_args = [
            (
                media_file,
                message,
                export_username,
                messages_output_dir,
                output_filename,
                content_hash,
                media_file.name,  # original_filename
            )
            for media_file, message, output_filename, content_hash in matched_files
        ]

        # Phase 1: Copy and rename files
        results = []
        if num_workers > 1:
            with Pool(processes=num_workers) as pool:
                results = list(
                    progress_bar(
                        pool.imap(_process_file_worker, process_args),
                        PHASE_PROCESS,
                        "Copying files",
                        total=len(process_args),
                    )
                )
        else:
            for args in progress_bar(process_args, PHASE_PROCESS, "Copying files"):
                results.append(_process_file_worker(args))

        # Collect successful files for EXIF processing
        # Result tuple: (success, output_path, message, exp_username, content_hash, original_filename)
        def extract_info(r):
            return (
                r[1],
                r[2],
                r[3],
                r[4],
                r[5],
            )  # (output_path, message, exp_username, hash, original_filename)

        collected = ImazingProcessor.collect_processing_results(results, extract_info)

        log.copied_files(collected.success_count, collected.failed_count)

        # Phase 2: Correct file extensions and batch process EXIF metadata
        log.exif_batch_start()
        file_paths, file_info, exif_rebuilt_count = ImazingProcessor.finalize_files(
            collected.file_paths,
            collected.file_info,
            _build_metadata_requests,
            dedup_manifest,
            path_index=0,
        )
        if file_paths:
            log.exif_written(len(file_info))

        # Print summary using shared utility
        print_processing_summary(
            success=collected.success_count,
            failed=collected.failed_count,
            total=collected.success_count + collected.failed_count,
            output_dir=str(messages_output_dir),
            extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
        )

        # Save dedup references for skipped secondary files
        if dedup_manifest and skipped_secondary_files:
            save_dedup_metadata(
                manifest=dedup_manifest,
                skipped_files=skipped_secondary_files,
                processor_name="iMessage-iMazing",
                output_dir=messages_output_dir,
            )

        return True

    # Require preprocessed input
    if not is_preprocessed(input_path):
        log.logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            input_path,
        )
        return False

    log.using_preprocessed(str(input_path))
    return _process_working_dir(input_path)
