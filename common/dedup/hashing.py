#!/usr/bin/env python3
"""
Perceptual Hashing for Cross-Processor Deduplication

Implements perceptual hashing (PHash) for images and videos to detect
visually similar media that may have been re-encoded or slightly modified.

Uses the perception library for fast perceptual hashing with PIL backend
for HEIC/HEIF support via pillow-heif.
"""

import base64
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple, List, Union

import xxhash

try:
    from perception import hashers
    from PIL import Image

    PERCEPTION_AVAILABLE = True
except ImportError:
    hashers = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment,misc]
    PERCEPTION_AVAILABLE = False

# Register HEIC/HEIF support if available
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIC_AVAILABLE = True
except ImportError:
    HEIC_AVAILABLE = False

# Configure perception to use PIL backend for HEIC support
if PERCEPTION_AVAILABLE:
    try:
        from perception import tools

        tools.set_backend("PIL")
    except (ImportError, AttributeError):
        # Older versions may not have set_backend or may use different config
        pass

logger = logging.getLogger(__name__)

# Default dhash size (16x16 grid = 256-bit hash)
DEFAULT_HASH_SIZE = 16

# Import extension definitions from utils.py (single source of truth)
from common.utils import (
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    LIVE_PHOTO_IMAGE_EXTENSIONS,
    LIVE_PHOTO_VIDEO_EXTENSIONS,
)


@dataclass
class LivePhotoEntry:
    """Represents a Live Photo pair (image + video) for deduplication.

    Live Photos are treated as a single unit for deduplication purposes.
    The content hash and perceptual hash are computed from the image only,
    ensuring that the same Live Photo captured on different devices or
    exported from different platforms is properly deduplicated.

    Attributes:
        image_path: Path to the image component (HEIC/JPG).
        video_path: Path to the video component (MOV), or None if not found.
        content_hash: xxHash64 of the image file only.
        perceptual_hash: dhash of the image, or None if not computed.
        image_timestamp: Capture timestamp from the image, if available.
    """

    image_path: Path
    video_path: Optional[Path] = None
    content_hash: str = ""
    perceptual_hash: Optional[str] = None
    image_timestamp: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class VideoInfo:
    """Container for video stream info extracted in a single FFprobe call.

    Used by _get_video_info() to consolidate has_video_stream and dimensions
    detection into one subprocess call for better performance.

    Attributes:
        has_video: True if the file contains a video stream.
        width: Video width in pixels, or None if no video stream.
        height: Video height in pixels, or None if no video stream.
    """

    has_video: bool
    width: Optional[int] = None
    height: Optional[int] = None


def is_perceptual_hash_available() -> bool:
    """Check if perceptual hashing dependencies are available.

    Returns:
        True if perception and Pillow are installed.
    """
    return PERCEPTION_AVAILABLE


