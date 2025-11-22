#!/usr/bin/env python3
"""
Dependency Checker for Media Processors

Provides centralized checking for system-level dependencies
(exiftool, ffmpeg) that are required by various processors.
"""

import subprocess


def check_exiftool() -> bool:
    """Check if exiftool is installed and available in PATH
    
    Returns:
        True if exiftool is available, False otherwise
    """
    try:
        subprocess.run(["exiftool", "-ver"], capture_output=True, check=True, stdin=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def check_ffmpeg() -> bool:
    """Check if ffmpeg is installed and available in PATH
    
    Returns:
        True if ffmpeg is available, False otherwise
    """
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True, stdin=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def print_exiftool_error() -> None:
    """Print installation instructions for exiftool"""
    print("ERROR: exiftool is not installed or not in PATH")
    print("Please install exiftool:")
    print("  macOS: brew install exiftool")
    print("  Linux: sudo apt-get install libimage-exiftool-perl")
    print("  Windows: Download from https://exiftool.org/")


def print_ffmpeg_error() -> None:
    """Print installation instructions for ffmpeg"""
    print("ERROR: ffmpeg is not installed or not in PATH")
    print("Please install ffmpeg:")
    print("  macOS: brew install ffmpeg")
    print("  Linux: sudo apt-get install ffmpeg")
    print("  Windows: Download from https://ffmpeg.org/")


def check_immich_cli() -> bool:
    """Check if Immich CLI is installed and available in PATH

    Returns:
        True if `immich` CLI is available, False otherwise
    """
    try:
        subprocess.run(["immich", "-V"], capture_output=True, check=True, stdin=subprocess.DEVNULL)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def print_immich_error() -> None:
    """Print installation instructions for Immich CLI"""
    print("ERROR: Immich CLI is not installed or not in PATH")
    print("Install via npm:")
    print("  npm i -g @immich/cli")
    print("Or use Docker:")
    print("  docker run -it ghcr.io/immich-app/immich-cli:latest")

