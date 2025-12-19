"""Google Photos Media Processor - handles renaming, metadata embedding, and timestamps."""

import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from common.dedup.catalog import catalog_media_files
from common.dedup.description import (
    SourceEntry,
    build_description,
    PLATFORM_GOOGLE,
    SERVICE_PHOTOS,
)
from common.dependency_checker import check_exiftool, print_exiftool_error
from common.files.utils import batch_correct_extensions
from common.dedup.service import is_file_primary_by_hash, save_dedup_metadata
from common.dedup.hashing import (
    compute_content_hash,
    is_live_photo_pair,
    LIVE_PHOTO_IMAGE_EXTENSIONS,
    LIVE_PHOTO_VIDEO_EXTENSIONS,
)
from common.media.exiftool import (
    batch_validate_exif,
    batch_rebuild_exif,
    batch_read_existing_metadata,
    batch_write_metadata_requests,
    DescriptionWriteSpec,
    GPSData,
    MetadataWriteRequest,
    PREAMBLE_WITH_IGNORE_ERRORS,
)
from common.output.logging import get_processor_logger
from common.processing import (
    process_batches_parallel,
    print_processing_summary,
    temp_processing_directory,
)
from common.utils import (
    ALL_MEDIA_EXTENSIONS,
    default_worker_count,
    extract_username_from_export_dir,
    FilenameGenerator,
    is_preprocessed_directory,
    update_file_timestamps,
)
from common.output.paths import OutputInfo
from processors.base import ProcessorBase
from processors.google_photos.preprocess import GooglePhotosPreprocessor

# Standard logger for worker processes and exception logging
logger = logging.getLogger(__name__)
# Standardized processor logger for consistent output
log = get_processor_logger(__name__)


def detect(input_path: Path) -> bool:
    """Check if this is a Google Photos export (has Google Photos/ with albums)."""
    try:
        photos_dir = input_path / "Google Photos"

        # Check if Google Photos directory exists
        if not photos_dir.exists() or not photos_dir.is_dir():
            return False

        # Check for album folders within Google Photos
        # Need at least one subdirectory (album)
        albums = [d for d in photos_dir.iterdir() if d.is_dir()]

        return len(albums) > 0

    except Exception as e:
        log.detection_failed("Google Photos", e)
        return False


