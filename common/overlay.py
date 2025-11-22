#!/usr/bin/env python3
"""
Snapchat Overlay Embed Module

This module provides functions for processing Snapchat memory exports by overlaying
transparent PNG/WebP overlays onto media files (images and videos).

For videos, creates MKV files with dual video tracks:
- Track 0 (default): Video with overlay embedded
- Track 1: Original video without overlay

Video processing uses a 4-pass approach:
1. Rotate video if needed and remove rotation metadata
2. Apply overlay to rotated video
3. Combine both videos into dual-track MKV
4. Embed metadata into final video

Video rotation metadata (common in portrait videos) is automatically detected and
the rotation is physically applied to the video. Output files have correct orientation
without rotation metadata, ensuring compatibility with all video players.
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
from common.video_encoder import (
    get_encoder_args,
    get_encoder_input_args,
    get_encoder_name,
    get_software_encoder_args,
    get_video_bitrate,
    is_hardware_accelerated,
    is_hardware_acceleration_error,
)

# Set up logging
logger = logging.getLogger(__name__)

# Detect encoder once at module load time
_ENCODER_NAME = get_encoder_name()
_IS_HARDWARE = is_hardware_accelerated()

logger.debug(f"Video encoder initialized: {_ENCODER_NAME} (hardware: {_IS_HARDWARE})")


def get_video_rotation(video_path: Path) -> Optional[int]:
    """
    Get video rotation metadata using ffprobe.

    Args:
        video_path: Path to the video file

    Returns:
        Rotation angle (0, 90, 180, 270) or None if no rotation
    """
    rotation_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream_tags=rotate:stream_side_data=rotation",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]

    try:
        result = subprocess.run(
            rotation_cmd, capture_output=True, text=True, timeout=10, check=False, stdin=subprocess.DEVNULL
        )

        logger.debug(
            f"[{video_path.name}] Rotation probe command: {' '.join(rotation_cmd)}"
        )
        logger.debug(
            f"[{video_path.name}] Rotation probe stdout: '{result.stdout.strip()}'"
        )
        logger.debug(
            f"[{video_path.name}] Rotation probe stderr: '{result.stderr.strip()}'"
        )

        if result.returncode == 0 and result.stdout.strip():
            rotation_output = result.stdout.strip().split("\n")[0]
            logger.debug(f"[{video_path.name}] Raw rotation value: '{rotation_output}'")
            try:
                rotation_value = float(rotation_output)
                # Normalize rotation to 0-359 range
                rotation_value = rotation_value % 360
                if rotation_value != 0:
                    logger.debug(
                        f"[{video_path.name}] Detected rotation: {int(rotation_value)}°"
                    )
                    return int(rotation_value)
            except (ValueError, TypeError) as e:
                logger.debug(f"[{video_path.name}] Failed to parse rotation value: {e}")
        else:
            logger.debug(
                f"[{video_path.name}] No rotation metadata found (return code: {result.returncode})"
            )
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout checking rotation for {video_path.name}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Error checking rotation for {video_path.name}: {e}")

    return None


# Multi-pass video processing helper functions


def _pass1_rotate_video(
    video_path: Path, rotation: Optional[int]
) -> tuple[Path, int, int, Optional[int]]:
    """
    PASS 1: Rotate video if needed and return final dimensions.

    Applies rotation transformation to video based on metadata, removes rotation metadata,
    and calculates final dimensions after rotation.

    Automatically falls back to software encoding if hardware acceleration fails.

    Args:
        video_path: Path to the input video
        rotation: Rotation angle (0, 90, 180, 270) or None

    Returns:
        Tuple of (rotated_video_path, final_width, final_height, original_bitrate)

    Raises:
        RuntimeError: If video processing fails (even with software fallback)
    """
    # Try hardware encoding first if available
    try:
        return _pass1_rotate_video_impl(video_path, rotation, use_hardware=True)
    except RuntimeError as e:
        error_msg = str(e)
        # Check if this is a hardware acceleration error
        if is_hardware_accelerated() and is_hardware_acceleration_error(error_msg):
            logger.warning(
                f"[{video_path.name}] Hardware encoding failed in Pass 1, "
                f"falling back to software encoding: {error_msg[:100]}"
            )
            try:
                return _pass1_rotate_video_impl(video_path, rotation, use_hardware=False)
            except RuntimeError as fallback_error:
                logger.error(
                    f"[{video_path.name}] Software encoding fallback also failed in Pass 1: {fallback_error}"
                )
                raise
        else:
            # Not a hardware error, or already using software, re-raise
            raise


def _pass1_rotate_video_impl(
    video_path: Path, rotation: Optional[int], use_hardware: bool = True
) -> tuple[Path, int, int, Optional[int]]:
    """
    PASS 1 Implementation: Rotate video if needed and return final dimensions.

    Applies rotation transformation to video based on metadata, removes rotation metadata,
    and calculates final dimensions after rotation.

    Args:
        video_path: Path to the input video
        rotation: Rotation angle (0, 90, 180, 270) or None
        use_hardware: Whether to use hardware acceleration (if available)

    Returns:
        Tuple of (rotated_video_path, final_width, final_height, original_bitrate)

    Raises:
        RuntimeError: If video processing fails
    """
    logger.debug(f"[{video_path.name}] PASS 1: Checking rotation and rotating if needed")

    # Detect original video bitrate for quality preservation
    original_bitrate = get_video_bitrate(video_path)
    if original_bitrate:
        logger.debug(
            f"[{video_path.name}] Detected original bitrate: {original_bitrate / 1_000_000:.2f} Mbps"
        )
    else:
        logger.debug(
            f"[{video_path.name}] Could not detect bitrate, using default quality settings"
        )

    # Get original video dimensions
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=s=x:p=0",
        str(video_path),
    ]

    probe_result = subprocess.run(
        probe_cmd, capture_output=True, text=True, timeout=10, check=False, stdin=subprocess.DEVNULL
    )
    if probe_result.returncode != 0:
        raise RuntimeError(f"Failed to probe video dimensions: {probe_result.stderr}")

    dimensions = probe_result.stdout.strip().rstrip("x")
    if not dimensions or "x" not in dimensions:
        raise RuntimeError(f"Failed to get valid video dimensions: '{dimensions}'")

    try:
        parts = dimensions.split("x")
        if len(parts) != 2:
            raise ValueError(f"Invalid dimension format: '{dimensions}'")
        orig_width, orig_height = map(int, parts)
    except (ValueError, TypeError) as e:
        raise RuntimeError(f"Failed to parse video dimensions '{dimensions}': {e}")

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
        # Swap dimensions for 90/270 degree rotations
        target_width, target_height = orig_height, orig_width
        logger.debug(
            f"[{video_path.name}] Target dimensions after {rotation}° rotation: {target_width}x{target_height}"
        )
    else:
        target_width, target_height = orig_width, orig_height

    # Create temp file for rotated video
    temp_rotated = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    temp_rotated.close()
    temp_rotated_path = Path(temp_rotated.name)

    if rotation:
        logger.debug(f"[{video_path.name}] Applying {rotation}° rotation")

        # Build rotation filter
        # Note: We invert the rotation direction because displaymatrix
        # indicates how to display the video, not how to physically rotate it
        if rotation == 90:
            rotation_filter = "transpose=2"  # Apply 90° counter-clockwise (270° CW)
        elif rotation == 180:
            rotation_filter = "hflip,vflip"  # 180° rotation
        elif rotation == 270:
            rotation_filter = "transpose=1"  # Apply 90° clockwise (270° CCW)
        else:
            logger.warning(
                f"[{video_path.name}] Unsupported rotation {rotation}°, copying without rotation"
            )
            rotation_filter = None

        if rotation_filter:
            # Apply transpose and remove rotation metadata
            # Note: We need to disable auto-rotation to prevent the decoder from
            # applying rotation before our transpose filter
            # We use sidedata filter to delete DISPLAYMATRIX side data
            
            # Get encoder args based on hardware/software preference
            if use_hardware and is_hardware_accelerated():
                encoder_input_args = get_encoder_input_args()  # Input args (e.g., hwaccel)
                encoder_output_args = get_encoder_args(original_bitrate)  # Output args (codec, bitrate, etc)
                encoder_name = get_encoder_name()
                logger.debug(f"[{video_path.name}] Pass 1 using hardware encoder: {encoder_name}")
            else:
                # Software encoding fallback
                encoder_input_args = []
                encoder_output_args = get_software_encoder_args(original_bitrate)
                encoder_name = "libx265"
                logger.debug(f"[{video_path.name}] Pass 1 using software encoder: {encoder_name}")
            
            # For VAAPI, we need to handle filters differently:
            # 1. Hardware decode produces VAAPI surfaces
            # 2. We need to download to CPU memory for software filters
            # 3. Apply the rotation filter
            # 4. Upload back to GPU for hardware encoding
            if encoder_name == "hevc_vaapi":
                combined_filter = (
                    f"hwdownload,format=nv12,{rotation_filter},"
                    f"sidedata=mode=delete:type=DISPLAYMATRIX,hwupload"
                )
            else:
                # Other encoders can use filters directly
                combined_filter = (
                    f"{rotation_filter},sidedata=mode=delete:type=DISPLAYMATRIX"
                )

            cmd_rotate = [
                "ffmpeg",
                *encoder_input_args,  # Input args BEFORE -i (e.g., hwaccel, hardware init)
                "-noautorotate",  # Prevent decoder from auto-rotating
                "-i",
                str(video_path),
                "-vf",
                combined_filter,
                *encoder_output_args,  # Output args AFTER -i (codec, bitrate, etc)
                "-c:a",
                "copy",
                "-y",
                str(temp_rotated_path),
            ]

            logger.debug(
                f"[{video_path.name}] Pass 1 ffmpeg command: {' '.join(cmd_rotate)}"
            )
            result = subprocess.run(
                cmd_rotate, capture_output=True, text=True, timeout=300, check=False, stdin=subprocess.DEVNULL
            )

            if result.stderr:
                logger.debug(
                    f"[{video_path.name}] Pass 1 ffmpeg stderr:\n{result.stderr}"
                )

            if result.returncode != 0:
                temp_rotated_path.unlink(missing_ok=True)
                raise RuntimeError(f"Pass 1 rotation failed: {result.stderr}")

            logger.debug(
                f"[{video_path.name}] Pass 1 complete - video rotated to {temp_rotated_path.name}"
            )
        else:
            # No valid rotation filter, copy original
            shutil.copy2(video_path, temp_rotated_path)
            logger.debug(
                f"[{video_path.name}] Pass 1 complete - video copied without rotation"
            )
    else:
        # No rotation needed, copy original
        shutil.copy2(video_path, temp_rotated_path)
        logger.debug(
            f"[{video_path.name}] Pass 1 complete - no rotation metadata found, video copied"
        )

    # Verify output dimensions
    verify_probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=s=x:p=0",
        str(temp_rotated_path),
    ]
    verify_result = subprocess.run(
        verify_probe_cmd, capture_output=True, text=True, timeout=10, check=False, stdin=subprocess.DEVNULL
    )
    if verify_result.returncode == 0:
        output_dims = verify_result.stdout.strip().rstrip("x")
        logger.debug(f"[{video_path.name}] Pass 1 output dimensions: {output_dims}")

        # Verify dimensions match expected
        if output_dims != f"{target_width}x{target_height}":
            logger.warning(
                f"[{video_path.name}] Pass 1 dimension mismatch! Expected {target_width}x{target_height}, got {output_dims}"
            )
    
    logger.debug(f"[{video_path.name}] Pass 1 complete")
    return temp_rotated_path, target_width, target_height, original_bitrate


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
            rotated_video_path, overlay_path, target_width, target_height,
            original_bitrate, use_hardware=True
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
                    rotated_video_path, overlay_path, target_width, target_height,
                    original_bitrate, use_hardware=False
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
                overlay_img = overlay_img.convert("RGBA")

            # Resize using high-quality Lanczos resampling if needed
            if orig_width != target_width or orig_height != target_height:
                logger.debug(
                    f"[{overlay_path.name}] Scaling overlay from {orig_width}x{orig_height} to {target_width}x{target_height}"
                )
                scaled_overlay = overlay_img.resize(
                    (target_width, target_height), Image.Resampling.LANCZOS
                )
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
            logger.debug(
                f"[{overlay_path.name}] Scaled overlay saved to {temp_overlay_path.name}"
            )

    except Exception as e:
        # Provide clear error message for corrupted overlays
        logger.error(f"Cannot open overlay file (possibly corrupted): {overlay_path}: {e}")
        raise RuntimeError(f"Pass 2 overlay scaling failed - corrupted overlay: {e}")

    # Create temp file for video with overlay
    temp_with_overlay = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    temp_with_overlay.close()
    temp_with_overlay_path = Path(temp_with_overlay.name)

    try:
        # Build ffmpeg command to apply overlay
        # Use -noautorotate to prevent FFmpeg from re-reading rotation metadata
        # Add sidedata filter to delete DISPLAYMATRIX side data

        # Get encoder args based on hardware/software preference
        if use_hardware and is_hardware_accelerated():
            encoder_input_args = get_encoder_input_args()  # Input args (e.g., hwaccel)
            encoder_output_args = get_encoder_args(original_bitrate)  # Output args (codec, bitrate, etc)
            encoder_name = get_encoder_name()
            logger.debug(f"[{rotated_video_path.name}] Pass 2 using hardware encoder: {encoder_name}")
        else:
            # Software encoding fallback
            encoder_input_args = []
            encoder_output_args = get_software_encoder_args(original_bitrate)
            encoder_name = "libx265"
            logger.debug(f"[{rotated_video_path.name}] Pass 2 using software encoder: {encoder_name}")
        
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
            filter_complex = "[0:v][1:v]overlay=0:0,sidedata=mode=delete:type=DISPLAYMATRIX"

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
            cmd_overlay, capture_output=True, text=True, timeout=300, check=False, stdin=subprocess.DEVNULL
        )

        if result.stderr:
            logger.debug(
                f"[{rotated_video_path.name}] Pass 2 ffmpeg stderr:\n{result.stderr}"
            )

        if result.returncode != 0:
            temp_with_overlay_path.unlink(missing_ok=True)
            raise RuntimeError(f"Pass 2 overlay application failed: {result.stderr}")

        logger.debug(f"[{rotated_video_path.name}] Pass 2 complete")
        return temp_with_overlay_path

    finally:
        # Clean up scaled overlay temp file
        try:
            temp_overlay_path.unlink()
            logger.debug(
                f"[{overlay_path.name}] Cleaned up scaled overlay: {temp_overlay_path.name}"
            )
        except Exception as e:
            logger.warning(
                f"[{overlay_path.name}] Failed to clean up scaled overlay: {e}"
            )


def _pass3_combine_tracks(with_overlay_path: Path, original_video_path: Path) -> Path:
    """
    PASS 3: Combine videos into dual-track MKV.

    Takes the video with overlay and the original rotated video, and combines
    them into a single MKV file with two video tracks:
    - Track 0 (default): Video with overlay embedded
    - Track 1: Original video without overlay

    Args:
        with_overlay_path: Path to video with overlay from Pass 2
        original_video_path: Path to original rotated video from Pass 1

    Returns:
        Path to dual-track MKV temp file

    Raises:
        RuntimeError: If video processing fails
    """
    logger.debug(
        f"[{with_overlay_path.name}] PASS 3: Combining tracks into dual-track MKV"
    )

    # Create temp file for dual-track MKV
    temp_dual = tempfile.NamedTemporaryFile(suffix=".mkv", delete=False)
    temp_dual.close()
    temp_dual_path = Path(temp_dual.name)

    # Build ffmpeg command to combine both videos into dual-track MKV
    # Use -noautorotate to prevent FFmpeg from re-reading rotation metadata
    cmd_combine = [
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
        "-y",
        str(temp_dual_path),
    ]

    logger.debug(
        f"[{with_overlay_path.name}] Pass 3 ffmpeg command: {' '.join(cmd_combine)}"
    )
    result = subprocess.run(
        cmd_combine, capture_output=True, text=True, timeout=300, check=False, stdin=subprocess.DEVNULL
    )

    if result.stderr:
        logger.debug(
            f"[{with_overlay_path.name}] Pass 3 ffmpeg stderr:\n{result.stderr}"
        )

    if result.returncode != 0:
        temp_dual_path.unlink(missing_ok=True)
        raise RuntimeError(f"Pass 3 track combination failed: {result.stderr}")

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
        str(temp_dual_path),
    ]
    verify_result = subprocess.run(
        verify_cmd, capture_output=True, text=True, timeout=10, check=False, stdin=subprocess.DEVNULL
    )

    if verify_result.returncode == 0:
        streams = verify_result.stdout.strip().split("\n")
        stream_count = len(streams)
        logger.debug(
            f"[{with_overlay_path.name}] Created {stream_count} video streams"
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

    logger.debug(f"[{with_overlay_path.name}] Pass 3 complete")
    return temp_dual_path


def _build_message_description(metadata: dict, export_username: str) -> str:
    """
    Build rich description for Snapchat Messages.

    Args:
        metadata: Metadata dict with conversation fields
        export_username: Username for source path

    Returns:
        Multi-line description string
    """
    conv_type = metadata.get("conversation_type")
    conv_id = metadata.get("conversation_id")

    if conv_id is None or conv_type is None:
        # Orphaned media - minimal metadata (only Source)
        return f"Source: Snapchat/{export_username}/messages"

    # Normal message with full context
    if conv_type == "dm":
        conversation_context = f"DM with {conv_id}"
    else:
        conversation_context = metadata.get("conversation_title", "Unknown Group")

    sender = metadata.get("sender", "unknown")
    content = metadata.get("content", "")

    description_parts = [
        f"Source: Snapchat/{export_username}/messages",
        f"Conversation: \"{conversation_context}\"",
        f"Sender: \"{sender}\"",
    ]

    if content:
        description_parts.append(f"Content: \"{content}\"")

    return "\n".join(description_parts)


def _pass4_embed_metadata(
    video_path: Path,
    output_path: Path,
    metadata: Optional[dict] = None,
    export_username: Optional[str] = None,
) -> bool:
    """
    PASS 4: Embed metadata into final video and move to output location.

    Takes the final dual-track video from Pass 3 and adds all metadata:
    - Creation time
    - GPS coordinates
    - Source description
    - Track titles and dispositions

    Then copies to the final output path.

    Auto-detects whether to use rich message metadata or simple memory metadata
    based on the presence of conversation_type and conversation_id fields.

    Args:
        video_path: Path to the video from Pass 3
        output_path: Final output path for the video
        metadata: Optional dict with 'date', 'latitude', 'longitude' for embedding
                  For messages: also 'conversation_type', 'conversation_id',
                  'conversation_title', 'sender', 'content'
        export_username: Optional username for source metadata

    Returns:
        True if successful, False otherwise
    """
    logger.debug(f"[{video_path.name}] PASS 4: Embedding metadata and finalizing")

    # Build ffmpeg command to add metadata and set final track properties
    cmd_metadata = [
        "ffmpeg",
        "-i",
        str(video_path),
        # Explicitly map all streams to preserve both video tracks
        "-map",
        "0:v:0",  # Map first video stream (with overlay)
        "-map",
        "0:v:1",  # Map second video stream (original)
        "-map",
        "0:a?",  # Map audio if present
        # Copy all streams without re-encoding
        "-c",
        "copy",
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

    # Add metadata if provided
    if metadata:
        # Add creation date
        if "date" in metadata:
            # Format: "2021-01-04 23:08:30 UTC" -> "2021-01-04T23:08:30Z"
            date_str = metadata["date"].replace(" UTC", "").replace(" ", "T") + "Z"
            cmd_metadata.extend(["-metadata", f"creation_time={date_str}"])
            logger.debug(f"[{video_path.name}] Adding creation time: {date_str}")

        # Add GPS coordinates as metadata tags
        if "latitude" in metadata and "longitude" in metadata:
            # Quote the location values to preserve negative signs
            location_value = f"{metadata['latitude']},{metadata['longitude']}"
            cmd_metadata.extend(
                [
                    "-metadata",
                    f"location={location_value}",
                    "-metadata",
                    f"location-eng={location_value}",
                ]
            )
            logger.debug(f"[{video_path.name}] Adding GPS: {location_value}")

    # Add source description metadata
    if export_username:
        # Auto-detect: if metadata has conversation fields, use rich format
        has_conversation_data = (
            metadata
            and metadata.get("conversation_type") is not None
            and metadata.get("conversation_id") is not None
        )

        if has_conversation_data:
            # Rich format for messages
            description = _build_message_description(metadata, export_username)
            logger.debug(f"[{video_path.name}] Using rich message description")
        else:
            # Simple format for memories
            description = f"Source: Snapchat/{export_username}/memories"
            logger.debug(f"[{video_path.name}] Using simple memories description")

        logger.debug(f"[{video_path.name}] Final description: {repr(description)}")

        cmd_metadata.extend(
            [
                "-metadata",
                f"comment={description}",
                "-metadata",
                f"description={description}",
            ]
        )

    # Add output path and overwrite flag
    cmd_metadata.extend(["-y", str(output_path)])

    logger.debug(f"[{video_path.name}] Pass 4 ffmpeg command: {' '.join(cmd_metadata)}")
    result = subprocess.run(
        cmd_metadata, capture_output=True, text=True, timeout=300, check=False, stdin=subprocess.DEVNULL
    )

    if result.stderr:
        logger.debug(f"[{video_path.name}] Pass 4 ffmpeg stderr:\n{result.stderr}")

    if result.returncode != 0:
        logger.error(
            f"[{video_path.name}] Pass 4 metadata embedding failed: {result.stderr}"
        )
        return False

    # Verify final output exists and has correct properties
    if not output_path.exists():
        logger.error(f"[{video_path.name}] Pass 4 failed - output file not created")
        return False

    # Verify output has 2 video streams
    verify_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v",
        "-show_entries",
        "stream=index,width,height,codec_name",
        "-of",
        "csv=p=0",
        str(output_path),
    ]
    verify_result = subprocess.run(
        verify_cmd, capture_output=True, text=True, timeout=10, check=False, stdin=subprocess.DEVNULL
    )

    if verify_result.returncode == 0:
        streams = verify_result.stdout.strip().split("\n")
        stream_count = len(streams)
        logger.debug(
            f"[{video_path.name}] Final output has {stream_count} video streams"
        )
        for idx, stream_info in enumerate(streams):
            track_label = "With Overlay (default)" if idx == 0 else "Original"
            logger.debug(
                f"[{video_path.name}]   Stream {idx} ({track_label}): {stream_info}"
            )

    logger.debug(f"[{video_path.name}] Pass 4 complete - saved to {output_path.name}")
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
                base_img = base_img.convert("RGBA")

            # Open the overlay image - catch specific PIL errors
            try:
                with Image.open(overlay_path) as overlay_img:
                    # Convert overlay to RGBA for transparency support
                    if overlay_img.mode != "RGBA":
                        overlay_img = overlay_img.convert("RGBA")

                    # Resize overlay to match base image if needed
                    if overlay_img.size != base_img.size:
                        overlay_img = overlay_img.resize(
                            base_img.size, Image.Resampling.LANCZOS
                        )

                    # Composite overlay on top of base image
                    result_img = Image.alpha_composite(base_img, overlay_img)

                    # Convert back to RGB for JPEG files
                    if output_path.suffix.lower() in [".jpg", ".jpeg"]:
                        # Create white background for JPEG
                        rgb_img = Image.new("RGB", result_img.size, (255, 255, 255))
                        rgb_img.paste(result_img, mask=result_img.split()[-1])
                        result_img = rgb_img

                    # Save the result
                    result_img.save(output_path, quality=quality, optimize=True)
                    return True

            except Exception as e:
                # Provide clear error message for corrupted overlays
                logger.error(f"Cannot open overlay file (possibly corrupted): {overlay_path}: {e}")
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
) -> bool:
    """
    Create an MKV video with dual tracks using a multi-pass approach.

    Pass 1: Rotate video if needed (apply transpose, remove rotation metadata)
    Pass 2: Apply overlay to rotated video
    Pass 3: Combine both videos into dual-track MKV
    Pass 4: Embed metadata into final video

    - Track 0 (default): Video with overlay embedded
    - Track 1: Original video without overlay

    Auto-detects whether to use rich message metadata or simple memory metadata
    based on the presence of conversation_type and conversation_id fields.

    Args:
        video_path: Path to the input video
        overlay_path: Path to the overlay image
        output_path: Path for the output MKV file
        metadata: Optional dict with 'date', 'latitude', 'longitude' for embedding
                  For messages: also 'conversation_type', 'conversation_id',
                  'conversation_title', 'sender', 'content'
        export_username: Optional username for source metadata

    Returns:
        True if successful, False otherwise
    """
    temp_files = []  # Track all temp files for cleanup

    try:
        logger.debug(f"[{video_path.name}] Starting multi-pass video processing")

        # Detect rotation metadata
        rotation = get_video_rotation(video_path)

        # PASS 1: Rotate video if needed
        try:
            rotated_video_path, final_width, final_height, original_bitrate = (
                _pass1_rotate_video(video_path, rotation)
            )
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

        # PASS 3: Combine both videos into dual-track MKV
        try:
            dual_track_path = _pass3_combine_tracks(
                with_overlay_path, rotated_video_path
            )
            temp_files.append(dual_track_path)
        except Exception as e:
            logger.error(f"[{video_path.name}] Pass 3 failed: {e}")
            return False

        # PASS 4: Embed metadata and move to final output
        try:
            success = _pass4_embed_metadata(
                dual_track_path, output_path, metadata, export_username
            )
            if not success:
                return False
        except Exception as e:
            logger.error(f"[{video_path.name}] Pass 4 failed: {e}")
            return False

        logger.debug(f"[{video_path.name}] Multi-pass processing complete")
        return True

    except Exception as e:
        logger.error(f"[{video_path.name}] Unexpected error during processing: {e}")
        return False

    finally:
        # Clean up all temporary files
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

