#!/usr/bin/env python3
"""
Video Encoder Detection Module

Detects the best available H.265/HEVC encoder with quality-focused settings.
Prioritizes hardware acceleration when available, with fallback to software encoding.

Quality modes are preferred over fixed bitrate to maintain consistency across
multiple encoding passes.
"""

import logging
import platform
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def get_video_bitrate(video_path: Path) -> Optional[int]:
    """
    Get the bitrate of a video file in bits per second.

    Args:
        video_path: Path to the video file

    Returns:
        Bitrate in bits per second, or None if detection fails
    """
    try:
        # Try to get overall bitrate first (includes video + audio)
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=bit_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode == 0 and result.stdout.strip():
            try:
                bitrate = int(result.stdout.strip())
                if bitrate > 0:
                    logger.debug(
                        f"Detected overall bitrate: {bitrate / 1_000_000:.2f} Mbps"
                    )
                    return bitrate
            except ValueError:
                pass

        # Fallback: try to get video stream bitrate specifically
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=bit_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            stdin=subprocess.DEVNULL,
        )

        if result.returncode == 0 and result.stdout.strip():
            try:
                bitrate = int(result.stdout.strip())
                if bitrate > 0:
                    logger.debug(
                        f"Detected video stream bitrate: {bitrate / 1_000_000:.2f} Mbps"
                    )
                    return bitrate
            except ValueError:
                pass

        logger.debug(f"Could not detect bitrate for {video_path.name}")
        return None

    except Exception as e:
        logger.warning(f"Error detecting video bitrate: {e}")
        return None


