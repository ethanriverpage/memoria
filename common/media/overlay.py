#!/usr/bin/env python3
"""
Snapchat Overlay Embed Module

This module provides functions for processing Snapchat memory exports by overlaying
transparent PNG/WebP overlays onto media files (images and videos).

For videos, creates MKV files with dual video tracks:
- Track 0 (default): Video with overlay embedded
- Track 1: Original video without overlay

Video processing uses a 3-pass approach:
1. Rotate video if needed and remove rotation metadata
2. Apply overlay to rotated video
3. Combine both videos into dual-track MKV and embed metadata

Video rotation metadata (common in portrait videos) is automatically detected and
the rotation is physically applied to the video. Output files have correct orientation
without rotation metadata, ensuring compatibility with all video players.

Performance Optimizations:
- Single FFprobe call to get all video metadata (rotation, dimensions, bitrate)
- Cached video metadata to avoid redundant probing
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional
import logging

try:
    from PIL import Image  # type: ignore
except ImportError:
    print("Error: PIL (Pillow) is required. Install it with: pip install Pillow")
    sys.exit(1)

# Import video encoder detection
from common.media.encoder import (
    get_encoder_args,
    get_encoder_input_args,
    get_encoder_name,
    get_software_encoder_args,
    is_hardware_accelerated,
    is_hardware_acceleration_error,
)

# Import shared video metadata extraction
from common.media.probe import VideoMetadata, get_video_metadata

# Import unified description format utilities
from common.dedup.description import (
    PLATFORM_SNAPCHAT,
    SERVICE_MESSAGES,
    SourceEntry,
    build_source_line,
    build_message_context,
)

# Set up logging
logger = logging.getLogger(__name__)

# Detect encoder once at module load time
_ENCODER_NAME = get_encoder_name()
_IS_HARDWARE = is_hardware_accelerated()

logger.debug(f"Video encoder initialized: {_ENCODER_NAME} (hardware: {_IS_HARDWARE})")


# Multi-pass video processing helper functions


def _pass1_rotate_video_with_metadata(
    video_path: Path, video_meta: VideoMetadata
) -> tuple[Path, int, int, Optional[int], bool]:
    """
    PASS 1: Rotate video if needed using pre-fetched metadata.

    Uses metadata from get_video_metadata() to avoid redundant FFprobe calls.
    When no rotation is needed, returns the original path directly to avoid
    unnecessary I/O copying.

    Args:
        video_path: Path to the input video
        video_meta: Pre-fetched VideoMetadata from get_video_metadata()

    Returns:
        Tuple of (video_path, final_width, final_height, original_bitrate, is_temp_file)
        The is_temp_file flag indicates whether the returned path is a temp file
        that should be cleaned up (True) or the original file (False).

    Raises:
        RuntimeError: If video processing fails
    """
    return _pass1_rotate_video_impl_with_metadata(
        video_path, video_meta, use_hardware=True
    )


def _pass1_rotate_video_impl_with_metadata(
    video_path: Path, video_meta: VideoMetadata, use_hardware: bool = True
) -> tuple[Path, int, int, Optional[int], bool]:
    """
    PASS 1 Implementation using pre-fetched metadata.

    Avoids redundant FFprobe calls by using pre-fetched VideoMetadata.
    When no rotation is needed, returns the original path directly to avoid
    unnecessary I/O copying.

    Args:
        video_path: Path to the input video
        video_meta: Pre-fetched VideoMetadata
        use_hardware: Whether to use hardware acceleration

    Returns:
        Tuple of (video_path, final_width, final_height, original_bitrate, is_temp_file)
        The is_temp_file flag indicates whether the returned path is a temp file
        that should be cleaned up (True) or the original file (False).

    Raises:
        RuntimeError: If video processing fails
    """
    logger.debug(f"[{video_path.name}] PASS 1: Rotating with pre-fetched metadata")

    # Use pre-fetched metadata
    orig_width = video_meta.width
    orig_height = video_meta.height
    rotation = video_meta.rotation
    original_bitrate = video_meta.bitrate

    if original_bitrate:
        logger.debug(
            f"[{video_path.name}] Using pre-fetched bitrate: {original_bitrate / 1_000_000:.2f} Mbps"
        )

    # Ensure dimensions are even for libx265
    if orig_width % 2 != 0:
        orig_width += 1
    if orig_height % 2 != 0:
        orig_height += 1

    logger.debug(
        f"[{video_path.name}] Original video dimensions: {orig_width}x{orig_height}"
    )

    # Calculate target dimensions after rotation
    if rotation and rotation in [90, 270]:
        target_width, target_height = orig_height, orig_width
        logger.debug(
            f"[{video_path.name}] Target dimensions after {rotation} rotation: {target_width}x{target_height}"
        )
    else:
        target_width, target_height = orig_width, orig_height

    if rotation:
        logger.debug(f"[{video_path.name}] Applying {rotation} rotation")

        # Create temp file for rotated output
        temp_rotated = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        temp_rotated.close()
        temp_rotated_path = Path(temp_rotated.name)

        # Build rotation filter
        if rotation == 90:
            rotation_filter = "transpose=2"
        elif rotation == 180:
            rotation_filter = "hflip,vflip"
        elif rotation == 270:
            rotation_filter = "transpose=1"
        else:
            logger.warning(
                f"[{video_path.name}] Unsupported rotation {rotation}, copying without rotation"
            )
            rotation_filter = None

        if rotation_filter:
            # Get encoder args
            if use_hardware and is_hardware_accelerated():
                encoder_input_args = get_encoder_input_args()
                encoder_output_args = get_encoder_args(original_bitrate)
                encoder_name = get_encoder_name()
            else:
                encoder_input_args = []
                encoder_output_args = get_software_encoder_args(original_bitrate)
                encoder_name = "libx265"

            # Build filter for VAAPI vs other encoders
            if encoder_name == "hevc_vaapi":
                combined_filter = (
                    f"hwdownload,format=nv12,{rotation_filter},"
                    f"sidedata=mode=delete:type=DISPLAYMATRIX,hwupload"
                )
            else:
                combined_filter = (
                    f"{rotation_filter},sidedata=mode=delete:type=DISPLAYMATRIX"
                )

            cmd_rotate = [
                "ffmpeg",
                *encoder_input_args,
                "-noautorotate",
                "-i",
                str(video_path),
                "-vf",
                combined_filter,
                *encoder_output_args,
                "-c:a",
                "copy",
                "-y",
                str(temp_rotated_path),
            ]

            logger.debug(
                f"[{video_path.name}] Pass 1 ffmpeg command: {' '.join(cmd_rotate)}"
            )
            result = subprocess.run(
                cmd_rotate,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                stdin=subprocess.DEVNULL,
            )

            if result.returncode != 0:
                temp_rotated_path.unlink(missing_ok=True)
                # Check for hardware error and retry with software
                if (
                    use_hardware
                    and is_hardware_accelerated()
                    and is_hardware_acceleration_error(result.stderr)
                ):
                    logger.warning(
                        f"[{video_path.name}] Hardware encoding failed, retrying with software"
                    )
                    return _pass1_rotate_video_impl_with_metadata(
                        video_path, video_meta, use_hardware=False
                    )
                raise RuntimeError(f"Pass 1 rotation failed: {result.stderr}")

            logger.debug(f"[{video_path.name}] Pass 1 complete - video rotated")
            return (
                temp_rotated_path,
                target_width,
                target_height,
                original_bitrate,
                True,
            )
        else:
            shutil.copy2(video_path, temp_rotated_path)
            logger.debug(
                f"[{video_path.name}] Pass 1 complete - copied without rotation"
            )
            return (
                temp_rotated_path,
                target_width,
                target_height,
                original_bitrate,
                True,
            )
    else:
        # No rotation needed - return original path directly to avoid unnecessary I/O
        logger.debug(f"[{video_path.name}] Pass 1 complete - no rotation needed")
        return video_path, target_width, target_height, original_bitrate, False


def _pass2_apply_overlay(
    rotated_video_path: Path,
    overlay_path: Path,
    target_width: int,
    target_height: int,
    original_bitrate: Optional[int] = None,
) -> Path:
    """
    PASS 2: Apply overlay to the rotated video.

    Scales the overlay to match the video dimensions and applies it to create
    the "with overlay" version of the video.

    Automatically falls back to software encoding if hardware acceleration fails.

    Args:
        rotated_video_path: Path to the rotated video from Pass 1
        overlay_path: Path to the overlay image
        target_width: Target video width
        target_height: Target video height
        original_bitrate: Optional original video bitrate for quality preservation

    Returns:
        Path to video with overlay temp file

    Raises:
        RuntimeError: If video processing fails (even with software fallback)
    """
    # Try hardware encoding first if available
    try:
        return _pass2_apply_overlay_impl(
            rotated_video_path,
            overlay_path,
            target_width,
            target_height,
            original_bitrate,
            use_hardware=True,
        )
    except RuntimeError as e:
        error_msg = str(e)
        # Check if this is a hardware acceleration error
        if is_hardware_accelerated() and is_hardware_acceleration_error(error_msg):
            logger.warning(
                f"[{rotated_video_path.name}] Hardware encoding failed in Pass 2, "
                f"falling back to software encoding: {error_msg[:100]}"
            )
            try:
                return _pass2_apply_overlay_impl(
                    rotated_video_path,
                    overlay_path,
                    target_width,
                    target_height,
                    original_bitrate,
                    use_hardware=False,
                )
            except RuntimeError as fallback_error:
                logger.error(
                    f"[{rotated_video_path.name}] Software encoding fallback also failed in Pass 2: {fallback_error}"
                )
                raise
        else:
            # Not a hardware error, or already using software, re-raise
            raise


def _pass2_apply_overlay_impl(
    rotated_video_path: Path,
    overlay_path: Path,
    target_width: int,
    target_height: int,
    original_bitrate: Optional[int] = None,
    use_hardware: bool = True,
) -> Path:
    """
    PASS 2 Implementation: Apply overlay to the rotated video.

    Scales the overlay to match the video dimensions and applies it to create
    the "with overlay" version of the video.

    Args:
        rotated_video_path: Path to the rotated video from Pass 1
        overlay_path: Path to the overlay image
        target_width: Target video width
        target_height: Target video height
        original_bitrate: Optional original video bitrate for quality preservation
        use_hardware: Whether to use hardware acceleration (if available)

    Returns:
        Path to video with overlay temp file

    Raises:
        RuntimeError: If video processing fails
    """
    logger.debug(
        f"[{rotated_video_path.name}] PASS 2: Applying overlay to rotated video"
    )

    # Check if overlay file exists before attempting to open
    if not overlay_path.exists():
        logger.error(f"Overlay file does not exist: {overlay_path}")
        raise RuntimeError(f"Overlay file does not exist: {overlay_path}")

    # Scale overlay to match video dimensions using PIL
    try:
        with Image.open(overlay_path) as overlay_img:
            orig_width, orig_height = overlay_img.size
            logger.debug(
                f"[{overlay_path.name}] Original overlay dimensions: {orig_width}x{orig_height}"
            )

            # Convert to RGBA if not already (for transparency support)
            if overlay_img.mode != "RGBA":
                converted = overlay_img.convert("RGBA")
                overlay_img.close()  # Close original to free memory
                overlay_img = converted

            # Resize using high-quality Lanczos resampling if needed
            if orig_width != target_width or orig_height != target_height:
                logger.debug(
                    f"[{overlay_path.name}] Scaling overlay from {orig_width}x{orig_height} to {target_width}x{target_height}"
                )
                scaled_overlay = overlay_img.resize(
                    (target_width, target_height), Image.Resampling.LANCZOS
                )
                overlay_img.close()  # Close pre-resize image
            else:
                logger.debug(
                    f"[{overlay_path.name}] Overlay already matches target dimensions"
                )
                scaled_overlay = overlay_img

            # Save scaled overlay to temp file
            temp_overlay = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            temp_overlay.close()
            temp_overlay_path = Path(temp_overlay.name)
            scaled_overlay.save(temp_overlay_path, format="PNG", optimize=True)
            scaled_overlay.close()  # Close after saving
            logger.debug(
                f"[{overlay_path.name}] Scaled overlay saved to {temp_overlay_path.name}"
            )

    except Exception as e:
        # Provide clear error message for corrupted overlays
        logger.error(
            f"Cannot open overlay file (possibly corrupted): {overlay_path}: {e}"
        )
        raise RuntimeError(f"Pass 2 overlay scaling failed - corrupted overlay: {e}")

    temp_with_overlay = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    temp_with_overlay.close()
    temp_with_overlay_path = Path(temp_with_overlay.name)

    try:
        # Use -noautorotate and sidedata filter to handle rotation metadata

        # Get encoder args based on hardware/software preference
        if use_hardware and is_hardware_accelerated():
            encoder_input_args = get_encoder_input_args()  # Input args (e.g., hwaccel)
            encoder_output_args = get_encoder_args(
                original_bitrate
            )  # Output args (codec, bitrate, etc)
            encoder_name = get_encoder_name()
            logger.debug(
                f"[{rotated_video_path.name}] Pass 2 using hardware encoder: {encoder_name}"
            )
        else:
            # Software encoding fallback
            encoder_input_args = []
            encoder_output_args = get_software_encoder_args(original_bitrate)
            encoder_name = "libx265"
            logger.debug(
                f"[{rotated_video_path.name}] Pass 2 using software encoder: {encoder_name}"
            )

        # For VAAPI, we need to handle overlay filter differently:
        # Hardware surfaces need to be downloaded to CPU, filtered, then uploaded
        if encoder_name == "hevc_vaapi":
            filter_complex = (
                "[0:v]hwdownload,format=nv12[v0];"
                "[v0][1:v]overlay=0:0[v1];"
                "[v1]sidedata=mode=delete:type=DISPLAYMATRIX,hwupload"
            )
        else:
            # Other encoders can use filters directly
            filter_complex = (
                "[0:v][1:v]overlay=0:0,sidedata=mode=delete:type=DISPLAYMATRIX"
            )

        cmd_overlay = [
            "ffmpeg",
            *encoder_input_args,  # Input args BEFORE -i (e.g., hwaccel, hardware init)
            "-noautorotate",
            "-i",
            str(rotated_video_path),
            "-i",
            str(temp_overlay_path),
            "-filter_complex",
            filter_complex,
            *encoder_output_args,  # Output args AFTER -i (codec, bitrate, etc)
            "-c:a",
            "copy",
            "-y",
            str(temp_with_overlay_path),
        ]

        logger.debug(
            f"[{rotated_video_path.name}] Pass 2 ffmpeg command: {' '.join(cmd_overlay)}"
        )
        result = subprocess.run(
            cmd_overlay,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode != 0:
            temp_with_overlay_path.unlink(missing_ok=True)
            raise RuntimeError(f"Pass 2 overlay application failed: {result.stderr}")

        logger.debug(f"[{rotated_video_path.name}] Pass 2 complete")
        return temp_with_overlay_path

    finally:
        try:
            temp_overlay_path.unlink()
            logger.debug(
                f"[{overlay_path.name}] Cleaned up scaled overlay: {temp_overlay_path.name}"
            )
        except Exception as e:
            logger.warning(
                f"[{overlay_path.name}] Failed to clean up scaled overlay: {e}"
            )


def _build_message_description(metadata: Optional[dict], export_username: str) -> str:
    """
    Build rich description for Snapchat Messages using unified format.

    Uses the standard description format from common/dedup/description.py:
        Source: Snapchat/{username}/messages
          - {sender} in "{conversation}": "{content}"

    Args:
        metadata: Metadata dict with conversation fields (can be None)
        export_username: Username for source path

    Returns:
        Multi-line description string with &#xa; separators
    """
    # Build source line using unified format
    source_entry = SourceEntry(
        processor=PLATFORM_SNAPCHAT,
        username=export_username,
        service=SERVICE_MESSAGES,
    )
    source_line = build_source_line(source_entry)

    if metadata is None:
        return source_line

    conv_type = metadata.get("conversation_type")
    conv_id = metadata.get("conversation_id")

    if conv_id is None or conv_type is None:
        # Orphaned media - minimal metadata (only Source)
        return source_line

    # Build conversation name
    if conv_type == "dm":
        conversation_name = f"DM with {conv_id}"
    else:
        conversation_name = metadata.get("conversation_title", "Unknown Group")

    sender = metadata.get("sender", "unknown")
    content = metadata.get("content", "")

    # Build message context using unified format
    msg_entry = SourceEntry(
        processor=PLATFORM_SNAPCHAT,
        sender=sender,
        conversation=conversation_name,
        content=content,
    )
    msg_line = build_message_context(msg_entry)

    description_parts = [source_line]
    if msg_line:
        description_parts.append(msg_line)

    return "&#xa;".join(description_parts)


def _pass3_combine_and_embed(
    with_overlay_path: Path,
    original_video_path: Path,
    output_path: Path,
    metadata: Optional[dict] = None,
    export_username: Optional[str] = None,
    description: Optional[str] = None,
) -> bool:
    """
    PASS 3: Combine videos into dual-track MKV and embed metadata.

    Takes the video with overlay and the original rotated video, combines
    them into a single MKV file with two video tracks, and embeds all metadata
    in a single FFmpeg invocation:
    - Track 0 (default): Video with overlay embedded
    - Track 1: Original video without overlay
    - Container metadata: creation time, GPS coordinates, source description

    If a description is provided, it is used directly. Otherwise, auto-detects
    whether to use rich message metadata or simple memory metadata based on
    the presence of conversation_type and conversation_id fields.

    Args:
        with_overlay_path: Path to video with overlay from Pass 2
        original_video_path: Path to original rotated video from Pass 1
        output_path: Final output path for the video
        metadata: Optional dict with 'date', 'latitude', 'longitude' for embedding
                  For messages: also 'conversation_type', 'conversation_id',
                  'conversation_title', 'sender', 'content'
        export_username: Optional username for source metadata
        description: Optional pre-built description string (takes precedence)

    Returns:
        True if successful, False otherwise

    Raises:
        RuntimeError: If video processing fails
    """
    logger.debug(
        f"[{with_overlay_path.name}] PASS 3: Combining tracks and embedding metadata"
    )

    # Build FFmpeg command for combining tracks and embedding metadata
    cmd = [
        "ffmpeg",
        "-noautorotate",
        "-i",
        str(with_overlay_path),  # Input 0: video with overlay
        "-noautorotate",
        "-i",
        str(original_video_path),  # Input 1: original rotated video
        # Map video from input 0 (with overlay) as stream 0
        "-map",
        "0:v",
        # Map video from input 1 (original) as stream 1
        "-map",
        "1:v",
        # Map audio from input 1 (original has audio)
        "-map",
        "1:a?",
        # Copy all streams without re-encoding
        "-c:v:0",
        "copy",
        "-c:v:1",
        "copy",
        "-c:a",
        "copy",
        # Don't copy side data (which includes displaymatrix rotation)
        "-map_metadata",
        "-1",
        # Set track titles
        "-metadata:s:v:0",
        "title=With Overlay",
        "-metadata:s:v:1",
        "title=Original",
        # Set stream 0 as default, stream 1 as non-default
        "-disposition:v:0",
        "default",
        "-disposition:v:1",
        "0",
    ]

    # Add container-level metadata if provided
    if metadata:
        # Add creation date
        if "date" in metadata:
            # Format: "2021-01-04 23:08:30 UTC" -> "2021-01-04T23:08:30Z"
            date_str = metadata["date"].replace(" UTC", "").replace(" ", "T") + "Z"
            cmd.extend(["-metadata", f"creation_time={date_str}"])
            logger.debug(f"[{with_overlay_path.name}] Adding creation time: {date_str}")

        # Add GPS coordinates as metadata tags
        if "latitude" in metadata and "longitude" in metadata:
            location_value = f"{metadata['latitude']},{metadata['longitude']}"
            cmd.extend(
                [
                    "-metadata",
                    f"location={location_value}",
                    "-metadata",
                    f"location-eng={location_value}",
                ]
            )
            logger.debug(f"[{with_overlay_path.name}] Adding GPS: {location_value}")

    # Add source description metadata
    if description:
        # Use pre-built description (from processor with full message context)
        final_description = description
        logger.debug(f"[{with_overlay_path.name}] Using provided description")
    elif export_username:
        # Auto-detect: if metadata has conversation fields, use rich format
        has_conversation_data = (
            metadata
            and metadata.get("conversation_type") is not None
            and metadata.get("conversation_id") is not None
        )

        if has_conversation_data:
            # Rich format for messages
            final_description = _build_message_description(metadata, export_username)
            logger.debug(f"[{with_overlay_path.name}] Using rich message description")
        else:
            # Simple format for memories
            final_description = f"Source: Snapchat/{export_username}/memories"
            logger.debug(
                f"[{with_overlay_path.name}] Using simple memories description"
            )
    else:
        final_description = None

    if final_description:
        logger.debug(
            f"[{with_overlay_path.name}] Final description: {repr(final_description)}"
        )

        cmd.extend(
            [
                "-metadata",
                f"comment={final_description}",
                "-metadata",
                f"description={final_description}",
            ]
        )

    # Add output path and overwrite flag
    cmd.extend(["-y", str(output_path)])

    logger.debug(f"[{with_overlay_path.name}] Pass 3 ffmpeg command: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        stdin=subprocess.DEVNULL,
    )

    if result.returncode != 0:
        logger.error(
            f"[{with_overlay_path.name}] Pass 3 combine and embed failed: {result.stderr}"
        )
        return False

    # Verify final output exists
    if not output_path.exists():
        logger.error(
            f"[{with_overlay_path.name}] Pass 3 failed - output file not created"
        )
        return False

    # Verify output has 2 video streams
    verify_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=index,codec_name,width,height",
        "-of",
        "csv=p=0",
        str(output_path),
    ]
    verify_result = subprocess.run(
        verify_cmd,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        stdin=subprocess.DEVNULL,
    )

    if verify_result.returncode == 0:
        streams = verify_result.stdout.strip().split("\n")
        stream_count = len(streams)
        logger.debug(
            f"[{with_overlay_path.name}] Final output has {stream_count} video streams"
        )
        for idx, stream_info in enumerate(streams):
            track_label = "With Overlay (default)" if idx == 0 else "Original"
            logger.debug(
                f"[{with_overlay_path.name}]   Stream {idx} ({track_label}): {stream_info}"
            )

        if stream_count != 2:
            logger.warning(
                f"[{with_overlay_path.name}] Expected 2 video streams, got {stream_count}"
            )

    logger.debug(
        f"[{with_overlay_path.name}] Pass 3 complete - saved to {output_path.name}"
    )
    return True


def create_image_with_overlay(
    image_path: Path, overlay_path: Path, output_path: Path, quality: int = 95
) -> bool:
    """
    Create an image with overlay composited on top.

    Args:
        image_path: Path to the base image
        overlay_path: Path to the overlay image
        output_path: Path for the output image
        quality: JPEG quality (default: 95)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Check if overlay file exists before attempting to open
        if not overlay_path.exists():
            logger.warning(f"Overlay file does not exist: {overlay_path}")
            return False

        # Open the base image
        with Image.open(image_path) as base_img:
            # Convert to RGBA if not already
            if base_img.mode != "RGBA":
                converted = base_img.convert("RGBA")
                base_img.close()
                base_img = converted

            # Open the overlay image - catch specific PIL errors
            try:
                with Image.open(overlay_path) as overlay_img:
                    # Convert overlay to RGBA for transparency support
                    if overlay_img.mode != "RGBA":
                        converted = overlay_img.convert("RGBA")
                        overlay_img.close()
                        overlay_img = converted

                    # Resize overlay to match base image if needed
                    if overlay_img.size != base_img.size:
                        resized = overlay_img.resize(
                            base_img.size, Image.Resampling.LANCZOS
                        )
                        overlay_img.close()
                        overlay_img = resized

                    # Composite overlay on top of base image
                    result_img = Image.alpha_composite(base_img, overlay_img)
                    overlay_img.close()  # Done with overlay

                    # Convert back to RGB for JPEG files
                    if output_path.suffix.lower() in [".jpg", ".jpeg"]:
                        # Create white background for JPEG
                        rgb_img = Image.new("RGB", result_img.size, (255, 255, 255))
                        rgb_img.paste(result_img, mask=result_img.split()[-1])
                        result_img.close()
                        result_img = rgb_img

                    # Save the result
                    result_img.save(output_path, quality=quality, optimize=True)
                    result_img.close()
                    return True

            except Exception as e:
                # Provide clear error message for corrupted overlays
                logger.error(
                    f"Cannot open overlay file (possibly corrupted): {overlay_path}: {e}"
                )
                return False

    except Exception as e:  # noqa: BLE001
        logger.error(f"Error creating image with overlay: {e}")
        return False


