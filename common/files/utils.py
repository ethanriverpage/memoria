#!/usr/bin/env python3
"""
File utility functions for media processing

Provides shared file type detection and extension correction functionality
used across multiple preprocessors.

Performance Features:
- Reflink (copy-on-write) support for instant file copies on Btrfs/XFS/APFS/ZFS
- Automatic fallback to standard copy on unsupported filesystems
"""

import logging
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Union

import magic

logger = logging.getLogger(__name__)

# =============================================================================
# Path Shortening for Logs
# =============================================================================

# Pattern to match export directory names like:
# - google-ethanriverpage-20250526
# - iph17pm-messages-20251121
# - snapchat-ethanriverpage-20251017_snapchat_messages_eeffbbd4
# - discord-minmatic-20251204
_EXPORT_NAME_PATTERN = re.compile(
    r"^[a-z0-9]+[-_][a-z0-9_]+-\d{8}(?:[-_].+)?$", re.IGNORECASE
)


def short_path(path: Union[str, Path]) -> str:
    """Shorten a path for logging to just export name and filename.

    Attempts to find the export directory name in the path and returns
    just the export name and the filename, omitting intermediate directories.
    Export names match patterns like:
    - google-username-20250526
    - iph17pm-messages-20251121
    - snapchat-username-20251017_snapchat_messages_abc123

    If no export name pattern is found, returns just the filename.

    Args:
        path: Full path to shorten

    Returns:
        Shortened path string in format "export-name/filename" or just "filename"

    Examples:
        >>> short_path("/mnt/media/originals/google-user-20250526/Photos/img.jpg")
        'google-user-20250526/img.jpg'
        >>> short_path("/mnt/media/processed/iph17pm-messages-20251121/messages/file.mov")
        'iph17pm-messages-20251121/file.mov'
        >>> short_path("/some/unknown/path/to/file.jpg")
        'file.jpg'
    """
    path = Path(path)
    parts = path.parts
    filename = path.name

    # Find the first component that looks like an export name
    for part in parts:
        if _EXPORT_NAME_PATTERN.match(part):
            return f"{part}/{filename}"

    # Fallback: just return the filename
    return filename


def get_export_name(path: Union[str, Path]) -> str:
    """Extract the export directory name from a path.

    Finds the first path component matching export name patterns like:
    - google-username-20250526
    - iph17pm-messages-20251121
    - snapchat-username-20251017_snapchat_messages_abc123

    Args:
        path: Full path (file or directory) to extract export name from

    Returns:
        Export name if found, otherwise the path's final component

    Examples:
        >>> get_export_name("/mnt/media/originals/google-user-20250526/Photos")
        'google-user-20250526'
        >>> get_export_name("/mnt/media/pre/snap-user-20251017_snap_msgs_abc/media")
        'snap-user-20251017_snap_msgs_abc'
    """
    path = Path(path)
    parts = path.parts

    # Find the first component that looks like an export name
    for part in parts:
        if _EXPORT_NAME_PATTERN.match(part):
            return part

    # Fallback: return the directory name
    return path.name


# Cache reflink support detection per-filesystem (mount point)
_reflink_support_cache: dict[str, bool] = {}