class VideoEncoder:
    """Video encoder configuration and detection"""

    def __init__(self):
        self.encoder_args = []
        self.encoder_name = "unknown"
        self.is_hardware = False
        self._detect_encoder()

    def _check_encoder_available(self, encoder_name: str) -> bool:
        """
        Check if a specific encoder is available and actually works in ffmpeg.

        Tests by attempting to initialize the encoder with a test command.

        Args:
            encoder_name: Name of the encoder to check (e.g., 'hevc_nvenc')

        Returns:
            True if encoder is available and functional, False otherwise
        """
        try:
            # First check if encoder is listed
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-encoders"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )
            if encoder_name not in result.stdout:
                return False

            # Test if encoder actually works by attempting to initialize it
            # Create a 1-frame test encode to /dev/null
            test_cmd = [
                "ffmpeg",
                "-hide_banner",
            ]
            
            # VAAPI encoders need hardware device initialization
            if "_vaapi" in encoder_name:
                test_cmd.extend([
                    "-init_hw_device",
                    "vaapi=va:/dev/dri/renderD128",
                    "-hwaccel",
                    "vaapi",
                    "-hwaccel_output_format",
                    "vaapi",
                ])
            
            test_cmd.extend([
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=320x240:d=0.1",
            ])
            
            # VAAPI encoders need format conversion
            if "_vaapi" in encoder_name:
                test_cmd.extend([
                    "-vf",
                    "format=nv12,hwupload",
                ])
            
            test_cmd.extend([
                "-c:v",
                encoder_name,
                "-f",
                "null",
                "-",
            ])

            result = subprocess.run(
                test_cmd,
                capture_output=True,
                text=True,
                timeout=5,
                stdin=subprocess.DEVNULL,
            )

            # Check if the test succeeded (return code 0)
            if result.returncode == 0:
                return True

            # If failed, log why
            if "Cannot load" in result.stderr or "not found" in result.stderr:
                logger.debug(
                    f"Encoder {encoder_name} listed but cannot load: {result.stderr[:200]}"
                )

            return False

        except Exception as e:
            logger.warning(
                f"Failed to check encoder availability for {encoder_name}: {e}"
            )
            return False

    def _detect_encoder(self):
        """
        Detect the best available HEVC encoder.

        Priority order:
        1. NVIDIA NVENC (best hw quality + constant quality mode)
        2. VideoToolbox on macOS (good quality + quality mode)
        3. Intel QSV (good quality + global quality mode)
        4. AMD AMF (quality mode available)
        5. Software libx265 (best quality, slowest)
        """
        system = platform.system()

        # Try NVIDIA NVENC first - best hardware encoder with CQ mode
        if self._check_encoder_available("hevc_nvenc"):
            # NVENC supports constant quality mode (-cq) similar to CRF
            # Lower values = better quality (15-28 typical range)
            self.encoder_args = [
                "-c:v",
                "hevc_nvenc",
                "-preset",
                "p4",  # p1 (fastest) to p7 (slowest/best). p4 = balanced
                "-cq",
                "18",  # Constant quality mode (similar to CRF)
                "-b:v",
                "0",  # Disable bitrate limit for pure CQ mode
                "-spatial_aq",
                "1",  # Spatial adaptive quantization for better quality
                "-temporal_aq",
                "1",  # Temporal adaptive quantization
            ]
            self.encoder_name = "hevc_nvenc"
            self.is_hardware = True
            logger.debug(
                "Using NVIDIA NVENC hardware encoder with constant quality mode (CQ=18)"
            )
            return

        # Try VideoToolbox on macOS - native hardware acceleration
        if system == "Darwin" and self._check_encoder_available("hevc_videotoolbox"):
            # VideoToolbox quality-based encoding
            # q:v scale: 0 (best) to 100 (worst). 20-30 is good quality
            self.encoder_args = [
                "-c:v",
                "hevc_videotoolbox",
                "-q:v",
                "20",  # Quality-based encoding (lower = better)
                "-tag:v",
                "hvc1",  # Compatibility tag for better player support
                "-allow_sw",
                "1",  # Allow software fallback if needed
            ]
            self.encoder_name = "hevc_videotoolbox"
            self.is_hardware = True
            logger.debug(
                "Using VideoToolbox hardware encoder with quality mode (q:v=20)"
            )
            return

        # Try Intel VAAPI on Linux - native hardware acceleration
        if system == "Linux" and self._check_encoder_available("hevc_vaapi"):
            # VAAPI quality-based encoding
            # qp scale: 0 (best) to 51 (worst), similar to CRF
            # Note: Input args (-init_hw_device, -hwaccel) are handled separately in get_input_args()
            self.encoder_args = [
                "-c:v",
                "hevc_vaapi",
                "-qp",
                "18",  # Quality level (lower = better, similar to CRF)
            ]
            self.encoder_name = "hevc_vaapi"
            self.is_hardware = True
            logger.debug(
                "Using Intel VAAPI hardware encoder with constant quality mode (qp=18)"
            )
            return

        # Try Intel Quick Sync Video
        if self._check_encoder_available("hevc_qsv"):
            # QSV supports global_quality for quality-based encoding
            # 0 (best) to 51 (worst), similar to CRF
            self.encoder_args = [
                "-c:v",
                "hevc_qsv",
                "-preset",
                "medium",  # veryslow, slower, slow, medium, fast, faster, veryfast
                "-global_quality",
                "18",  # Quality level (lower = better)
                "-look_ahead",
                "1",  # Enable lookahead for better quality
            ]
            self.encoder_name = "hevc_qsv"
            self.is_hardware = True
            logger.debug("Using Intel QSV hardware encoder with global quality mode")
            return

        # Try AMD AMF (Windows)
        if self._check_encoder_available("hevc_amf"):
            # AMF quality-based encoding
            self.encoder_args = [
                "-c:v",
                "hevc_amf",
                "-quality",
                "quality",  # "speed", "balanced", or "quality"
                "-rc",
                "cqp",  # Constant quantization parameter mode
                "-qp_i",
                "18",  # I-frame QP
                "-qp_p",
                "18",  # P-frame QP
            ]
            self.encoder_name = "hevc_amf"
            self.is_hardware = True
            logger.debug("Using AMD AMF hardware encoder with constant QP mode")
            return

        # Fallback to software encoding (best quality, but slow)
        logger.debug(
            "No hardware HEVC encoder detected, using software encoder (libx265)"
        )
        self.encoder_args = [
            "-c:v",
            "libx265",
            "-preset",
            "medium",  # Faster than "slow", still good quality
            "-crf",
            "18",  # Constant rate factor (18 = near visually lossless)
            "-x265-params",
            "log-level=error",  # Reduce x265 log verbosity
        ]
        self.encoder_name = "libx265"
        self.is_hardware = False

    def get_encoder_args(self, target_bitrate: Optional[int] = None) -> list:
        """
        Get the encoder arguments for ffmpeg.

        Args:
            target_bitrate: Optional target bitrate in bits per second.
                          If provided and encoder supports it, will use bitrate mode
                          instead of quality mode for more predictable results.

        Returns:
            List of ffmpeg encoder arguments
        """
        # Return only output-side encoder args (input args handled separately)
        return self._get_output_encoder_args(target_bitrate)

    def get_input_args(self) -> list:
        """
        Get input-side arguments for ffmpeg (e.g., hwaccel, hardware device init).
        These must be placed BEFORE the -i input_file in the ffmpeg command.

        Returns:
            List of ffmpeg input arguments
        """
        if self.encoder_name == "hevc_vaapi":
            return [
                "-init_hw_device",
                "vaapi=va:/dev/dri/renderD128",
                "-hwaccel",
                "vaapi",
                "-hwaccel_output_format",
                "vaapi",
            ]
        # Other encoders don't need special input args
        return []

    def _get_output_encoder_args(self, target_bitrate: Optional[int] = None) -> list:
        """
        Get output-side encoder arguments for ffmpeg (codec, quality, bitrate, etc).
        These are placed AFTER the input file and BEFORE the output file.

        Args:
            target_bitrate: Optional target bitrate in bits per second.

        Returns:
            List of ffmpeg output encoder arguments
        """
        # If no target bitrate specified, return default quality-based args
        if target_bitrate is None:
            return self.encoder_args.copy()

        # Convert bitrate to Mbps and add 15% headroom
        target_mbps = target_bitrate / 1_000_000
        adjusted_mbps = target_mbps * 1.15

        logger.debug(
            f"Using dynamic bitrate: {target_mbps:.2f} Mbps → {adjusted_mbps:.2f} Mbps (with headroom)"
        )

        # Common bitrate arguments used by most encoders
        bitrate_args = [
            "-b:v", f"{adjusted_mbps:.1f}M",
            "-maxrate:v", f"{adjusted_mbps * 1.2:.1f}M",
            "-bufsize:v", f"{adjusted_mbps * 2:.1f}M",
        ]
        
        # Encoder-specific configurations
        encoder_configs = {
            "hevc_videotoolbox": [
                "-c:v", "hevc_videotoolbox",
                *bitrate_args,
                "-tag:v", "hvc1",
                "-allow_sw", "1",
            ],
            "hevc_vaapi": [
                "-c:v", "hevc_vaapi",
                *bitrate_args,
            ],
            "hevc_nvenc": [
                "-c:v", "hevc_nvenc",
                "-preset", "p4",
                *bitrate_args,
                "-spatial_aq", "1",
                "-temporal_aq", "1",
            ],
            "hevc_qsv": [
                "-c:v", "hevc_qsv",
                "-preset", "medium",
                *bitrate_args,
                "-look_ahead", "1",
            ],
            "hevc_amf": [
                "-c:v", "hevc_amf",
                "-quality", "quality",
                "-b:v", f"{adjusted_mbps:.1f}M",
                "-maxrate:v", f"{adjusted_mbps * 1.2:.1f}M",
            ],
            "libx265": [
                "-c:v", "libx265",
                "-preset", "medium",
                *bitrate_args,
                "-x265-params", "log-level=error",
            ],
        }
        
        # Return encoder-specific config or default args
        return encoder_configs.get(self.encoder_name, self.encoder_args.copy())

    def get_encoder_name(self) -> str:
        """
        Get the name of the detected encoder.

        Returns:
            Encoder name (e.g., 'hevc_nvenc', 'hevc_videotoolbox', 'libx265')
        """
        return self.encoder_name

    def is_hardware_accelerated(self) -> bool:
        """
        Check if the encoder uses hardware acceleration.

        Returns:
            True if hardware accelerated, False if software
        """
        return self.is_hardware