class GooglePhotosProcessor(ProcessorBase):
    """Processor for Google Photos exports."""

    @staticmethod
    def detect(input_path: Path) -> bool:
        return detect(input_path)

    @staticmethod
    def get_name() -> str:
        return "Google Photos"

    @staticmethod
    def get_priority() -> int:
        return 50

    @staticmethod
    def get_output_info() -> OutputInfo:
        return OutputInfo(
            platform="Google",
            service="photos",
            username_prefix="google",
        )

    @staticmethod
    def supports_dedup() -> bool:
        return True

    @staticmethod
    def build_catalog(input_dir: str, **kwargs):
        """Build catalog of files with hashes and metadata."""
        input_path = Path(input_dir)
        photos_dir = input_path / "Google Photos"
        workers = kwargs.get("workers", 4)

        if not photos_dir.exists():
            log.logger.warning(
                "Google Photos directory not found: %s", photos_dir
            )
            return

        # Collect all media files
        live_photo_videos = set()  # Track videos that are part of Live Photo pairs

        # First pass: collect all media files
        all_files = []
        for album_dir in photos_dir.iterdir():
            if not album_dir.is_dir():
                continue
            for file_path in album_dir.iterdir():
                if (
                    file_path.is_file()
                    and file_path.suffix.lower() in ALL_MEDIA_EXTENSIONS
                ):
                    all_files.append(file_path)

        # Second pass: identify Live Photo pairs
        for file_path in all_files:
            suffix = file_path.suffix.lower()
            if suffix in LIVE_PHOTO_IMAGE_EXTENSIONS:
                # Look for matching video
                for video_ext in LIVE_PHOTO_VIDEO_EXTENSIONS:
                    video_path = file_path.with_suffix(video_ext)
                    if video_path.exists() and is_live_photo_pair(
                        file_path, video_path
                    ):
                        live_photo_videos.add(video_path)
                        break

        # Third pass: filter out Live Photo videos (they'll be cataloged with their image)
        media_files = [f for f in all_files if f not in live_photo_videos]

        log.logger.info(
            "Found %d media files to catalog (%d Live Photo videos grouped with images)",
            len(media_files),
            len(live_photo_videos),
        )

        def extract_google_photos_metadata(file_path: Path) -> Dict[str, Any]:
            """Extract metadata from Google Photos JSON sidecar."""
            from datetime import timezone

            metadata: Dict[str, Any] = {}

            # Extract metadata from JSON sidecar if present
            json_path = file_path.with_suffix(file_path.suffix + ".json")
            if not json_path.exists():
                # Try supplemental metadata pattern
                json_path = Path(str(file_path) + ".supplemental-metadata.json")

            if json_path.exists():
                try:
                    with open(json_path, "r", encoding="utf-8") as f:
                        json_data = json.load(f)

                    # Extract timestamp
                    photo_taken = json_data.get("photoTakenTime", {})
                    if photo_taken.get("timestamp"):
                        ts = int(photo_taken["timestamp"])
                        dt = datetime.fromtimestamp(ts, timezone.utc)
                        metadata["capture_timestamp"] = dt.isoformat()

                    # Extract GPS
                    geo_data = json_data.get("geoData") or json_data.get("geoDataExif")
                    if geo_data:
                        metadata["gps"] = {
                            "latitude": geo_data.get("latitude", 0.0),
                            "longitude": geo_data.get("longitude", 0.0),
                        }

                    # Extract people tags
                    people = json_data.get("people", [])
                    if people:
                        metadata["people"] = [
                            p.get("name") for p in people if p.get("name")
                        ]

                except Exception as e:
                    logger.debug(f"Failed to parse JSON for {file_path}: {e}")

            # Add album information
            album_name = file_path.parent.name
            metadata["albums"] = [album_name]

            # Check if this is a Live Photo image and record video path
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
            processor_name="Google Photos",
            input_path=input_path,
            media_files=media_files,
            workers=workers,
            metadata_extractor=extract_google_photos_metadata,
        )

    @staticmethod
    def process(
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> bool:
        """Process Google Photos export."""
        # Check for required dependencies
        if not check_exiftool():
            print_exiftool_error()
            return False

        try:
            # Choose content-type subdirectory under provided base output
            if output_dir:
                processor_output = str(Path(output_dir) / "photos")
            else:
                processor_output = kwargs.get("output", "final_googlephotos/photos")

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
            log.logger.error("Error in GooglePhotosProcessor: %s", e)
            return False


def get_processor():
    """Return processor class for auto-discovery."""
    return GooglePhotosProcessor


def _clean_iso_timestamp(timestamp_str: str) -> str:
    """Remove timezone suffix from ISO 8601 timestamp for parsing."""
    if not timestamp_str:
        return ""
    # Remove Z or timezone offset for parsing
    return timestamp_str.replace("Z", "").replace("+00:00", "")


def get_live_photo_group_key(media_data):
    """Generate a key to identify files that are part of the same live photo."""
    timestamp = media_data.get("capture_timestamp", "")
    original_filename = media_data.get("original_filename", "")
    # Use the filename without extension as the group identifier
    if original_filename:
        base_name = os.path.splitext(original_filename)[0]
    else:
        base_name = ""
    return (timestamp, base_name)


def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str, str]],
) -> List[MetadataWriteRequest]:
    """Build MetadataWriteRequest objects for Google Photos files."""
    requests: List[MetadataWriteRequest] = []

    for file_path, media_data, _album_name, export_username in file_info:
        # Build datetime tags
        datetime_tags: List[Tuple[str, str]] = []
        timestamp_str = media_data.get("capture_timestamp")
        if timestamp_str:
            date_obj = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            exif_date = date_obj.strftime("%Y:%m:%d %H:%M:%S")
            datetime_tags = [
                ("DateTimeOriginal", exif_date),
                ("CreateDate", exif_date),
                ("ModifyDate", exif_date),
            ]

        # Build GPS data
        gps: Optional[GPSData] = None
        gps_data = media_data.get("gps", {})
        latitude = gps_data.get("latitude", 0.0)
        longitude = gps_data.get("longitude", 0.0)
        altitude = gps_data.get("altitude", 0.0)

        if latitude != 0.0 or longitude != 0.0:
            gps = GPSData(
                latitude=float(latitude),
                longitude=float(longitude),
                altitude=float(altitude) if altitude != 0.0 else None,
            )

        # Build description using unified format
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username=export_username,
            service=SERVICE_PHOTOS,
        )
        default_description = build_description(entry)

        requests.append(
            MetadataWriteRequest(
                file_path=file_path,
                arg_preamble=PREAMBLE_WITH_IGNORE_ERRORS,
                datetime_tags=datetime_tags,
                gps=gps,
                description=DescriptionWriteSpec(
                    default_description=default_description,
                ),
                processor_name="google_photos",
            )
        )

    return requests


