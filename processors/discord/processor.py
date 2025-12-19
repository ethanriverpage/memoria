#!/usr/bin/env python3
"""
Discord Processor

Processes Discord data exports, handling attachment downloads
and metadata embedding for media files.
"""

import json
import logging
import re
import shutil
from multiprocessing import Pool
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.catalog import catalog_media_files
from common.dedup.service import is_file_primary_by_hash, save_dedup_metadata
from common.files.utils import batch_correct_extensions
from common.dedup.hashing import compute_content_hash
from common.dependency_checker import (
    check_exiftool,
    print_exiftool_error,
)
from common.media.exiftool import (
    batch_read_existing_metadata,
    batch_rebuild_exif,
    batch_validate_exif,
    batch_write_metadata_requests,
    DEFAULT_PREAMBLE,
    DescriptionWriteSpec,
    MetadataWriteRequest,
)
from common.processing import (
    get_preprocessing_cache_path,
    is_preprocessing_cache_valid,
    print_processing_summary,
    temp_processing_directory,
)
from common.output.progress import PHASE_PROCESS, progress_bar
from common.utils import (
    default_worker_count,
    FilenameGenerator,
    get_media_type,
    is_preprocessed_directory,
    sanitize_filename,
    update_file_timestamps,
)
from processors.base import ProcessorBase
from processors.discord.preprocess import DiscordPreprocessor

logger = logging.getLogger(__name__)


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

        # Check for preprocessed Discord export
        metadata_file = input_path / "metadata.json"
        media_dir = input_path / "media"

        if metadata_file.exists() and media_dir.exists():
            try:
                with open(metadata_file, "r", encoding="utf-8") as f:
                    metadata = json.load(f)

                # Discord preprocessed format has conversations dict
                if isinstance(metadata, dict) and "conversations" in metadata:
                    export_info = metadata.get("export_info", {})
                    # Check for Discord-specific indicators
                    if (
                        "downloads_successful" in export_info
                        or "downloads_failed" in export_info
                    ):
                        return True
                    # Check for Discord-style conversation structure
                    conversations = metadata.get("conversations", {})
                    if conversations:
                        # Check first conversation for Discord-specific fields
                        first_conv = next(iter(conversations.values()), {})
                        if "guild_name" in first_conv or "original_urls" in str(
                            first_conv
                        ):
                            return True

            except (json.JSONDecodeError, KeyError):
                pass

        return False

    except Exception as e:
        logger.debug(f"Detection failed for Discord: {e}")
        return False


class DiscordProcessor(ProcessorBase):
    """Processor for Discord data exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        return "Discord"

    @staticmethod
    def get_priority() -> int:
        return 70

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def build_catalog(input_dir: str, **kwargs) -> Dict[str, Any]:
        """Build catalog of files with hashes and metadata."""
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        empty_catalog = {
            "processor": "Discord",
            "input_dir": str(input_path),
            "files": [],
        }

        # Determine the working directory for cataloging
        working_dir = None
        media_dir = input_path / "media"

        if media_dir.exists():
            # Already preprocessed
            working_dir = input_path
        else:
            # Check for raw export structure (Messages/ directory with channel folders)
            messages_dir = input_path / "Messages"
            if messages_dir.exists():
                # Check for existing preprocessing cache
                cache_path = get_preprocessing_cache_path(input_path, "Discord")

                if is_preprocessing_cache_valid(cache_path):
                    logger.info(f"Using preprocessing cache: {cache_path}")
                    working_dir = cache_path
                else:
                    # Auto-preprocess to cache directory (downloads attachments)
                    logger.info(
                        f"Raw Discord export detected, preprocessing to cache: {cache_path}"
                    )
                    logger.info("This will download attachments from Discord CDN...")
                    cache_path.mkdir(parents=True, exist_ok=True)

                    preprocessor = DiscordPreprocessor(
                        export_path=input_path,
                        output_dir=cache_path,
                        workers=workers,
                    )
                    preprocessor.process()

                    working_dir = cache_path
            else:
                logger.warning(f"No media directory found in: {input_path}")
                return empty_catalog

        if working_dir is None:
            return empty_catalog

        # Now catalog from the working directory
        media_dir = working_dir / "media"
        if not media_dir.exists():
            logger.warning(
                f"No media directory found after preprocessing: {working_dir}"
            )
            return empty_catalog

        # Collect all media files
        media_files = [
            f for f in media_dir.iterdir() if f.is_file() and get_media_type(f)
        ]

        return catalog_media_files(
            processor_name="Discord",
            input_path=working_dir,
            media_files=media_files,
            workers=workers,
        )

    @staticmethod
    def process(
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process Discord export."""
        # Check for required dependencies
        if not check_exiftool():
            print_exiftool_error()
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
                temp_dir=kwargs.get("temp_dir", "../pre"),
                verbose=kwargs.get("verbose", False),
                workers=kwargs.get("workers"),
                dedup_manifest=dedup_manifest,
            )
            return True

        except Exception as e:
            logger.exception(f"Error in DiscordProcessor: {e}")
            return False