# Global encoder instance - detect once at module import
_global_encoder: Optional[VideoEncoder] = None


def get_video_encoder() -> VideoEncoder:
    """
    Get the global video encoder instance (singleton pattern).

    The encoder is detected once and reused for all encoding operations.

    Returns:
        VideoEncoder instance
    """
    global _global_encoder
    if _global_encoder is None:
        _global_encoder = VideoEncoder()
    return _global_encoder


def get_encoder_args(target_bitrate: Optional[int] = None) -> list:
    """
    Convenience function to get encoder arguments.

    Args:
        target_bitrate: Optional target bitrate in bits per second

    Returns:
        List of ffmpeg encoder arguments
    """
    return get_video_encoder().get_encoder_args(target_bitrate)


def get_encoder_input_args() -> list:
    """
    Convenience function to get encoder input arguments.
    These must be placed BEFORE the -i input_file in the ffmpeg command.

    Returns:
        List of ffmpeg input arguments (e.g., hwaccel, hardware device init)
    """
    return get_video_encoder().get_input_args()


def get_encoder_name() -> str:
    """
    Convenience function to get encoder name.

    Returns:
        Encoder name string
    """
    return get_video_encoder().get_encoder_name()


def is_hardware_accelerated() -> bool:
    """
    Convenience function to check if hardware acceleration is available.

    Returns:
        True if hardware accelerated, False if software
    """
    return get_video_encoder().is_hardware_accelerated()


