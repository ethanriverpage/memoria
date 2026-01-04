#!/usr/bin/env python3
"""
FFprobe Video Metadata Extraction

Provides a unified interface for extracting video metadata using FFprobe.
Consolidates logic previously duplicated across hashing.py and overlay.py.

This module extracts:
- Stream presence (has_video)
- Dimensions (width, height)
- Rotation metadata (from tags or side_data displaymatrix)
- Bitrate (from stream or format level)
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from common.files.utils import short_path

logger = logging.getLogger(__name__)


@dataclass
class VideoMetadata:
    """Container for video metadata extracted in a single FFprobe call.

    Used to consolidate multiple ffprobe calls into one for better performance.
    Supports all common video metadata needs across the codebase.

    Attributes:
        has_video: True if the file contains a video stream.
        width: Video width in pixels, or None if no video stream.
        height: Video height in pixels, or None if no video stream.
        rotation: Rotation in degrees (90, 180, 270), or None if no rotation.
        bitrate: Video bitrate in bits/second, or None if unavailable.
    """

    has_video: bool
    width: Optional[int] = None
    height: Optional[int] = None
    rotation: Optional[int] = None
    bitrate: Optional[int] = None


@dataclass(frozen=True)
class VideoProbeFailure:
    """Diagnostic details when ffprobe fails.

    This module intentionally does *not* decide whether a failure is a warning,
    matching failure, or processing failure. Callers apply policy.
    """

    reason_code: str
    stderr_excerpt: str = ""


def quick_isobmff_header_check(
    video_path: Path, header_bytes: int = 64 * 1024
) -> Tuple[bool, str]:
    """
    Fast heuristic to check if a file looks like an ISO BMFF / QuickTime container.

    Returns:
        (ok, reason_code)
        - ok=True: header resembles a plausible MOV/MP4 container
        - ok=False: header does not resemble a plausible MOV/MP4 container

    Notes:
        This is NOT authoritative. Some valid videos may not match this heuristic.
        Use ffprobe for an authoritative determination.
    """
    try:
        with open(video_path, "rb") as f:
            head = f.read(header_bytes)
    except FileNotFoundError:
        return False, "file_not_found"
    except (OSError, ValueError) as e:
        return False, f"read_failed:{type(e).__name__}"

    # QuickTime/MP4 almost always contains 'ftyp' very early.
    if b"ftyp" in head:
        return True, "header_has_ftyp"

    # Some edge cases may still have recognizable atoms early even without ftyp.
    if b"moov" in head[:4096] or b"mdat" in head[:4096]:
        return True, "header_has_moov_or_mdat_early"

    return False, "header_missing_ftyp"


def get_video_metadata_with_failure(
    video_path: Path, timeout: int = 10
) -> Tuple[Optional[VideoMetadata], Optional[VideoProbeFailure]]:
    """
    Probe video metadata and return structured failure info when probing fails.

    Returns:
        (metadata, failure)
        - If probing succeeds: (VideoMetadata(...), None)
        - If probing fails: (None, VideoProbeFailure(...))
    """
    try:
        # Single ffprobe call to get all metadata as JSON
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,bit_rate,codec_type:stream_tags=rotate:stream_side_data",
            "-show_entries",
            "format=bit_rate",
            "-of",
            "json",
            str(video_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            low = stderr.lower()
            reason = "ffprobe_failed"
            if "moov atom not found" in low:
                reason = "moov_atom_not_found"
            elif "invalid data found" in low:
                reason = "invalid_data"

            logger.debug(
                "FFprobe failed for %s: %s", short_path(video_path), result.stderr
            )
            return None, VideoProbeFailure(
                reason_code=reason,
                stderr_excerpt=stderr[:300],
            )

        data = json.loads(result.stdout)

        # Extract stream info
        streams = data.get("streams", [])
        if not streams:
            logger.debug("No video streams found in %s", short_path(video_path))
            return VideoMetadata(has_video=False), None

        stream = streams[0]

        # Check if this is actually a video stream
        codec_type = stream.get("codec_type", "")
        if codec_type and codec_type.lower() != "video":
            logger.debug("First stream is not video in %s", short_path(video_path))
            return VideoMetadata(has_video=False), None

        # Extract dimensions
        width = stream.get("width")
        height = stream.get("height")

        if not width or not height:
            logger.debug(
                "Missing dimensions in stream data for %s", short_path(video_path)
            )
            return VideoMetadata(has_video=False), None

        # Extract rotation from tags or side_data
        rotation = _extract_rotation(stream)

        # Extract bitrate - try format first, then stream
        bitrate = _extract_bitrate(stream, data.get("format", {}))

        return (
            VideoMetadata(
                has_video=True,
                width=width,
                height=height,
                rotation=rotation,
                bitrate=bitrate,
            ),
            None,
        )

    except subprocess.TimeoutExpired:
        logger.warning("Timeout probing video metadata for %s", short_path(video_path))
        return None, VideoProbeFailure(reason_code="timeout")
    except json.JSONDecodeError as e:
        logger.warning(
            "Failed to parse FFprobe JSON for %s: %s", short_path(video_path), e
        )
        return None, VideoProbeFailure(reason_code="json_decode_error")
    except FileNotFoundError:
        logger.warning("ffprobe not found, cannot extract video metadata")
        return None, VideoProbeFailure(reason_code="ffprobe_not_found")
    except (OSError, ValueError) as e:
        logger.debug(
            "Error probing video metadata for %s: %s", short_path(video_path), e
        )
        return None, VideoProbeFailure(reason_code=f"error:{type(e).__name__}")


def get_video_metadata(video_path: Path, timeout: int = 10) -> Optional[VideoMetadata]:
    """
    Get all video metadata in a single FFprobe call.

    Consolidates stream presence, dimensions, rotation, and bitrate detection
    into one probe for better performance (avoids multiple subprocess calls).

    Args:
        video_path: Path to the video file.
        timeout: Timeout in seconds for the ffprobe command (default 10).

    Returns:
        VideoMetadata with all extracted info, or None if probing failed entirely.
        If the file has no video stream, returns VideoMetadata(has_video=False).
    """
    metadata, _failure = get_video_metadata_with_failure(video_path, timeout=timeout)
    return metadata


def _extract_rotation(stream: dict) -> Optional[int]:
    """
    Extract rotation from stream tags or side_data displaymatrix.

    Args:
        stream: FFprobe stream dictionary.

    Returns:
        Rotation in degrees (90, 180, 270), or None if no rotation.
    """
    rotation = None

    # Check stream tags first (older method)
    tags = stream.get("tags", {})
    if "rotate" in tags:
        try:
            rotation = int(float(tags["rotate"])) % 360
            if rotation == 0:
                rotation = None
        except (ValueError, TypeError):
            pass

    # Check side_data_list for displaymatrix rotation (newer method)
    if rotation is None:
        side_data_list = stream.get("side_data_list", [])
        for side_data in side_data_list:
            if side_data.get("side_data_type") == "Display Matrix":
                try:
                    rot = side_data.get("rotation")
                    if rot is not None:
                        # Normalize rotation to 0-359 range
                        # Negative values (e.g., -90) become positive equivalents
                        # (e.g., 270) via modulo
                        rotation = int(float(rot)) % 360
                        if rotation == 0:
                            rotation = None
                except (ValueError, TypeError):
                    pass

    return rotation


def _extract_bitrate(stream: dict, format_info: dict) -> Optional[int]:
    """
    Extract bitrate from format or stream level.

    Args:
        stream: FFprobe stream dictionary.
        format_info: FFprobe format dictionary.

    Returns:
        Bitrate in bits/second, or None if unavailable.
    """
    bitrate = None

    # Try format-level bitrate first (more reliable for container bitrate)
    if format_info.get("bit_rate"):
        try:
            bitrate = int(format_info["bit_rate"])
        except (ValueError, TypeError):
            pass

    # Fall back to stream-level bitrate
    if bitrate is None and stream.get("bit_rate"):
        try:
            bitrate = int(stream["bit_rate"])
        except (ValueError, TypeError):
            pass

    return bitrate
