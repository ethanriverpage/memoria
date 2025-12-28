"""
Media processing utilities for images and videos.

This package provides:
- Batch EXIF metadata operations via exiftool
- Overlay embedding for Snapchat memories
- Video encoding with hardware acceleration detection
"""

from common.media.exiftool import (
    GPSData,
    DescriptionWriteSpec,
    MetadataWriteRequest,
    batch_write_metadata_requests,
    batch_read_existing_metadata,
    batch_validate_exif,
    batch_rebuild_exif,
    DEFAULT_PREAMBLE,
    PREAMBLE_WITH_IGNORE_ERRORS,
)
from common.media.encoder import (
    get_encoder_args,
    get_encoder_input_args,
    get_encoder_name,
    get_software_encoder_args,
    is_hardware_accelerated,
)
from common.media.probe import (
    VideoMetadata,
    get_video_metadata,
)

# Note: overlay module has many exports, import directly when needed
# from common.media.overlay import embed_overlay, process_video_with_overlay, ...

__all__ = [
    # Exiftool
    "GPSData",
    "DescriptionWriteSpec",
    "MetadataWriteRequest",
    "batch_write_metadata_requests",
    "batch_read_existing_metadata",
    "batch_validate_exif",
    "batch_rebuild_exif",
    "DEFAULT_PREAMBLE",
    "PREAMBLE_WITH_IGNORE_ERRORS",
    # Encoder
    "get_encoder_args",
    "get_encoder_input_args",
    "get_encoder_name",
    "get_software_encoder_args",
    "is_hardware_accelerated",
    # Probe
    "VideoMetadata",
    "get_video_metadata",
]