def _detect_reflink_support(path: Path) -> bool:
    """Check if filesystem at path supports reflinks (cached per mount point)."""
    # Get the mount point for this path
    try:
        # Use os.path.realpath to resolve symlinks
        real_path = os.path.realpath(str(path))
        # Find mount point by walking up until we hit a different device
        mount_point = real_path
        while mount_point != "/":
            parent = os.path.dirname(mount_point)
            if os.stat(parent).st_dev != os.stat(mount_point).st_dev:
                break
            mount_point = parent
    except (OSError, ValueError):
        mount_point = "/"

    # Check cache
    if mount_point in _reflink_support_cache:
        return _reflink_support_cache[mount_point]

    # Default to False, will be updated if reflink test succeeds
    _reflink_support_cache[mount_point] = False

    system = platform.system()

    if system == "Linux":
        # Check /proc/mounts for filesystem type
        try:
            with open("/proc/mounts", "r") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 3:
                        mount_path, fs_type = parts[1], parts[2]
                        if mount_point.startswith(mount_path):
                            if fs_type in ("btrfs", "xfs", "bcachefs"):
                                _reflink_support_cache[mount_point] = True
                                logger.debug(
                                    f"Reflink support detected: {fs_type} at {mount_point}"
                                )
                                return True
        except (OSError, IOError):
            pass
    elif system == "Darwin":
        # macOS APFS supports reflinks via clonefile
        # Check if running on APFS (default since High Sierra)
        try:
            result = subprocess.run(
                ["diskutil", "info", mount_point],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "APFS" in result.stdout:
                _reflink_support_cache[mount_point] = True
                logger.debug(f"Reflink support detected: APFS at {mount_point}")
                return True
        except (subprocess.SubprocessError, FileNotFoundError):
            pass

    return False


def copy_file(
    src: Union[str, Path],
    dst: Union[str, Path],
    preserve_metadata: bool = True,
) -> None:
    """Copy file with reflink optimization on supported filesystems."""
    src = Path(src)
    dst = Path(dst)

    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")

    # Ensure destination directory exists
    dst.parent.mkdir(parents=True, exist_ok=True)

    system = platform.system()

    # Try reflink copy first
    if _detect_reflink_support(dst.parent):
        try:
            if system == "Linux":
                # Use cp --reflink=always for guaranteed reflink
                cmd = ["cp", "--reflink=always"]
                if preserve_metadata:
                    cmd.append("--preserve=all")
                cmd.extend([str(src), str(dst)])

                subprocess.run(
                    cmd,
                    capture_output=True,
                    check=True,
                    timeout=60,
                )
                logger.debug(f"Reflink: {src.name} -> {short_path(dst)}")
                return

            elif system == "Darwin":
                # macOS: use cp -c for clonefile
                cmd = ["cp", "-c"]
                if preserve_metadata:
                    cmd.append("-p")
                cmd.extend([str(src), str(dst)])

                subprocess.run(
                    cmd,
                    capture_output=True,
                    check=True,
                    timeout=60,
                )
                logger.debug(f"Clone: {src.name} -> {short_path(dst)}")
                return

        except subprocess.CalledProcessError as e:
            # Reflink failed (e.g., cross-device), fall through to standard copy
            logger.debug(
                f"Reflink copy failed, falling back to standard copy: {e.stderr.decode() if e.stderr else str(e)}"
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # cp command not available or timed out
            pass

    # Standard copy fallback
    if preserve_metadata:
        shutil.copy2(src, dst)
    else:
        shutil.copy(src, dst)


def copy_file_safe(
    src: Union[str, Path],
    dst: Union[str, Path],
    preserve_metadata: bool = True,
) -> bool:
    """Copy file with error handling, return True on success."""
    try:
        copy_file(src, dst, preserve_metadata)
        return True
    except Exception as e:
        logger.error(f"Copy failed: {short_path(src)} -> {short_path(dst)}: {e}")
        return False


# MIME type to extension mapping
# Comprehensive mapping covering images, videos, and audio formats
MIME_TO_EXTENSION = {
    # Images
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/tiff": ".tiff",
    "image/bmp": ".bmp",
    "image/avif": ".avif",
    # Videos
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/x-m4v": ".m4v",
    # Audio
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
}

# Import extension definitions from utils.py (single source of truth)
# These are used for cross-category validation in detect_and_correct_extension()
from common.utils import (  # noqa: E402
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    AUDIO_EXTENSIONS,
)


def batch_correct_extensions(
    file_paths: list[Path],
) -> dict[str, str]:
    """Correct file extensions based on actual MIME type. Returns old->new path map."""
    corrections: dict[str, str] = {}
    corrected_count = 0

    for file_path in file_paths:
        if not file_path.exists():
            continue

        original_ext = file_path.suffix.lower()

        try:
            mime = magic.from_file(str(file_path), mime=True)
            detected_ext = MIME_TO_EXTENSION.get(mime)

            if detected_ext and detected_ext != original_ext:
                # Check for cross-category conversion (e.g., image -> video)
                original_is_image = original_ext in IMAGE_EXTENSIONS
                original_is_video = original_ext in VIDEO_EXTENSIONS
                original_is_audio = original_ext in AUDIO_EXTENSIONS
                detected_is_image = detected_ext in IMAGE_EXTENSIONS
                detected_is_video = detected_ext in VIDEO_EXTENSIONS
                detected_is_audio = detected_ext in AUDIO_EXTENSIONS

                # Only allow same-category corrections
                same_category = (
                    (original_is_image and detected_is_image)
                    or (original_is_video and detected_is_video)
                    or (original_is_audio and detected_is_audio)
                )

                if not same_category:
                    logger.debug(
                        f"Skipping cross-category extension correction for {file_path.name}: "
                        f"{original_ext} -> {detected_ext}"
                    )
                    continue

                # Build new path with corrected extension
                new_path = file_path.with_suffix(detected_ext)

                # Handle collision if new path already exists
                if new_path.exists() and new_path != file_path:
                    counter = 1
                    while new_path.exists():
                        new_path = file_path.with_stem(
                            f"{file_path.stem}_{counter}"
                        ).with_suffix(detected_ext)
                        counter += 1

                # Rename the file
                os.rename(file_path, new_path)
                corrections[str(file_path)] = str(new_path)
                corrected_count += 1

        except Exception as e:
            logger.debug(f"python-magic failed for {file_path.name}: {e}")

    if corrected_count > 0:
        logger.info(f"Corrected {corrected_count} file extension(s)")

    return corrections


def get_mime_type(file_path: Path) -> Optional[str]:
    """Get the MIME type of a file using python-magic."""
    try:
        return magic.from_file(str(file_path), mime=True)
    except Exception as e:
        logger.debug(f"Failed to get MIME type for {file_path}: {e}")
        return None