def create_video_with_overlay(
    video_path: Path,
    overlay_path: Path,
    output_path: Path,
    metadata: Optional[dict] = None,
    export_username: Optional[str] = None,
    description: Optional[str] = None,
) -> bool:
    """
    Create an MKV video with dual tracks using a multi-pass approach.

    Pass 1: Rotate video if needed (apply transpose, remove rotation metadata)
    Pass 2: Apply overlay to rotated video
    Pass 3: Combine both videos into dual-track MKV and embed metadata

    - Track 0 (default): Video with overlay embedded
    - Track 1: Original video without overlay

    If a description is provided, it is used directly. Otherwise, auto-detects
    whether to use rich message metadata or simple memory metadata based on
    the presence of conversation_type and conversation_id fields.

    Uses a single FFprobe call to get all video metadata (rotation, dimensions,
    bitrate) for better performance.

    Args:
        video_path: Path to the input video
        overlay_path: Path to the overlay image
        output_path: Path for the output MKV file
        metadata: Optional dict with 'date', 'latitude', 'longitude' for embedding
                  For messages: also 'conversation_type', 'conversation_id',
                  'conversation_title', 'sender', 'content'
        export_username: Optional username for source metadata
        description: Optional pre-built description string (takes precedence)

    Returns:
        True if successful, False otherwise
    """
    temp_files = []  # Track all temp files for cleanup

    try:
        logger.debug(f"[{video_path.name}] Starting multi-pass video processing")

        # Get all video metadata in single FFprobe call (rotation, dimensions, bitrate)
        video_meta = get_video_metadata(video_path)
        if video_meta is None:
            logger.error(f"[{video_path.name}] Failed to probe video metadata")
            return False

        # PASS 1: Rotate video if needed (pass pre-fetched metadata)
        try:
            rotated_video_path, final_width, final_height, original_bitrate, is_temp = (
                _pass1_rotate_video_with_metadata(video_path, video_meta)
            )
            # Only add to temp_files if it's a temp file (rotation was applied)
            # When no rotation needed, original path is returned and must not be deleted
            if is_temp:
                temp_files.append(rotated_video_path)
        except Exception as e:
            logger.error(f"[{video_path.name}] Pass 1 failed: {e}")
            return False

        # PASS 2: Apply overlay to rotated video
        try:
            with_overlay_path = _pass2_apply_overlay(
                rotated_video_path,
                overlay_path,
                final_width,
                final_height,
                original_bitrate,
            )
            temp_files.append(with_overlay_path)
        except Exception as e:
            logger.error(f"[{video_path.name}] Pass 2 failed: {e}")
            return False

        # PASS 3: Combine both videos into dual-track MKV and embed metadata
        try:
            success = _pass3_combine_and_embed(
                with_overlay_path,
                rotated_video_path,
                output_path,
                metadata,
                export_username,
                description,
            )
            if not success:
                return False
        except Exception as e:
            logger.error(f"[{video_path.name}] Pass 3 failed: {e}")
            return False

        logger.debug(f"[{video_path.name}] Multi-pass processing complete")
        return True

    except Exception as e:
        logger.error(f"[{video_path.name}] Unexpected error during processing: {e}")
        return False

    finally:
        for temp_file in temp_files:
            try:
                if temp_file.exists():
                    temp_file.unlink()
                    logger.debug(
                        f"[{video_path.name}] Cleaned up temp file: {temp_file.name}"
                    )
            except Exception as e:
                logger.warning(
                    f"[{video_path.name}] Failed to clean up {temp_file.name}: {e}"
                )
