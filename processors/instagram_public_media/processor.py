"""
Instagram Media Processor

This processor is designed to be used through memoria.py.
It handles renaming Instagram media files, embedding metadata, and updating filesystem timestamps.
"""
import json
import logging
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

from common.progress import PHASE_PROCESS, progress_bar
from processors.instagram_public_media.preprocess import InstagramPreprocessor
from processors.base import ProcessorBase
from common.dependency_checker import check_exiftool, print_exiftool_error
from common.utils import extract_username_from_export_dir, should_cleanup_temp
from common.exiftool_batch import (
    batch_validate_exif,
    batch_rebuild_exif,
    batch_read_existing_metadata,
    batch_write_metadata_instagram_public,
)

# Set up logging
logger = logging.getLogger(__name__)


# ============================================================================
# Processor Detection and Registration (for unified memoria.py)
# ============================================================================


def detect(input_path: Path) -> bool:
    """Check if this processor can handle the input directory

    Detection criteria for Instagram Public Media (New Format):
    - Directory contains 'media/posts/' or 'media/archived_posts/'
    - These directories contain YYYYMM date-organized folders

    Args:
        input_path: Path to the input directory

    Returns:
        True if this is a new Instagram export, False otherwise
    """
    try:
        media_dir = input_path / "media"
        posts_dir = media_dir / "posts"
        archived_posts_dir = media_dir / "archived_posts"

        # Check if media directory exists
        if not media_dir.exists() or not media_dir.is_dir():
            return False

        # Check for posts or archived_posts directories
        has_posts = posts_dir.exists() and posts_dir.is_dir()
        has_archived = archived_posts_dir.exists() and archived_posts_dir.is_dir()

        if not (has_posts or has_archived):
            return False

        # Verify date-organized structure (YYYYMM folders)
        date_folder_pattern = re.compile(r"^\d{6}$")  # YYYYMM format

        # Check posts directory for date folders
        if has_posts:
            date_folders = [
                d
                for d in posts_dir.iterdir()
                if d.is_dir() and date_folder_pattern.match(d.name)
            ]
            if len(date_folders) > 0:
                return True

        # Check archived_posts directory for date folders
        if has_archived:
            date_folders = [
                d
                for d in archived_posts_dir.iterdir()
                if d.is_dir() and date_folder_pattern.match(d.name)
            ]
            if len(date_folders) > 0:
                return True

        return False

    except Exception as e:
        logger.debug(f"Detection failed for Instagram Public Media: {e}")
        return False


class InstagramPublicMediaProcessor(ProcessorBase):
    """Processor for Instagram public media exports (new format)"""

    @staticmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input"""
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        """Return processor name"""
        return "Instagram Public Media"

    @staticmethod
    def get_priority() -> int:
        """Return priority (higher = run first)"""
        return 60  # Medium priority

    @staticmethod
    def process(input_dir: str, output_dir: str = None, **kwargs) -> bool:
        """Process Instagram public media export

        Args:
            input_dir: Path to input directory
            output_dir: Optional base output directory
            **kwargs: Additional arguments (verbose, workers, temp_dir)

        Returns:
            True if processing succeeded, False otherwise
        """
        # Check for required dependencies
        if not check_exiftool():
            print_exiftool_error()
            return False

        try:
            # Choose subdirectory under provided base output
            if output_dir:
                processor_output = str(Path(output_dir) / "public-media")
            else:
                processor_output = kwargs.get("output", "final_instagram")

            # Call processing logic directly
            process_logic(
                input_dir=input_dir,
                output_dir=processor_output,
                temp_dir=kwargs.get("temp_dir", "../pre"),
                verbose=kwargs.get("verbose", False),
                workers=kwargs.get("workers"),
            )
            return True

        except Exception as e:
            logger.error(f"Error in InstagramPublicMediaProcessor: {e}")
            return False


def get_processor():
    """Return processor class for auto-discovery

    This function is called by the unified memoria.py during
    automatic processor discovery.

    Returns:
        InstagramPublicMediaProcessor class (not instance, as it uses static methods)
    """
    return InstagramPublicMediaProcessor


# ============================================================================
# Helper Functions
# ============================================================================


def generate_unique_filename(
    post_data, media_type, export_username, extension, used_filenames
):
    """Generate a unique filename for a processed media file

    Format: insta-{media_type}-{exportUsername}-YYYYMMDD.extension
    If duplicate: insta-{media_type}-{exportUsername}-YYYYMMDD_N.extension

    Args:
        post_data: Dict containing post metadata
        media_type: Type of media (posts, archived_posts, etc.)
        export_username: Username extracted from input directory
        extension: File extension (including the dot)
        used_filenames: Set tracking already-used filenames

    Returns:
        str: Generated filename
    """
    # Parse date from post_data
    # Format: "2025-08-17 11:23:00"
    date_str = post_data["timestamp"]

    # Handle None timestamp
    if date_str is None:
        # Use a fallback date for posts without timestamps
        date_key = "00000000"
    else:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        date_key = date_obj.strftime("%Y%m%d")

    # Generate base filename without sequence number
    base_filename = f"insta-{media_type}-{export_username}-{date_key}{extension}"

    # Check if base filename is already used
    if base_filename not in used_filenames:
        used_filenames.add(base_filename)
        return base_filename

    # If duplicate, find the next available sequence number
    sequence = 1
    while True:
        filename = (
            f"insta-{media_type}-{export_username}-{date_key}_{sequence}{extension}"
        )
        if filename not in used_filenames:
            used_filenames.add(filename)
            return filename
        sequence += 1


def update_filesystem_timestamps(file_path, post_data):
    """Update filesystem creation and modification timestamps to match post date

    Args:
        file_path: Path to the file
        post_data: Dict containing post metadata with 'timestamp' field

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Parse date from post_data
        # Format: "2025-08-17 11:23:00"
        date_str = post_data["timestamp"]

        # Skip if no timestamp
        if date_str is None:
            return False

        date_obj = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")

        # Convert to Unix timestamp
        timestamp = date_obj.timestamp()

        # Update both access time and modification time
        os.utime(file_path, (timestamp, timestamp))
        return True
    except Exception as e:
        logger.warning(f"Failed to update timestamps for {file_path}: {e}")
        return False


