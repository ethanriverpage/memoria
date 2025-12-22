#!/usr/bin/env python3
"""Tests for common/media/encoder.py video encoder detection and configuration.

Tests cover:
- VideoEncoder class initialization
- Encoder detection logic
- Encoder argument generation
- Bitrate-based encoding configuration
- Hardware acceleration error detection
- Software fallback encoding
"""

from pathlib import Path
from unittest.mock import patch, MagicMock


from common.media.encoder import (
    VideoEncoder,
    get_encoder_args,
    get_encoder_input_args,
    get_encoder_name,
    get_software_encoder_args,
    get_video_bitrate,
    is_hardware_accelerated,
    is_hardware_acceleration_error,
)


class TestGetVideoBitrate:
    """Tests for get_video_bitrate function."""

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """Should return None for non-existent file."""
        missing = tmp_path / "missing.mp4"
        result = get_video_bitrate(missing)
        assert result is None

    def test_returns_none_on_ffprobe_failure(self, tmp_path: Path) -> None:
        """Should return None when ffprobe fails."""
        test_file = tmp_path / "test.mp4"
        test_file.write_bytes(b"not a video")

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""

        with patch("common.media.encoder.subprocess.run", return_value=mock_result):
            result = get_video_bitrate(test_file)

        assert result is None

    def test_returns_bitrate_on_success(self, tmp_path: Path) -> None:
        """Should return bitrate when ffprobe succeeds."""
        test_file = tmp_path / "test.mp4"
        test_file.write_bytes(b"video content")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "5000000\n"  # 5 Mbps

        with patch("common.media.encoder.subprocess.run", return_value=mock_result):
            result = get_video_bitrate(test_file)

        assert result == 5000000

    def test_handles_non_numeric_output(self, tmp_path: Path) -> None:
        """Should return None for non-numeric ffprobe output."""
        test_file = tmp_path / "test.mp4"
        test_file.write_bytes(b"content")

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "N/A\n"

        with patch("common.media.encoder.subprocess.run", return_value=mock_result):
            result = get_video_bitrate(test_file)

        assert result is None


class TestVideoEncoder:
    """Tests for VideoEncoder class."""

    def test_init_detects_encoder(self) -> None:
        """Should detect an encoder on initialization."""
        with patch.object(VideoEncoder, "_check_encoder_available", return_value=False):
            encoder = VideoEncoder()

        # Should fall back to software encoder
        assert encoder.encoder_name == "libx265"
        assert encoder.is_hardware is False

    def test_nvenc_detection(self) -> None:
        """Should detect NVIDIA NVENC when available."""

        def mock_check(name: str) -> bool:
            return name == "hevc_nvenc"

        with patch.object(VideoEncoder, "_check_encoder_available", side_effect=mock_check):
            encoder = VideoEncoder()

        assert encoder.encoder_name == "hevc_nvenc"
        assert encoder.is_hardware is True

    def test_videotoolbox_detection_on_macos(self) -> None:
        """Should detect VideoToolbox on macOS."""

        def mock_check(name: str) -> bool:
            return name == "hevc_videotoolbox"

        with patch.object(
            VideoEncoder, "_check_encoder_available", side_effect=mock_check
        ), patch("common.media.encoder.platform.system", return_value="Darwin"):
            encoder = VideoEncoder()

        assert encoder.encoder_name == "hevc_videotoolbox"
        assert encoder.is_hardware is True

    def test_vaapi_detection_on_linux(self) -> None:
        """Should detect VAAPI on Linux."""

        def mock_check(name: str) -> bool:
            return name == "hevc_vaapi"

        with patch.object(
            VideoEncoder, "_check_encoder_available", side_effect=mock_check
        ), patch("common.media.encoder.platform.system", return_value="Linux"):
            encoder = VideoEncoder()

        assert encoder.encoder_name == "hevc_vaapi"
        assert encoder.is_hardware is True

    def test_qsv_detection(self) -> None:
        """Should detect Intel QSV when available."""

        def mock_check(name: str) -> bool:
            return name == "hevc_qsv"

        with patch.object(VideoEncoder, "_check_encoder_available", side_effect=mock_check):
            encoder = VideoEncoder()

        assert encoder.encoder_name == "hevc_qsv"
        assert encoder.is_hardware is True

    def test_amf_detection(self) -> None:
        """Should detect AMD AMF when available."""

        def mock_check(name: str) -> bool:
            return name == "hevc_amf"

        with patch.object(VideoEncoder, "_check_encoder_available", side_effect=mock_check):
            encoder = VideoEncoder()

        assert encoder.encoder_name == "hevc_amf"
        assert encoder.is_hardware is True

    def test_priority_order_nvenc_first(self) -> None:
        """Should prefer NVENC over other hardware encoders."""

        def mock_check(name: str) -> bool:
            # All encoders available
            return name in ["hevc_nvenc", "hevc_qsv", "hevc_amf"]

        with patch.object(VideoEncoder, "_check_encoder_available", side_effect=mock_check):
            encoder = VideoEncoder()

        # NVENC should win
        assert encoder.encoder_name == "hevc_nvenc"

    def test_software_fallback(self) -> None:
        """Should fall back to libx265 when no hardware available."""
        with patch.object(VideoEncoder, "_check_encoder_available", return_value=False):
            encoder = VideoEncoder()

        assert encoder.encoder_name == "libx265"
        assert encoder.is_hardware is False
        assert "-crf" in encoder.encoder_args


