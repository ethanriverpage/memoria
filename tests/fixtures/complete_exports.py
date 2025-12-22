#!/usr/bin/env python3
"""Complete test export fixtures for integration testing.

These fixtures create comprehensive export structures that closely mimic
real exports, with multiple files, edge cases, and realistic metadata.

Unlike the minimal generators in generators.py, these create exports with:
- Multiple albums/conversations
- Various file types
- Edge cases (unicode, special chars, etc.)
- Realistic metadata structures
"""

import json
from pathlib import Path
from typing import Dict

from tests.fixtures.media_samples import write_media_file
from tests.fixtures.generators import (
    create_google_photos_export,
    create_google_chat_export,
    create_google_voice_export,
    create_snapchat_memories_export,
    create_snapchat_messages_export,
    create_instagram_messages_export,
    create_instagram_public_export,
    create_instaloader_export,
    create_discord_export,
    create_imessage_mac_export,
    create_imessage_imazing_export,
)


def create_comprehensive_google_photos_export(base_path: Path) -> Path:
    """Create a comprehensive Google Photos export with multiple albums and edge cases.

    Includes:
    - Multiple albums with different content
    - Various image and video formats
    - Files with unicode names
    - Files with special characters
    - Archived content
    - Shared album indicators
    """
    albums = {
        "Vacation 2021": [
            "beach_sunset.jpg",
            "mountain_view.heic",
            "video_clip.mp4",
        ],
        "Family Photos": [
            "birthday_party.jpg",
            "graduation.png",
            "holiday_dinner.jpg",
        ],
        "Screenshots": [
            "screenshot_2021-01-15.png",
            "screenshot_2021-01-16.png",
        ],
        "Camera Roll": [
            "IMG_0001.HEIC",
            "IMG_0002.HEIC",
            "IMG_0003.MOV",
        ],
        "Shared Album - Trip": [
            "group_photo.jpg",
        ],
    }

    return create_google_photos_export(
        base_path,
        username="testuser",
        albums=albums,
        include_json_metadata=True,
    )


def create_comprehensive_discord_export(base_path: Path) -> Path:
    """Create a comprehensive Discord export with multiple channels.

    Includes:
    - Server channels
    - DM conversations
    - Group DMs
    - Various attachment types
    """
    channels = {
        "c100001": {
            "name": "general",
            "type": "server",
            "guild_name": "Gaming Server",
            "messages": [
                {
                    "ID": "msg001",
                    "Timestamp": "2021-01-01 12:00:00",
                    "Contents": "Hello everyone!",
                    "Attachments": "",
                },
                {
                    "ID": "msg002",
                    "Timestamp": "2021-01-01 12:05:00",
                    "Contents": "Check out this meme",
                    "Attachments": "https://cdn.discord.com/attachments/1/2/meme.jpg",
                },
            ],
        },
        "c100002": {
            "name": "memes",
            "type": "server",
            "guild_name": "Gaming Server",
            "messages": [
                {
                    "ID": "msg003",
                    "Timestamp": "2021-01-02 14:00:00",
                    "Contents": "LOL",
                    "Attachments": "https://cdn.discord.com/attachments/1/2/funny.png",
                },
            ],
        },
        "c200001": {
            "name": "DM with Friend",
            "type": "dm",
            "messages": [
                {
                    "ID": "msg004",
                    "Timestamp": "2021-01-03 10:00:00",
                    "Contents": "Hey!",
                    "Attachments": "",
                },
                {
                    "ID": "msg005",
                    "Timestamp": "2021-01-03 10:01:00",
                    "Contents": "Check this video",
                    "Attachments": "https://cdn.discord.com/attachments/1/2/video.mp4",
                },
            ],
        },
        "c200002": {
            "name": "Group Chat",
            "type": "dm",
            "messages": [
                {
                    "ID": "msg006",
                    "Timestamp": "2021-01-04 16:00:00",
                    "Contents": "Group photo from today",
                    "Attachments": "https://cdn.discord.com/attachments/1/2/group.jpg",
                },
            ],
        },
    }

    return create_discord_export(base_path, username="testuser", channels=channels)


def create_comprehensive_snapchat_memories_export(base_path: Path) -> Path:
    """Create a comprehensive Snapchat Memories export.

    Includes:
    - Multiple memories with overlays
    - Videos and images
    - Various dates
    """
    memories = [
        {
            "date": "2021-01-01 12:00:00 UTC",
            "media_type": "Image",
            "media_filename": "snap_001.jpg",
            "overlay_filename": "overlay_001.png",
        },
        {
            "date": "2021-01-02 14:30:00 UTC",
            "media_type": "Video",
            "media_filename": "snap_002.mp4",
            "overlay_filename": "overlay_002.png",
        },
        {
            "date": "2021-01-03 09:15:00 UTC",
            "media_type": "Image",
            "media_filename": "snap_003.jpg",
            "overlay_filename": None,  # No overlay
        },
        {
            "date": "2021-02-14 18:00:00 UTC",
            "media_type": "Image",
            "media_filename": "valentines_snap.jpg",
            "overlay_filename": "valentines_overlay.png",
        },
        {
            "date": "2021-06-15 10:00:00 UTC",
            "media_type": "Video",
            "media_filename": "summer_video.mp4",
            "overlay_filename": "summer_overlay.png",
        },
    ]

    return create_snapchat_memories_export(
        base_path,
        username="testuser",
        memories=memories,
        include_overlays=True,
    )


