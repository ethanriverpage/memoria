"""
Google Voice Messages Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Google Voice message media files, embedding metadata, and updating filesystem timestamps.
"""

import logging
import multiprocessing
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.catalog import catalog_media_files
from common.dedup.service import is_file_primary_by_hash
from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_GOOGLE,
    SERVICE_VOICE,
)
from common.files.utils import batch_correct_extensions, copy_file
from common.processing import print_processing_summary
from common.output.progress import PHASE_PROCESS, progress_bar
from common.output.paths import OutputInfo
from processors.google_voice.preprocess import GoogleVoicePreprocessor
from processors.base import ProcessorBase
from common.dependency_checker import check_exiftool, print_exiftool_error
from common.utils import (
    ALL_MEDIA_EXTENSIONS,
    FilenameGenerator,
    is_preprocessed_directory,
    sanitize_filename,
    update_file_timestamps,
)
from common.media.exiftool import (
    DescriptionWriteSpec,
    MetadataWriteRequest,
    PREAMBLE_WITH_IGNORE_ERRORS,
)

# Set up logging
logger = logging.getLogger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory

    Detection criteria for Google Voice:
    - Directory contains 'Voice/Calls/' subdirectory
    - Calls directory contains HTML files with pattern: +XXXXXXXXXX - Text - YYYY-MM-DDTHH_MM_SSZ.html

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is a Google Voice export, False otherwise
    """
    try:
        voice_dir = input_path / "Voice"
        calls_dir = voice_dir / "Calls"

        # Check if Voice/Calls structure exists
        if not voice_dir.exists() or not voice_dir.is_dir():
            return False

        if not calls_dir.exists() or not calls_dir.is_dir():
            return False

        # Check for at least one HTML file matching the Voice pattern
        # Pattern: +XXXXXXXXXX - Text - YYYY-MM-DDTHH_MM_SSZ.html
        voice_pattern = re.compile(
            r"^\+\d+ - Text - \d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}Z\.html$"
        )

        for file_path in calls_dir.iterdir():
            if file_path.is_file() and voice_pattern.match(file_path.name):
                return True

        return False

    except Exception as e:
        logger.debug(f"Detection failed for Google Voice: {e}")
        return False


class GoogleVoiceProcessor(ProcessorBase):
    """Processor for Google Voice exports"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input"""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name"""
        return "Google Voice"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first)"""
        return 50  # Medium priority

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Google",
            service="voice",
            username_prefix="google",
        )

    @staticmethod
    def supports_dedup() -> bool:
        """Return True - Google Voice supports cross-processor deduplication."""
        return True

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata.

        Scans all media files in the Google Voice export, computes hashes,
        and returns a catalog structure without copying any files.

        Args:
            input_dir: Path to input directory
            **kwargs: Additional arguments (verbose, workers)

        Returns:
            Catalog dictionary with processor name and file entries.
        """
        input_path = Path(input_dir)
        workers = kwargs.get("workers", 4)

        # Find media in Google Voice structure
        voice_dir = input_path / "Voice" / "Calls"
        if not voice_dir.exists():
            return

        media_files = []
        for file_path in voice_dir.rglob("*"):
            if file_path.is_file() and file_path.suffix.lower() in ALL_MEDIA_EXTENSIONS:
                media_files.append(file_path)

        yield from catalog_media_files(
            processor_name="Google Voice",
            input_path=input_path,
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
        """Process Google Voice export

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            dedup_manifest: Optional deduplication manifest
            **kwargs: Additional arguments (verbose, workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Check for required dependencies
        if not check_exiftool():
            print_exiftool_error()
            return False

        try:
            # Choose content-type subdirectory under provided base output
            if output_dir:
                processor_output = str(Path(output_dir) / "voice")
            else:
                processor_output = kwargs.get("output", "final_googlevoice")

            # Call processing logic directly
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
            logger.error(f"Error in GoogleVoiceProcessor: {e}")
            return False


# ============================================================================
# Helper Functions
# ============================================================================


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str, str]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Google Voice files.

    Args:
        file_info: List of (file_path, message_data, conversation_name, export_username) tuples

    Returns:
        List of MetadataWriteRequest objects
    """
    requests: List[MetadataWriteRequest] = []

    for file_path, message_data, conversation_name, export_username in file_info:
        # Build datetime tags
        datetime_tags: List[Tuple[str, str]] = []
        timestamp_str = message_data.get("timestamp")
        if timestamp_str:
            date_obj = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            exif_date = date_obj.strftime("%Y:%m:%d %H:%M:%S")
            datetime_tags = [
                ("DateTimeOriginal", exif_date),
                ("CreateDate", exif_date),
                ("ModifyDate", exif_date),
            ]

        # Build description using unified format
        other_party = conversation_name.replace("Group: ", "")
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username=export_username,
            service=SERVICE_VOICE,
            conversation=(
                f"DM with {other_party}"
                if "Group:" not in conversation_name
                else other_party
            ),
        )
        default_description = build_description(entry)

        requests.append(
            MetadataWriteRequest(
                file_path=file_path,
                arg_preamble=PREAMBLE_WITH_IGNORE_ERRORS,
                datetime_tags=datetime_tags,
                gps=None,
                description=DescriptionWriteSpec(
                    default_description=default_description,
                ),
                processor_name="google_voice",
            )
        )

    return requests


def process_media_batch(batch_args):
    """Process a batch of media files (worker function for multiprocessing)

    Copies files, corrects extensions, and updates timestamps.
    EXIF validation/rebuild and metadata writing happen in the main process.

    Args:
        batch_args: List of tuples, each containing (media_file, message_data,
                    conversation_name, output_filename, media_dir, output_dir, export_username)

    Returns:
        List of (success, output_path, file_info_tuple) tuples where file_info_tuple is
        (output_path, message_data, conversation_name, export_username) or None on failure
    """
    results = []

    # Phase 1: Copy all files
    file_paths = []
    file_info = []

    for args_tuple in batch_args:
        (
            media_file,
            message_data,
            conversation_name,
            output_filename,
            media_dir,
            output_dir,
            export_username,
        ) = args_tuple

        media_path = os.path.join(media_dir, media_file)
        output_path = os.path.join(output_dir, output_filename)

        if not os.path.exists(media_path):
            logger.warning(f"Media file not found: {media_path}")
            results.append((False, None, None))
            continue

        try:
            copy_file(media_path, output_path)
            file_paths.append(output_path)
            file_info.append(
                (output_path, message_data, conversation_name, export_username)
            )
        except Exception as e:
            logger.error(f"Failed to copy {media_file}: {e}")
            results.append((False, None, None))

    if not file_paths:
        return results if results else [(False, None, None)] * len(batch_args)

    # Phase 2: Correct file extensions based on actual content
    corrections = batch_correct_extensions([Path(p) for p in file_paths])
    if corrections:
        # Update file_paths and file_info with corrected paths
        file_paths = [corrections.get(p, p) for p in file_paths]
        file_info = [
            (corrections.get(fp, fp), md, cn, eu) for fp, md, cn, eu in file_info
        ]

    # Phase 3: Update timestamps and compile results
    for output_path, message_data, conversation_name, export_username in file_info:
        update_file_timestamps(output_path, message_data.get("timestamp"))
        results.append(
            (
                True,
                output_path,
                (output_path, message_data, conversation_name, export_username),
            )
        )

    return results


def process_logic(
    input_dir,
    output_dir="final_googlevoice",
    temp_dir="../pre",
    verbose=False,
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Google Voice exports

    Args:
        input_dir: Input directory (raw export or preprocessed)
        output_dir: Output directory for processed files
        temp_dir: Directory for temporary preprocessing files
        verbose: Enable verbose logging
        workers: Number of parallel workers (None = auto-detect)
        dedup_manifest: Optional deduplication manifest for filtering secondary files
    """
    # Logging is configured by the main process
    # Verbose mode enables detailed logging to file

    # Check for exiftool
    if not check_exiftool():
        print_exiftool_error()
        return

    logger.info("Google Voice Messages Media Processor")
    logger.info("=" * 50)

    def _process_working_directory(working_dir: str) -> None:
        # Configuration
        metadata_file = os.path.join(working_dir, "metadata.json")
        media_dir = os.path.join(working_dir, "media")

        # Check for metadata file (should exist after preprocessing or if already preprocessed)
        if not os.path.exists(metadata_file):
            logger.error(f"Metadata file not found: {metadata_file}")
            return

        # Check for media directory
        if not os.path.exists(media_dir):
            logger.error(f"Media directory not found: {media_dir}")
            return

        # Load metadata
        logger.info(f"Loading metadata from {metadata_file}...")
        metadata_json = GoogleVoiceProcessor.load_metadata(Path(metadata_file))

        export_info = metadata_json.get("export_info", {})
        conversations = metadata_json.get("conversations", [])

        # Count total messages with media
        total_media_files = sum(
            len(message.get("media_files", []))
            for conversation in conversations
            for message in conversation.get("messages", [])
        )

        logger.info(f"Found {len(conversations)} conversations")
        logger.info(f"Found {total_media_files} media files to process")

        # Extract export username from export_info
        export_username = export_info.get("export_username", "unknown")
        logger.info(f"Export username: {export_username}")

        # Create output directory (including parents)
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Determine number of workers
        from common.utils import default_worker_count

        num_workers = workers if workers is not None else default_worker_count()
        logger.debug(f"Using {num_workers} parallel workers")

        # Pre-generate all output filenames to avoid race conditions
        logger.debug("Pre-generating filenames...")
        filename_gen = FilenameGenerator("gvoice")
        processing_tasks = []

        skipped_dedup = 0
        # Get file_hashes lookup table for dedup optimization
        file_hashes = metadata_json.get("file_hashes", {})

        for conversation in conversations:
            conversation_name = conversation.get("conversation_name", "unknown")
            sanitized_name = sanitize_filename(conversation_name)
            messages = conversation.get("messages", [])

            for message in messages:
                media_files = message.get("media_files", [])

                for media_file in media_files:
                    # Skip secondary files based on dedup manifest (using hash-based lookup)
                    # Use cached file_hashes from preprocessing when available
                    content_hash = file_hashes.get(media_file)
                    if dedup_manifest:
                        if content_hash:
                            # Use cached hash from preprocessing (avoids re-hashing)
                            if not is_file_primary_by_hash(
                                dedup_manifest, content_hash, "Google Voice"
                            ):
                                skipped_dedup += 1
                                continue
                        else:
                            # Fallback for files without cached hash
                            full_path = os.path.join(media_dir, media_file)
                            if GoogleVoiceProcessor.should_skip_for_dedup(
                                full_path, dedup_manifest, "Google Voice"
                            ):
                                skipped_dedup += 1
                                continue

                    # Get file extension
                    file_ext = os.path.splitext(media_file)[1].lower()

                    # Generate output filename using shared FilenameGenerator
                    identifier = f"{export_username}-{sanitized_name}"
                    output_filename = filename_gen.generate(
                        message.get("timestamp"),
                        identifier,
                        file_ext,
                    )

                    # Create task tuple for worker
                    processing_tasks.append(
                        (
                            media_file,
                            message,
                            conversation_name,
                            output_filename,
                            media_dir,
                            output_dir,
                            export_username,
                        )
                    )

        if skipped_dedup > 0:
            logger.info(f"Skipped {skipped_dedup} secondary files (dedup)")

        # Process media files in parallel
        logger.info("Processing media files to %s/", output_dir)
        logger.info("%s", "=" * 50)

        # Group tasks into batches of 100 files each
        batch_size = 100
        batched_tasks = []
        for i in range(0, len(processing_tasks), batch_size):
            batched_tasks.append(processing_tasks[i : i + batch_size])

        # Process batches in parallel (copy files only)
        with multiprocessing.Pool(processes=num_workers) as pool:
            batch_results = list(
                progress_bar(
                    pool.imap(process_media_batch, batched_tasks),
                    PHASE_PROCESS,
                    "Copying files",
                    total=len(batched_tasks),
                )
            )

        # Flatten results and collect successful files for EXIF processing
        success_count = 0
        failed_count = 0
        file_paths = []
        file_info = []

        for batch_result in batch_results:
            for success, output_path, info_tuple in batch_result:
                if success and output_path:
                    success_count += 1
                    file_paths.append(output_path)
                    file_info.append(info_tuple)
                else:
                    failed_count += 1

        logger.info(f"Copied {success_count} files ({failed_count} failed)")

        # Consolidated EXIF processing
        exif_rebuilt_count = 0
        if file_paths:
            logger.info("Batch processing EXIF metadata...")
            exif_rebuilt_count = GoogleVoiceProcessor.batch_process_exif(
                file_paths, file_info, _build_metadata_requests
            )
            logger.info(f"EXIF metadata written for {len(file_info)} files")

        # Print summary using shared utility
        print_processing_summary(
            success=success_count,
            failed=failed_count,
            total=len(processing_tasks),
            output_dir=output_dir,
            extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
        )

    # Determine if input needs preprocessing
    if is_preprocessed_directory(input_dir):
        logger.info(f"Input directory is already preprocessed: {input_dir}")
        _process_working_directory(input_dir)
        return

    logger.info(f"Input directory is raw export: {input_dir}")
    logger.info("Running preprocessing...")

    # Run preprocessing into a managed temp directory, then process within it.
    with GoogleVoicePreprocessor.temp_output_dir(temp_dir, "gvoice") as temp_dir_path:
        logger.info(f"Preprocessing to: {temp_dir_path}")

        # Run preprocessing with final output directory for failure tracking
        # output_dir is already the processor-specific path
        final_output_path = Path(output_dir)
        preprocessor = GoogleVoicePreprocessor(
            export_path=Path(input_dir),
            output_dir=temp_dir_path,
            workers=workers,
            final_output_dir=final_output_path,
        )
        if not preprocessor.run():
            return False

        logger.info(f"Preprocessing complete. Using: {temp_dir_path}")
        _process_working_directory(str(temp_dir_path))


def get_processor():
    """Return processor class for auto-discovery."""
    return GoogleVoiceProcessor
