#!/usr/bin/env python3
"""
Build upload targets (path + album name) based on processor OutputInfo.

Uses the processor's OutputInfo to build consistent Platform/User/Service
album names that match the output directory structure.

Album naming follows the description.py format:
    {Platform}/{username}[/{service}]

Examples:
    Google/john.doe@gmail.com/photos
    Instagram/johndoe/messages
    iMessage/mac
    Discord/johndoe
"""

from pathlib import Path
from typing import List, Optional, Tuple, Type, TYPE_CHECKING

from common.output.paths import OutputInfo
from common.detection import detect_from_dirname

if TYPE_CHECKING:
    from processors.base import ProcessorBase

# Processor name that requires special subfolder handling
_INSTAGRAM_PUBLIC_MEDIA_NAME = "Instagram Public Media"


def _ig_public_media_targets(output_base: Path, username: str) -> List[Tuple[str, str]]:
    """Instagram public media: per-subfolder albums under Instagram/{username}/*

    This processor outputs to multiple service subfolders (posts, stories, reels, etc.)
    and each subfolder should be uploaded to a separate album.

    Args:
        output_base: Base output directory for the processor.
        username: Extracted username from export directory.

    Returns:
        List of (path, album) tuples for each existing subfolder.
    """
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
    processor: "ProcessorBase | Type[ProcessorBase] | OutputInfo",
    input_dir: str,
    output_base: str,
) -> List[Tuple[str, str]]:
    """Build upload targets using processor's OutputInfo.

    Album names follow the description.py format: Platform/username[/service]

    Args:
        processor: Processor instance/class or OutputInfo.
        input_dir: Path to export directory (for username extraction)
        output_base: Base output directory (already includes Platform/User/Service)

    Returns:
        List of (path, album) tuples for upload
    """
    out_base = Path(output_base)

    # Track processor name for special handling
    processor_name: Optional[str] = None

    # Get OutputInfo from processor
    if isinstance(processor, OutputInfo):
        output_info = processor
    else:
        get_name = getattr(processor, "get_name", None)
        get_output_info = getattr(processor, "get_output_info", None)
        if not callable(get_name) or not callable(get_output_info):
            raise TypeError(
                "processor must be ProcessorBase (or subclass) or OutputInfo"
            )

        # Processor instance/class - get both name and output info
        processor_name = get_name()
        output_info = get_output_info()

    # Extract username using the prefix from OutputInfo
    username = (
        detect_from_dirname(Path(input_dir), output_info.username_prefix) or "unknown"
    )

    # Handle Instagram Public Media specially (has nested subfolder structure)
    # Instagram Public Media outputs to public-media/{service} subfolders
    # (Instaloader format exports are also handled by this processor as posts/)
    if processor_name == _INSTAGRAM_PUBLIC_MEDIA_NAME:
        return _ig_public_media_targets(out_base, username)

    # Build album name: Platform/username[/service]
    if output_info.service:
        album = f"{output_info.platform}/{username}/{output_info.service}"
    else:
        album = f"{output_info.platform}/{username}"

    return [(str(out_base), album)]
