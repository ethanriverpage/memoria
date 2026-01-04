#!/usr/bin/env python3
"""
iMessage Processor

This processor handles iMessage exports from Mac and iPhone backups.
It supports cross-export consolidation for deduplication across multiple devices.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.service import (
    get_canonical_timestamp,
    get_live_photo_base_for_hash,
    save_dedup_metadata,
)
from common.config.runtime import default_thread_count
from common.dedup.description import (
    SourceEntry,
    build_source_line,
    format_message_line,
    PLATFORM_IMESSAGE,
)
from common.failures import FailureTracker
from common.files.naming import FilenameGenerator
from common.files.timestamps import update_file_timestamps
from common.files.utils import copy_file, short_path
from common.media.exiftool import (
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    MetadataWriteRequest,
    build_datetime_tags,
)
from common.output.logging import get_processor_logger
from common.output.paths import OutputInfo
from common.output.summary import print_processing_summary
from processors.base import ProcessorBase
from processors.imessage.preprocess import IMessagePreprocessor
from processors.imessage.preprocess_imazing import ImazingPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


# ============================================================================
# iMazing Export Detection Helpers
# ============================================================================


def _is_imazing_raw_export(input_path: Path) -> bool:
    """Check if a directory is an iMazing raw (unpreprocessed) export.

    iMazing exports have distinctive markers:
    1. Device-Info.txt containing "iMazing" or "DigiDNA"
    2. vCard files (Contact - *.vcf) containing "iMazing" or "DigiDNA"
    3. Messages CSV files + media files with iMazing filename pattern

    Args:
        input_path: Directory to check

    Returns:
        True if this is an iMazing raw export
    """
    import re
    from common.files.types import ALL_EXTENSIONS as ALL_MEDIA_EXTENSIONS

    # Check for Device-Info.txt with iMazing reference
    device_info = input_path / "Device-Info.txt"
    if device_info.exists():
        try:
            content = device_info.read_text(encoding="utf-8")
            if "iMazing" in content or "DigiDNA" in content:
                return True
        except Exception:
            pass

    # Check for iMazing-style vCard files
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
        # Pattern: "YYYY-MM-DD HH MM SS - Name - filename.ext"
        pattern = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2} \d{2} \d{2} - .+ - .+$")

        for f in input_path.iterdir():
            if f.is_file() and f.suffix.lower() in ALL_MEDIA_EXTENSIONS:
                if pattern.match(f.name):
                    return True

    return False


def _is_sqlite_raw_export(input_path: Path) -> bool:
    """Check if a directory is a SQLite-based raw iMessage export.

    Args:
        input_path: Directory to check

    Returns:
        True if this is a SQLite raw export (Mac or iPhone)
    """
    # Mac export: chat.db + Attachments/
    if (input_path / "chat.db").exists() and (input_path / "Attachments").is_dir():
        return True

    # iPhone export: SMS/sms.db + SMS/Attachments/
    sms_dir = input_path / "SMS"
    if sms_dir.exists():
        if (sms_dir / "sms.db").exists() and (sms_dir / "Attachments").is_dir():
            return True

    return False


def _categorize_inputs(
    input_dirs: List[str],
) -> Tuple[List[Path], List[Path], List[Path], List[Path]]:
    """Categorize input directories by type.

    Args:
        input_dirs: List of input directory paths

    Returns:
        Tuple of (sqlite_raw, imazing_raw, preprocessed, unknown) path lists.
        Unknown directories are those that could not be classified and should
        be handled as errors (not silently processed).
    """
    sqlite_raw: List[Path] = []
    imazing_raw: List[Path] = []
    preprocessed: List[Path] = []
    unknown: List[Path] = []

    for input_dir in input_dirs:
        input_path = Path(input_dir)

        # Check if already preprocessed
        if IMessageProcessor.is_preprocessed_export_dir(
            input_path, container_key="conversations"
        ):
            preprocessed.append(input_path)
        elif _is_imazing_raw_export(input_path):
            imazing_raw.append(input_path)
        elif _is_sqlite_raw_export(input_path):
            sqlite_raw.append(input_path)
        else:
            # Unknown type - do not silently treat as SQLite
            unknown.append(input_path)

    return sqlite_raw, imazing_raw, preprocessed, unknown


# ============================================================================
# Processor Detection and Registration
# ============================================================================


class IMessageProcessor(ProcessorBase):
    """Processor for iMessage exports from Mac and iPhone."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory.

        Detection criteria for iMessage:
        - Mac export: chat.db + Attachments/ directory
        - iPhone export: SMS/sms.db + SMS/Attachments/ directory
        - iMazing export: Device-Info.txt with iMazing markers, or Messages CSV + media
        - Preprocessed: metadata.json with platform="iMessage" and conversations
        """
        try:
            # Check for Mac export: chat.db + Attachments/
            if (input_path / "chat.db").exists() and (
                input_path / "Attachments"
            ).is_dir():
                return True

            # Check for iPhone export: SMS/sms.db + SMS/Attachments/
            sms_dir = input_path / "SMS"
            if sms_dir.exists():
                if (sms_dir / "sms.db").exists() and (sms_dir / "Attachments").is_dir():
                    return True

            # Check for iMazing raw export
            if _is_imazing_raw_export(input_path):
                return True

            # Check for preprocessed iMessage export using platform/service markers
            # Both SQLite and iMazing preprocessed exports use platform="iMessage"
            if ProcessorBase.is_preprocessed_for_platform(
                input_path, platform="iMessage", service=None
            ):
                return True

            return False

        except Exception as e:
            log.detection_failed("iMessage", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "iMessage"

    @staticmethod
    def get_priority() -> int:
        # Slowest processor in typical full runs (~hours) - run later when multiple processors match
        return 49

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="iMessage",
            service=None,
            username_prefix="imessage",
        )

    @classmethod
    def get_output_subdirectory(cls) -> str:
        # iMessage uses "messages" as output subdirectory (service is None)
        return "messages"

    @staticmethod
    def supports_consolidation() -> bool:
        """Enable cross-export consolidation for iMessage.

        When True, memoria.py will group all detected iMessage exports
        and call process_consolidated() instead of process() for each.
        """
        return True

    @staticmethod
    def supports_dedup() -> bool:
        """Return True - iMessage supports cross-processor deduplication."""
        return True

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if iMessage export needs preprocessing.

        Returns True for:
        - SQLite exports (Mac or iPhone) that haven't been preprocessed
        - iMazing exports that haven't been preprocessed
        """
        input_path = Path(input_dir)

        # Check if already preprocessed (validated metadata schema)
        if IMessageProcessor.is_preprocessed_export_dir(
            input_path, container_key="conversations"
        ):
            return False

        # Check for SQLite raw export structure (chat.db or SMS/sms.db)
        if (input_path / "chat.db").exists():
            return True
        sms_dir = input_path / "SMS"
        if sms_dir.exists() and (sms_dir / "sms.db").exists():
            return True

        # Check for iMazing raw export
        if _is_imazing_raw_export(input_path):
            return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw iMessage export.

        Dispatches to the appropriate preprocessor based on export format:
        - ImazingPreprocessor for iMazing exports
        - IMessagePreprocessor for SQLite exports (Mac/iPhone)
        """
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        threads = kwargs.get("threads")

        cache_path.mkdir(parents=True, exist_ok=True)

        # Dispatch to appropriate preprocessor based on export format
        if _is_imazing_raw_export(input_path):
            logger.info("Preprocessing iMazing export to: %s", cache_path)
            preprocessor = ImazingPreprocessor(
                export_path=input_path,
                output_dir=cache_path,
                threads=threads,
            )
        else:
            logger.info("Preprocessing iMessage SQLite export to: %s", cache_path)
            preprocessor = IMessagePreprocessor(
                export_paths=input_path,
                output_dir=cache_path,
                threads=threads,
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
            **kwargs: Additional arguments (threads)

        Yields:
            Catalog entries with processor name and file data.
        """
        input_path = Path(input_dir)
        threads = kwargs.get("threads", 4)

        def extract_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
            """Extract metadata from preprocessed entry."""
            result: Dict[str, Any] = {}
            if "sender" in entry:
                result["sender"] = entry["sender"]
            if "conversation_title" in entry:
                result["conversation"] = entry["conversation_title"]
            if "created" in entry:
                result["timestamp"] = entry["created"]
            if "content" in entry:
                result["content"] = entry["content"]
            return result

        catalog_iter = IMessageProcessor.catalog_from_preprocessed_if_available(
            processor_name="iMessage",
            input_path=input_path,
            threads=threads,
            metadata_extractor=extract_metadata,
            container_key="conversations",
        )
        if catalog_iter is not None:
            yield from catalog_iter
            return

        logger.warning("Directory not preprocessed, skipping catalog: %s", input_path)

    @classmethod
    def process_consolidated(
        cls,
        input_dirs: List[str],
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process multiple iMessage exports as a consolidated unit.

        Called by memoria.py when CONSOLIDATE_EXPORTS=true (default) and
        multiple iMessage exports are detected. Handles mixed inputs:
        - SQLite exports (Mac/iPhone): consolidated together via IMessagePreprocessor
        - iMazing exports: preprocessed separately via ImazingPreprocessor
        - Already preprocessed: processed directly

        All outputs write to the same directory using the same dedup manifest.

        Args:
            input_dirs: List of paths to iMessage export directories
            output_dir: Base output directory
            dedup_manifest: Optional deduplication manifest
            **kwargs: Additional arguments (threads, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        if not cls.check_dependencies():
            return False

        try:
            # Calculate output directory using the subdirectory helper
            if output_dir:
                processor_output = Path(output_dir) / cls.get_output_subdirectory()
            else:
                processor_output = Path(
                    kwargs.pop("output", None) or cls.get_default_output()
                )

            threads = kwargs.get("threads")
            temp_dir = kwargs.get("temp_dir")

            # Categorize inputs by type
            sqlite_raw, imazing_raw, preprocessed, unknown = _categorize_inputs(
                input_dirs
            )

            # Fail early if any directories could not be classified
            if unknown:
                for unk_path in unknown:
                    logger.error(
                        "Unknown input type for directory: %s. "
                        "Expected SQLite export (chat.db), iMazing export (Device-Info.txt), "
                        "or preprocessed export (metadata.json with conversations).",
                        unk_path.name,
                    )
                return False

            logger.info(
                "Categorized %d inputs: %d SQLite, %d iMazing, %d preprocessed",
                len(input_dirs),
                len(sqlite_raw),
                len(imazing_raw),
                len(preprocessed),
            )

            # Build list of preprocessed directories to process
            dirs_to_process: List[Path] = list(preprocessed)

            # Preprocess SQLite exports together (consolidation)
            if sqlite_raw:
                logger.info(
                    "Consolidating %d SQLite exports together...", len(sqlite_raw)
                )
                # Use temp directory for consolidated preprocessing
                if temp_dir:
                    consolidated_cache = Path(temp_dir) / "imessage_consolidated"
                else:
                    consolidated_cache = sqlite_raw[0].parent / "imessage_consolidated"

                consolidated_cache.mkdir(parents=True, exist_ok=True)

                preprocessor = IMessagePreprocessor(
                    export_paths=sqlite_raw,
                    output_dir=consolidated_cache,
                    threads=threads,
                )

                if preprocessor.run():
                    dirs_to_process.append(consolidated_cache)
                else:
                    logger.error("Failed to preprocess consolidated SQLite exports")
                    return False

            # Preprocess each iMazing export separately
            for imazing_path in imazing_raw:
                logger.info("Preprocessing iMazing export: %s", imazing_path.name)
                if temp_dir:
                    imazing_cache = Path(temp_dir) / f"imazing_{imazing_path.name}"
                else:
                    imazing_cache = (
                        imazing_path.parent
                        / f"imazing_{imazing_path.name}_preprocessed"
                    )

                imazing_cache.mkdir(parents=True, exist_ok=True)

                preprocessor = ImazingPreprocessor(
                    export_path=imazing_path,
                    output_dir=imazing_cache,
                    threads=threads,
                )

                if preprocessor.run():
                    dirs_to_process.append(imazing_cache)
                else:
                    logger.warning(
                        "Failed to preprocess iMazing export: %s", imazing_path.name
                    )
                    # Continue with other exports

            if not dirs_to_process:
                logger.error("No preprocessed directories to process")
                return False

            # Process each preprocessed directory (all write to same output)
            # Use namespace when processing multiple dirs to avoid artifact overwrites
            use_namespace = len(dirs_to_process) > 1
            success = True
            for preprocessed_dir in dirs_to_process:
                # Use directory name as namespace for artifact filenames
                export_namespace = preprocessed_dir.name if use_namespace else None
                result = process_logic(
                    input_dirs=[str(preprocessed_dir)],
                    output_dir=str(processor_output),
                    threads=threads,
                    dedup_manifest=dedup_manifest,
                    export_namespace=export_namespace,
                )
                if not result:
                    success = False

            return success

        except Exception as e:
            logger.error("Error in %s: %s", cls.get_name(), e)
            return False

    @classmethod
    def process(
        cls,
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process a single iMessage export.

        Overrides the base template to delegate to process_consolidated
        with a single-element list, ensuring the same code path handles
        both single and multi-export modes.
        """
        return cls.process_consolidated(
            [input_dir], output_dir, dedup_manifest=dedup_manifest, **kwargs
        )

    @classmethod
    def _process_impl(
        cls,
        input_dir: str,
        output_dir: str,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Implement processing for single export (via process_consolidated)."""
        # This method satisfies the abstract requirement but is not called
        # directly since process() overrides the base template.
        return process_logic(
            input_dirs=[input_dir],
            output_dir=output_dir,
            threads=kwargs.get("threads"),
            dedup_manifest=dedup_manifest,
        )


# ============================================================================
# Helper Functions
# ============================================================================


def _extract_date_from_source_export(source_export: str) -> Optional[str]:
    """Extract date string from source_export directory name.

    Examples:
        "imessage-iph13p-20220426" -> "20220426"
        "imessage-m3_mbp-20251202" -> "20251202"

    Args:
        source_export: Directory name like "imessage-{device}-YYYYMMDD"

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

    Args:
        source_export: Directory name like "imessage-{device}-YYYYMMDD"

    Returns:
        Device identifier, or "unknown" if pattern doesn't match
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
    """Build the description string for an iMessage using unified format.

    Args:
        message: Message data dictionary.
        export_username: Username of the export owner.

    Returns:
        Formatted description string.
    """
    from datetime import datetime as dt

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
                        export_date = dt.strptime(date_str, "%Y%m%d")
                        if (
                            oldest_export_date is None
                            or export_date < oldest_export_date
                        ):
                            oldest_export_date = export_date
                            oldest_msg = msg
                except (ValueError, IndexError):
                    pass

        # Extract device from oldest export's source_export
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
        # Merged message - list all conversations, deduplicating identical lines
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
            msg_line = format_message_line(msg_entry)

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
        msg_line = format_message_line(msg_entry)
        if msg_line:
            description_parts.append(msg_line)

        source_description = "&#xa;".join(description_parts)

    # Add Live Photo marker if applicable
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
        date_str = message.get("canonical_timestamp") or (
            message.get("primary_created") if is_merged else message.get("created")
        )
        datetime_tags = build_datetime_tags(date_str, strip_utc=True)

        # Build description
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
                processor_name="imessage",
                content_hash=content_hash,
                original_filename=original_filename,
            )
        )

    return requests


def build_filename_index(metadata: dict) -> dict:
    """Build index mapping media_file to message metadata.

    Args:
        metadata: Parsed metadata.json content

    Returns:
        Dict mapping media_file -> message dict

    Note:
        Only adds orphaned_media entries if they don't already exist in the index.
        Orphaned media files can have the same filename as database entries (e.g., IMG_0001.jpeg),
        but we prefer the database entry which has valid metadata.
    """
    return ProcessorBase.build_media_filename_index(
        metadata,
        media_keys=["media_files"],
        include_orphaned=True,
        orphaned_prefer_existing=True,
    )


def _extract_message_timestamp(message: dict) -> str | None:
    """Extract timestamp string from iMessage message metadata.

    Args:
        message: Message metadata with 'created' or 'primary_created' field

    Returns:
        Timestamp string or None if not found
    """
    if "primary_created" in message:
        return message["primary_created"]
    elif "messages" in message and message["messages"]:
        return message["messages"][0].get("created")
    else:
        return message.get("created")


def _process_file_worker(args_tuple):
    """Worker function for processing files (Phase 1: copy and rename).

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
        update_file_timestamps(output_path, timestamp_str)

        return (
            True,
            str(output_path),
            message,
            export_username,
            content_hash,
            original_filename,
        )

    except Exception as e:
        logger.error("Error processing %s: %s", media_path.name, e)
        return (
            False,
            None,
            message,
            export_username,
            content_hash,
            original_filename,
        )


# ============================================================================
# Main Processing Logic
# ============================================================================


def process_logic(
    input_dirs: List[str],
    output_dir: Optional[str] = None,
    threads: Optional[int] = None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
    export_namespace: Optional[str] = None,
) -> bool:
    """Core processing logic for iMessage exports.

    Expects a single preprocessed input directory (via memoria.py preprocessing
    phase or by calling IMessageProcessor.preprocess() directly).

    Args:
        input_dirs: List containing a single preprocessed input directory
        output_dir: Output directory for processed files (final path, no subdirs created)
        threads: Number of parallel threads
        dedup_manifest: Optional deduplication manifest for filtering secondary files
        export_namespace: Optional namespace for artifact filenames when processing
            multiple exports into the same output directory.

    Returns:
        True if processing succeeded, False otherwise
    """
    # Check for required tools
    if not IMessageProcessor.check_dependencies():
        return False

    log.processor_start("iMessage Processor")

    # Convert to paths
    input_paths = [Path(d) for d in input_dirs]

    # Require single preprocessed input
    if len(input_paths) != 1:
        logger.error(
            "iMessage process_logic requires a single preprocessed input. "
            "Got %d inputs. Use preprocess() to consolidate multiple exports first.",
            len(input_paths),
        )
        return False

    working_dir = input_paths[0]

    if not IMessageProcessor.is_preprocessed_export_dir(
        working_dir, container_key="conversations"
    ):
        logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            working_dir,
        )
        return False

    logger.info("Processing preprocessed export: %s", short_path(working_dir))

    # Determine output directory (use as-is, subdirectory created by caller)
    if output_dir:
        messages_output_dir = Path(output_dir)
    else:
        messages_output_dir = Path("final_imessage/messages")

    messages_output_dir.mkdir(parents=True, exist_ok=True)

    return _process_working_directory(
        working_dir,
        messages_output_dir,
        threads,
        dedup_manifest,
        export_namespace=export_namespace,
    )


def _process_working_directory(
    working_dir: Path,
    output_dir: Path,
    threads: Optional[int],
    dedup_manifest: Optional[Dict[str, Any]] = None,
    export_namespace: Optional[str] = None,
) -> bool:
    """Process a working directory (preprocessed export).

    Args:
        working_dir: Path to preprocessed directory with metadata.json
        output_dir: Output directory for processed files
        threads: Number of parallel threads
        dedup_manifest: Optional deduplication manifest for filtering secondary files
        export_namespace: Optional namespace for artifact filenames (dedup refs, failure reports).
            Used when processing multiple exports into the same output directory.

    Returns:
        True if processing succeeded, False otherwise
    """
    # Load metadata
    metadata_file = working_dir / "metadata.json"

    if not metadata_file.exists():
        logger.error("Metadata file not found: %s", short_path(metadata_file))
        return False

    log.loading_metadata(short_path(metadata_file))
    metadata = IMessageProcessor.load_metadata(
        metadata_file,
        validate=True,
        require_source_paths=True,
        processor_name="iMessage",
    )

    export_info = metadata.get("export_info", {})
    export_username = export_info.get("export_username")
    log.export_username(export_username)

    # Initialize failure tracker for this export
    failure_tracker = FailureTracker(
        processor_name="iMessage",
        export_directory=str(working_dir),
    )

    # Build filename index
    filename_index = build_filename_index(metadata)
    log.found_files(len(filename_index), "media")

    # Filter out secondary files based on dedup manifest (using hash-based lookup)
    # Use cached file_hashes from preprocessing when available to avoid re-hashing
    file_hashes = metadata.get("file_hashes", {})
    skipped_dedup = 0
    skipped_secondary_files: list[str] = []
    missing_source_path = 0
    missing_path_examples: list[str] = []
    missing_file_count = 0
    missing_file_examples: list[str] = []

    # Match files to messages and pre-generate filenames
    log.generating_filenames()
    filename_gen = FilenameGenerator(
        "imessage",
        export_username,
    )

    # Build live photo pairing maps from metadata
    # Map: pair_key -> set of video media_filenames that have that key
    live_photo_videos_by_key: dict[str, set[str]] = {}
    for media_filename, message in filename_index.items():
        if message.get("is_live_photo_video"):
            pair_keys = message.get("live_photo_pair_keys", [])
            for pair_key in pair_keys:
                if pair_key not in live_photo_videos_by_key:
                    live_photo_videos_by_key[pair_key] = set()
                live_photo_videos_by_key[pair_key].add(media_filename)

    # Two-pass filename generation for deterministic live photo pairing
    # Pass A: non-live-video items (images and regular files)
    # Pass B: live-photo video items (reuse base from paired image)
    live_photo_bases_by_key: dict[str, str] = {}  # pair_key -> base filename
    pending_items: list[tuple[str, dict]] = []  # items to process
    live_video_items: list[tuple[str, dict]] = []  # deferred for Pass B

    # Collect and categorize items, applying validation/dedup filtering
    for media_filename, message in filename_index.items():
        # Get source path from source_paths dict (keyed by filename)
        source_paths = message.get("source_paths", {})
        source_path_str = source_paths.get(media_filename)
        if not source_path_str:
            missing_source_path += 1
            if len(missing_path_examples) < 5:
                missing_path_examples.append(media_filename)
            continue

        source_path = Path(source_path_str)
        if not source_path.exists():
            missing_file_count += 1
            if len(missing_file_examples) < 5:
                missing_file_examples.append(short_path(source_path))
            continue

        # Dedup filtering
        content_hash = file_hashes.get(media_filename)
        if IMessageProcessor.should_skip_for_dedup(
            dedup_manifest, content_hash, "iMessage", source_path_str
        ):
            skipped_dedup += 1
            skipped_secondary_files.append(source_path_str)
            continue

        # Categorize: live-photo videos go to Pass B, everything else to Pass A
        if message.get("is_live_photo_video"):
            live_video_items.append((media_filename, message))
        else:
            pending_items.append((media_filename, message))

    matched_files = []

    # Pass A: Process non-live-video items (images and regular files)
    for media_filename, message in pending_items:
        source_paths = message.get("source_paths", {})
        source_path_str = source_paths.get(media_filename)
        source_path = Path(source_path_str)
        content_hash = file_hashes.get(media_filename)

        file_ext = source_path.suffix.lower()
        timestamp = _extract_message_timestamp(message)
        canonical_ts = get_canonical_timestamp(dedup_manifest, content_hash, timestamp)
        message["canonical_timestamp"] = canonical_ts
        output_filename = filename_gen.generate(canonical_ts, file_ext)

        # If this image has a live-photo video pair, store base and reserve .mov
        pair_keys = message.get("live_photo_pair_keys", [])
        has_video_pair = any(pk in live_photo_videos_by_key for pk in pair_keys)
        if has_video_pair:
            base = output_filename[: -len(file_ext)] if file_ext else output_filename
            # Store base for each pair key so the video can find it
            for pk in pair_keys:
                if pk in live_photo_videos_by_key:
                    live_photo_bases_by_key[pk] = base
            # Reserve .mov extension for the paired video to prevent collisions
            reserved_mov = f"{base}.mov"
            filename_gen.used_filenames.add(reserved_mov)

        matched_files.append((source_path, message, output_filename, content_hash))

    # Pass B: Process live-photo video items (reuse base from paired image)
    for media_filename, message in live_video_items:
        source_paths = message.get("source_paths", {})
        source_path_str = source_paths.get(media_filename)
        source_path = Path(source_path_str)
        content_hash = file_hashes.get(media_filename)

        file_ext = source_path.suffix.lower()
        timestamp = _extract_message_timestamp(message)
        canonical_ts = get_canonical_timestamp(dedup_manifest, content_hash, timestamp)
        message["canonical_timestamp"] = canonical_ts

        # Try to reuse base from paired image using live_photo_pair_keys
        pair_keys = message.get("live_photo_pair_keys", [])
        matched_base = None
        for pk in pair_keys:
            if pk in live_photo_bases_by_key:
                matched_base = live_photo_bases_by_key[pk]
                break

        if matched_base:
            output_filename = f"{matched_base}{file_ext}"
            # Ensure filename is registered (belt-and-suspenders)
            filename_gen.used_filenames.add(output_filename)
        else:
            # Cross-export fallback: use HEIC's canonical info from manifest
            # This handles cases where HEIC was processed by a different export
            heic_hash = message.get("live_photo_image_hash")
            if heic_hash and dedup_manifest:
                matched_base = get_live_photo_base_for_hash(
                    dedup_manifest,
                    heic_hash,
                    "imessage",
                    export_username,
                )

            if matched_base:
                output_filename = f"{matched_base}{file_ext}"
                filename_gen.used_filenames.add(output_filename)
            else:
                # Final fallback: no paired image found
                logger.warning(
                    "Live photo video %s has no paired image (live_photo_pair_keys=%s), "
                    "generating independent filename",
                    media_filename,
                    pair_keys,
                )
                output_filename = filename_gen.generate(canonical_ts, file_ext)

        matched_files.append((source_path, message, output_filename, content_hash))

    if skipped_dedup > 0:
        log.skipped_secondary(skipped_dedup)
    if missing_source_path > 0:
        logger.info(
            "Missing source_path for %d files (e.g. %s)",
            missing_source_path,
            ", ".join(missing_path_examples),
        )
    if missing_file_count > 0:
        logger.info(
            "Source file not found for %d files (e.g. %s)",
            missing_file_count,
            ", ".join(missing_file_examples),
        )

    log.matched_files(len(matched_files))

    # Check for no media files (export-level soft-fail)
    if not matched_files:
        log.logger.warning("No media files found to process.")
        failure_tracker.add_export_failure(
            reason="no_media_files",
            error_details="No media files found in export to process",
        )
        failure_tracker.handle_failures(output_dir, report_namespace=export_namespace)
        return False

    # Process files
    log.processing_start(str(output_dir))

    num_threads = threads if threads is not None else default_thread_count()
    log.using_threads(num_threads)

    # Prepare arguments (include content_hash and original_filename for EXIF)
    process_args = [
        (
            media_file,
            message,
            export_username,
            output_dir,
            output_filename,
            content_hash,
            media_file.name,  # original_filename
        )
        for media_file, message, output_filename, content_hash in matched_files
    ]

    # Phase 1: Copy and rename files
    results = IMessageProcessor.process_items_threaded(
        tasks=process_args,
        worker_fn=_process_file_worker,
        num_threads=num_threads,
        description="Copying files",
    )

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

    collected = IMessageProcessor.collect_processing_results(results, extract_info)

    log.copied_files(collected.success_count, collected.failed_count)

    # Phase 2: Correct file extensions and batch process EXIF metadata
    log.exif_batch_start()

    # Build metadata lookup for failure tracking
    file_info_map = {info[0]: info for info in collected.file_info}

    def metadata_for_file(file_path: str) -> Optional[dict]:
        info = file_info_map.get(file_path)
        return info[1] if info else None  # info[1] is message

    file_paths, file_info, exif_rebuilt_count = IMessageProcessor.finalize_files(
        collected.file_paths,
        collected.file_info,
        _build_metadata_requests,
        dedup_manifest,
        path_index=0,
        failure_tracker=failure_tracker,
        metadata_for_file=metadata_for_file,
    )
    if file_paths:
        log.exif_written(len(file_info))

    # Handle failures - move failed outputs and generate report
    failure_tracker.handle_failures(output_dir, report_namespace=export_namespace)

    # Save dedup references for skipped secondary files
    if dedup_manifest and skipped_secondary_files:
        # Use namespaced filename if processing multiple exports into same output
        dedup_filename = (
            f"dedup_references__{export_namespace}.json"
            if export_namespace
            else "dedup_references.json"
        )
        save_dedup_metadata(
            manifest=dedup_manifest,
            skipped_files=skipped_secondary_files,
            processor_name="iMessage",
            output_dir=output_dir,
            metadata_filename=dedup_filename,
        )

    # Print summary using shared utility
    print_processing_summary(
        success=collected.success_count,
        failed=collected.failed_count,
        total=len(matched_files),
        output_dir=str(output_dir),
        extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
    )

    return True
