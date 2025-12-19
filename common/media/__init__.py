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
    batch_write_metadata,
    build_metadata_request,
)
from common.media.encoder import (
    get_video_bitrate,
    get_encoder_args,
    get_encoder_input_args,
    get_encoder_name,
    get_software_encoder_args,
    is_hardware_accelerated,
)

# Note: overlay module has many exports, import directly when needed
# from common.media.overlay import embed_overlay, process_video_with_overlay, ...

__all__ = [
    # Exiftool
    "GPSData",
    "DescriptionWriteSpec",
    "MetadataWriteRequest",
    "batch_write_metadata",
    "build_metadata_request",
    # Encoder
    "get_video_bitrate",
    "get_encoder_args",
    "get_encoder_input_args",
    "get_encoder_name",
    "get_software_encoder_args",
    "is_hardware_accelerated",
]