def compute_content_hash(file_path: Union[str, Path]) -> str:
    """Compute xxHash64 content hash for a file.

    This is the canonical content hash used for exact-match deduplication.
    xxHash64 is extremely fast (GB/s throughput) while providing excellent
    collision resistance for deduplication purposes.

    Args:
        file_path: Path to the file to hash.

    Returns:
        Hex string of the xxHash64 digest.

    Raises:
        FileNotFoundError: If the file doesn't exist.
        IOError: If the file can't be read.
    """
    hasher = xxhash.xxh64()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_image_hash(
    image_path: Path,
    hash_size: int = DEFAULT_HASH_SIZE,
) -> Optional[str]:
    """Compute perceptual hash (PHash) for an image.

    Uses perception library's PHash which is robust against scaling, aspect
    ratio changes, and minor color adjustments. Faster than imagehash.

    Args:
        image_path: Path to the image file.
        hash_size: Size of the hash grid (default 16 for 256-bit hash).

    Returns:
        Hex string of the perceptual hash, or None if hashing failed.
    """
    if not PERCEPTION_AVAILABLE:
        logger.warning("perception not available, skipping perceptual hash")
        return None

    # These are guaranteed non-None when PERCEPTION_AVAILABLE is True
    assert Image is not None
    assert hashers is not None

    try:
        # Create hasher with specified hash size
        hasher = hashers.PHash(hash_size=hash_size)

        with Image.open(image_path) as img:
            # Downsample early to reduce memory usage, especially for large
            # HEIC images which can consume 50MB+ when fully decoded.
            target_size = (hash_size + 1) * 8  # Small buffer for quality
            if img.width > target_size or img.height > target_size:
                # Calculate size maintaining aspect ratio
                ratio = min(target_size / img.width, target_size / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                # draft() modifies image in-place during decode (memory efficient)
                # Only works for JPEG; for other formats we resize after load
                if image_path.suffix.lower() in (".jpg", ".jpeg"):
                    img.draft("RGB", new_size)
                    img.load()
                else:
                    # For non-JPEG (HEIC, PNG, etc.), load then resize
                    img.load()
                    img = img.resize(new_size, Image.Resampling.BILINEAR)

            # Convert to RGB if necessary (handles RGBA, palette images, etc.)
            if img.mode not in ("RGB", "L"):
                # For palette images with transparency, convert to RGBA first
                # to avoid PIL warning about transparency expressed in bytes
                if img.mode == "P" and "transparency" in img.info:
                    img = img.convert("RGBA").convert("RGB")
                else:
                    img = img.convert("RGB")

            # Compute perceptual hash using perception library
            # perception accepts PIL Image objects directly
            hash_b64 = hasher.compute(img)

            # perception returns base64-encoded hashes; convert to hex for
            # compatibility with existing hamming_distance/hash_similarity functions
            hash_bytes = base64.b64decode(hash_b64)
            return hash_bytes.hex()

    except Exception as e:
        logger.debug(f"Failed to compute perceptual hash for {image_path}: {e}")
        return None


def compute_image_hash_with_dims(
    image_path: Path,
    hash_size: int = DEFAULT_HASH_SIZE,
) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    """Compute perceptual hash AND dimensions in a single image open.

    This is an optimized version of compute_image_hash() that also returns
    the image dimensions, avoiding the need for a separate Image.open() call
    to get dimensions.

    Args:
        image_path: Path to the image file.
        hash_size: Size of the hash grid (default 16 for 256-bit hash).

    Returns:
        Tuple of (perceptual_hash, dimensions) where:
        - perceptual_hash: Hex string of the hash, or None if hashing failed.
        - dimensions: Tuple of (width, height), or None if detection failed.
    """
    if not PERCEPTION_AVAILABLE:
        logger.warning("perception not available, skipping perceptual hash")
        return (None, None)

    # These are guaranteed non-None when PERCEPTION_AVAILABLE is True
    assert Image is not None
    assert hashers is not None

    try:
        # Create hasher with specified hash size
        hasher = hashers.PHash(hash_size=hash_size)

        with Image.open(image_path) as img:
            # Capture original dimensions BEFORE any resizing
            original_dimensions = img.size

            # Downsample early to reduce memory usage, especially for large
            # HEIC images which can consume 50MB+ when fully decoded.
            target_size = (hash_size + 1) * 8  # Small buffer for quality
            if img.width > target_size or img.height > target_size:
                # Calculate size maintaining aspect ratio
                ratio = min(target_size / img.width, target_size / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                # draft() modifies image in-place during decode (memory efficient)
                # Only works for JPEG; for other formats we resize after load
                if image_path.suffix.lower() in (".jpg", ".jpeg"):
                    img.draft("RGB", new_size)
                    img.load()
                else:
                    # For non-JPEG (HEIC, PNG, etc.), load then resize
                    img.load()
                    img = img.resize(new_size, Image.Resampling.BILINEAR)

            # Convert to RGB if necessary (handles RGBA, palette images, etc.)
            if img.mode not in ("RGB", "L"):
                # For palette images with transparency, convert to RGBA first
                # to avoid PIL warning about transparency expressed in bytes
                if img.mode == "P" and "transparency" in img.info:
                    img = img.convert("RGBA").convert("RGB")
                else:
                    img = img.convert("RGB")

            # Compute perceptual hash using perception library
            # perception accepts PIL Image objects directly
            hash_b64 = hasher.compute(img)

            # perception returns base64-encoded hashes; convert to hex for
            # compatibility with existing hamming_distance/hash_similarity functions
            hash_bytes = base64.b64decode(hash_b64)
            return (hash_bytes.hex(), original_dimensions)

    except Exception as e:
        logger.debug(f"Failed to compute perceptual hash for {image_path}: {e}")
        return (None, None)


def _get_video_info(video_path: Path) -> Optional[VideoInfo]:
    """Get video stream presence and dimensions in ONE ffprobe call.

    This consolidates has_video_stream() and get_video_dimensions() into a
    single subprocess call for better performance.

    Args:
        video_path: Path to the video file.

    Returns:
        VideoInfo with has_video and dimensions, or None if ffprobe failed.
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type,width,height",
            "-of",
            "csv=p=0",
            str(video_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=10,
        )

        if result.returncode != 0:
            return VideoInfo(has_video=False)

        output = result.stdout.decode().strip()
        if not output:
            return VideoInfo(has_video=False)

        # Output format: "codec_type,width,height" e.g., "video,1920,1080"
        # Take only the first line (first video stream)
        first_line = output.split("\n")[0].strip()
        parts = first_line.split(",")

        # Check if this is a video stream
        if len(parts) < 1 or "video" not in parts[0].lower():
            return VideoInfo(has_video=False)

        # Extract dimensions if available
        width = None
        height = None
        if len(parts) >= 3:
            try:
                width = int(parts[1])
                height = int(parts[2])
            except (ValueError, TypeError):
                pass

        return VideoInfo(has_video=True, width=width, height=height)

    except Exception as e:
        logger.debug(f"Failed to get video info for {video_path}: {e}")
        return None


def _extract_frame_unchecked(
    video_path: Path,
    position: float = 0.0,
) -> Optional[Path]:
    """Extract a frame from a video WITHOUT checking has_video_stream first.

    This is an internal helper used when we've already verified the video
    stream exists (e.g., via _get_video_info). Avoids a redundant ffprobe call.

    Args:
        video_path: Path to the video file.
        position: Position in seconds to extract frame.

    Returns:
        Path to temporary frame image, or None if extraction failed.
    """
    try:
        # Create temporary file for frame
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            frame_path = Path(tmp.name)

        # Use ffmpeg to extract frame with hardware acceleration
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-hwaccel",
            "auto",  # Auto-detect best hardware decoder
            "-ss",
            str(position),  # Seek position (before -i for fast seeking)
            "-i",
            str(video_path),
            "-vframes",
            "1",  # Extract 1 frame
            "-q:v",
            "2",  # Quality level
            "-f",
            "image2",
            str(frame_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=30,
        )

        if result.returncode != 0:
            logger.debug(
                f"ffmpeg frame extraction failed for {video_path}: "
                f"{result.stderr.decode()[:200]}"
            )
            try:
                frame_path.unlink()
            except Exception:
                pass
            return None

        # Verify frame was created
        if not frame_path.exists() or frame_path.stat().st_size == 0:
            logger.debug(f"Frame extraction produced no output for {video_path}")
            try:
                frame_path.unlink()
            except Exception:
                pass
            return None

        return frame_path

    except subprocess.TimeoutExpired:
        logger.warning(f"ffmpeg timed out extracting frame from {video_path}")
        return None
    except FileNotFoundError:
        logger.warning("ffmpeg not found, cannot extract video frames")
        return None
    except Exception as e:
        logger.debug(f"Failed to extract frame from {video_path}: {e}")
        return None


def compute_video_hash_with_dims(
    video_path: Path,
    hash_size: int = DEFAULT_HASH_SIZE,
    frame_position: float = 0.0,
) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    """Compute video hash AND dimensions with minimal subprocess calls.

    This is an optimized version that makes ONE ffprobe call to get both
    has_video_stream and dimensions, then extracts a frame for hashing.
    Reduces subprocess calls from 3 to 2 per video file.

    Args:
        video_path: Path to the video file.
        hash_size: Size of the hash grid (default 16 for 256-bit hash).
        frame_position: Position in seconds to extract frame (default 0.0).

    Returns:
        Tuple of (perceptual_hash, dimensions) where:
        - perceptual_hash: Hex string of the hash, or None if hashing failed.
        - dimensions: Tuple of (width, height), or None if no video stream.
    """
    if not PERCEPTION_AVAILABLE:
        logger.warning("perception not available, skipping perceptual hash")
        return (None, None)

    # Single ffprobe call to get has_video + dimensions
    video_info = _get_video_info(video_path)
    if video_info is None or not video_info.has_video:
        return (None, None)

    dimensions = None
    if video_info.width is not None and video_info.height is not None:
        dimensions = (video_info.width, video_info.height)

    # Extract frame (skip redundant has_video_stream check)
    frame_path = _extract_frame_unchecked(video_path, frame_position)
    if frame_path is None:
        return (None, dimensions)

    try:
        # Compute hash on extracted frame
        hash_val = compute_image_hash(frame_path, hash_size)
        return (hash_val, dimensions)
    finally:
        # Clean up temporary frame file
        try:
            frame_path.unlink()
        except Exception:
            pass


def compute_perceptual_hash_with_dims(
    file_path: Path,
    hash_size: int = DEFAULT_HASH_SIZE,
) -> Tuple[Optional[str], Optional[Tuple[int, int]]]:
    """Compute perceptual hash AND dimensions for any supported media file.

    This is an optimized version of compute_perceptual_hash() that also returns
    the media dimensions, avoiding the need for a separate file open to get
    dimensions. For images, this saves one Image.open() call. For videos,
    this reduces ffprobe calls from 3 to 2.

    Args:
        file_path: Path to the media file.
        hash_size: Size of the hash grid (default 16 for 256-bit hash).

    Returns:
        Tuple of (perceptual_hash, dimensions) where:
        - perceptual_hash: Hex string of the hash, or None if hashing failed.
        - dimensions: Tuple of (width, height), or None if detection failed.
    """
    suffix = file_path.suffix.lower()

    if suffix in IMAGE_EXTENSIONS:
        return compute_image_hash_with_dims(file_path, hash_size)
    elif suffix in VIDEO_EXTENSIONS:
        return compute_video_hash_with_dims(file_path, hash_size)
    else:
        logger.debug(f"Unsupported file type for perceptual hashing: {suffix}")
        return (None, None)


def hamming_distance(hash1: str, hash2: str) -> int:
    """Calculate Hamming distance between two hash strings.

    Args:
        hash1: First hex hash string.
        hash2: Second hex hash string.

    Returns:
        Number of differing bits.

    Raises:
        ValueError: If hashes have different lengths.
    """
    if len(hash1) != len(hash2):
        raise ValueError(f"Hash lengths differ: {len(hash1)} vs {len(hash2)}")

    # Convert hex to integers and XOR
    val1 = int(hash1, 16)
    val2 = int(hash2, 16)
    xor = val1 ^ val2

    # Count differing bits
    return bin(xor).count("1")


def hash_similarity(hash1: str, hash2: str) -> float:
    """Calculate similarity ratio between two perceptual hashes.

    Args:
        hash1: First hex hash string.
        hash2: Second hex hash string.

    Returns:
        Similarity ratio from 0.0 (completely different) to 1.0 (identical).

    Raises:
        ValueError: If hashes have different lengths.
    """
    if not hash1 or not hash2:
        return 0.0

    if len(hash1) != len(hash2):
        raise ValueError(f"Hash lengths differ: {len(hash1)} vs {len(hash2)}")

    total_bits = len(hash1) * 4  # Each hex char = 4 bits
    distance = hamming_distance(hash1, hash2)
    return 1.0 - (distance / total_bits)


def are_similar(
    hash1: str,
    hash2: str,
    threshold: float = 0.92,
) -> bool:
    """Check if two perceptual hashes indicate similar images.

    Args:
        hash1: First hex hash string.
        hash2: Second hex hash string.
        threshold: Minimum similarity ratio to be considered similar.

    Returns:
        True if hashes indicate similar images.
    """
    if not hash1 or not hash2:
        return False

    try:
        return hash_similarity(hash1, hash2) >= threshold
    except ValueError:
        return False


def get_image_dimensions(file_path: Path) -> Optional[Tuple[int, int]]:
    """Get dimensions of an image file.

    Args:
        file_path: Path to the image file.

    Returns:
        Tuple of (width, height) or None if detection failed.
    """
    if not PERCEPTION_AVAILABLE:
        return None

    # Guaranteed non-None when PERCEPTION_AVAILABLE is True
    assert Image is not None

    try:
        with Image.open(file_path) as img:
            return img.size
    except Exception as e:
        logger.debug(f"Failed to get dimensions for {file_path}: {e}")
        return None


def get_video_dimensions(video_path: Path) -> Optional[Tuple[int, int]]:
    """Get dimensions of a video file using ffprobe.

    Args:
        video_path: Path to the video file.

    Returns:
        Tuple of (width, height) or None if detection failed.
    """
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0",
            str(video_path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=10,
        )

        if result.returncode == 0:
            output = result.stdout.decode().strip()
            if output:
                # Handle multiple video streams or multiline output
                # Take only the first line (first video stream)
                first_line = output.split("\n")[0].strip()
                if "," in first_line:
                    parts = first_line.split(",")
                    # Ensure we have exactly 2 numeric parts
                    if len(parts) >= 2:
                        try:
                            width = int(parts[0])
                            height = int(parts[1])
                            return (width, height)
                        except ValueError:
                            pass  # Fall through to return None

    except Exception as e:
        logger.debug(f"Failed to get video dimensions for {video_path}: {e}")

    return None


def get_media_dimensions(file_path: Path) -> Optional[Tuple[int, int]]:
    """Get dimensions of any supported media file.

    Args:
        file_path: Path to the media file.

    Returns:
        Tuple of (width, height) or None if detection failed.
    """
    suffix = file_path.suffix.lower()

    if suffix in IMAGE_EXTENSIONS:
        return get_image_dimensions(file_path)
    elif suffix in VIDEO_EXTENSIONS:
        return get_video_dimensions(file_path)
    else:
        return None


def is_live_photo_pair(
    image_path: Path,
    video_path: Path,
) -> bool:
    """Check if an image and video form a Live Photo pair.

    Live Photos consist of a HEIC/JPG image with an accompanying MOV video.
    They are matched by:
    1. Same base filename (before extension)
    2. Video has .mov extension
    3. Image has .heic/.heif/.jpg/.jpeg extension

    Args:
        image_path: Path to the potential image component.
        video_path: Path to the potential video component.

    Returns:
        True if the files form a Live Photo pair.
    """
    image_path = Path(image_path)
    video_path = Path(video_path)

    # Check extensions
    if image_path.suffix.lower() not in LIVE_PHOTO_IMAGE_EXTENSIONS:
        return False
    if video_path.suffix.lower() not in LIVE_PHOTO_VIDEO_EXTENSIONS:
        return False

    # Check base filename match (case-insensitive)
    image_stem = image_path.stem.lower()
    video_stem = video_path.stem.lower()

    if image_stem != video_stem:
        return False

    return True


def find_live_photo_video(
    image_path: Path,
    search_dir: Optional[Path] = None,
) -> Optional[Path]:
    """Find the video component of a Live Photo given the image.

    Searches for a MOV file with the same base name as the image.

    Args:
        image_path: Path to the image component.
        search_dir: Directory to search in. Defaults to image's parent dir.

    Returns:
        Path to the video component, or None if not found.
    """
    image_path = Path(image_path)

    # Check if this is a potential Live Photo image
    if image_path.suffix.lower() not in LIVE_PHOTO_IMAGE_EXTENSIONS:
        return None

    # Search in provided directory or image's directory
    if search_dir is None:
        search_dir = image_path.parent

    # Look for MOV with same stem
    for ext in LIVE_PHOTO_VIDEO_EXTENSIONS:
        video_path = search_dir / f"{image_path.stem}{ext}"
        if video_path.exists():
            return video_path

        # Also check case variations
        video_path = search_dir / f"{image_path.stem}{ext.upper()}"
        if video_path.exists():
            return video_path

    return None


def find_live_photo_pairs(
    directory: Path,
    recursive: bool = False,
) -> List[LivePhotoEntry]:
    """Find all Live Photo pairs in a directory.

    Scans the directory for image files and matches them with their
    video components to create LivePhotoEntry objects.

    Args:
        directory: Directory to scan.
        recursive: Whether to scan subdirectories.

    Returns:
        List of LivePhotoEntry objects for each Live Photo found.
    """
    directory = Path(directory)
    pairs = []
    seen_videos = set()

    # Collect all files
    if recursive:
        all_files = list(directory.rglob("*"))
    else:
        all_files = list(directory.iterdir())

    # Filter to image files
    image_files = [
        f
        for f in all_files
        if f.is_file() and f.suffix.lower() in LIVE_PHOTO_IMAGE_EXTENSIONS
    ]

    for image_path in image_files:
        video_path = find_live_photo_video(image_path)

        if video_path and video_path not in seen_videos:
            seen_videos.add(video_path)
            pairs.append(
                LivePhotoEntry(
                    image_path=image_path,
                    video_path=video_path,
                )
            )

    return pairs


def get_live_photo_content_hash(live_photo: LivePhotoEntry) -> Optional[str]:
    """Compute content hash for a Live Photo (image only).

    Args:
        live_photo: LivePhotoEntry to hash.

    Returns:
        xxHash64 hex digest of the image file.
    """
    try:
        hasher = xxhash.xxh64()
        with open(live_photo.image_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hasher.update(chunk)
        return hasher.hexdigest()
    except Exception as e:
        logger.warning(f"Failed to hash Live Photo {live_photo.image_path}: {e}")
        return None
