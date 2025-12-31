#!/usr/bin/env python3
"""
Discord Processor

Processes Discord data exports, handling attachment downloads
and metadata embedding for media files.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_DISCORD,
)
from common.dedup.service import (
    get_canonical_timestamp,
    save_dedup_metadata,
)
from common.failure_tracker import FailureTracker
from common.files.utils import copy_file
from common.media.exiftool import (
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    MetadataWriteRequest,
    build_datetime_tags,
)
from common.output.logging import get_processor_logger
from common.processing import print_processing_summary
from common.utils import (
    default_worker_count,
    FilenameGenerator,
    get_media_type,
    update_file_timestamps,
)
from common.files.utils import short_path
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.discord.preprocess import DiscordPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


class DiscordProcessor(ProcessorBase):
    """Processor for Discord data exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this is a Discord export (Messages/index.json or preprocessed)."""
        try:
            # Check for raw Discord export structure
            messages_dir = input_path / "Messages"
            if messages_dir.exists() and messages_dir.is_dir():
                index_file = messages_dir / "index.json"
                if index_file.exists():
                    # Verify at least one channel folder exists
                    channel_folders = [
                        d
                        for d in messages_dir.iterdir()
                        if d.is_dir() and d.name.startswith("c")
                    ]
                    if channel_folders:
                        return True

            # Check for preprocessed Discord export using platform/service markers
            if ProcessorBase.is_preprocessed_for_platform(
                input_path, platform="Discord", service=None
            ):
                return True

            return False

        except Exception as e:
            log.detection_failed("Discord", e)
            return False

    @staticmethod
    def get_name() -> str:
        return "Discord"

    @staticmethod
    def get_priority() -> int:
        # Fast processor (~minutes) - run early when multiple processors match
        return 90

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Discord",
            service=None,
            username_prefix="discord",
        )

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def get_required_dependencies() -> List[str]:
        # Requires ffmpeg for large video metadata fallback when ExifTool
        # cannot rewrite in-place (e.g., "Can't yet handle sidx box")
        return ["exiftool", "ffmpeg"]

    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        """Check if Discord export needs preprocessing."""
        input_path = Path(input_dir)

        # Check if already preprocessed (validated metadata schema)
        if DiscordProcessor.is_preprocessed_export_dir(
            input_path, container_key="conversations"
        ):
            return False

        # Check for raw export structure (Messages/ directory)
        messages_dir = input_path / "Messages"
        if messages_dir.exists():
            return True

        return False

    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        """Run preprocessing on raw Discord export."""
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")

        log.logger.info("Preprocessing Discord export to: %s", cache_path)

        cache_path.mkdir(parents=True, exist_ok=True)

        preprocessor = DiscordPreprocessor(
            export_path=input_path,
            output_dir=cache_path,
            workers=workers,
        )

        if preprocessor.run():
            return str(cache_path)
        return None

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata (generator).

        Requires preprocessed directories (with metadata.json and source_path).
        """
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        def extract_preprocessed_metadata(entry: Dict[str, Any]) -> Dict[str, Any]:
            """Extract metadata from preprocessed entry."""
            result: Dict[str, Any] = {}
            # Build conversation from guild + channel
            guild_name = entry.get("guild_name")
            channel_title = entry.get("channel_title", "")
            if guild_name:
                result["conversation"] = f"{guild_name} / {channel_title}"
            elif channel_title:
                result["conversation"] = channel_title
            if "sender" in entry:
                result["sender"] = entry["sender"]
            if "timestamp" in entry:
                result["timestamp"] = entry["timestamp"]
            if "content" in entry:
                result["content"] = entry["content"]
            return result

        catalog_iter = DiscordProcessor.catalog_from_preprocessed_if_available(
            processor_name="Discord",
            input_path=input_path,
            workers=workers,
            metadata_extractor=extract_preprocessed_metadata,
            container_key="conversations",
        )
        if catalog_iter is not None:
            yield from catalog_iter
        else:
            log.logger.warning(
                "Directory not preprocessed, skipping catalog: %s", input_path
            )

    @classmethod
    def process(
        cls,
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process Discord export."""
        # Check for required dependencies
        if not cls.check_dependencies():
            return False

        try:
            # Create messages subdirectory under output
            if output_dir:
                processor_output = str(Path(output_dir) / "messages")
            else:
                processor_output = kwargs.get("output", "final_discord/messages")

            # Call processing logic
            process_logic(
                input_dir=input_dir,
                output_dir=processor_output,
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.exception(f"Error in DiscordProcessor: {e}")
            return False


def _build_description(message_info: dict, export_username: str) -> str:
    """Build the description string for a Discord message using unified format."""
    message = message_info.get("message", {})
    channel_type = message_info.get("channel_type", "unknown")
    channel_title = message_info.get("channel_title", "Unknown")
    guild_name = message_info.get("guild_name")

    # Build conversation name based on channel type
    if channel_type == "dm":
        # Extract username, stripping discriminator (e.g., #0) if present
        match = re.search(r"Direct Message with (.+?)(?:#\d+)?$", channel_title)
        if match:
            recipient = match.group(1)
            conv_name = f"DM with {recipient}"
        else:
            conv_name = f"DM with {channel_title}"
    elif channel_type == "group_dm":
        conv_name = "Group DM"
    elif channel_type == "server":
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

    # Build description using unified format
    entry = SourceEntry(
        processor=PLATFORM_DISCORD,
        username=export_username,
        conversation=conv_name,
        content=message.get("content", ""),
    )
    return build_description(entry)


def _build_metadata_requests(
    file_info: List[Tuple[Optional[str], dict, str, Optional[str], Optional[str]]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Discord files.

    Args:
        file_info: List of (file_path, message_info, export_username, content_hash,
                   original_filename) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for (
        file_path,
        message_info,
        export_username,
        content_hash,
        original_filename,
    ) in file_info:
        if file_path is None:
            continue
        message = message_info.get("message", {})

        # Build datetime tags (Discord format: "YYYY-MM-DD HH:MM:SS UTC")
        canonical_ts = message.get("canonical_timestamp") or message.get(
            "timestamp", ""
        )
        datetime_tags = build_datetime_tags(canonical_ts, strip_utc=True)

        # Build description
        default_description = _build_description(message_info, export_username)

        requests.append(
            MetadataWriteRequest(
                file_path=file_path,
                arg_preamble=DEFAULT_PREAMBLE,
                datetime_tags=datetime_tags,
                gps=None,
                description=DescriptionWriteSpec(
                    default_description=default_description,
                ),
                processor_name="discord",
                content_hash=content_hash,
                original_filename=original_filename,
            )
        )

    return requests


def build_filename_index(metadata: dict) -> Dict[str, dict]:
    """Build index mapping media filename to message metadata."""

    def _discord_context_builder(conversation: dict, message: dict) -> dict:
        """Build Discord-specific context with channel information."""
        return {
            "message": message,
            "channel_type": conversation.get("type"),
            "channel_title": conversation.get("title"),
            "guild_name": conversation.get("guild_name"),
        }

    return ProcessorBase.build_media_filename_index(
        metadata,
        media_keys=["media_files"],
        context_builder=_discord_context_builder,
    )


def _process_file_worker(args_tuple):
    """Worker function for processing individual files."""
    (
        media_path,
        message_info,
        export_username,
        output_dir,
        output_filename,
        content_hash,
        original_filename,
    ) = args_tuple

    try:
        output_path = output_dir / output_filename

        # Copy file to output
        copy_file(media_path, output_path)

        # Update filesystem timestamps - Discord uses "YYYY-MM-DD HH:MM:SS UTC" format
        timestamp_str = message_info["message"].get(
            "canonical_timestamp"
        ) or message_info["message"].get("timestamp", "")
        if timestamp_str:
            update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")

        return (
            True,
            str(output_path),
            message_info,
            export_username,
            content_hash,
            original_filename,
        )

    except Exception as e:
        logger.error("Error processing %s: %s", media_path.name, e)
        return (
            False,
            None,
            message_info,
            export_username,
            content_hash,
            original_filename,
        )


def process_logic(
    input_dir: str,
    output_dir: str = "processed_discord",
    workers: Optional[int] = None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Core processing logic for Discord exports.

    Expects input_dir to be preprocessed (via memoria.py preprocessing phase
    or by calling DiscordProcessor.preprocess() directly).

    Returns:
        True if processing succeeded, False otherwise.
    """
    # Validate input directory exists
    input_path = Path(input_dir)
    if not input_path.exists():
        log.file_not_found(str(input_path), "Input directory")
        return False

    # Check for required tools
    if not DiscordProcessor.check_dependencies():
        return False

    log.processor_start("Discord Processor")

    output_path = Path(output_dir)

    # Require preprocessed input
    if not DiscordProcessor.is_preprocessed_export_dir(
        input_path, container_key="conversations"
    ):
        log.logger.error(
            "Input directory is not preprocessed: %s. "
            "Call preprocess() first or run via memoria.py.",
            input_path,
        )
        return False

    log.already_preprocessed(str(input_path))
    return _process_working_directory(input_path, output_path, workers, dedup_manifest)


def _process_working_directory(
    working_dir: Path,
    output_dir: Path,
    workers: Optional[int],
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Process a working directory (preprocessed export).

    Returns:
        True if processing succeeded, False otherwise.
    """
    # Configuration
    metadata_file = working_dir / "metadata.json"

    # Check for metadata file
    if not metadata_file.exists():
        log.file_not_found(str(metadata_file), "Metadata file")
        return False

    # Load metadata
    log.loading_metadata(str(metadata_file))
    metadata = DiscordProcessor.load_metadata(
        metadata_file,
        validate=True,
        require_source_paths=True,
        processor_name="Discord",
    )

    export_info = metadata.get("export_info", {})
    export_username = export_info.get("export_username")
    log.export_username(export_username)

    # Initialize failure tracker for this export
    failure_tracker = FailureTracker(
        processor_name="Discord",
        export_directory=str(working_dir),
    )

    # Build filename index
    log.building_index("filename")
    filename_index = build_filename_index(metadata)
    log.found_items(len(filename_index), "media files in metadata")

    # Build list of media files from source_paths in metadata
    log.logger.info("Collecting media files from source_path entries...")
    media_files = []
    invalid_sources = 0
    invalid_examples: list[str] = []
    for filename, info in filename_index.items():
        message = info.get("message", {})
        source_paths = message.get("source_paths", {})
        source_path_str = source_paths.get(filename)
        if source_path_str:
            source_path = Path(source_path_str)
            if source_path.exists() and get_media_type(source_path):
                media_files.append(source_path)
            else:
                invalid_sources += 1
                if len(invalid_examples) < 5:
                    invalid_examples.append(short_path(source_path))
    if invalid_sources:
        logger.warning(
            "Source file missing or not media for %d entries (e.g. %s)",
            invalid_sources,
            ", ".join(invalid_examples),
        )
    log.found_files(len(media_files), "media")

    # Filter out secondary files based on dedup manifest (using hash-based lookup)
    file_hashes = metadata.get("file_hashes", {})
    skipped_secondary_files = []
    if dedup_manifest:
        original_count = len(media_files)
        filtered_files = []
        for f in media_files:
            content_hash = file_hashes.get(f.name)
            source_path_str = str(f)
            if DiscordProcessor.should_skip_for_dedup(
                dedup_manifest, content_hash, "Discord", source_path_str
            ):
                skipped_secondary_files.append(str(f))
            else:
                filtered_files.append(f)
        media_files = filtered_files
        skipped_count = original_count - len(media_files)
        log.skipped_secondary(skipped_count)

    if not media_files:
        log.logger.warning("No media files found to process.")
        failure_tracker.add_export_failure(
            reason="no_media_files",
            error_details="No media files found in export to process",
        )
        failure_tracker.handle_failures(output_dir)
        return False

    # Create output directory (subdirectory already set by caller)
    output_dir.mkdir(exist_ok=True, parents=True)

    # Match files to metadata and generate output filenames
    log.logger.info("Matching files to metadata...")
    matched_files = []
    unmatched_files = []
    filename_gen = FilenameGenerator(
        "discord", export_username, timestamp_format="%Y-%m-%d %H:%M:%S"
    )

    for media_file in media_files:
        if media_file.name in filename_index:
            message_info = filename_index[media_file.name]
            # Generate output filename from timestamp (FilenameGenerator handles date + uniqueness)
            ext = media_file.suffix.lower()
            timestamp = message_info["message"].get("timestamp", "")
            # Remove " UTC" suffix for parsing
            timestamp_clean = timestamp.replace(" UTC", "").strip() if timestamp else ""
            # Get content_hash from preprocessing
            content_hash = file_hashes.get(media_file.name)
            canonical_ts = get_canonical_timestamp(
                dedup_manifest, content_hash, timestamp_clean
            )
            message_info["message"]["canonical_timestamp"] = canonical_ts
            output_filename = filename_gen.generate(
                canonical_ts or timestamp_clean, ext
            )
            matched_files.append(
                (media_file, message_info, output_filename, content_hash)
            )
        else:
            unmatched_files.append(media_file)

    log.matched_files(len(matched_files), len(unmatched_files))

    # Process matched files
    log.logger.info("Processing %d files to %s/", len(matched_files), output_dir)
    log.phase_separator()

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    log.using_workers(num_workers)

    # Prepare arguments for parallel processing
    process_args = []
    for media_file, message_info, output_filename, content_hash in matched_files:
        # Get original filename for XMP:PreservedFileName embedding
        original_filename = media_file.name

        process_args.append(
            (
                media_file,
                message_info,
                export_username,
                output_dir,
                output_filename,
                content_hash,
                original_filename,
            )
        )

    # Process files (multiprocess or sequential depending on worker count)
    results = DiscordProcessor.process_items_parallel(
        tasks=process_args,
        worker_fn=_process_file_worker,
        num_workers=num_workers,
        description="Processing files",
    )

    # Collect results for EXIF processing
    # Result tuple: (success, output_path, message_info, exp_username, content_hash, original_filename)
    def extract_info(r):
        return (
            r[1],
            r[2],
            r[3],
            r[4],
            r[5],
        )  # (output_path, message_info, exp_username, hash, original_filename)

    collected = DiscordProcessor.collect_processing_results(results, extract_info)

    log.copied_files(collected.success_count, collected.failed_count)

    # Correct file extensions and batch process EXIF metadata
    log.exif_batch_start()

    # Build metadata lookup for failure tracking
    def metadata_for_file(file_path: str) -> Optional[dict]:
        # file_info tuples: (output_path, message_info, exp_username, hash, original_filename)
        for info in collected.file_info:
            if info[0] == file_path:
                return info[1].get("message") if isinstance(info[1], dict) else None
        return None

    _, _, exif_rebuilt_count = DiscordProcessor.finalize_files(
        collected.file_paths,
        collected.file_info,
        _build_metadata_requests,
        dedup_manifest,
        path_index=0,
        failure_tracker=failure_tracker,
        metadata_for_file=metadata_for_file,
    )

    # Handle unmatched files via FailureTracker
    if unmatched_files:
        log.logger.info("Processing %d unmatched files...", len(unmatched_files))
        for media_file in unmatched_files:
            failure_tracker.add_orphaned_media(
                source_path=media_file,
                reason="No matching metadata found",
            )

    # Handle failures - copy orphaned files and generate report
    failure_tracker.handle_failures(output_dir)

    # Save dedup references for skipped secondary files
    if dedup_manifest and skipped_secondary_files:
        save_dedup_metadata(
            manifest=dedup_manifest,
            skipped_files=skipped_secondary_files,
            processor_name="Discord",
            output_dir=output_dir,
        )

    # Print summary using shared utility
    print_processing_summary(
        success=collected.success_count,
        failed=collected.failed_count,
        total=len(media_files),
        output_dir=str(output_dir),
        extra_stats={
            "EXIF structures rebuilt": exif_rebuilt_count,
            "Unmatched": len(unmatched_files),
        },
    )

    if unmatched_files:
        log.logger.info(
            "Unmatched files were tracked via FailureTracker (see issues/failure-report.json)."
        )

    return True