def process_media_batch(batch_args):
    """Process a batch of media files (copy, correct extensions, update timestamps)."""
    results = []

    # Phase 1: Copy all files
    file_paths = []
    file_info = []

    for args_tuple in batch_args:
        (
            media_file,
            media_data,
            album_name,
            output_filename,
            media_dir,
            output_dir,
            export_username,
        ) = args_tuple

        media_path = os.path.join(media_dir, media_file)
        output_path = os.path.join(output_dir, output_filename)

        if not os.path.exists(media_path):
            log.missing_file(media_path)
            results.append((False, None, None))
            continue

        try:
            shutil.copy2(media_path, output_path)
            file_paths.append(output_path)
            file_info.append((output_path, media_data, album_name, export_username))
        except Exception as e:
            log.copy_error(media_file, e)
            results.append((False, None, None))

    if not file_paths:
        return results if results else [(False, None, None)] * len(batch_args)

    # Phase 2: Correct file extensions based on actual content
    corrections = batch_correct_extensions([Path(p) for p in file_paths])
    if corrections:
        # Update file_paths and file_info with corrected paths
        file_paths = [corrections.get(p, p) for p in file_paths]
        file_info = [
            (corrections.get(fp, fp), md, an, eu) for fp, md, an, eu in file_info
        ]

    # Phase 3: Update timestamps and compile results
    for output_path, media_data, album_name, export_username in file_info:
        # Google Photos uses ISO 8601 format: "2018-01-10T00:30:37Z"
        timestamp_str = media_data.get("capture_timestamp")
        if timestamp_str:
            update_file_timestamps(output_path, timestamp_str, "%Y-%m-%dT%H:%M:%S")
        results.append(
            (True, output_path, (output_path, media_data, album_name, export_username))
        )

    return results