def create_comprehensive_instagram_export(base_path: Path) -> Path:
    """Create a comprehensive Instagram Messages export.

    Includes:
    - Multiple conversations
    - Various attachment types
    - Long message threads
    """
    conversations = {
        "friend_one_12345": {
            "title": "Friend One",
            "messages": [
                {
                    "sender": "Friend One",
                    "timestamp": "2021-01-01T12:00:00",
                    "content": "Hey!",
                },
                {
                    "sender": "testuser",
                    "timestamp": "2021-01-01T12:01:00",
                    "content": "Hi there!",
                },
                {
                    "sender": "Friend One",
                    "timestamp": "2021-01-01T12:02:00",
                    "content": "Check this photo",
                    "media": "photo_from_friend.jpg",
                },
            ],
        },
        "group_chat_67890": {
            "title": "Group Chat",
            "messages": [
                {
                    "sender": "User A",
                    "timestamp": "2021-01-02T10:00:00",
                    "content": "Group message 1",
                },
                {
                    "sender": "User B",
                    "timestamp": "2021-01-02T10:01:00",
                    "content": "Group message 2",
                    "media": "group_photo.jpg",
                },
                {
                    "sender": "testuser",
                    "timestamp": "2021-01-02T10:02:00",
                    "content": "My response",
                },
            ],
        },
    }

    return create_instagram_messages_export(
        base_path,
        username="testuser",
        conversations=conversations,
        new_format=True,
    )


def create_all_processor_exports(base_path: Path) -> Dict[str, Path]:
    """Create exports for all 11 processors in separate directories.

    Returns:
        Dict mapping processor name to export path
    """
    exports = {}

    # Create directories for each processor
    processors = [
        "google_photos",
        "google_chat",
        "google_voice",
        "snapchat_memories",
        "snapchat_messages",
        "instagram_messages",
        "instagram_public",
        "instaloader",
        "discord",
        "imessage",
        "imessage_imazing",
    ]

    for proc in processors:
        proc_dir = base_path / proc
        proc_dir.mkdir(parents=True)
        exports[proc] = proc_dir

    # Create exports
    create_google_photos_export(exports["google_photos"])
    create_google_chat_export(exports["google_chat"])
    create_google_voice_export(exports["google_voice"])
    create_snapchat_memories_export(exports["snapchat_memories"])
    create_snapchat_messages_export(exports["snapchat_messages"])
    create_instagram_messages_export(exports["instagram_messages"])
    create_instagram_public_export(exports["instagram_public"])
    create_instaloader_export(exports["instaloader"])
    create_discord_export(exports["discord"])
    create_imessage_mac_export(exports["imessage"])

    # For iMessage iMazing, create a specific structure
    _create_imazing_export(exports["imessage_imazing"])

    return exports


def _create_imazing_export(base_path: Path) -> Path:
    """Create a minimal iMazing iMessage export using the generator."""
    return create_imessage_imazing_export(base_path)


def create_export_with_edge_cases(base_path: Path, processor_type: str) -> Path:
    """Create an export with various edge cases for testing robustness.

    Edge cases included:
    - Filenames with unicode characters
    - Very long filenames
    - Filenames with special characters
    - Empty directories
    - Missing metadata for some files
    - Duplicate content
    """
    export_dir = base_path / "edge_case_export"
    export_dir.mkdir(parents=True)

    if processor_type == "google_photos":
        # Create with edge case filenames
        photos_dir = export_dir / "Google Photos"
        photos_dir.mkdir()

        # Album with unicode name
        unicode_album = photos_dir / "Vacation 2021"
        unicode_album.mkdir()
        write_media_file(unicode_album / "cafe_photo.jpg", "jpeg")
        (unicode_album / "cafe_photo.jpg.json").write_text(json.dumps({
            "title": "cafe_photo.jpg",
            "photoTakenTime": {"timestamp": "1609459200"},
        }))

        # Album with special characters
        special_album = photos_dir / "Photos (1) [backup]"
        special_album.mkdir()
        write_media_file(special_album / "photo-copy.jpg", "jpeg")
        (special_album / "photo-copy.jpg.json").write_text(json.dumps({
            "title": "photo-copy.jpg",
            "photoTakenTime": {"timestamp": "1609459200"},
        }))

        # File without metadata
        normal_album = photos_dir / "Camera"
        normal_album.mkdir()
        write_media_file(normal_album / "orphan_photo.jpg", "jpeg")
        # No JSON for this one

        # Empty album
        (photos_dir / "Empty Album").mkdir()

        # Duplicate content
        write_media_file(unicode_album / "duplicate1.jpg", "jpeg")
        write_media_file(special_album / "duplicate2.jpg", "jpeg")
        # Same content in different locations

    return export_dir


def create_large_export(base_path: Path, num_files: int = 100) -> Path:
    """Create a large export for performance testing.

    Args:
        base_path: Base directory for export
        num_files: Number of files to create

    Returns:
        Path to created export
    """
    export_dir = base_path / "large_export"
    _photos_dir = export_dir / "Google Photos"  # noqa: F841 - Reserved for future use

    # Distribute files across multiple albums
    num_albums = max(1, num_files // 20)
    files_per_album = num_files // num_albums

    albums = {}
    for album_idx in range(num_albums):
        album_name = f"Album_{album_idx:03d}"
        albums[album_name] = [
            f"photo_{album_idx:03d}_{file_idx:04d}.jpg"
            for file_idx in range(files_per_album)
        ]

    return create_google_photos_export(
        export_dir,
        username="testuser",
        albums=albums,
        include_json_metadata=True,
    )