def get_processor():
    """Return processor class for auto-discovery."""
    return DiscordProcessor


def _build_description(message_info: dict, export_username: str) -> str:
    """Build the description string for a Discord message."""
    message = message_info.get("message", {})
    channel_type = message_info.get("channel_type", "unknown")
    channel_title = message_info.get("channel_title", "Unknown")
    guild_name = message_info.get("guild_name")

    description_parts = [f"Source: Discord/{export_username}"]

    # Build conversation name based on channel type
    if channel_type == "dm":
        if "Direct Message with" in channel_title:
            recipient = channel_title.replace("Direct Message with ", "")
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

    # Add conversation context with message content
    content = message.get("content", "")
    if content:
        content = content.replace("\n", " ")
        if len(content) > 100:
            content = content[:97] + "..."
        description_parts.append(f'  - in "{conv_name}": "{content}"')
    else:
        description_parts.append(f'  - in "{conv_name}"')

    return "&#xa;".join(description_parts)


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Discord files."""
    requests: List[MetadataWriteRequest] = []

    for file_path, message_info, export_username in file_info:
        message = message_info.get("message", {})

        # Build datetime tags
        datetime_tags: List[Tuple[str, str]] = []
        timestamp = message.get("timestamp", "")
        if timestamp:
            # Discord format: "YYYY-MM-DD HH:MM:SS UTC"
            date_str = timestamp.replace(" UTC", "").strip()
            exif_date = date_str.replace("-", ":")
            datetime_tags = [
                ("DateTimeOriginal", exif_date),
                ("CreateDate", exif_date),
                ("ModifyDate", exif_date),
            ]

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
            )
        )

    return requests


def load_metadata(metadata_path: Path) -> dict:
    """Load and parse metadata.json."""
    with open(metadata_path, "r", encoding="utf-8") as f:
        return json.load(f)


def build_filename_index(metadata: dict) -> Dict[str, dict]:
    """Build index mapping media filename to message metadata."""
    filename_index = {}

    for channel_id, conv_data in metadata.get("conversations", {}).items():
        for message in conv_data.get("messages", []):
            media_files = message.get("media_files", [])
            for filename in media_files:
                # Store both message and channel context
                filename_index[filename] = {
                    "message": message,
                    "channel_id": channel_id,
                    "channel_type": conv_data.get("type"),
                    "channel_title": conv_data.get("title"),
                    "guild_name": conv_data.get("guild_name"),
                }

    return filename_index


def _build_discord_identifier(message_info: dict, export_username: str) -> str:
    """Build identifier for Discord filename based on channel type."""
    channel_title = message_info.get("channel_title", "unknown")
    channel_type = message_info.get("channel_type", "unknown")

    # Sanitize channel title for filename
    if channel_type == "dm":
        # Extract username from DM title if possible
        # Format: "Direct Message with username#0"
        match = re.search(r"Direct Message with (.+?)(?:#\d+)?$", channel_title)
        if match:
            channel_part = sanitize_filename(match.group(1))
        else:
            channel_part = "dm"
    elif channel_type == "group_dm":
        channel_part = "group"
    else:
        # Server channel - use channel name
        channel_part = sanitize_filename(
            channel_title.split(" in ")[0] if " in " in channel_title else channel_title
        )

    # Truncate channel part if too long
    if len(channel_part) > 30:
        channel_part = channel_part[:30]

    return f"{export_username}-{channel_part}"


def _process_file_worker(args_tuple):
    """Worker function for processing individual files."""
    media_path, message_info, export_username, output_dir, output_filename = args_tuple

    try:
        output_path = output_dir / output_filename

        # Copy file to output
        shutil.copy2(media_path, output_path)

        # Update filesystem timestamps - Discord uses "YYYY-MM-DD HH:MM:SS UTC" format
        timestamp_str = message_info["message"].get("timestamp", "")
        if timestamp_str:
            update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")

        return (True, str(output_path), message_info, export_username)

    except Exception as e:
        logger.error(f"Error processing {media_path.name}: {e}")
        return (False, None, message_info, export_username)


def process_logic(
    input_dir: str,
    output_dir: str = "processed_discord",
    temp_dir: str = "../pre",
    verbose: bool = False,
    workers: Optional[int] = None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Discord exports."""
    # Validate input directory exists
    input_path = Path(input_dir)
    if not input_path.exists():
        logger.error(f"Input directory not found: {input_path}")
        return

    # Check for required tools
    if not check_exiftool():
        print_exiftool_error()
        return

    logger.info("Discord Processor")
    logger.info("=" * 50)

    # Determine if input needs preprocessing
    output_path = Path(output_dir)

    # Check if input is already preprocessed
    if is_preprocessed_directory(str(input_path)):
        logger.info(f"Input directory is already preprocessed: {input_path}")
        _process_working_directory(input_path, output_path, workers, dedup_manifest)
    else:
        # Check for preprocessing cache from catalog phase
        cache_path = get_preprocessing_cache_path(input_path, "Discord")

        if is_preprocessing_cache_valid(cache_path):
            logger.info(f"Using preprocessing cache from catalog phase: {cache_path}")
            _process_working_directory(cache_path, output_path, workers, dedup_manifest)
        else:
            logger.info(f"Input directory is raw export: {input_path}")
            logger.info("Running preprocessing (downloading attachments)...")

            # Use context manager for temp directory with automatic cleanup
            with temp_processing_directory(temp_dir, "discord") as temp_dir_path:
                logger.info(f"Preprocessing to: {temp_dir_path}")

                # Run preprocessing
                # Note: output_dir already has /messages appended by the caller (process method)
                preprocessor = DiscordPreprocessor(
                    export_path=input_path,
                    output_dir=temp_dir_path,
                    workers=workers,
                    final_output_dir=output_path,
                )
                preprocessor.process()

                logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
                _process_working_directory(
                    temp_dir_path, output_path, workers, dedup_manifest
                )