def process_logic(
    input_dir,
    output_dir="final_googlephotos",
    temp_dir="../pre",
    verbose=False,  # noqa: ARG001
    workers=None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Core processing logic for Google Photos exports."""

    # Check for exiftool
    if not check_exiftool():
        print_exiftool_error()
        return

    log.processor_start("Google Photos Media Processor")

    # Check if input is already preprocessed
    if is_preprocessed_directory(input_dir):
        log.already_preprocessed(input_dir)
        _process_working_directory(input_dir, output_dir, workers, dedup_manifest)
    else:
        log.raw_export_detected(input_dir)
        log.running_preprocessing()

        # Use context manager for temp directory with automatic cleanup
        with temp_processing_directory(temp_dir, "gphotos") as temp_dir_path:
            log.preprocessing_to(str(temp_dir_path))

            # Run preprocessing with final output directory for failure tracking
            final_output_path = Path(output_dir)
            preprocessor = GooglePhotosPreprocessor(
                export_path=Path(input_dir),
                output_dir=temp_dir_path,
                workers=workers,
                final_output_dir=final_output_path,
            )
            preprocessor.process()

            log.preprocessing_done(str(temp_dir_path))
            _process_working_directory(
                str(temp_dir_path), output_dir, workers, dedup_manifest
            )


def _process_working_directory(
    working_dir,
    output_dir,
    workers,
    dedup_manifest: Optional[Dict[str, Any]] = None,
):
    """Process a working directory (preprocessed export)."""
    # Configuration
    metadata_file = os.path.join(working_dir, "metadata.json")
    media_dir = os.path.join(working_dir, "media")

    # Check for metadata file (should exist after preprocessing or if already preprocessed)
    if not os.path.exists(metadata_file):
        log.file_not_found(metadata_file, "Metadata file")
        return

    # Check for media directory
    if not os.path.exists(media_dir):
        log.file_not_found(media_dir, "Media directory")
        return

    # Load metadata
    log.loading_metadata(metadata_file)
    with open(metadata_file, "r", encoding="utf-8") as f:
        metadata_json = json.load(f)

    export_info = metadata_json.get("export_info", {})
    media_files = metadata_json.get("media_files", [])

    # Count total media files
    total_media_files = len(media_files)

    log.found_files(total_media_files, "media")

    # Filter out secondary files based on dedup manifest (using hash-based lookup)
    skipped_secondary_files = []
    if dedup_manifest:
        original_count = len(media_files)
        filtered_media_files = []
        for media_data in media_files:
            media_file = media_data.get("filename")
            if media_file:
                full_path = os.path.join(media_dir, media_file)
                content_hash = compute_content_hash(full_path)
                if is_file_primary_by_hash(
                    dedup_manifest, content_hash, "Google Photos"
                ):
                    filtered_media_files.append(media_data)
                else:
                    skipped_secondary_files.append(full_path)
        media_files = filtered_media_files
        skipped_count = original_count - len(media_files)
        log.skipped_secondary(skipped_count)

    # Extract export username from export_name
    # Pattern: "google-{username}-{date}" (date can be YYYYMMDD or YYYY-MM-DD)
    export_name = export_info.get("export_name", "")
    export_username = extract_username_from_export_dir(export_name, "google")

    log.export_username(export_username)

    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Determine number of workers
    num_workers = workers if workers is not None else default_worker_count()
    log.using_workers(num_workers)

    # Pre-generate all output filenames to avoid race conditions
    log.generating_filenames()

    # First pass: Group live photos together
    # Live photo pairs (image + video with same timestamp/filename) need the same sequence
    # Map: live_photo_group_key -> list of (media_data, file_ext)
    live_photo_groups = {}

    for media_data in media_files:
        media_file = media_data.get("filename")
        if not media_file:
            continue
        file_ext = os.path.splitext(media_file)[1].lower()
        live_photo_key = get_live_photo_group_key(media_data)
        if live_photo_key not in live_photo_groups:
            live_photo_groups[live_photo_key] = []
        live_photo_groups[live_photo_key].append((media_data, file_ext))

    # Second pass: Generate filenames using FilenameGenerator
    # For live photo pairs, reuse base filename (with same sequence) for both files
    filename_gen = FilenameGenerator("gphotos", timestamp_format="%Y-%m-%dT%H:%M:%S")
    live_photo_bases = {}  # Map: live_photo_key -> base filename (without extension)

    processing_tasks = []

    for media_data in media_files:
        media_file = media_data.get("filename")

        if not media_file:
            logger.warning("Skipping media file without filename")
            continue

        file_ext = os.path.splitext(media_file)[1].lower()
        live_photo_key = get_live_photo_group_key(media_data)
        is_live_photo_pair = len(live_photo_groups.get(live_photo_key, [])) > 1

        if is_live_photo_pair and live_photo_key in live_photo_bases:
            # Second file in live photo pair - reuse base from first file
            output_filename = f"{live_photo_bases[live_photo_key]}{file_ext}"
        else:
            # Generate filename using FilenameGenerator (mechanism handles uniqueness)
            timestamp_str = media_data.get("capture_timestamp")
            timestamp_clean = _clean_iso_timestamp(timestamp_str)
            output_filename = filename_gen.generate(
                timestamp_clean, export_username, file_ext
            )

            # If this is first file in a live photo pair, record base for second file
            if is_live_photo_pair:
                # Extract base without extension for reuse
                base = (
                    output_filename[: -len(file_ext)] if file_ext else output_filename
                )
                live_photo_bases[live_photo_key] = base

        # Get primary album name for metadata (use first album in list)
        albums_list = media_data.get("albums", [])
        album_name = albums_list[0] if albums_list else "unknown"

        # Create task tuple for worker
        processing_tasks.append(
            (
                media_file,
                media_data,
                album_name,
                output_filename,
                media_dir,
                output_dir,
                export_username,
            )
        )

    # Process media files in parallel
    log.processing_start(output_dir)

    # Use shared batch processing utility (copy files only)
    results = process_batches_parallel(
        tasks=processing_tasks,
        worker_fn=process_media_batch,
        num_workers=num_workers,
        batch_size=100,
        description="Copying files",
    )

    # Collect successful files for EXIF processing
    success_count = 0
    failed_count = 0
    file_paths = []
    file_info = []

    for success, output_path, info_tuple in results:
        if success and output_path:
            success_count += 1
            file_paths.append(output_path)
            file_info.append(info_tuple)
        else:
            failed_count += 1

    log.copied_files(success_count, failed_count)

    # Consolidated EXIF processing
    exif_rebuilt_count = 0
    if file_paths:
        log.exif_batch_start()

        # Validate and rebuild corrupted EXIF
        corrupted_files = batch_validate_exif(file_paths)
        if corrupted_files:
            log.exif_rebuilding(len(corrupted_files))
            batch_rebuild_exif(list(corrupted_files))
            exif_rebuilt_count = len(corrupted_files)

        # Batch read and write metadata
        existing_metadata_map = batch_read_existing_metadata(file_paths)
        requests = _build_metadata_requests(file_info)
        batch_write_metadata_requests(requests, existing_metadata_map)
        log.exif_written(len(file_info))

    # Save dedup references for skipped secondary files
    if dedup_manifest and skipped_secondary_files:
        save_dedup_metadata(
            manifest=dedup_manifest,
            skipped_files=skipped_secondary_files,
            processor_name="Google Photos",
            output_dir=Path(output_dir),
        )

    # Print summary using shared utility
    print_processing_summary(
        success=success_count,
        failed=failed_count,
        total=len(processing_tasks),
        output_dir=output_dir,
        extra_stats={"EXIF structures rebuilt": exif_rebuilt_count},
    )
