#!/usr/bin/env python3
"""
Create a trimmed test dataset from a large media originals collection.

This script samples files from each export type to create a minimal but
comprehensive test dataset that covers all edge cases without requiring
hours of processing time.

Each processor type has custom sampling logic to ensure the resulting
dataset is actually functional for testing:
- Google Photos: Media + JSON sidecar pairs
- Snapchat Memories: Media + overlays + filtered metadata.json
- Snapchat Messages: Sampled channels with JSON + chat_media
- Instagram Messages: Sampled conversation folders with HTML + media
- Instagram Public Media: Media + JSON sidecars
- Discord: Sampled channel folders with all JSON files
- iMessage: Database + sampled attachments

Usage:
    python create_test_dataset.py --source /mnt/media/originals
    python create_test_dataset.py --source /mnt/media/originals --output /mnt/media/temp/test_dataset
    python create_test_dataset.py --source /mnt/media/originals --dry-run
"""

import argparse
import json
import logging
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# Media file extensions to consider
MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
    ".mkv",
    ".3gp",
}


@dataclass
class SampledFiles:
    """Result of sampling an export directory."""

    files_to_copy: List[Path] = field(default_factory=list)
    # Files to write with custom content: (dest_rel_path, content)
    files_to_write: List[Tuple[Path, str]] = field(default_factory=list)
    total_size: int = 0
    # For iMessage: (source_db, attachment_ids, message_ids, chat_ids)
    filtered_imessage_db: Optional[Tuple[Path, Set[int], Set[int], Set[int]]] = None


def is_media_file(path: Path) -> bool:
    """Check if path is a media file."""
    return path.suffix.lower() in MEDIA_EXTENSIONS