def process_media_batch(batch_args):
    """Process a batch of media files (worker function for multiprocessing)
    
    Args:
        batch_args: List of tuples, each containing (media_file, post_data, 
                    output_filename, media_dir, output_dir, export_username, media_type)
    
    Returns:
        List of (success, failed, exif_rebuilt) tuples
    """
    # Phase 1: Copy all files
    file_paths = []
    file_info = []
    
    for args_tuple in batch_args:
        (
            media_file,
            post_data,
            output_filename,
            media_dir,
            output_dir,
            export_username,
            media_type,
        ) = args_tuple
        
        media_path = os.path.join(media_dir, media_file)
        media_type_dir = os.path.join(output_dir, media_type)
        output_path = os.path.join(media_type_dir, output_filename)
        
        if not os.path.exists(media_path):
            logger.warning(f"Media file not found: {media_path}")
            continue
        
        try:
            shutil.copy2(media_path, output_path)
            file_paths.append(output_path)
            file_info.append((output_path, post_data, export_username, media_type))
        except Exception as e:
            logger.error(f"Failed to copy {media_file}: {e}")
    
    if not file_paths:
        return [(False, True, False)] * len(batch_args)
    
    # Phase 2: Batch validate and rebuild
    corrupted_files = batch_validate_exif(file_paths)
    if corrupted_files:
        batch_rebuild_exif(list(corrupted_files))
    
    # Phase 3: Batch read metadata, then batch write
    existing_metadata_map = batch_read_existing_metadata(file_paths)
    batch_write_metadata_instagram_public(file_info, existing_metadata_map)
    
    # Phase 4: Update timestamps and compile results
    results = []
    for output_path, post_data, _, _ in file_info:
        update_filesystem_timestamps(output_path, post_data)
        exif_rebuilt = output_path in corrupted_files
        results.append((True, False, exif_rebuilt))
    
    # Add failed results for files that didn't get copied
    while len(results) < len(batch_args):
        results.append((False, True, False))
    
    return results


def is_preprocessed(input_dir):
    """Check if input directory is already preprocessed

    Args:
        input_dir: Path to input directory

    Returns:
        bool: True if already preprocessed, False if raw export
    """
    input_path = Path(input_dir)
    metadata_file = input_path / "metadata.json"
    media_dir = input_path / "media"

    return metadata_file.exists() and media_dir.exists()