class TestEncoderArgs:
    """Tests for encoder argument generation."""

    def test_get_encoder_args_quality_mode(self) -> None:
        """Should return quality-based args without bitrate."""
        with patch.object(VideoEncoder, "_check_encoder_available", return_value=False):
            encoder = VideoEncoder()
            args = encoder.get_encoder_args()

        assert "-c:v" in args
        assert "libx265" in args
        assert "-crf" in args

    def test_get_encoder_args_with_bitrate(self) -> None:
        """Should return bitrate-based args when bitrate provided."""
        with patch.object(VideoEncoder, "_check_encoder_available", return_value=False):
            encoder = VideoEncoder()
            args = encoder.get_encoder_args(target_bitrate=10_000_000)  # 10 Mbps

        assert "-b:v" in args
        # Should include 15% headroom
        assert any("11" in arg for arg in args)  # ~11.5 Mbps with headroom

    def test_get_input_args_vaapi(self) -> None:
        """Should return VAAPI init args for VAAPI encoder."""

        def mock_check(name: str) -> bool:
            return name == "hevc_vaapi"

        with patch.object(
            VideoEncoder, "_check_encoder_available", side_effect=mock_check
        ), patch("common.media.encoder.platform.system", return_value="Linux"):
            encoder = VideoEncoder()
            input_args = encoder.get_input_args()

        assert "-init_hw_device" in input_args
        assert "-hwaccel" in input_args
        assert "vaapi" in input_args

    def test_get_input_args_software(self) -> None:
        """Should return empty list for software encoder."""
        with patch.object(VideoEncoder, "_check_encoder_available", return_value=False):
            encoder = VideoEncoder()
            input_args = encoder.get_input_args()

        assert input_args == []


class TestSoftwareEncoderArgs:
    """Tests for get_software_encoder_args function."""

    def test_quality_mode_without_bitrate(self) -> None:
        """Should use CRF mode without bitrate."""
        args = get_software_encoder_args()

        assert "-c:v" in args
        assert "libx265" in args
        assert "-crf" in args

    def test_bitrate_mode_with_bitrate(self) -> None:
        """Should use bitrate mode with target bitrate."""
        args = get_software_encoder_args(target_bitrate=5_000_000)

        assert "-b:v" in args
        assert "-maxrate:v" in args
        assert "-bufsize:v" in args

    def test_includes_log_level_error(self) -> None:
        """Should suppress x265 logging."""
        args = get_software_encoder_args()

        assert "-x265-params" in args
        assert any("log-level=error" in arg for arg in args)


