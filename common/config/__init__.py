"""
Configuration utilities for media processors.

This package provides:
- Environment variable loading from .env files
- Runtime configuration (worker count, boolean env parsing)
- System dependency checking (exiftool, ffmpeg, etc.)
"""

from common.config.env import load_dotenv_file
from common.config.runtime import (
    default_worker_count,
    parse_bool_env,
    should_cleanup_preprocessing,
)
from common.config.dependencies import (
    check_exiftool,
    check_ffmpeg,
    check_immich_cli,
    check_pillow,
    print_exiftool_error,
    print_ffmpeg_error,
    print_immich_error,
    print_pillow_error,
)

__all__ = [
    # Environment
    "load_dotenv_file",
    # Runtime
    "default_worker_count",
    "parse_bool_env",
    "should_cleanup_preprocessing",
    # Dependencies
    "check_exiftool",
    "check_ffmpeg",
    "check_immich_cli",
    "check_pillow",
    "print_exiftool_error",
    "print_ffmpeg_error",
    "print_immich_error",
    "print_pillow_error",
]