def process_logic(
    input_dir, output_dir="final_media", temp_dir="../pre", verbose=False, workers=None
):
    """Core processing logic for Instagram Public Media exports

    Args:
        input_dir: Input directory (raw export or preprocessed)
        output_dir: Output directory for processed files
        temp_dir: Directory for temporary preprocessing files
        verbose: Enable verbose logging
        workers: Number of parallel workers (None = auto-detect)
    """
    # Logging is configured by the main process
    # Verbose mode enables detailed logging to file

    # Check for exiftool
    if not check_exiftool():
        print_exiftool_error()
        return

    logger.info("Instagram Media Processor")
    logger.info("=" * 50)

    # Determine if input needs preprocessing
    temp_dir_created = None

    try:
        if is_preprocessed(input_dir):
            logger.info(f"Input directory is already preprocessed: {input_dir}")
            working_dir = input_dir
        else:
            logger.info(f"Input directory is raw export: {input_dir}")
            logger.info("Running preprocessing...")

            # Create temp directory for preprocessing
            temp_base = Path(temp_dir).resolve()
            temp_base.mkdir(parents=True, exist_ok=True)

            # Create unique temp subdirectory
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            unique_id = str(uuid.uuid4())[:8]
            temp_dir_created = temp_base / f"temp_{timestamp}_{unique_id}"
            temp_dir_created.mkdir(parents=True, exist_ok=True)

            logger.info(f"Preprocessing to: {temp_dir_created}")

            # Run preprocessing with final output directory for failure tracking
            # output_dir is already the processor-specific path
            final_output_path = Path(output_dir)
            preprocessor = InstagramPreprocessor(
                export_path=Path(input_dir),
                output_dir=temp_dir_created,
                workers=workers,
                final_output_dir=final_output_path,
            )
            preprocessor.process()

            working_dir = str(temp_dir_created)
            logger.info(f"Preprocessing complete. Using: {working_dir}")

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
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata_json = json.load(f)

        export_info = metadata_json.get("export_info", {})
        media_posts = metadata_json.get("media", [])

        logger.info(f"Found {len(media_posts)} posts to process")

        # Extract export username from export_info
        export_name = export_info.get("export_name", "")
        if export_name:
            export_username = extract_username_from_export_dir(export_name, "instagram")
        else:
            export_username = "unknown"

        logger.info(f"Export username: {export_username}")

        # Create output directory
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Determine number of workers
        from common.utils import default_worker_count

        num_workers = workers if workers is not None else default_worker_count()
        logger.debug(f"Using {num_workers} parallel workers")

        # Pre-generate all output filenames to avoid race conditions
        logger.debug("Pre-generating filenames...")
        used_filenames = set()
        processing_tasks = []
        media_types_set = set()

        for post in media_posts:
            media_type = post.get("media_type", "unknown")
            media_files = post.get("media_files", [])
            media_types_set.add(media_type)

            for media_file in media_files:
                # Get file extension
                file_ext = os.path.splitext(media_file)[1].lower()

                # Generate output filename
                output_filename = generate_unique_filename(
                    post, media_type, export_username, file_ext, used_filenames
                )

                # Create task tuple for worker
                processing_tasks.append(
                    (
                        media_file,
                        post,
                        output_filename,
                        media_dir,
                        output_dir,
                        export_username,
                        media_type,
                    )
                )

        # Create subdirectories for each media type
        logger.debug(f"Creating subdirectories for {len(media_types_set)} media types...")
        for media_type in media_types_set:
            media_type_dir = os.path.join(output_dir, media_type)
            Path(media_type_dir).mkdir(parents=True, exist_ok=True)
            logger.debug(f"Created directory: {media_type_dir}")

        # Process media files in parallel
        print(f"\nProcessing media files to {output_dir}/")
        logger.info("=" * 50)

        success_count = 0
        failed_count = 0
        exif_rebuilt_count = 0

        # Group tasks into batches of 100 files each
        batch_size = 100
        batched_tasks = []
        for i in range(0, len(processing_tasks), batch_size):
            batched_tasks.append(processing_tasks[i:i + batch_size])

        # Process batches in parallel
        with multiprocessing.Pool(processes=num_workers) as pool:
            batch_results = list(
                progress_bar(
                    pool.imap(process_media_batch, batched_tasks),
                    PHASE_PROCESS,
                    "Creating files",
                    total=len(batched_tasks),
                )
            )

        # Flatten results
        results = []
        for batch_result in batch_results:
            results.extend(batch_result)

        # Aggregate results
        for success, failed, exif_rebuilt in results:
            if success:
                success_count += 1
            if failed:
                failed_count += 1
            if exif_rebuilt:
                exif_rebuilt_count += 1

        # Summary
        print("\n" + "=" * 50)
        print("Processing complete!")
        print(f"  Successfully processed: {success_count}")
        print(f"  Failed: {failed_count}")
        print(f"  EXIF structures rebuilt: {exif_rebuilt_count}")
        print(f"  Total: {len(processing_tasks)}")
        print(f"\nFinal files saved to: {os.path.abspath(output_dir)}")
        print(f"Files organized into {len(media_types_set)} media type subfolders:")
        for media_type in sorted(media_types_set):
            print(f"  - {media_type}/")

    finally:
        # Clean up temp directory if it was created
        if temp_dir_created and temp_dir_created.exists():
            if should_cleanup_temp():
                logger.debug(f"Cleaning up temporary directory: {temp_dir_created}")
                shutil.rmtree(temp_dir_created)
            else:
                logger.info(f"Temp cleanup disabled. Temporary directory preserved at: {temp_dir_created}")
