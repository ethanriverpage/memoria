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
    conversations: List[Path] = []
    for subdir in ["Groups", "Users"]:
        sub_path = chat_dir / subdir
        if sub_path.exists():
            for conv_dir in sub_path.iterdir():
                if conv_dir.is_dir() and (conv_dir / "messages.json").exists():
                    conversations.append(conv_dir)

    if not conversations:
        return result

    # Sample conversations
    random.shuffle(conversations)
    selected_convs = conversations[:max_conversations]

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
    """Sample Snapchat Messages: JSON files + sampled chat_media.

    Structure: snapchat-username/messages/{json/,chat_media/,html/}
    Note: chat_media is directly under messages/, not inside json/
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

    # Always copy the JSON files (they're not that large)
    for json_file in ["chat_history.json", "snap_history.json"]:
        path = json_dir / json_file
        if path.exists():
            result.files_to_copy.append(path)

    # Sample media from chat_media
    if chat_media_dir.exists():
        all_media = [
            f for f in chat_media_dir.iterdir() if f.is_file() and is_media_file(f)
        ]
        random.shuffle(all_media)
        result.files_to_copy.extend(all_media[:max_media])

        # Also include some overlays if present
        overlays = [
            f
            for f in chat_media_dir.iterdir()
            if f.is_file() and "overlay" in f.name.lower()
        ]
        result.files_to_copy.extend(overlays[:5])

    # Copy HTML files too
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
    export_path: Path, max_conversations: int = 5
) -> SampledFiles:
    """Sample Instagram Messages: complete conversation folders with HTML + media.

    Structure: instagram-username/your_instagram_activity/messages/inbox/{conv}/
               or instagram-username/messages/inbox/{conv}/
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
    convs_with_media: List[Path] = []
    convs_without_media: List[Path] = []

    for conv_dir in inbox_dir.iterdir():
        if conv_dir.is_dir():
            html_files = list(conv_dir.glob("message_*.html"))
            if html_files:
                # Check if this conversation has media
                has_media = False
                for media_subdir in ["photos", "videos", "audio"]:
                    media_path = conv_dir / media_subdir
                    if media_path.exists() and any(media_path.iterdir()):
                        has_media = True
                        break

                if has_media:
                    convs_with_media.append(conv_dir)
                else:
                    convs_without_media.append(conv_dir)

    # Prioritize conversations WITH media
    selected: List[Path] = []
    random.shuffle(convs_with_media)
    selected.extend(convs_with_media[: max_conversations - 1])

    # Add at least one without media for edge case testing
    if convs_without_media:
        random.shuffle(convs_without_media)
        selected.append(convs_without_media[0])

    # Fill remaining slots with more media convs
    if len(selected) < max_conversations and len(convs_with_media) > len(selected):
        remaining = [c for c in convs_with_media if c not in selected]
        selected.extend(remaining[: max_conversations - len(selected)])

    # Copy entire conversation folders (limited media per conversation)
    for conv_dir in selected:
        # Always copy HTML files
        for f in conv_dir.glob("message_*.html"):
            result.files_to_copy.append(f)

        # Copy limited media from each subdirectory
        for media_subdir in ["photos", "videos", "audio"]:
            media_path = conv_dir / media_subdir
            if media_path.exists():
                media_files = [f for f in media_path.iterdir() if f.is_file()]
                random.shuffle(media_files)
                result.files_to_copy.extend(media_files[:3])  # Max 3 per type

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


def sample_imessage(export_path: Path, max_attachments: int = 10) -> SampledFiles:
    """Sample iMessage: database file + sampled attachments.

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

    # Always copy the database (required for processing)
    result.files_to_copy.append(chat_db)

    # Copy WAL/SHM files if present
    for suffix in ["-shm", "-wal"]:
        wal_file = chat_db.parent / f"{chat_db.name}{suffix}"
        if wal_file.exists():
            result.files_to_copy.append(wal_file)

    # Sample attachments using fast find
    if attachments_dir.exists():
        # iMessage attachments are usually in deep paths like AA/BB/guid/file
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

    # iMessage exports
    if re.match(r"(iph.*|mac)-messages-\d{8}", name):
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

        # Update stats
        stats["exports_processed"] += 1
        stats["files_copied"] += copied
        stats["files_written"] += written
        stats["total_size_bytes"] += result.sampled.total_size

        proc_name = result.processor_name
        if proc_name not in stats["by_processor"]:
            stats["by_processor"][proc_name] = {"exports": 0, "files": 0}
        stats["by_processor"][proc_name]["exports"] += 1
        stats["by_processor"][proc_name]["files"] += len(result.sampled.files_to_copy)

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
        print(f"  {proc}: {proc_stats['exports']} exports, {proc_stats['files']} files")
    print("=" * 60)

    if args.dry_run:
        print("\nThis was a dry run. No files were copied.")
        print(f"Run without --dry-run to create the dataset at {args.output}")


if __name__ == "__main__":
    main()