def _process_working_directory(
    working_dir: Path,
    output_dir: Path,
    workers: Optional[int],
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Process a working directory (preprocessed export)."""
    # Configuration
    metadata_file = working_dir / "metadata.json"
    media_dir = working_dir / "media"

    # Check for metadata file
    if not metadata_file.exists():
        logger.error(f"Metadata file not found: {metadata_file}")
        return

    # Check for media directory
    if not media_dir.exists():
        logger.warning(f"Media directory not found: {media_dir}")
        logger.warning("No media files to process.")
        return

    # Load metadata
    logger.info(f"Loading metadata from {metadata_file}...")
    metadata = load_metadata(metadata_file)

    export_username = metadata.get("export_info", {}).get("export_username", "unknown")
    logger.info(f"Export username: {export_username}")

    # Build filename index
    logger.info("Building filename index...")
    filename_index = build_filename_index(metadata)
    logger.info(f"Found {len(filename_index)} media files in metadata")

    # Scan media directory for actual files
    logger.info(f"Scanning media directory: {media_dir}")
    media_files = list(media_dir.glob("*"))
    media_files = [f for f in media_files if f.is_file() and get_media_type(f)]
    logger.info(f"Found {len(media_files)} media files on disk")

    # Filter out secondary files based on dedup manifest (using hash-based lookup)
    skipped_secondary_files = []
    if dedup_manifest:
        original_count = len(media_files)
        filtered_files = []
        for f in media_files:
            content_hash = compute_content_hash(f)
            if is_file_primary_by_hash(dedup_manifest, content_hash, "Discord"):
                filtered_files.append(f)
            else:
                skipped_secondary_files.append(str(f))
        media_files = filtered_files
        skipped_count = original_count - len(media_files)
        if skipped_count > 0:
            logger.info(f"Skipped {skipped_count} secondary files (dedup)")

    if not media_files:
        logger.warning("No media files found to process.")
        return

    # Create output directory (subdirectory already set by caller)
    output_dir.mkdir(exist_ok=True, parents=True)

    # Match files to metadata and generate output filenames
    logger.info("Matching files to metadata...")
    matched_files = []
    unmatched_files = []
    filename_gen = FilenameGenerator("discord", timestamp_format="%Y-%m-%d %H:%M:%S")

    for media_file in media_files:
        if media_file.name in filename_index:
            message_info = filename_index[media_file.name]
            # Build identifier (policy) and delegate to FilenameGenerator (mechanism)
            ext = media_file.suffix.lower()
            identifier = _build_discord_identifier(message_info, export_username)
            timestamp = message_info["message"].get("timestamp", "")
            # Remove " UTC" suffix for parsing
            timestamp_clean = timestamp.replace(" UTC", "").strip() if timestamp else ""
            output_filename = filename_gen.generate(timestamp_clean, identifier, ext)
            matched_files.append((media_file, message_info, output_filename))
        else:
            unmatched_files.append(media_file)

    logger.info(f"Matched {len(matched_files)} files")
    logger.info(f"Unmatched {len(unmatched_files)} files")

    # Process matched files
    logger.info(f"Processing {len(matched_files)} files to {output_dir}/")
    logger.info("=" * 50)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    logger.info(f"Using {num_workers} parallel workers")

    # Prepare arguments for parallel processing
    process_args = []
    for media_file, message_info, output_filename in matched_files:
        process_args.append(
            (
                media_file,
                message_info,
                export_username,
                output_dir,
                output_filename,
            )
        )

    # Process files in parallel
    success_count = 0
    failed_count = 0

    if num_workers > 1:
        with Pool(processes=num_workers) as pool:
            results = list(
                progress_bar(
                    pool.imap(_process_file_worker, process_args),
                    PHASE_PROCESS,
                    "Processing files",
                    total=len(process_args),
                )
            )
    else:
        # Single-threaded for debugging
        results = []
        for args_tuple in progress_bar(process_args, PHASE_PROCESS, "Processing files"):
            results.append(_process_file_worker(args_tuple))

    # Collect results for EXIF processing
    file_paths = []
    file_info = []

    for success, output_path, message_info, exp_username in results:
        if success and output_path:
            success_count += 1
            file_paths.append(output_path)
            file_info.append((output_path, message_info, exp_username))
        else:
            failed_count += 1

    logger.info(
        f"\nCopied {success_count} files ({len(file_info)} for EXIF processing)"
    )

    # Correct file extensions based on actual content
    if file_paths:
        corrections = batch_correct_extensions([Path(p) for p in file_paths])
        if corrections:
            # Update file_paths and file_info with corrected paths
            file_paths = [corrections.get(p, p) for p in file_paths]
            file_info = [(corrections.get(fp, fp), mi, eu) for fp, mi, eu in file_info]

    # Batch process EXIF operations
    exif_rebuilt_count = 0
    if file_paths:
        logger.info("Batch processing EXIF metadata...")

        # Batch validate and rebuild
        corrupted_files = batch_validate_exif(file_paths)
        if corrupted_files:
            logger.info(
                f"Rebuilding {len(corrupted_files)} corrupted EXIF structures..."
            )
            batch_rebuild_exif(list(corrupted_files))
            exif_rebuilt_count = len(corrupted_files)

        # Batch read and write metadata
        existing_metadata_map = batch_read_existing_metadata(file_paths)
        requests = _build_metadata_requests(file_info)
        batch_write_metadata_requests(requests, existing_metadata_map)

    # Handle unmatched files
    if unmatched_files:
        logger.info(f"Processing {len(unmatched_files)} unmatched files...")
        failed_matching_dir = output_dir / "issues" / "failed-matching" / "media"
        failed_matching_dir.mkdir(exist_ok=True, parents=True)

        for media_file in unmatched_files:
            try:
                shutil.copy2(media_file, failed_matching_dir / media_file.name)
            except Exception as e:
                logger.error(f"Error copying unmatched file {media_file.name}: {e}")

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
        success=success_count,
        failed=failed_count,
        total=len(media_files),
        output_dir=str(output_dir),
        extra_stats={
            "EXIF structures rebuilt": exif_rebuilt_count,
            "Unmatched": len(unmatched_files),
        },
    )

    if unmatched_files:
        logger.info(
            f"Unmatched files saved to: {(output_dir / 'issues' / 'failed-matching' / 'media').absolute()}"
        )