def fast_find_media(
    directory: Path, max_depth: int = 5, limit: int = 500
) -> List[Path]:
    """Use 'find' command for fast media file discovery.

    Much faster than Python's rglob for large directories.
    """
    if not directory.exists():
        return []

    # Build find command with media extensions
    ext_patterns = []
    for ext in MEDIA_EXTENSIONS:
        ext_patterns.extend(["-iname", f"*{ext}", "-o"])
    # Remove trailing -o
    ext_patterns = ext_patterns[:-1]

    cmd = [
        "find",
        str(directory),
        "-maxdepth",
        str(max_depth),
        "-type",
        "f",
        "(",
        *ext_patterns,
        ")",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,  # 30 second timeout
        )
        files = [Path(p) for p in result.stdout.strip().split("\n") if p]
        return files[:limit]
    except (subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        logger.warning(f"Fast find failed, falling back to scandir: {e}")
        return _scandir_media(directory, max_depth, limit)


def _scandir_media(
    directory: Path, max_depth: int = 5, limit: int = 500, current_depth: int = 0
) -> List[Path]:
    """Fallback using os.scandir (faster than rglob but still Python)."""
    if current_depth >= max_depth:
        return []

    files: List[Path] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if len(files) >= limit:
                    break
                if entry.is_file(follow_symlinks=False):
                    if is_media_file(Path(entry.path)):
                        files.append(Path(entry.path))
                elif entry.is_dir(follow_symlinks=False):
                    if not entry.name.startswith("."):
                        files.extend(
                            _scandir_media(
                                Path(entry.path),
                                max_depth,
                                limit - len(files),
                                current_depth + 1,
                            )
                        )
    except (PermissionError, OSError):
        pass
    return files


def get_google_photos_sidecars(media_path: Path) -> List[Path]:
    """Get all possible sidecar files for a Google Photos media file."""
    sidecars = []
    parent = media_path.parent
    name = media_path.name
    stem = media_path.stem

    # Google Photos uses various sidecar naming patterns
    patterns = [
        f"{name}.json",
        f"{name}.supplemental-metadata.json",
        f"{name}.supplemental-me.json",
        f"{name}.supple.json",
        f"{name}.suppl.json",
        f"{stem}.json",
    ]

    for pattern in patterns:
        sidecar = parent / pattern
        if sidecar.exists() and sidecar not in sidecars:
            sidecars.append(sidecar)

    # Also check for any JSON starting with the stem (handles numbered duplicates)
    try:
        for f in parent.iterdir():
            if f.is_file() and f.suffix == ".json" and f.name.startswith(stem):
                if f not in sidecars:
                    sidecars.append(f)
    except (PermissionError, OSError):
        pass

    return sidecars


def sample_google_photos(export_path: Path, max_files: int = 30) -> SampledFiles:
    """Sample Google Photos export: media files with their JSON sidecars.

    Structure: google-username/Google Photos/{album}/photo.jpg + photo.jpg.json
    """
    result = SampledFiles()
    photos_dir = export_path / "Google Photos"

    if not photos_dir.exists():
        # Try Takeout structure
        photos_dir = export_path / "Takeout" / "Google Photos"
        if not photos_dir.exists():
            return result

    # Use fast find instead of rglob (much faster on large directories)
    # Google Photos typically has album/file structure, so maxdepth 3 is enough
    all_media = fast_find_media(photos_dir, max_depth=3, limit=max_files * 5)

    if not all_media:
        return result

    # Sample with variety (different albums, file types)
    by_album: Dict[str, List[Path]] = {}
    for f in all_media:
        album = f.parent.name
        if album not in by_album:
            by_album[album] = []
        by_album[album].append(f)

    selected: List[Path] = []

    # Take a few from each album, prioritizing variety
    files_per_album = max(1, max_files // len(by_album)) if by_album else max_files
    for album, files in by_album.items():
        random.shuffle(files)
        selected.extend(files[:files_per_album])
        if len(selected) >= max_files:
            break

    selected = selected[:max_files]

    # Add files and their sidecars
    for media_file in selected:
        result.files_to_copy.append(media_file)
        result.files_to_copy.extend(get_google_photos_sidecars(media_file))

    # Include global metadata files
    for json_file in [
        "print-subscriptions.json",
        "shared_album_comments.json",
        "user-generated-memory-titles.json",
    ]:
        path = photos_dir / json_file
        if path.exists():
            result.files_to_copy.append(path)

    result.total_size = sum(
        f.stat().st_size for f in result.files_to_copy if f.exists()
    )
    return result


def sample_google_chat(export_path: Path, max_conversations: int = 5) -> SampledFiles:
    """Sample Google Chat: complete conversation folders with messages.json + media.

    Structure: google-username/Google Chat/{Groups|Users}/{name}/messages.json + media

    Prioritizes conversations that contain media files to ensure test coverage.
    """
    result = SampledFiles()
    chat_dir = export_path / "Google Chat"

    if not chat_dir.exists():
        chat_dir = export_path / "Takeout" / "Google Chat"
        if not chat_dir.exists():
            return result

    # Always include Users/*/user_info.json when present.
    #
    # Rationale:
    # - The Google Chat processor detector requires the `Google Chat/Users/` directory to exist.
    # - The preprocessor reads `Users/*/user_info.json` to detect the export username/email.
    # - `Users/` does not contain `messages.json`, so "conversation sampling" alone will
    #   never copy anything under `Users/`, causing the directory to be missing in the
    #   trimmed dataset and breaking detection.
    users_dir = chat_dir / "Users"
    if users_dir.exists() and users_dir.is_dir():
        user_info_files = sorted(users_dir.rglob("user_info.json"))
        for user_info_path in user_info_files:
            if user_info_path.is_file():
                result.files_to_copy.append(user_info_path)

    # Find all conversation folders (those with messages.json)
    # Categorize by whether they have media files
    convs_with_media: List[Path] = []
    convs_without_media: List[Path] = []

    for subdir in ["Groups", "Users"]:
        sub_path = chat_dir / subdir
        if sub_path.exists():
            for conv_dir in sub_path.iterdir():
                if conv_dir.is_dir() and (conv_dir / "messages.json").exists():
                    # Check if conversation has any media files
                    has_media = any(
                        f.is_file() and is_media_file(f) for f in conv_dir.rglob("*")
                    )
                    if has_media:
                        convs_with_media.append(conv_dir)
                    else:
                        convs_without_media.append(conv_dir)

    if not convs_with_media and not convs_without_media:
        return result

    # Prioritize conversations WITH media
    selected_convs: List[Path] = []

    # First, take conversations with media (up to max_conversations - 1)
    random.shuffle(convs_with_media)
    selected_convs.extend(convs_with_media[: max_conversations - 1])

    # Add at least one without media for edge case testing (if available)
    if convs_without_media:
        random.shuffle(convs_without_media)
        selected_convs.append(convs_without_media[0])

    # Fill remaining slots with more media conversations
    if len(selected_convs) < max_conversations:
        remaining_with_media = [c for c in convs_with_media if c not in selected_convs]
        selected_convs.extend(
            remaining_with_media[: max_conversations - len(selected_convs)]
        )

    # If still not enough, add more without media
    if len(selected_convs) < max_conversations:
        remaining_without = [c for c in convs_without_media if c not in selected_convs]
        selected_convs.extend(
            remaining_without[: max_conversations - len(selected_convs)]
        )

    logger.debug(
        f"Google Chat: {len(convs_with_media)} convs with media, "
        f"{len(convs_without_media)} without, selected {len(selected_convs)}"
    )

    # Copy entire conversation folders
    for conv_dir in selected_convs:
        for f in conv_dir.rglob("*"):
            if f.is_file():
                result.files_to_copy.append(f)

    result.total_size = sum(
        f.stat().st_size for f in result.files_to_copy if f.exists()
    )
    return result


def sample_snapchat_memories(export_path: Path, max_files: int = 20) -> SampledFiles:
    """Sample Snapchat Memories: media + overlays + filtered metadata.json.

    Structure: snapchat-username/memories/{media/,overlays/,metadata.json}
    """
    result = SampledFiles()
    memories_dir = export_path / "memories"

    if not memories_dir.exists():
        return result

    media_dir = memories_dir / "media"
    overlays_dir = memories_dir / "overlays"
    metadata_path = memories_dir / "metadata.json"

    if not media_dir.exists() or not metadata_path.exists():
        return result

    # Load metadata
    try:
        with open(metadata_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    except (json.JSONDecodeError, OSError):
        return result

    if not isinstance(metadata, list):
        return result

    # Sample entries from metadata
    random.shuffle(metadata)
    selected_entries = metadata[:max_files]

    # Collect referenced files
    selected_filenames: Set[str] = set()
    for entry in selected_entries:
        media_filename = entry.get("media_filename")
        overlay_filename = entry.get("overlay_filename")

        if media_filename:
            selected_filenames.add(media_filename)
            media_file = media_dir / media_filename
            if media_file.exists():
                result.files_to_copy.append(media_file)

        if overlay_filename:
            overlay_file = overlays_dir / overlay_filename
            if overlay_file.exists():
                result.files_to_copy.append(overlay_file)

    # Create filtered metadata.json
    filtered_metadata = [
        entry for entry in metadata if entry.get("media_filename") in selected_filenames
    ]
    filtered_content = json.dumps(filtered_metadata, indent=2, ensure_ascii=False)
    result.files_to_write.append((metadata_path, filtered_content))

    result.total_size = sum(
        f.stat().st_size for f in result.files_to_copy if f.exists()
    )
    return result


def sample_snapchat_messages(export_path: Path, max_media: int = 15) -> SampledFiles:
    """Sample Snapchat Messages: filtered JSON + sampled chat_media.

    Structure: snapchat-username/messages/{json/,chat_media/,html/}
    Note: chat_media is directly under messages/, not inside json/

    Unlike the old approach of copying full JSON files, we now filter
    chat_history.json to only include messages that reference the sampled
    media files. This prevents thousands of orphaned metadata entries.
    """
    result = SampledFiles()
    messages_dir = export_path / "messages"

    if not messages_dir.exists():
        return result

    json_dir = messages_dir / "json"
    if not json_dir.exists():
        json_dir = messages_dir  # Sometimes JSON is directly in messages/

    # chat_media is directly under messages/, NOT inside json/
    chat_media_dir = messages_dir / "chat_media"

    # Sample media from chat_media first so we know which messages to keep
    selected_media_filenames: Set[str] = set()

    if chat_media_dir.exists():
        all_media = [
            f for f in chat_media_dir.iterdir() if f.is_file() and is_media_file(f)
        ]
        random.shuffle(all_media)
        selected_media = all_media[:max_media]
        result.files_to_copy.extend(selected_media)
        selected_media_filenames = {f.name for f in selected_media}

        # Also include overlays for selected media (Snapchat overlay naming patterns)
        for media_file in selected_media:
            stem = media_file.stem
            # Check for exact overlay patterns used by Snapchat
            overlay_patterns = [
                f"{stem}_overlay.png",
                f"{stem}-overlay.png",
                f"{stem.replace('_', '-')}_overlay.png",
            ]
            # For media files with UUID pattern like "2023-06-22_media~UUID"
            # the overlay is typically "2023-06-22_overlay~UUID.png"
            if "~" in stem:
                date_part, uuid_part = stem.split("~", 1)
                if "_media" in date_part:
                    overlay_stem = date_part.replace("_media", "_overlay")
                    overlay_patterns.append(f"{overlay_stem}~{uuid_part}.png")

            for pattern in overlay_patterns:
                overlay_file = chat_media_dir / pattern
                if overlay_file.exists() and overlay_file not in result.files_to_copy:
                    result.files_to_copy.append(overlay_file)

    # Load and filter chat_history.json
    # Media filenames contain the Media ID from JSON, e.g.:
    # JSON: "Media IDs":"b~EiQSFWJoWXE0UGtjWndZSDlhUEt1VWV1MhoAGgAyAXxIAlAEYAE"
    # File: 2019-02-12_b~EiQSFWJoWXE0UGtjWndZSDlhUEt1VWV1MhoAGgAyAXxIAlAEYAE.jpg
    chat_history_path = json_dir / "chat_history.json"
    if chat_history_path.exists() and selected_media_filenames:
        try:
            # Build a set of Media IDs that correspond to our selected files
            selected_media_ids: Set[str] = set()
            for filename in selected_media_filenames:
                # Extract Media ID from filename (after date prefix, before extension)
                # Format: YYYY-MM-DD_<media_id>.ext or YYYY-MM-DD_media~<uuid>.ext
                parts = filename.split("_", 1)
                if len(parts) > 1:
                    media_id_with_ext = parts[1]
                    media_id = Path(media_id_with_ext).stem
                    selected_media_ids.add(media_id)

            with open(chat_history_path, "r", encoding="utf-8") as f:
                chat_history = json.load(f)

            # Filter to only include messages that reference selected media
            # chat_history is a dict with conversation names as keys
            if isinstance(chat_history, dict):
                filtered_history = {}
                for conv_id, messages in chat_history.items():
                    if isinstance(messages, list):
                        filtered_messages = []
                        for msg in messages:
                            # Check if message references any of our selected media
                            media_ids_str = msg.get("Media IDs", "")
                            if media_ids_str:
                                # Check if this media ID is in our selected set
                                if media_ids_str in selected_media_ids:
                                    filtered_messages.append(msg)
                        if filtered_messages:
                            filtered_history[conv_id] = filtered_messages
                if filtered_history:
                    result.files_to_write.append(
                        (
                            chat_history_path,
                            json.dumps(filtered_history, indent=2, ensure_ascii=False),
                        )
                    )
                else:
                    # No matches - copy the JSON anyway for structure detection
                    result.files_to_copy.append(chat_history_path)
            else:
                # Unexpected format, copy as-is
                result.files_to_copy.append(chat_history_path)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to filter chat_history.json: {e}")
            # Fall back to copying the full file
            result.files_to_copy.append(chat_history_path)
    elif chat_history_path.exists():
        # No media selected, just copy the JSON for structure
        result.files_to_copy.append(chat_history_path)

    # Copy snap_history.json as-is (usually smaller and references different media)
    snap_history_path = json_dir / "snap_history.json"
    if snap_history_path.exists():
        result.files_to_copy.append(snap_history_path)

    # Copy HTML files too (for reference, though not used by processor)
    html_dir = messages_dir / "html"
    if html_dir.exists():
        for f in html_dir.iterdir():
            if f.is_file() and f.suffix == ".html":
                result.files_to_copy.append(f)

    result.total_size = sum(
        f.stat().st_size for f in result.files_to_copy if f.exists()
    )
    return result


def sample_instagram_messages(
    export_path: Path, max_conversations: int = 5, max_media_per_conv: int = 10
) -> SampledFiles:
    """Sample Instagram Messages: complete conversation folders with HTML + media.

    Structure: instagram-username/your_instagram_activity/messages/inbox/{conv}/
               or instagram-username/messages/inbox/{conv}/

    Copies ALL media from selected conversations (not just a subset) to ensure
    HTML references match copied media files.
    """
    result = SampledFiles()

    # Try both current and legacy paths
    inbox_paths = [
        export_path / "your_instagram_activity" / "messages" / "inbox",
        export_path / "messages" / "inbox",
    ]

    inbox_dir = None
    for path in inbox_paths:
        if path.exists():
            inbox_dir = path
            break

    if not inbox_dir:
        return result

    # Find conversation folders, categorizing by whether they have media
    # Also track media count for smarter selection
    convs_with_media: List[Tuple[Path, int]] = []  # (path, media_count)
    convs_without_media: List[Path] = []

    for conv_dir in inbox_dir.iterdir():
        if conv_dir.is_dir():
            html_files = list(conv_dir.glob("message_*.html"))
            if html_files:
                # Count media files in this conversation
                media_count = 0
                for media_subdir in ["photos", "videos", "audio"]:
                    media_path = conv_dir / media_subdir
                    if media_path.exists():
                        media_count += sum(
                            1 for f in media_path.iterdir() if f.is_file()
                        )

                if media_count > 0:
                    convs_with_media.append((conv_dir, media_count))
                else:
                    convs_without_media.append(conv_dir)

    # Prioritize conversations WITH media, preferring those with reasonable amounts
    # (not too many to avoid bloating the test dataset)
    selected: List[Path] = []

    # Sort by media count and prefer conversations with 1-20 media files
    convs_with_media.sort(key=lambda x: (0 if 1 <= x[1] <= 20 else 1, x[1]))
    random.shuffle(convs_with_media)  # Randomize within similar counts

    # Take conversations with media (up to max_conversations - 1)
    for conv_dir, _ in convs_with_media[: max_conversations - 1]:
        selected.append(conv_dir)

    # Add at least one without media for edge case testing
    if convs_without_media:
        random.shuffle(convs_without_media)
        selected.append(convs_without_media[0])

    # Fill remaining slots with more media convs
    if len(selected) < max_conversations:
        remaining = [(c, n) for c, n in convs_with_media if c not in selected]
        for conv_dir, _ in remaining[: max_conversations - len(selected)]:
            selected.append(conv_dir)

    logger.debug(
        f"Instagram Messages: {len(convs_with_media)} convs with media, "
        f"{len(convs_without_media)} without, selected {len(selected)}"
    )

    # Copy entire conversation folders - ALL media to ensure HTML references work
    for conv_dir in selected:
        # Always copy HTML files
        for f in conv_dir.glob("message_*.html"):
            result.files_to_copy.append(f)

        # Copy ALL media from each subdirectory (limited per conversation to avoid bloat)
        conv_media_count = 0
        for media_subdir in ["photos", "videos", "audio"]:
            media_path = conv_dir / media_subdir
            if media_path.exists():
                media_files = [f for f in media_path.iterdir() if f.is_file()]
                # Limit total media per conversation to avoid very large test datasets
                remaining_slots = max_media_per_conv - conv_media_count
                if remaining_slots > 0:
                    result.files_to_copy.extend(media_files[:remaining_slots])
                    conv_media_count += min(len(media_files), remaining_slots)

    # Copy index files
    messages_root = inbox_dir.parent
    for index_file in ["chats.html", "secret_conversations.html"]:
        path = messages_root / index_file
        if path.exists():
            result.files_to_copy.append(path)

    # Also sample Instagram Public Media if it exists in the same export
    # This ensures both processors can work on the same export
    public_media_result = _sample_instagram_public_in_combined(export_path)
    result.files_to_copy.extend(public_media_result.files_to_copy)

    result.total_size = sum(
        f.stat().st_size for f in result.files_to_copy if f.exists()
    )
    return result


def _sample_instagram_public_in_combined(
    export_path: Path, max_files: int = 10
) -> SampledFiles:
    """Sample Instagram Public Media when it exists in a combined export.

    Called from sample_instagram_messages to ensure both messages and public
    media are included when they coexist.
    """
    result = SampledFiles()

    # Check for public media structure (content/ or your_instagram_activity/media/)
    media_roots = [
        export_path / "content",
        export_path / "your_instagram_activity" / "media",
    ]

    for media_root in media_roots:
        if not media_root.exists():
            continue

        # Sample from each media type subdirectory
        for subdir_name in ["posts", "stories", "reels", "other"]:
            subdir = media_root / subdir_name
            if subdir.exists():
                # Find media files (limited depth)
                media_files = fast_find_media(subdir, max_depth=3, limit=max_files)
                random.shuffle(media_files)
                selected = media_files[: max_files // 4]  # Split across types

                for media_file in selected:
                    result.files_to_copy.append(media_file)
                    # Copy JSON sidecar if exists
                    sidecar = media_file.parent / f"{media_file.stem}.json"
                    if sidecar.exists():
                        result.files_to_copy.append(sidecar)

    return result


def sample_instagram_public(export_path: Path, max_files: int = 20) -> SampledFiles:
    """Sample Instagram Public Media: media + JSON sidecar pairs.

    Structure: instagram-username/media/posts/YYYYMM/photo.jpg + photo.json
               or instagram-username/public-media/...
    """
    result = SampledFiles()

    # Try various paths
    media_paths = [
        export_path / "media",
        export_path / "public-media",
        export_path / "your_instagram_activity" / "media",
    ]

    # Use fast find instead of rglob
    all_media: List[Path] = []
    for base_path in media_paths:
        if base_path.exists():
            all_media.extend(
                fast_find_media(base_path, max_depth=4, limit=max_files * 3)
            )
            if len(all_media) >= max_files * 3:
                break

    if not all_media:
        return result

    # Sample with variety
    random.shuffle(all_media)
    selected = all_media[:max_files]

    # Add files and their JSON sidecars
    for media_file in selected:
        result.files_to_copy.append(media_file)
        # Instagram public uses stem.json pattern
        sidecar = media_file.parent / f"{media_file.stem}.json"
        if sidecar.exists():
            result.files_to_copy.append(sidecar)

    result.total_size = sum(
        f.stat().st_size for f in result.files_to_copy if f.exists()
    )
    return result


def sample_discord(export_path: Path, max_channels: int = 5) -> SampledFiles:
    """Sample Discord: channel folders with all JSON files intact.

    Structure: discord-username/Messages/c{id}/{messages.json,channel.json}
    """
    result = SampledFiles()
    messages_dir = export_path / "Messages"

    if not messages_dir.exists():
        return result

    # Always copy index.json
    index_path = messages_dir / "index.json"
    if index_path.exists():
        result.files_to_copy.append(index_path)

    # Find channel folders
    channels = [
        d for d in messages_dir.iterdir() if d.is_dir() and d.name.startswith("c")
    ]

    if not channels:
        return result

    # Sample channels
    random.shuffle(channels)
    selected_channels = channels[:max_channels]

    # Copy all files from selected channels
    for channel_dir in selected_channels:
        for f in channel_dir.iterdir():
            if f.is_file():
                result.files_to_copy.append(f)

    # Copy Servers directory structure (useful for context)
    servers_dir = export_path / "Servers"
    if servers_dir.exists():
        index = servers_dir / "index.json"
        if index.exists():
            result.files_to_copy.append(index)
        # Sample a few server folders
        server_folders = [d for d in servers_dir.iterdir() if d.is_dir()]
        for server in server_folders[:3]:
            for f in server.iterdir():
                if f.is_file():
                    result.files_to_copy.append(f)

    # Copy Account info (usually just a few small files)
    account_dir = export_path / "Account"
    if account_dir.exists():
        try:
            for f in account_dir.iterdir():
                if f.is_file():
                    result.files_to_copy.append(f)
        except (PermissionError, OSError):
            pass

    # Copy README
    readme = export_path / "README.txt"
    if readme.exists():
        result.files_to_copy.append(readme)

    result.total_size = sum(
        f.stat().st_size for f in result.files_to_copy if f.exists()
    )
    return result


def _resolve_imessage_attachment_path(
    filename: str, attachments_dir: Path
) -> Optional[Path]:
    """Resolve an iMessage attachment path to an actual file on disk.

    iMessage stores attachment paths in various formats:
    - Mac: ~/Library/Messages/Attachments/...
    - iPhone: Various relative or absolute paths
    - iMazing: Relative paths within Attachments/

    Args:
        filename: The filename field from the attachment table
        attachments_dir: The Attachments directory in the export

    Returns:
        Path to the file if it exists, None otherwise
    """
    if not filename:
        return None

    # Try multiple path resolution strategies
    candidates: List[Path] = []

    # Strategy 1: Mac format ~/Library/Messages/Attachments/...
    if filename.startswith("~/"):
        rel_path = filename.replace("~/Library/Messages/Attachments/", "")
        candidates.append(attachments_dir / rel_path)

    # Strategy 2: file:// URL format
    elif filename.startswith("file://"):
        path_part = filename.replace("file://", "")
        if "/Attachments/" in path_part:
            rel_path = path_part.split("/Attachments/", 1)[1]
            candidates.append(attachments_dir / rel_path)

    # Strategy 3: Full path containing /Attachments/
    elif "/Attachments/" in filename:
        rel_path = filename.split("/Attachments/", 1)[1]
        candidates.append(attachments_dir / rel_path)

    # Strategy 4: Path containing MediaDomain (iMazing iPhone format)
    # e.g., MediaDomain-Library/SMS/Attachments/...
    elif "MediaDomain" in filename:
        # Extract path after Attachments/
        if "/Attachments/" in filename:
            rel_path = filename.split("/Attachments/", 1)[1]
            candidates.append(attachments_dir / rel_path)
        # Also try the last component as a fallback
        candidates.append(attachments_dir / Path(filename).name)

    # Strategy 5: Direct relative path (might already be relative to Attachments)
    else:
        # Try as-is first
        candidates.append(attachments_dir / filename)
        # Try just the filename
        candidates.append(attachments_dir / Path(filename).name)

    # Also try searching for the filename in subdirectories (for deeply nested structures)
    base_name = Path(filename).name
    if base_name and len(base_name) > 4:  # Avoid searching for very short names
        # Quick check in common subdirectory patterns (2-level deep)
        for subdir1 in attachments_dir.iterdir() if attachments_dir.exists() else []:
            if subdir1.is_dir():
                for subdir2 in subdir1.iterdir():
                    if subdir2.is_dir():
                        candidate = subdir2 / base_name
                        if candidate.exists():
                            candidates.append(candidate)
                            break

    # Return the first candidate that exists and is a file
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except (PermissionError, OSError):
            continue

    return None


def sample_imessage(export_path: Path, max_attachments: int = 10) -> SampledFiles:
    """Sample iMessage: filtered database + sampled attachments.

    Creates a filtered SQLite database containing only the sampled attachments
    and their related messages/chats/handles to avoid orphaned metadata.

    IMPORTANT: This function never falls back to copying the full database,
    as that would create thousands of orphaned metadata entries. If we can't
    properly filter, we only copy the attachment files without database filtering.

    Structure: mac-messages/chat.db + Attachments/
               or iphone-messages/SMS/sms.db + SMS/Attachments/
    """
    result = SampledFiles()

    # Detect Mac vs iPhone format
    chat_db = export_path / "chat.db"
    attachments_dir = export_path / "Attachments"

    if not chat_db.exists():
        # Try iPhone/iMazing format
        sms_dir = export_path / "SMS"
        if sms_dir.exists():
            chat_db = sms_dir / "sms.db"
            attachments_dir = sms_dir / "Attachments"

    if not chat_db.exists():
        return result

    try:
        # Query the database to find attachments with their file paths
        conn = sqlite3.connect(str(chat_db))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Query attachments that exist and are complete (transfer_state = 5)
        query = """
        SELECT
            a.ROWID as attachment_id,
            a.filename,
            maj.message_id,
            cmj.chat_id
        FROM attachment a
        JOIN message_attachment_join maj ON a.ROWID = maj.attachment_id
        JOIN chat_message_join cmj ON maj.message_id = cmj.message_id
        WHERE a.filename IS NOT NULL
          AND a.transfer_state = 5
        """
        cursor.execute(query)
        rows = cursor.fetchall()

        # Build list of attachments that actually exist on disk
        valid_attachments: List[Tuple[int, Path, int, int]] = []
        for row in rows:
            file_path = _resolve_imessage_attachment_path(
                row["filename"], attachments_dir
            )
            if file_path and is_media_file(file_path):
                valid_attachments.append(
                    (row["attachment_id"], file_path, row["message_id"], row["chat_id"])
                )

        conn.close()

        if not valid_attachments:
            # No valid attachments found - copy files only, NO database
            # Copying the full database would create orphaned metadata
            logger.debug(
                f"iMessage: No valid attachments found in {chat_db}, "
                f"copying files only (no database)"
            )
            if attachments_dir.exists():
                all_attachments = fast_find_media(
                    attachments_dir, max_depth=6, limit=max_attachments * 3
                )
                random.shuffle(all_attachments)
                result.files_to_copy.extend(all_attachments[:max_attachments])
            result.total_size = sum(
                f.stat().st_size for f in result.files_to_copy if f.exists()
            )
            return result

        # Sample attachments
        random.shuffle(valid_attachments)
        selected = valid_attachments[:max_attachments]

        # Add selected attachment files
        selected_attachment_ids: Set[int] = set()
        selected_message_ids: Set[int] = set()
        selected_chat_ids: Set[int] = set()

        for att_id, file_path, msg_id, chat_id in selected:
            result.files_to_copy.append(file_path)
            selected_attachment_ids.add(att_id)
            selected_message_ids.add(msg_id)
            selected_chat_ids.add(chat_id)

        # Create filtered database content
        # We'll write this as a tuple: (original_db_path, attachment_ids, message_ids, chat_ids)
        # The copy logic will create a filtered database
        result.filtered_imessage_db = (
            chat_db,
            selected_attachment_ids,
            selected_message_ids,
            selected_chat_ids,
        )

        logger.debug(
            f"iMessage: Sampled {len(selected)} attachments from "
            f"{len(valid_attachments)} valid attachments"
        )

    except (sqlite3.Error, OSError) as e:
        logger.warning(f"Error querying iMessage database {chat_db}: {e}")
        # Do NOT copy the full database - just copy some attachment files
        # Copying the full database would create orphaned metadata entries
        if attachments_dir.exists():
            all_attachments = fast_find_media(
                attachments_dir, max_depth=6, limit=max_attachments * 3
            )
            random.shuffle(all_attachments)
            result.files_to_copy.extend(all_attachments[:max_attachments])

    # Copy MessagesMetaData if present (iMazing format) - usually small
    metadata_dir = export_path / "MessagesMetaData"
    if metadata_dir.exists():
        try:
            for f in metadata_dir.iterdir():
                if f.is_file():
                    result.files_to_copy.append(f)
        except (PermissionError, OSError):
            pass

    result.total_size = sum(
        f.stat().st_size for f in result.files_to_copy if f.exists()
    )
    return result


def create_filtered_imessage_db(
    source_db: Path,
    dest_db: Path,
    attachment_ids: Set[int],
    message_ids: Set[int],
    chat_ids: Set[int],
) -> bool:
    """Create a filtered iMessage database with only selected records.

    IMPORTANT: This function never falls back to copying the full database.
    If filtering fails, we skip creating the database entirely to avoid
    orphaned metadata entries.

    Args:
        source_db: Path to original chat.db or sms.db
        dest_db: Path to write filtered database
        attachment_ids: Set of attachment ROWIDs to include
        message_ids: Set of message ROWIDs to include
        chat_ids: Set of chat ROWIDs to include

    Returns:
        True if successful, False otherwise
    """
    src_conn = None
    dest_conn = None

    try:
        # Connect to source
        src_conn = sqlite3.connect(str(source_db))
        src_conn.row_factory = sqlite3.Row

        # Create destination database
        dest_db.parent.mkdir(parents=True, exist_ok=True)
        if dest_db.exists():
            dest_db.unlink()

        dest_conn = sqlite3.connect(str(dest_db))

        # Get the schema from source - only include the tables we need
        # Skip tables that might contain references to non-selected attachments
        required_tables = {
            "attachment",
            "message",
            "chat",
            "handle",
            "message_attachment_join",
            "chat_message_join",
            "chat_handle_join",
        }

        schema_query = """
        SELECT name, sql FROM sqlite_master
        WHERE type = 'table' AND sql IS NOT NULL
        """
        src_cursor = src_conn.cursor()
        src_cursor.execute(schema_query)
        table_schemas = src_cursor.fetchall()

        # Create only the required tables in destination
        dest_cursor = dest_conn.cursor()
        for name, sql in table_schemas:
            if name.lower() in required_tables:
                try:
                    dest_cursor.execute(sql)
                except sqlite3.Error as e:
                    logger.debug(f"Skipping table {name}: {e}")

        # Also create indexes for the tables we created
        index_query = """
        SELECT sql FROM sqlite_master
        WHERE type = 'index' AND sql IS NOT NULL
        """
        src_cursor.execute(index_query)
        for (sql,) in src_cursor.fetchall():
            if sql:
                try:
                    dest_cursor.execute(sql)
                except sqlite3.Error:
                    pass  # Indexes are optional

        # Copy filtered data for key tables
        # 1. attachment table - only selected attachments
        if attachment_ids:
            placeholders = ",".join("?" * len(attachment_ids))
            src_cursor.execute(
                f"SELECT * FROM attachment WHERE ROWID IN ({placeholders})",
                list(attachment_ids),
            )
            rows = src_cursor.fetchall()
            if rows:
                cols = [desc[0] for desc in src_cursor.description]
                insert_sql = f"INSERT INTO attachment ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                for row in rows:
                    try:
                        dest_cursor.execute(insert_sql, list(row))
                    except sqlite3.Error:
                        pass

        # 2. message table - only messages with selected attachments
        if message_ids:
            placeholders = ",".join("?" * len(message_ids))
            src_cursor.execute(
                f"SELECT * FROM message WHERE ROWID IN ({placeholders})",
                list(message_ids),
            )
            rows = src_cursor.fetchall()
            if rows:
                cols = [desc[0] for desc in src_cursor.description]
                insert_sql = f"INSERT INTO message ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                for row in rows:
                    try:
                        dest_cursor.execute(insert_sql, list(row))
                    except sqlite3.Error:
                        pass

        # 3. chat table - only related chats
        if chat_ids:
            placeholders = ",".join("?" * len(chat_ids))
            src_cursor.execute(
                f"SELECT * FROM chat WHERE ROWID IN ({placeholders})",
                list(chat_ids),
            )
            rows = src_cursor.fetchall()
            if rows:
                cols = [desc[0] for desc in src_cursor.description]
                insert_sql = f"INSERT INTO chat ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                for row in rows:
                    try:
                        dest_cursor.execute(insert_sql, list(row))
                    except sqlite3.Error:
                        pass

        # 4. message_attachment_join - only for selected messages/attachments
        if message_ids and attachment_ids:
            msg_placeholders = ",".join("?" * len(message_ids))
            att_placeholders = ",".join("?" * len(attachment_ids))
            src_cursor.execute(
                f"""SELECT * FROM message_attachment_join
                    WHERE message_id IN ({msg_placeholders})
                    AND attachment_id IN ({att_placeholders})""",
                list(message_ids) + list(attachment_ids),
            )
            rows = src_cursor.fetchall()
            if rows:
                cols = [desc[0] for desc in src_cursor.description]
                insert_sql = f"INSERT INTO message_attachment_join ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                for row in rows:
                    try:
                        dest_cursor.execute(insert_sql, list(row))
                    except sqlite3.Error:
                        pass

        # 5. chat_message_join - only for selected chats/messages
        if chat_ids and message_ids:
            chat_placeholders = ",".join("?" * len(chat_ids))
            msg_placeholders = ",".join("?" * len(message_ids))
            src_cursor.execute(
                f"""SELECT * FROM chat_message_join
                    WHERE chat_id IN ({chat_placeholders})
                    AND message_id IN ({msg_placeholders})""",
                list(chat_ids) + list(message_ids),
            )
            rows = src_cursor.fetchall()
            if rows:
                cols = [desc[0] for desc in src_cursor.description]
                insert_sql = f"INSERT INTO chat_message_join ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                for row in rows:
                    try:
                        dest_cursor.execute(insert_sql, list(row))
                    except sqlite3.Error:
                        pass

        # 6. handle table - get handles from selected messages
        if message_ids:
            msg_placeholders = ",".join("?" * len(message_ids))
            src_cursor.execute(
                f"""SELECT DISTINCT h.* FROM handle h
                    JOIN message m ON m.handle_id = h.ROWID
                    WHERE m.ROWID IN ({msg_placeholders})""",
                list(message_ids),
            )
            rows = src_cursor.fetchall()
            if rows:
                cols = [desc[0] for desc in src_cursor.description]
                insert_sql = f"INSERT INTO handle ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                for row in rows:
                    try:
                        dest_cursor.execute(insert_sql, list(row))
                    except sqlite3.Error:
                        pass

        # 7. chat_handle_join - for selected chats (only handles we've inserted)
        if chat_ids:
            # Get the handle IDs we actually inserted
            dest_cursor.execute("SELECT ROWID FROM handle")
            inserted_handle_ids = {row[0] for row in dest_cursor.fetchall()}

            if inserted_handle_ids:
                chat_placeholders = ",".join("?" * len(chat_ids))
                handle_placeholders = ",".join("?" * len(inserted_handle_ids))
                src_cursor.execute(
                    f"""SELECT * FROM chat_handle_join
                        WHERE chat_id IN ({chat_placeholders})
                        AND handle_id IN ({handle_placeholders})""",
                    list(chat_ids) + list(inserted_handle_ids),
                )
                rows = src_cursor.fetchall()
                if rows:
                    cols = [desc[0] for desc in src_cursor.description]
                    insert_sql = f"INSERT INTO chat_handle_join ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})"
                    for row in rows:
                        try:
                            dest_cursor.execute(insert_sql, list(row))
                        except sqlite3.Error:
                            pass

        dest_conn.commit()

        logger.debug(
            f"Created filtered iMessage DB: {len(attachment_ids)} attachments, "
            f"{len(message_ids)} messages, {len(chat_ids)} chats"
        )
        return True

    except (sqlite3.Error, OSError) as e:
        logger.warning(f"Error creating filtered iMessage database: {e}")
        # Do NOT fall back to copying the original database - this would
        # create thousands of orphaned metadata entries
        if dest_db.exists():
            try:
                dest_db.unlink()
            except OSError:
                pass
        return False

    finally:
        if src_conn:
            try:
                src_conn.close()
            except sqlite3.Error:
                pass
        if dest_conn:
            try:
                dest_conn.close()
            except sqlite3.Error:
                pass


def sample_instagram_old(export_path: Path, max_files: int = 15) -> SampledFiles:
    """Sample old-format Instagram: UTC-timestamped files + sidecars.

    Structure: instagram-username/YYYY-MM-DD_HH-MM-SS_UTC.jpg + .txt
    """
    result = SampledFiles()

    # Find files matching old Instagram pattern
    pattern = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_UTC")

    all_media: List[Path] = []
    for f in export_path.iterdir():
        if f.is_file() and is_media_file(f) and pattern.search(f.name):
            all_media.append(f)

    if not all_media:
        return result

    random.shuffle(all_media)
    selected = all_media[:max_files]

    for media_file in selected:
        result.files_to_copy.append(media_file)
        # Old format uses .txt and .json sidecars
        for ext in [".txt", ".json"]:
            sidecar = media_file.parent / f"{media_file.stem}{ext}"
            if sidecar.exists():
                result.files_to_copy.append(sidecar)

    result.total_size = sum(
        f.stat().st_size for f in result.files_to_copy if f.exists()
    )
    return result


# Thread-safe lock for directory creation
_dir_lock = threading.Lock()


def _copy_single_file(src_file: Path, source_root: Path, dest_root: Path) -> bool:
    """Copy a single file, preserving directory structure. Thread-safe."""
    try:
        rel_path = src_file.relative_to(source_root)
    except ValueError:
        rel_path = Path(src_file.name)

    dest_path = dest_root / rel_path

    with _dir_lock:
        dest_path.parent.mkdir(parents=True, exist_ok=True)

    if not dest_path.exists():
        shutil.copy2(src_file, dest_path)
        return True
    return False


def copy_files_parallel(
    files: List[Path],
    source_root: Path,
    dest_root: Path,
    dry_run: bool = False,
    max_workers: int = 8,
) -> int:
    """Copy files in parallel, preserving directory structure."""
    if not files:
        return 0

    if dry_run:
        return len(files)

    copied = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_copy_single_file, f, source_root, dest_root): f
            for f in files
        }
        for future in as_completed(futures):
            try:
                if future.result():
                    copied += 1
            except Exception as e:
                logger.debug(f"Failed to copy {futures[future]}: {e}")
    return copied


def detect_and_sample_export(export_path: Path) -> Tuple[str, SampledFiles]:
    """Detect export type and sample appropriately.

    Returns: (processor_name, sampled_files)
    """
    name = export_path.name.lower()

    # Google exports
    if re.match(r"google-.*-\d{8}", name):
        photos_result = sample_google_photos(export_path)
        chat_result = sample_google_chat(export_path)

        # Combine results
        combined = SampledFiles()
        combined.files_to_copy = photos_result.files_to_copy + chat_result.files_to_copy
        combined.files_to_write = (
            photos_result.files_to_write + chat_result.files_to_write
        )
        combined.total_size = photos_result.total_size + chat_result.total_size

        if photos_result.files_to_copy and chat_result.files_to_copy:
            return "Google Photos+Chat", combined
        elif photos_result.files_to_copy:
            return "Google Photos", combined
        elif chat_result.files_to_copy:
            return "Google Chat", combined
        return "Google", combined

    # Instagram exports
    if re.match(r"instagram-.*-\d{8}", name):
        messages_result = sample_instagram_messages(export_path)
        public_result = sample_instagram_public(export_path)
        old_result = sample_instagram_old(export_path)

        combined = SampledFiles()
        combined.files_to_copy = (
            messages_result.files_to_copy
            + public_result.files_to_copy
            + old_result.files_to_copy
        )
        combined.total_size = (
            messages_result.total_size
            + public_result.total_size
            + old_result.total_size
        )

        # Include start_here.html if present
        start = export_path / "start_here.html"
        if start.exists():
            combined.files_to_copy.append(start)

        return "Instagram", combined

    # Snapchat exports
    if re.match(r"snapchat-.*-\d{8}", name):
        memories_result = sample_snapchat_memories(export_path)
        messages_result = sample_snapchat_messages(export_path)

        combined = SampledFiles()
        combined.files_to_copy = (
            memories_result.files_to_copy + messages_result.files_to_copy
        )
        combined.files_to_write = (
            memories_result.files_to_write + messages_result.files_to_write
        )
        combined.total_size = memories_result.total_size + messages_result.total_size

        return "Snapchat", combined

    # Discord exports
    if re.match(r"discord-.*-\d{8}", name):
        return "Discord", sample_discord(export_path)

    # iMessage exports (various naming patterns)
    # - imessage-iph13p-20220426, imessage-m3_mbp-20251202
    # - iph13p-messages-20220426, mac-messages-20251202
    if re.match(r"(imessage-|iph.*-messages-|mac-messages-)\S+-?\d{8}", name):
        return "iMessage", sample_imessage(export_path)

    return "Unknown", SampledFiles()


@dataclass
class ExportResult:
    """Result from processing a single export directory."""

    export_name: str
    processor_name: str
    sampled: SampledFiles


def process_single_export(export_dir: Path, local_seed: int) -> Optional[ExportResult]:
    """Process a single export directory with thread-local random state."""
    local_random = random.Random(local_seed)

    # Temporarily set random state
    old_state = random.getstate()
    random.setstate(local_random.getstate())

    try:
        processor_name, sampled = detect_and_sample_export(export_dir)

        if not sampled.files_to_copy and not sampled.files_to_write:
            return None

        return ExportResult(
            export_name=export_dir.name,
            processor_name=processor_name,
            sampled=sampled,
        )
    finally:
        random.setstate(old_state)


def create_test_dataset(
    source_dir: Path,
    output_dir: Path,
    dry_run: bool = False,
    seed: Optional[int] = None,
    max_workers: int = 4,
) -> Dict:
    """Create a trimmed test dataset from the source originals."""
    # Generate random seed if not provided
    if seed is None:
        seed = random.randint(0, 2**31 - 1)
        logger.info(f"Using random seed: {seed}")

    random.seed(seed)

    stats = {
        "exports_processed": 0,
        "files_copied": 0,
        "files_written": 0,
        "total_size_bytes": 0,
        "by_processor": {},
        "seed": seed,  # Include seed in stats for reproducibility
    }

    if not source_dir.exists():
        logger.error(f"Source directory does not exist: {source_dir}")
        return stats

    export_dirs = [d for d in source_dir.iterdir() if d.is_dir()]
    logger.info(f"Found {len(export_dirs)} potential export directories")
    logger.info(f"Using {max_workers} worker threads")

    # Phase 1: Sample files from all exports in parallel
    results: List[ExportResult] = []
    base_seed = seed

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                process_single_export, export_dir, base_seed + i
            ): export_dir
            for i, export_dir in enumerate(sorted(export_dirs))
        }

        for future in as_completed(futures):
            export_dir = futures[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
                    logger.info(
                        f"Sampled {result.processor_name}: {result.export_name} "
                        f"({len(result.sampled.files_to_copy)} files, "
                        f"{result.sampled.total_size / 1024 / 1024:.1f} MB)"
                    )
            except Exception as e:
                logger.warning(f"Failed to process {export_dir.name}: {e}")

    # Phase 2: Copy all files
    logger.info(f"Processing {len(results)} exports...")

    for result in results:
        # Copy files
        copied = copy_files_parallel(
            result.sampled.files_to_copy,
            source_dir,
            output_dir,
            dry_run,
            max_workers=max_workers * 2,
        )

        # Write filtered files
        written = 0
        for src_path, content in result.sampled.files_to_write:
            try:
                rel_path = src_path.relative_to(source_dir)
            except ValueError:
                rel_path = Path(src_path.name)

            dest_path = output_dir / rel_path
            if dry_run:
                logger.debug(f"  Would write filtered: {rel_path}")
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with open(dest_path, "w", encoding="utf-8") as f:
                    f.write(content)
            written += 1

        # Create filtered iMessage database if applicable
        if result.sampled.filtered_imessage_db:
            src_db, att_ids, msg_ids, chat_ids = result.sampled.filtered_imessage_db
            try:
                rel_path = src_db.relative_to(source_dir)
            except ValueError:
                rel_path = Path(src_db.parent.name) / src_db.name
            dest_db = output_dir / rel_path
            if dry_run:
                logger.debug(
                    f"  Would create filtered iMessage DB: {rel_path} "
                    f"({len(att_ids)} attachments)"
                )
            else:
                create_filtered_imessage_db(src_db, dest_db, att_ids, msg_ids, chat_ids)
            written += 1

        # Update stats
        stats["exports_processed"] += 1
        stats["files_copied"] += copied
        stats["files_written"] += written
        stats["total_size_bytes"] += result.sampled.total_size

        # Count media files specifically
        media_file_count = sum(
            1 for f in result.sampled.files_to_copy if is_media_file(f)
        )

        proc_name = result.processor_name
        if proc_name not in stats["by_processor"]:
            stats["by_processor"][proc_name] = {
                "exports": 0,
                "files": 0,
                "media_files": 0,
            }
        stats["by_processor"][proc_name]["exports"] += 1
        stats["by_processor"][proc_name]["files"] += len(result.sampled.files_to_copy)
        stats["by_processor"][proc_name]["media_files"] += media_file_count

    # Copy standalone files (like contacts.vcf), excluding archives
    archive_extensions = {".zip", ".rar", ".tar", ".gz", ".7z", ".bz2", ".xz"}
    standalone_files = [
        f
        for f in source_dir.iterdir()
        if f.is_file() and f.suffix.lower() not in archive_extensions
    ]
    if standalone_files:
        logger.info(f"Copying {len(standalone_files)} standalone files")
        copy_files_parallel(standalone_files, source_dir, output_dir, dry_run)

    return stats


def main():
    cpu_count = os.cpu_count() or 4

    parser = argparse.ArgumentParser(
        description="Create a trimmed test dataset from media originals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--source",
        "-s",
        type=Path,
        default=Path("/mnt/media/originals"),
        help="Path to the originals directory (default: /mnt/media/originals)",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("/mnt/media/temp/test_dataset"),
        help="Path for test dataset (default: /mnt/media/temp/test_dataset)",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be done without copying files",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducible sampling (default: random)",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=cpu_count,
        help=f"Number of parallel worker threads (default: {cpu_count})",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose output",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Overwrite output directory without prompting",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if args.dry_run:
        logger.info("DRY RUN - no files will be copied")

    if not args.source.exists():
        logger.error(f"Source directory does not exist: {args.source}")
        sys.exit(1)

    if args.output.exists() and not args.dry_run:
        if args.force:
            shutil.rmtree(args.output)
        else:
            response = input(
                f"Output directory {args.output} exists. Overwrite? [y/N] "
            )
            if response.lower() != "y":
                logger.info("Aborted")
                sys.exit(0)
            shutil.rmtree(args.output)

    if not args.dry_run:
        args.output.mkdir(parents=True, exist_ok=True)

    logger.info(f"Creating test dataset from {args.source}")
    logger.info(f"Output directory: {args.output}")

    stats = create_test_dataset(
        source_dir=args.source,
        output_dir=args.output,
        dry_run=args.dry_run,
        seed=args.seed,
        max_workers=args.workers,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("TEST DATASET CREATION SUMMARY")
    print("=" * 60)
    print(f"Exports processed: {stats['exports_processed']}")
    print(f"Files copied: {stats['files_copied']}")
    print(f"Files written (filtered): {stats['files_written']}")
    print(f"Total size: {stats['total_size_bytes'] / 1024 / 1024:.1f} MB")
    print(
        f"Seed used: {stats.get('seed', 'unknown')} (use --seed {stats.get('seed', 'N')} to reproduce)"
    )
    print()
    print("By processor:")
    for proc, proc_stats in stats["by_processor"].items():
        media_count = proc_stats.get("media_files", 0)
        print(
            f"  {proc}: {proc_stats['exports']} exports, {proc_stats['files']} files ({media_count} media)"
        )

    # Validate coverage - warn if expected processors are missing or have no media
    warnings = []
    for proc, proc_stats in stats["by_processor"].items():
        media_count = proc_stats.get("media_files", 0)
        if media_count == 0:
            warnings.append(
                f"  WARNING: {proc} has 0 media files - processor may fail or skip"
            )

    if warnings:
        print()
        print("WARNINGS:")
        for w in warnings:
            print(w)

    print("=" * 60)

    if args.dry_run:
        print("\nThis was a dry run. No files were copied.")
        print(f"Run without --dry-run to create the dataset at {args.output}")


if __name__ == "__main__":
    main()
