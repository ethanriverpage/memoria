#!/usr/bin/env python3
"""iMazing iMessage Processor - handles iMazing-format exports with filename metadata."""

import json
import logging
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.catalog import catalog_media_files
from common.dedup.description import (
    SourceEntry,
    build_source_line,
    build_message_context,
    PLATFORM_IMESSAGE,
)
from common.dedup.service import save_dedup_metadata
from common.dependency_checker import check_exiftool, print_exiftool_error
from common.files.utils import copy_file
from common.dedup.hashing import (
    is_live_photo_pair,
    LIVE_PHOTO_IMAGE_EXTENSIONS,
    LIVE_PHOTO_VIDEO_EXTENSIONS,
)
from common.media.exiftool import (
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    MetadataWriteRequest,
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
        media_dir = input_path / "media"

        if metadata_file.exists() and media_dir.exists():
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


class ImazingProcessor(ProcessorBase):
    """Processor for iMazing-format iMessage exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        return detect(input_path)

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
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata."""
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        all_files = []
        live_photo_videos = set()

        # Collect from root directory
        for file_path in input_path.iterdir():
            if file_path.is_file() and file_path.suffix.lower() in ALL_MEDIA_EXTENSIONS:
                all_files.append(file_path)

        # Also check media/ directory if preprocessed
        media_dir = input_path / "media"
        if media_dir.exists():
            for file_path in media_dir.iterdir():
                if (
                    file_path.is_file()
                    and file_path.suffix.lower() in ALL_MEDIA_EXTENSIONS
                ):
                    all_files.append(file_path)

        # Identify Live Photo pairs
        for file_path in all_files:
            suffix = file_path.suffix.lower()
            if suffix in LIVE_PHOTO_IMAGE_EXTENSIONS:
                for video_ext in LIVE_PHOTO_VIDEO_EXTENSIONS:
                    video_path = file_path.with_suffix(video_ext)
                    if video_path.exists() and is_live_photo_pair(
                        file_path, video_path
                    ):
                        live_photo_videos.add(video_path)
                        break

        # Filter out Live Photo videos
        media_files = [f for f in all_files if f not in live_photo_videos]

        log.logger.info(
            "Found %d media files to catalog (%d Live Photo videos grouped with images)",
            len(media_files),
            len(live_photo_videos),
        )

        def extract_live_photo_metadata(file_path: Path) -> Dict[str, Any]:
            """Extract Live Photo metadata for a file."""
            metadata = {}
            suffix = file_path.suffix.lower()
            if suffix in LIVE_PHOTO_IMAGE_EXTENSIONS:
                for video_ext in LIVE_PHOTO_VIDEO_EXTENSIONS:
                    video_path = file_path.with_suffix(video_ext)
                    if video_path.exists() and is_live_photo_pair(
                        file_path, video_path
                    ):
                        metadata["live_photo_video"] = str(video_path)
                        break
            return metadata

        yield from catalog_media_files(
            processor_name="iMessage-iMazing",
            input_path=input_path,
            media_files=media_files,
            workers=workers,
            metadata_extractor=extract_live_photo_metadata,
        )

    @staticmethod
    def process(
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process an iMazing iMessage export."""
        return process_logic(
            input_dir=input_dir,
            output_dir=output_dir,
            temp_dir=kwargs.get("temp_dir", "../pre"),
            verbose=kwargs.get("verbose", False),
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
    file_info: List[Tuple[Optional[str], dict, str]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for iMessage files."""
    requests: List[MetadataWriteRequest] = []

    for file_path, message, export_username in file_info:
        if file_path is None:
            continue
        is_merged = "messages" in message and isinstance(message["messages"], list)

        datetime_tags: List[Tuple[str, str]] = []
        if is_merged:
            date_str = message.get("primary_created")
        else:
            date_str = message.get("created")

        if date_str:
            exif_date = date_str.replace("-", ":").replace(" UTC", "")
            datetime_tags = [
                ("DateTimeOriginal", exif_date),
                ("CreateDate", exif_date),
                ("ModifyDate", exif_date),
            ]

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
            )
        )

    return requests


def is_preprocessed(input_path: Path) -> bool:
    """Check if input directory is already preprocessed."""
    metadata_file = input_path / "metadata.json"
    media_dir = input_path / "media"

    if not metadata_file.exists() or not media_dir.exists():
        return False

    # Verify it's an iMazing preprocessed export
    try:
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)
        return (
            "conversations" in metadata
            and "export_info" in metadata
            and metadata.get("export_info", {}).get("export_format") == "imazing"
        )
    except (json.JSONDecodeError, KeyError):
        return False


