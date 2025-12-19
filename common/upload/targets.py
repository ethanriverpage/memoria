#!/usr/bin/env python3
"""
Build upload targets (path + album name) based on processor OutputInfo.

Uses the processor's OutputInfo to build consistent Platform/User/Service
album names that match the output directory structure.
"""

from pathlib import Path
from typing import List, Tuple, Union, TYPE_CHECKING

from common.output.paths import OutputInfo
from common.utils import extract_username_from_export_dir

if TYPE_CHECKING:
    from processors.base import ProcessorBase


def _ig_public_media_targets(output_base: Path, username: str) -> List[Tuple[str, str]]:
    """Instagram public media: per-subfolder albums under Instagram/{username}/*"""
    targets: List[Tuple[str, str]] = []
    # Expected subfolders - each becomes its own service
    subfolders = [
        "posts",
        "archived_posts",
        "profile",
        "stories",
        "reels",
        "other",
    ]
    for sub in subfolders:
        folder = output_base / sub
        if folder.exists() and folder.is_dir():
            album = f"Instagram/{username}/{sub}"
            targets.append((str(folder), album))
    return targets


def build_upload_targets(
    processor: Union["ProcessorBase", OutputInfo, str],
    input_dir: str,
    output_base: str,
) -> List[Tuple[str, str]]:
    """Build upload targets using processor's OutputInfo.

    Args:
        processor: Processor instance, OutputInfo, or processor name (deprecated)
        input_dir: Path to export directory (for username extraction)
        output_base: Base output directory (already includes Platform/User/Service)

    Returns:
        List of (path, album) tuples for upload
    """
    out_base = Path(output_base)

    # Get OutputInfo from processor
    if isinstance(processor, OutputInfo):
        output_info = processor
    elif isinstance(processor, str):
        # Legacy: processor name string - should not happen with new code
        # Fall back to returning the base path with no album
        return [(str(out_base), "")]
    else:
        # Processor instance
        output_info = processor.get_output_info()

    # Extract username using the prefix from OutputInfo
    username = extract_username_from_export_dir(input_dir, output_info.username_prefix)

    # Handle Instagram Public Media specially (has nested subfolder structure)
    if output_info.platform == "Instagram" and output_info.service == "posts":
        return _ig_public_media_targets(out_base, username)

    # Build album name: Platform/username[/service]
    if output_info.service:
        album = f"{output_info.platform}/{username}/{output_info.service}"
    else:
        album = f"{output_info.platform}/{username}"

    return [(str(out_base), album)]
