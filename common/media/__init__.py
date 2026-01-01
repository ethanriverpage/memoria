"""
Media processing utilities for images and videos.

This package provides:
- Batch EXIF metadata operations via exiftool (import from common.media.exiftool)
- Overlay embedding for Snapchat memories (import from common.media.overlay)
- Video encoding with hardware acceleration detection
- Video probing utilities

Note: exiftool and overlay modules have many exports and are involved in
complex import chains. Import directly from the submodules when needed:
    from common.media.exiftool import batch_write_metadata_requests, ...
    from common.media.overlay import embed_overlay, ...
"""

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
    VideoProbeFailure,
    get_video_metadata_with_failure,
    quick_isobmff_header_check,
)

__all__ = [
    # Encoder
    "get_encoder_args",
    "get_encoder_input_args",
    "get_encoder_name",
    "get_software_encoder_args",
    "is_hardware_accelerated",
    # Probe
    "VideoMetadata",
    "get_video_metadata",
    "VideoProbeFailure",
    "get_video_metadata_with_failure",
    "quick_isobmff_header_check",
]