class TestHardwareAccelerationError:
    """Tests for is_hardware_acceleration_error function."""

    def test_detects_hwaccel_init_error(self) -> None:
        """Should detect hwaccel initialization error."""
        error = "hwaccel initialisation returned error"
        assert is_hardware_acceleration_error(error) is True

    def test_detects_format_conversion_error(self) -> None:
        """Should detect format conversion error."""
        error = "Impossible to convert between the formats"
        assert is_hardware_acceleration_error(error) is True

    def test_detects_vaapi_setup_error(self) -> None:
        """Should detect VAAPI setup failure."""
        error = "Failed setup for format vaapi"
        assert is_hardware_acceleration_error(error) is True

    def test_detects_cuda_setup_error(self) -> None:
        """Should detect CUDA setup failure."""
        error = "Failed setup for format cuda"
        assert is_hardware_acceleration_error(error) is True

    def test_detects_hwaccel_retrieve_error(self) -> None:
        """Should detect hwaccel data retrieval failure."""
        error = "hwaccel_retrieve_data failed"
        assert is_hardware_acceleration_error(error) is True

    def test_returns_false_for_other_errors(self) -> None:
        """Should return False for non-hardware errors."""
        error = "File not found"
        assert is_hardware_acceleration_error(error) is False

    def test_case_insensitive(self) -> None:
        """Should be case-insensitive."""
        error = "HWACCEL INITIALISATION RETURNED ERROR"
        assert is_hardware_acceleration_error(error) is True


class TestGlobalEncoderFunctions:
    """Tests for global encoder accessor functions."""

    def test_get_encoder_name_returns_string(self) -> None:
        """Should return encoder name as string."""
        with patch(
            "common.media.encoder.get_video_encoder"
        ) as mock_get:
            mock_encoder = MagicMock()
            mock_encoder.get_encoder_name.return_value = "hevc_nvenc"
            mock_get.return_value = mock_encoder

            name = get_encoder_name()

        assert name == "hevc_nvenc"

    def test_is_hardware_accelerated_returns_bool(self) -> None:
        """Should return boolean for hardware acceleration status."""
        with patch(
            "common.media.encoder.get_video_encoder"
        ) as mock_get:
            mock_encoder = MagicMock()
            mock_encoder.is_hardware_accelerated.return_value = True
            mock_get.return_value = mock_encoder

            result = is_hardware_accelerated()

        assert result is True

    def test_get_encoder_args_delegates_to_instance(self) -> None:
        """Should delegate to VideoEncoder instance."""
        with patch(
            "common.media.encoder.get_video_encoder"
        ) as mock_get:
            mock_encoder = MagicMock()
            mock_encoder.get_encoder_args.return_value = ["-c:v", "libx265"]
            mock_get.return_value = mock_encoder

            args = get_encoder_args()

        assert args == ["-c:v", "libx265"]

    def test_get_encoder_input_args_delegates_to_instance(self) -> None:
        """Should delegate to VideoEncoder instance."""
        with patch(
            "common.media.encoder.get_video_encoder"
        ) as mock_get:
            mock_encoder = MagicMock()
            mock_encoder.get_input_args.return_value = ["-hwaccel", "vaapi"]
            mock_get.return_value = mock_encoder

            args = get_encoder_input_args()

        assert args == ["-hwaccel", "vaapi"]


class TestEncoderCheckAvailable:
    """Tests for encoder availability checking."""

    def test_check_encoder_returns_false_when_not_listed(self) -> None:
        """Should return False if encoder not in ffmpeg encoders list."""
        with patch.object(VideoEncoder, "_check_encoder_available", return_value=False):
            encoder = VideoEncoder()

        # Should fall back to software
        assert encoder.encoder_name == "libx265"

    def test_encoder_name_set_correctly_when_available(self) -> None:
        """Should set encoder name when detected."""

        def mock_check(name: str) -> bool:
            return name == "hevc_nvenc"

        with patch.object(VideoEncoder, "_check_encoder_available", side_effect=mock_check):
            encoder = VideoEncoder()

        assert encoder.encoder_name == "hevc_nvenc"
        assert encoder.is_hardware is True


class TestEncoderBitrateConfigs:
    """Tests for encoder-specific bitrate configurations."""

    def test_nvenc_bitrate_config(self) -> None:
        """Should generate NVENC-specific bitrate config."""

        def mock_check(name: str) -> bool:
            return name == "hevc_nvenc"

        with patch.object(VideoEncoder, "_check_encoder_available", side_effect=mock_check):
            encoder = VideoEncoder()
            args = encoder.get_encoder_args(target_bitrate=10_000_000)

        assert "hevc_nvenc" in args
        assert "-b:v" in args
        assert "-spatial_aq" in args

    def test_libx265_bitrate_config(self) -> None:
        """Should generate libx265 bitrate config."""
        with patch.object(VideoEncoder, "_check_encoder_available", return_value=False):
            encoder = VideoEncoder()
            args = encoder.get_encoder_args(target_bitrate=10_000_000)

        assert "libx265" in args
        assert "-b:v" in args
        assert "-x265-params" in args