def get_software_encoder_args(target_bitrate: Optional[int] = None) -> list:
    """
    Get software encoder arguments for fallback when hardware encoding fails.

    This function provides a software encoder configuration that can be used
    when hardware acceleration fails for specific videos.

    Args:
        target_bitrate: Optional target bitrate in bits per second

    Returns:
        List of ffmpeg encoder arguments for software encoding
    """
    if target_bitrate is None:
        # Use CRF mode for quality-based encoding
        return [
            "-c:v",
            "libx265",
            "-preset",
            "medium",
            "-crf",
            "23",  # Slightly lower quality than default to speed up fallback
            "-x265-params",
            "log-level=error",
        ]
    else:
        # Use bitrate mode
        target_mbps = target_bitrate / 1_000_000
        adjusted_mbps = target_mbps * 1.15
        return [
            "-c:v",
            "libx265",
            "-preset",
            "medium",
            "-b:v",
            f"{adjusted_mbps:.1f}M",
            "-maxrate:v",
            f"{adjusted_mbps * 1.2:.1f}M",
            "-bufsize:v",
            f"{adjusted_mbps * 2:.1f}M",
            "-x265-params",
            "log-level=error",
        ]


def is_hardware_acceleration_error(error_message: str) -> bool:
    """
    Check if an error message indicates a hardware acceleration failure.

    These errors suggest the hardware encoder failed to initialize for a specific
    video, even though it was detected as available during startup.

    Args:
        error_message: The error message from ffmpeg stderr

    Returns:
        True if the error is a hardware acceleration failure that can be retried
        with software encoding
    """
    hardware_error_patterns = [
        "hwaccel initialisation returned error",
        "Impossible to convert between the formats",
        "Failed setup for format vaapi",
        "Failed setup for format cuda",
        "Failed setup for format qsv",
        "hwaccel_retrieve_data failed",
        "No hw frames available",
        "hardware accelerator failed to decode picture",
    ]
    
    error_lower = error_message.lower()
    return any(pattern.lower() in error_lower for pattern in hardware_error_patterns)

