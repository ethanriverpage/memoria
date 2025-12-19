#!/usr/bin/env python3
"""
Output path configuration for Platform/User/Service structure.

Processors define their output metadata via OutputInfo, and this module
provides the mechanism to build consistent output paths.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from common.utils import extract_username_from_export_dir


@dataclass(frozen=True)
class OutputInfo:
    """Processor output configuration for Platform/User/Service structure.

    Attributes:
        platform: Platform name (e.g., "Google", "Instagram", "iMessage")
        service: Service name (e.g., "photos", "messages") or None for single-service platforms
        username_prefix: Prefix for username extraction from export directory name
                        (e.g., "google", "instagram", "imessage")
    """

    platform: str
    service: Optional[str]
    username_prefix: str


def build_processor_output_path(
    output_info: OutputInfo,
    input_dir: str,
    base_output_dir: str,
) -> str:
    """Build Platform/User output path using processor metadata.

    Args:
        output_info: Processor's output configuration
        input_dir: Path to export directory (for username extraction)
        base_output_dir: Base output directory from args

    Returns:
        Path as: base_output_dir/Platform/username
        Processor will append service subdirectory in process()

    Examples:
        >>> info = OutputInfo("Google", "photos", "google")
        >>> build_processor_output_path(info, "google-johndoe-20250526", "/output")
        '/output/Google/johndoe'

        >>> info = OutputInfo("iMessage", None, "imessage")
        >>> build_processor_output_path(info, "iph13p-messages-20220426", "/output")
        '/output/iMessage/iph13p'
    """
    username = extract_username_from_export_dir(input_dir, output_info.username_prefix)
    return str(Path(base_output_dir) / output_info.platform / username)