def build_filename_index(metadata: dict) -> dict:
    """Build index mapping media_file to message metadata."""
    filename_index = {}

    for _conv_id, conv_data in metadata.get("conversations", {}).items():
        for message in conv_data.get("messages", []):
            media_file = message.get("media_file")
            if media_file:
                filename_index[media_file] = message

    for message in metadata.get("orphaned_media", []):
        media_file = message.get("media_file")
        if media_file:
            filename_index[media_file] = message

    return filename_index


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
    """Worker function for processing files (copy and rename)."""
    media_path, message, export_username, output_dir, output_filename = args_tuple

    try:
        output_path = output_dir / output_filename

        # Copy file
        copy_file(media_path, output_path)

        # Update timestamps
        timestamp_str = _extract_message_timestamp(message)
        update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")

        return (True, str(output_path), message, export_username)

    except Exception as e:
        log.processing_error(media_path.name, e)
        return (False, None, message, export_username)


def process_logic(
    input_dir: str,
    output_dir: Optional[str] = None,
    temp_dir: str = "../pre",
    verbose: bool = False,  # noqa: ARG001
    workers: Optional[int] = None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Core processing logic for iMazing iMessage exports."""
    # Check for required tools
    if not check_exiftool():
        print_exiftool_error()
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
        media_dir = working_dir / "media"

        if not metadata_file.exists():
            log.file_not_found(str(metadata_file), "Metadata file")
            return False

        if not media_dir.exists():
            log.file_not_found(str(media_dir), "Media directory")
            return False

        log.loading_metadata(str(metadata_file))
        metadata = ImazingProcessor.load_metadata(metadata_file)

        export_username = metadata.get("export_info", {}).get(
            "export_username", "unknown"
        )
        log.export_username(export_username)

        # Build filename index
        filename_index = build_filename_index(metadata)
        log.found_items(len(filename_index), "media files to process")

        # Scan media directory
        media_files = list(media_dir.iterdir())
        media_files = [f for f in media_files if f.is_file()]
        log.found_files(len(media_files), "files in media directory")

        # Filter out secondary files based on dedup manifest (using hash-based lookup)
        skipped_secondary_files = []
        if dedup_manifest:
            original_count = len(media_files)
            filtered_files = []
            for f in media_files:
                if ImazingProcessor.should_skip_for_dedup(
                    str(f), dedup_manifest, "iMessage iMazing"
                ):
                    skipped_secondary_files.append(str(f))
                else:
                    filtered_files.append(f)
            media_files = filtered_files
            skipped_count = original_count - len(media_files)
            log.skipped_secondary(skipped_count)

        # Match files to messages and pre-generate filenames
        log.logger.info("Matching files and generating filenames...")
        filename_gen = FilenameGenerator(
            "imessage", timestamp_format="%Y-%m-%d %H:%M:%S UTC"
        )
        matched_files = []

        for media_file in media_files:
            if media_file.name in filename_index:
                message = filename_index[media_file.name]
                file_ext = media_file.suffix.lower()

                # Build identifier (policy) and delegate to FilenameGenerator (mechanism)
                identifier = _build_imessage_identifier(message)
                timestamp = _extract_message_timestamp(message)
                output_filename = filename_gen.generate(timestamp, identifier, file_ext)
                matched_files.append((media_file, message, output_filename))
            else:
                log.no_metadata_for_file(media_file.name)

        log.matched_files(len(matched_files))

        # Process files
        log.processing_start(str(messages_output_dir))

        num_workers = workers if workers is not None else default_worker_count()
        log.using_workers(num_workers)

        # Prepare arguments
        process_args = [
            (media_file, message, export_username, messages_output_dir, output_filename)
            for media_file, message, output_filename in matched_files
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
        success_count = 0
        failed_count = 0
        file_paths = []
        file_info = []

        for success, output_path, message, exp_username in results:
            if success and output_path:
                success_count += 1
                file_paths.append(output_path)
                file_info.append((output_path, message, exp_username))
            else:
                failed_count += 1

        log.copied_files(success_count, failed_count)

        # Phase 2: Correct file extensions based on actual content
        file_paths, file_info = ImazingProcessor.correct_extensions_and_update_info(
            file_paths, file_info, path_index=0
        )

        # Phase 3: Batch EXIF processing
        exif_rebuilt_count = 0
        if file_paths:
            log.exif_batch_start()
            exif_rebuilt_count = ImazingProcessor.batch_process_exif(
                file_paths, file_info, _build_metadata_requests
            )
            log.exif_written(len(file_info))

        # Print summary using shared utility
        print_processing_summary(
            success=success_count,
            failed=failed_count,
            total=success_count + failed_count,
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

    # Check if input is already preprocessed
    if is_preprocessed(input_path):
        log.using_preprocessed(str(input_path))
        return _process_working_dir(input_path)

    # Need to run preprocessing into a managed temp directory, then process within it.
    log.running_preprocessing()
    with ImazingPreprocessor.temp_output_dir(temp_dir, "imazing") as temp_dir_path:
        log.preprocessing_to(str(temp_dir_path))

        preprocessor = ImazingPreprocessor(
            export_path=input_path,
            output_dir=temp_dir_path,
            workers=workers,
            final_output_dir=messages_output_dir,
        )

        if not preprocessor.run():
            log.logger.error("Preprocessing failed")
            return False

        log.preprocessing_done(str(temp_dir_path))
        return _process_working_dir(temp_dir_path)


def get_processor():
    """Return processor class for auto-discovery."""
    return ImazingProcessor
