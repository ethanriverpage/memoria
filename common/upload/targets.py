#!/usr/bin/env python3
"""
Build upload targets (path + album name) based on processor and export.

Implements the album naming rules provided by the user across Instagram,
Google, and Snapchat processors.
"""

from pathlib import Path
from typing import List, Tuple

from common.utils import extract_username_from_export_dir


def _ig_public_media_targets(output_base: Path, username: str) -> List[Tuple[str, str]]:
    """Instagram public media: per-subfolder albums under Instagram/{username}/*"""
    targets: List[Tuple[str, str]] = []
    # Expected subfolders
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
    processor_name: str, input_dir: str, output_base: str
) -> List[Tuple[str, str]]:
    """Return a list of (path, album) tuples to upload for a processor run."""
    out_base = Path(output_base)

    # Handle Instagram Public Media (multiple targets)
    if processor_name in ("Instagram Public Media", "Instagram Old Public Media"):
        username = extract_username_from_export_dir(input_dir, "instagram")
        return _ig_public_media_targets(out_base, username)

    # Handle Snapchat Messages (special username extraction)
    if processor_name == "Snapchat Messages":
        username = extract_username_from_export_dir(input_dir, "snapmsgs")
        if username == "unknown":
            username = extract_username_from_export_dir(input_dir, "snapchat")
        album = f"Snapchat/{username}/messages"
        messages_path = out_base / "messages"
        return [(str(messages_path), album)]

    # Handle Discord
    if processor_name == "Discord":
        username = extract_username_from_export_dir(input_dir, "discord")
        album = f"Discord/{username}"
        messages_path = out_base / "messages"
        return [(str(messages_path), album)]

    # Handle iMessage (including iMazing exports)
    if processor_name in ("iMessage", "iMessage-iMazing"):
        # Extract device identifier from directory name (e.g., "iph13p" from "iph13p-messages-20220426")
        input_path = Path(input_dir)
        dir_name = input_path.name
        # Pattern: {device}-messages-YYYYMMDD or mac-messages-YYYYMMDD
        if "-messages-" in dir_name:
            device = dir_name.split("-messages-")[0]
        else:
            device = "unknown"
        album = f"iMessage/{device}"
        messages_path = out_base / "messages"
        return [(str(messages_path), album)]

    # Handle Snapchat Memories with subdirectory
    if processor_name == "Snapchat Memories":
        username = extract_username_from_export_dir(input_dir, "snapchat")
        album = f"Snapchat/{username}/memories"
        memories_path = out_base / "memories"
        return [(str(memories_path), album)]

    # Simple mapping for single-target processors
    # Format: processor_name -> (prefix_for_username_extraction, album_template)
    simple_mappings = {
        "Instagram Messages": ("instagram", "Instagram/{username}/messages"),
        "Google Chat": ("google", "Google Chat/{username}"),
        "Google Photos": ("google", "Google Photos/{username}"),
        "Google Voice": ("google", "Google Voice/{username}"),
    }

    if processor_name in simple_mappings:
        prefix, album_template = simple_mappings[processor_name]
        username = extract_username_from_export_dir(input_dir, prefix)
        album = album_template.format(username=username)
        return [(str(out_base), album)]

    # Default: no targets
    return []
