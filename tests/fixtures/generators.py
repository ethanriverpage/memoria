"""
Test export generator functions.

These functions create minimal but valid export directory structures
for each processor type. Used by fixtures to create test data.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional

from tests.fixtures.media_samples import (
    write_media_file,
    create_imessage_chat_db,
)


def create_google_photos_export(
    base_path: Path,
    username: str = "testuser",
    albums: Optional[Dict[str, List[str]]] = None,
    include_json_metadata: bool = True,
) -> Path:
    """Create a minimal Google Photos export structure.

    Structure:
        {base_path}/Google Photos/
            {album_name}/
                photo1.jpg
                photo1.jpg.json (optional metadata)

    Args:
        base_path: Base directory for the export
        username: Username for naming (used in metadata)
        albums: Dict mapping album names to list of photo filenames
        include_json_metadata: Whether to include JSON metadata files

    Returns:
        Path to the created export directory
    """
    if albums is None:
        albums = {"Test Album": ["photo1.jpg", "photo2.png"]}

    photos_dir = base_path / "Google Photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    # Create Profile/Profile.json for username detection
    profile_dir = base_path / "Profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    profile_json = {
        "name": {"givenName": username, "familyName": "Test"},
        "emails": [{"value": f"{username}@gmail.com"}],
    }
    (profile_dir / "Profile.json").write_text(json.dumps(profile_json, indent=2))

    for album_name, files in albums.items():
        album_dir = photos_dir / album_name
        album_dir.mkdir(exist_ok=True)

        for filename in files:
            # Determine media type from extension
            ext = filename.split(".")[-1].lower()
            media_path = album_dir / filename
            write_media_file(media_path, ext)

            # Create corresponding JSON metadata
            if include_json_metadata:
                json_path = album_dir / f"{filename}.json"
                metadata = {
                    "title": filename,
                    "photoTakenTime": {
                        "timestamp": "1609459200",  # 2021-01-01
                        "formatted": "Jan 1, 2021, 12:00:00 AM UTC",
                    },
                    "geoData": {
                        "latitude": 0.0,
                        "longitude": 0.0,
                        "altitude": 0.0,
                    },
                    "description": f"Test photo in {album_name}",
                }
                json_path.write_text(json.dumps(metadata, indent=2))

    return base_path


def create_google_chat_export(
    base_path: Path,
    username: str = "testuser",
    conversations: Optional[Dict[str, List[Dict]]] = None,
    include_media: bool = True,
) -> Path:
    """Create a minimal Google Chat export structure.

    Structure:
        {base_path}/Google Chat/
            Groups/{group_name}/
                group_info.json
                messages.json
                {media_file}
            Users/{user_name}/
                messages.json
                {media_file}

    Args:
        base_path: Base directory for the export
        username: Username for naming
        conversations: Dict mapping conv names to list of message dicts
        include_media: Whether to include media files

    Returns:
        Path to the created export directory
    """
    if conversations is None:
        conversations = {
            "Groups/Test Group": [
                {
                    "creator": {"name": "User One", "email": "user1@example.com"},
                    "created_date": "2021-01-01T12:00:00Z",
                    "text": "Hello!",
                    "attached_files": (
                        [{"export_name": "image.jpg"}] if include_media else []
                    ),
                }
            ],
            "Users/Other User": [
                {
                    "creator": {"name": username, "email": f"{username}@example.com"},
                    "created_date": "2021-01-02T12:00:00Z",
                    "text": "Hi there",
                    "attached_files": [],
                }
            ],
        }

    chat_dir = base_path / "Google Chat"
    groups_dir = chat_dir / "Groups"
    users_dir = chat_dir / "Users"
    groups_dir.mkdir(parents=True, exist_ok=True)
    users_dir.mkdir(parents=True, exist_ok=True)

    for conv_path, messages in conversations.items():
        conv_dir = chat_dir / conv_path
        conv_dir.mkdir(parents=True, exist_ok=True)

        # Write messages.json
        messages_file = conv_dir / "messages.json"
        messages_file.write_text(json.dumps({"messages": messages}, indent=2))

        # For Groups, also create group_info.json (required for detection)
        if conv_path.startswith("Groups/"):
            group_info = {
                "name": conv_path.split("/")[-1],
                "members": [{"name": "Member 1"}, {"name": "Member 2"}],
            }
            (conv_dir / "group_info.json").write_text(json.dumps(group_info, indent=2))

        # Create media files referenced in messages
        if include_media:
            for msg in messages:
                for attached in msg.get("attached_files", []):
                    filename = attached.get("export_name")
                    if filename:
                        media_path = conv_dir / filename
                        ext = filename.split(".")[-1].lower()
                        write_media_file(media_path, ext)

    return base_path


def create_google_voice_export(
    base_path: Path,
    username: str = "testuser",
    calls: Optional[List[Dict]] = None,
) -> Path:
    """Create a minimal Google Voice export structure.

    Structure:
        {base_path}/Voice/Calls/
            {contact} - Text - {timestamp}.html
            {media_file}

    Note: HTML filename pattern must match: +XXXXXXXXXX - Text - YYYY-MM-DDTHH_MM_SSZ.html

    Args:
        base_path: Base directory for the export
        username: Username for naming
        calls: List of call/text metadata dicts

    Returns:
        Path to the created export directory
    """
    if calls is None:
        calls = [
            {
                "contact": "+1234567890",
                "type": "Text",
                "timestamp": "2021-01-01T12_00_00Z",  # Format: YYYY-MM-DDTHH_MM_SSZ
                "messages": [
                    {"sender": "Me", "text": "Hello", "time": "12:00 PM"},
                    {"sender": "+1234567890", "text": "Hi!", "time": "12:01 PM"},
                ],
                "media": ["image.jpg"],
            }
        ]

    voice_dir = base_path / "Voice" / "Calls"
    voice_dir.mkdir(parents=True, exist_ok=True)

    for call in calls:
        contact = call["contact"]
        call_type = call["type"]
        # Ensure timestamp format matches detection pattern: YYYY-MM-DDTHH_MM_SSZ
        timestamp = call["timestamp"]
        if ":" in timestamp:
            # Convert from ISO format to Voice format
            timestamp = timestamp.replace(":", "_").rstrip("Z") + "Z"

        # Create HTML file with exact pattern required by detection
        # Pattern: +XXXXXXXXXX - Text - YYYY-MM-DDTHH_MM_SSZ.html
        html_filename = f"{contact} - {call_type} - {timestamp}.html"
        html_content = f"""<!DOCTYPE html>
<html>
<head><title>{call_type} with {contact}</title></head>
<body>
<div class="message">
"""
        for msg in call.get("messages", []):
            html_content += f'<div class="sender">{msg["sender"]}</div>'
            html_content += f'<div class="text">{msg["text"]}</div>'
            html_content += f'<div class="time">{msg["time"]}</div>'
        html_content += """</div>
</body>
</html>"""
        (voice_dir / html_filename).write_text(html_content)

        # Create media files
        for media_file in call.get("media", []):
            ext = media_file.split(".")[-1].lower()
            write_media_file(voice_dir / media_file, ext)

    return base_path


def create_snapchat_memories_export(
    base_path: Path,
    username: str = "testuser",
    memories: Optional[List[Dict]] = None,
    include_overlays: bool = True,
) -> Path:
    """Create a minimal Snapchat Memories export structure.

    Structure:
        {base_path}/
            media/
                {media_filename}
            overlays/
                {overlay_filename}
            metadata.json

    Args:
        base_path: Base directory for the export
        username: Username for naming
        memories: List of memory metadata dicts
        include_overlays: Whether to include overlay files

    Returns:
        Path to the created export directory
    """
    if memories is None:
        memories = [
            {
                "date": "2021-01-01 12:00:00 UTC",
                "media_type": "Image",
                "media_filename": "memory1.jpg",
                "overlay_filename": "overlay1.png" if include_overlays else None,
            },
            {
                "date": "2021-01-02 12:00:00 UTC",
                "media_type": "Video",
                "media_filename": "memory2.mp4",
                "overlay_filename": "overlay2.png" if include_overlays else None,
            },
        ]

    # Create directories
    media_dir = base_path / "media"
    overlays_dir = base_path / "overlays"
    media_dir.mkdir(parents=True, exist_ok=True)
    overlays_dir.mkdir(parents=True, exist_ok=True)

    # Create media and overlay files
    for memory in memories:
        media_filename = memory["media_filename"]
        ext = media_filename.split(".")[-1].lower()
        write_media_file(media_dir / media_filename, ext)

        overlay_filename = memory.get("overlay_filename")
        if overlay_filename and include_overlays:
            write_media_file(overlays_dir / overlay_filename, "png")

    # Write metadata.json
    metadata_path = base_path / "metadata.json"
    metadata_path.write_text(json.dumps(memories, indent=2))

    return base_path


def create_snapchat_messages_export(
    base_path: Path,
    username: str = "testuser",
    conversations: Optional[Dict] = None,
    raw_format: bool = True,
) -> Path:
    """Create a minimal Snapchat Messages export structure.

    Raw Structure:
        {base_path}/json/
            chat_history.json
            snap_history.json

    Preprocessed Structure:
        {base_path}/
            metadata.json
            media/
            overlays/

    Args:
        base_path: Base directory for the export
        username: Username for naming
        conversations: Conversation data
        raw_format: If True, create raw export; if False, create preprocessed

    Returns:
        Path to the created export directory
    """
    if raw_format:
        # Create raw export structure
        json_dir = base_path / "json"
        json_dir.mkdir(parents=True, exist_ok=True)

        # Create chat_media directory (required by preprocessor)
        chat_media_dir = base_path / "chat_media"
        chat_media_dir.mkdir(parents=True, exist_ok=True)

        chat_history = {
            "Received Saved Chat History": [
                {
                    "From": "Other User",
                    "Media Type": "IMAGE",
                    "Created": "2021-01-01 12:00:00 UTC",
                }
            ],
            "Sent Saved Chat History": [
                {
                    "To": "Other User",
                    "Media Type": "VIDEO",
                    "Created": "2021-01-02 12:00:00 UTC",
                }
            ],
        }

        snap_history = {
            "Received Snap History": [
                {
                    "From": "Other User",
                    "Media Type": "Image",
                    "Created": "2021-01-01 12:30:00 UTC",
                }
            ],
            "Sent Snap History": [],
        }

        (json_dir / "chat_history.json").write_text(json.dumps(chat_history, indent=2))
        (json_dir / "snap_history.json").write_text(json.dumps(snap_history, indent=2))
    else:
        # Create preprocessed structure
        media_dir = base_path / "media"
        overlays_dir = base_path / "overlays"
        media_dir.mkdir(parents=True, exist_ok=True)
        overlays_dir.mkdir(parents=True, exist_ok=True)

        if conversations is None:
            conversations = {
                "other_user": {
                    "title": "Other User",
                    "type": "dm",
                    "messages": [
                        {
                            "created": "2021-01-01 12:00:00 UTC",
                            "sender": "Other User",
                            "media_id": "b~abc123",
                            "media_files": ["2021-01-01_b~abc123.jpg"],
                            "source_paths": {
                                "2021-01-01_b~abc123.jpg": str(
                                    media_dir / "2021-01-01_b~abc123.jpg"
                                )
                            },
                        }
                    ],
                }
            }

        metadata = {
            "export_info": {
                "export_path": str(base_path),
                "export_username": username,
                "processed_date": "2021-01-15T00:00:00",
                "platform": "Snapchat",
                "service": "messages",
            },
            "conversations": conversations,
            "unmatched_media": [],
            "file_hashes": {},
        }

        (base_path / "metadata.json").write_text(json.dumps(metadata, indent=2))

        # Create a test media file
        write_media_file(media_dir / "2021-01-01_b~abc123.jpg", "jpeg")

    return base_path


def create_instagram_messages_export(
    base_path: Path,
    username: str = "testuser",
    conversations: Optional[Dict] = None,
    new_format: bool = True,
) -> Path:
    """Create a minimal Instagram Messages export structure.

    New Format:
        {base_path}/your_instagram_activity/messages/inbox/
            {conversation}/
                message_1.html
                {media_files}

    Legacy Format:
        {base_path}/messages/inbox/
            {conversation}/
                message_1.html
                {media_files}

    Args:
        base_path: Base directory for the export
        username: Username for naming
        conversations: Dict mapping conversation names to message data
        new_format: If True, use new format path; if False, use legacy format

    Returns:
        Path to the created export directory
    """
    if new_format:
        inbox_dir = base_path / "your_instagram_activity" / "messages" / "inbox"
        personal_info_dir = base_path / "personal_information" / "personal_information"
    else:
        inbox_dir = base_path / "messages" / "inbox"
        personal_info_dir = base_path / "personal_information"

    inbox_dir.mkdir(parents=True, exist_ok=True)

    # Create personal_information.html for username detection
    personal_info_dir.mkdir(parents=True, exist_ok=True)
    personal_info_html = f"""<!DOCTYPE html>
<html>
<head><title>Personal information</title></head>
<body>
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
<table style="table-layout: fixed;">
<tr><td colspan="2" class="_2pin _a6_q">Email<div><div>{username}@example.com</div></div></td></tr>
<tr><td colspan="2" class="_2pin _a6_q">Username<div><div>{username}</div></div></td></tr>
<tr><td colspan="2" class="_2pin _a6_q">Name<div><div>Test User</div></div></td></tr>
</table>
</div>
</body>
</html>"""
    (personal_info_dir / "personal_information.html").write_text(personal_info_html)

    if conversations is None:
        conversations = {
            "otheruser_123456": {
                "title": "Other User",
                "messages": [
                    {
                        "sender": "Other User",
                        "timestamp": "2021-01-01T12:00:00",
                        "content": "Hello!",
                        "media": "photo.jpg",
                    },
                    {
                        "sender": username,
                        "timestamp": "2021-01-01T12:01:00",
                        "content": "Hi!",
                    },
                ],
            }
        }

    for conv_name, conv_data in conversations.items():
        conv_dir = inbox_dir / conv_name
        conv_dir.mkdir(exist_ok=True)

        # Create message HTML file
        html_content = """<!DOCTYPE html>
<html>
<head><title>Instagram Messages</title></head>
<body>
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
"""
        for msg in conv_data.get("messages", []):
            html_content += f"""<div class="_a6-p">
<div class="_3-95 _a6-o">{msg["sender"]}</div>
<div class="_a6-p">{msg.get("content", "")}</div>
<div class="_3-94 _a6-o">{msg["timestamp"]}</div>
</div>
"""
            # Create media file if present
            if "media" in msg:
                media_filename = msg["media"]
                ext = media_filename.split(".")[-1].lower()
                write_media_file(conv_dir / media_filename, ext)

        html_content += """</div>
</body>
</html>"""
        (conv_dir / "message_1.html").write_text(html_content)

    return base_path


def _format_instagram_timestamp(iso_timestamp: str) -> str:
    """Convert ISO timestamp to Instagram HTML format.

    Input: "2021-01-01T12:00:00"
    Output: "Jan 01, 2021 12:00 pm"
    """
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y %-I:%M %p").lower()
    except (ValueError, AttributeError):
        return "Jan 01, 2021 12:00 pm"


def _generate_instagram_post_html(
    media_path: str,
    caption: str,
    timestamp: str,
) -> str:
    """Generate HTML for a single Instagram post container.

    Uses the CSS classes expected by the preprocessor.
    """
    formatted_ts = _format_instagram_timestamp(timestamp)
    return f"""<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
<h2 class="_3-95 _2pim _a6-h _a6-i">{caption}</h2>
<div class="_3-95 _a6-p">
<a href="{media_path}"><img src="{media_path}" /></a>
</div>
<div class="_3-94 _a6-o">{formatted_ts}</div>
</div>
"""


def create_instagram_public_export(
    base_path: Path,
    username: str = "testuser",
    posts: Optional[List[Dict]] = None,
    include_archived: bool = True,
) -> Path:
    """Create a minimal Instagram Public Media export structure.

    Structure:
        {base_path}/
            media/
                posts/YYYYMM/
                    {media_file}
                archived_posts/YYYYMM/
                    {media_file}
            your_instagram_activity/
                media/
                    posts_1.html      # HTML metadata for posts
                    archived_posts.html  # HTML metadata for archived posts

    Args:
        base_path: Base directory for the export
        username: Username for naming
        posts: List of post metadata dicts with keys:
            - filename: e.g., "202101/photo1.jpg"
            - caption: Post caption text
            - timestamp: ISO format timestamp
            - archived: bool (goes to archived_posts if True)
        include_archived: Whether to include archived posts

    Returns:
        Path to the created export directory
    """
    if posts is None:
        posts = [
            {
                "filename": "202101/photo1.jpg",
                "caption": "Test post",
                "timestamp": "2021-01-01T12:00:00",
                "archived": False,
            },
            {
                "filename": "202101/photo2.jpg",
                "caption": "Archived post",
                "timestamp": "2021-01-15T12:00:00",
                "archived": True,
            },
        ]

    media_dir = base_path / "media"
    posts_dir = media_dir / "posts"
    archived_dir = media_dir / "archived_posts"

    posts_dir.mkdir(parents=True, exist_ok=True)
    if include_archived:
        archived_dir.mkdir(parents=True, exist_ok=True)

    # Create personal_information.html for username detection (new format)
    personal_info_dir = base_path / "personal_information" / "personal_information"
    personal_info_dir.mkdir(parents=True, exist_ok=True)
    personal_info_html = f"""<!DOCTYPE html>
<html>
<head><title>Personal information</title></head>
<body>
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
<table style="table-layout: fixed;">
<tr><td colspan="2" class="_2pin _a6_q">Email<div><div>{username}@example.com</div></div></td></tr>
<tr><td colspan="2" class="_2pin _a6_q">Username<div><div>{username}</div></div></td></tr>
<tr><td colspan="2" class="_2pin _a6_q">Name<div><div>Test User</div></div></td></tr>
</table>
</div>
</body>
</html>"""
    (personal_info_dir / "personal_information.html").write_text(personal_info_html)

    # Create the HTML metadata directory
    html_dir = base_path / "your_instagram_activity" / "media"
    html_dir.mkdir(parents=True, exist_ok=True)

    # Collect posts by type for HTML generation
    regular_posts_html = []
    archived_posts_html = []

    for post in posts:
        filename = post["filename"]
        is_archived = post.get("archived", False)

        if is_archived and not include_archived:
            continue

        target_dir = archived_dir if is_archived else posts_dir
        file_path = target_dir / filename
        file_path.parent.mkdir(parents=True, exist_ok=True)

        ext = filename.split(".")[-1].lower()
        write_media_file(file_path, ext)

        # Build media path relative to export root for HTML href
        if is_archived:
            media_path = f"media/archived_posts/{filename}"
        else:
            media_path = f"media/posts/{filename}"

        # Generate HTML for this post
        post_html = _generate_instagram_post_html(
            media_path=media_path,
            caption=post.get("caption", ""),
            timestamp=post.get("timestamp", "2021-01-01T12:00:00"),
        )

        if is_archived:
            archived_posts_html.append(post_html)
        else:
            regular_posts_html.append(post_html)

    # Write posts_1.html if there are regular posts
    if regular_posts_html:
        posts_html_content = f"""<!DOCTYPE html>
<html>
<head><title>Posts</title></head>
<body>
{"".join(regular_posts_html)}
</body>
</html>"""
        (html_dir / "posts_1.html").write_text(posts_html_content)

    # Write archived_posts.html if there are archived posts
    if archived_posts_html:
        archived_html_content = f"""<!DOCTYPE html>
<html>
<head><title>Archived Posts</title></head>
<body>
{"".join(archived_posts_html)}
</body>
</html>"""
        (html_dir / "archived_posts.html").write_text(archived_html_content)

    return base_path


def create_instaloader_export(
    base_path: Path,
    username: str = "testuser",
    media_files: Optional[List[Dict]] = None,
) -> Path:
    """Create a minimal Instaloader scrape data structure.

    Instaloader (https://instaloader.github.io/) is a tool to download Instagram
    content. This is NOT an official Instagram export format.

    Structure:
        {base_path}/
            YYYY-MM-DD_HH-MM-SS_UTC.jpg
            YYYY-MM-DD_HH-MM-SS_UTC.txt (caption)
            YYYY-MM-DD_HH-MM-SS_UTC.json (metadata)

    Args:
        base_path: Base directory for the export
        username: Username for naming
        media_files: List of media file metadata dicts

    Returns:
        Path to the created export directory
    """
    if media_files is None:
        media_files = [
            {
                "timestamp": "2021-01-01_12-00-00",
                "extension": "jpg",
                "caption": "Test caption",
            },
            {
                "timestamp": "2021-01-02_12-00-00",
                "extension": "mp4",
                "caption": None,
            },
            {
                # Carousel post
                "timestamp": "2021-01-03_12-00-00",
                "extension": "jpg",
                "suffix": "_1",
                "caption": "Carousel caption",
            },
            {
                "timestamp": "2021-01-03_12-00-00",
                "extension": "jpg",
                "suffix": "_2",
                "caption": None,  # Only first has caption
            },
        ]

    base_path.mkdir(parents=True, exist_ok=True)

    for media in media_files:
        timestamp = media["timestamp"]
        ext = media["extension"]
        suffix = media.get("suffix", "")

        filename = f"{timestamp}_UTC{suffix}.{ext}"
        write_media_file(base_path / filename, ext)

        # Create caption file if present (txt files match detection pattern)
        caption = media.get("caption")
        if caption:
            caption_file = f"{timestamp}_UTC{suffix}.txt"
            (base_path / caption_file).write_text(caption)

        # Create JSON metadata with owner.username for username detection
        json_file = f"{timestamp}_UTC{suffix}.json"
        json_metadata = {
            "node": {
                "owner": {
                    "username": username,
                    "id": "607823542",
                    "full_name": "Test User",
                    "is_private": False,
                },
                "taken_at_timestamp": 1609502400,  # 2021-01-01
            }
        }
        (base_path / json_file).write_text(json.dumps(json_metadata, indent=2))

    return base_path


# Backward compatibility alias
create_instagram_old_export = create_instaloader_export


def create_discord_export(
    base_path: Path,
    username: str = "testuser",
    channels: Optional[Dict] = None,
) -> Path:
    """Create a minimal Discord export structure.

    Structure:
        {base_path}/Messages/
            index.json
            c{channel_id}/
                messages.json
                {media_files}

    Args:
        base_path: Base directory for the export
        username: Username for naming
        channels: Dict mapping channel IDs to channel data

    Returns:
        Path to the created export directory
    """
    if channels is None:
        channels = {
            "c123456789": {
                "name": "general",
                "type": "server",
                "guild_name": "Test Server",
                "messages": [
                    {
                        "ID": "msg001",
                        "Timestamp": "2021-01-01 12:00:00",
                        "Contents": "Hello world!",
                        "Attachments": "https://cdn.discord.com/attachments/123/456/image.jpg",
                    },
                ],
            },
            "c987654321": {
                "name": "DM with User",
                "type": "dm",
                "messages": [
                    {
                        "ID": "msg002",
                        "Timestamp": "2021-01-02 12:00:00",
                        "Contents": "Private message",
                        "Attachments": "",
                    },
                ],
            },
        }

    messages_dir = base_path / "Messages"
    messages_dir.mkdir(parents=True, exist_ok=True)

    # Create Account/user.json for username detection
    account_dir = base_path / "Account"
    account_dir.mkdir(parents=True, exist_ok=True)
    user_data = {
        "id": "247424274304991233",
        "username": username,
        "discriminator": 0,
        "global_name": username,
        "email": f"{username}@example.com",
    }
    (account_dir / "user.json").write_text(json.dumps(user_data, indent=2))

    # Create index.json
    index = {}
    for channel_id, data in channels.items():
        index[channel_id] = data.get("name", "Unknown Channel")

    (messages_dir / "index.json").write_text(json.dumps(index, indent=2))

    # Create channel folders
    for channel_id, data in channels.items():
        channel_dir = messages_dir / channel_id
        channel_dir.mkdir(exist_ok=True)

        # Create messages.json with channel.json structure
        channel_data = {
            "id": channel_id.replace("c", ""),
            "type": 0 if data.get("type") == "server" else 1,
            "name": data.get("name"),
            "guild": (
                {"id": "guild123", "name": data.get("guild_name", "")}
                if data.get("guild_name")
                else None
            ),
        }
        (channel_dir / "channel.json").write_text(json.dumps(channel_data, indent=2))

        messages_data = data.get("messages", [])
        (channel_dir / "messages.json").write_text(json.dumps(messages_data, indent=2))

        # Create attachment files (simulated)
        for msg in messages_data:
            attachments = msg.get("Attachments", "")
            if attachments:
                # Extract filename from URL
                filename = attachments.split("/")[-1]
                ext = filename.split(".")[-1].lower()
                write_media_file(channel_dir / filename, ext)

    return base_path


def create_imessage_mac_export(
    base_path: Path,
    username: str = "testuser",
    conversations: Optional[List[Dict]] = None,
) -> Path:
    """Create a minimal iMessage Mac export structure.

    Structure:
        {base_path}/
            chat.db
            Attachments/
                {attachment_path}

    Args:
        base_path: Base directory for the export
        username: Username for naming
        conversations: List of conversation data dicts

    Returns:
        Path to the created export directory
    """
    base_path.mkdir(parents=True, exist_ok=True)

    # Create chat.db
    db_path = base_path / "chat.db"
    create_imessage_chat_db(db_path)

    # Create Attachments directory
    attachments_dir = base_path / "Attachments"
    attachments_dir.mkdir(exist_ok=True)

    if conversations is None:
        conversations = [
            {
                "chat_identifier": "+1234567890",
                "display_name": None,
                "messages": [
                    {
                        "text": "Hello!",
                        "is_from_me": 0,
                        "date": 631152000000000000,  # iMessage timestamp
                        "attachment": "00/00/image.jpg",
                    },
                ],
            },
        ]

    # Populate database and create attachment files
    import sqlite3

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    handle_id = 1
    chat_id = 1
    msg_id = 1
    attach_id = 1

    for conv in conversations:
        chat_identifier = conv["chat_identifier"]

        # Insert handle
        cursor.execute(
            "INSERT INTO handle (ROWID, id, service) VALUES (?, ?, ?)",
            (handle_id, chat_identifier, "iMessage"),
        )

        # Insert chat
        cursor.execute(
            "INSERT INTO chat (ROWID, guid, chat_identifier, display_name) VALUES (?, ?, ?, ?)",
            (chat_id, f"chat{chat_id}", chat_identifier, conv.get("display_name")),
        )

        # Link handle to chat
        cursor.execute(
            "INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (?, ?)",
            (chat_id, handle_id),
        )

        for msg in conv.get("messages", []):
            # Insert message
            has_attachment = 1 if msg.get("attachment") else 0
            cursor.execute(
                """INSERT INTO message
                   (ROWID, guid, text, handle_id, date, is_from_me, cache_has_attachments)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    msg_id,
                    f"msg{msg_id}",
                    msg.get("text"),
                    handle_id,
                    msg.get("date", 0),
                    msg.get("is_from_me", 0),
                    has_attachment,
                ),
            )

            # Link message to chat
            cursor.execute(
                "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
                (chat_id, msg_id),
            )

            # Create attachment if present
            if msg.get("attachment"):
                attachment_path = msg["attachment"]
                full_path = attachments_dir / attachment_path
                full_path.parent.mkdir(parents=True, exist_ok=True)

                ext = attachment_path.split(".")[-1].lower()
                write_media_file(full_path, ext)

                # Insert attachment record
                cursor.execute(
                    """INSERT INTO attachment
                       (ROWID, guid, filename, mime_type, transfer_name)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        attach_id,
                        f"attach{attach_id}",
                        f"~/Library/Messages/Attachments/{attachment_path}",
                        f"image/{ext}",
                        attachment_path.split("/")[-1],
                    ),
                )

                # Link attachment to message
                cursor.execute(
                    "INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (?, ?)",
                    (msg_id, attach_id),
                )
                attach_id += 1

            msg_id += 1

        handle_id += 1
        chat_id += 1

    conn.commit()
    conn.close()

    return base_path


def create_imessage_iphone_export(
    base_path: Path,
    username: str = "testuser",
    conversations: Optional[List[Dict]] = None,
) -> Path:
    """Create a minimal iMessage iPhone export structure.

    Structure:
        {base_path}/SMS/
            sms.db
            Attachments/
                {attachment_path}

    Args:
        base_path: Base directory for the export
        username: Username for naming
        conversations: List of conversation data dicts

    Returns:
        Path to the created export directory
    """
    sms_dir = base_path / "SMS"
    sms_dir.mkdir(parents=True, exist_ok=True)

    # Create the export using the Mac structure within SMS/
    # but rename chat.db to sms.db
    create_imessage_mac_export(sms_dir, username, conversations)

    # Rename chat.db to sms.db
    (sms_dir / "chat.db").rename(sms_dir / "sms.db")

    return base_path


def create_imessage_imazing_export(
    base_path: Path,
    username: str = "testuser",
    conversations: Optional[List[Dict]] = None,
) -> Path:
    """Create a minimal iMessage iMazing export structure.

    Structure:
        {base_path}/
            Device-Info.txt (contains iMazing reference)
            Messages - Contact Name.csv
            YYYY-MM-DD HH MM SS - Contact - filename.jpg

    Args:
        base_path: Base directory for the export
        username: Username for naming
        conversations: List of conversation data dicts

    Returns:
        Path to the created export directory
    """
    base_path.mkdir(parents=True, exist_ok=True)

    # Create Device-Info.txt with iMazing reference
    device_info = base_path / "Device-Info.txt"
    device_info.write_text("Exported by iMazing 2.17.0\nDevice: iPhone 13 Pro")

    if conversations is None:
        conversations = [
            {
                "contact_name": "John Doe",
                "messages": [
                    {
                        "date": "2021-01-15 14:30:00",
                        "sender": "John Doe",
                        "text": "Hello!",
                        "attachment": "photo.jpg",
                    },
                    {
                        "date": "2021-01-15 14:31:00",
                        "sender": username,
                        "text": "Hi there!",
                    },
                ],
            }
        ]

    for conv in conversations:
        contact_name = conv["contact_name"]

        # Create CSV file for this conversation
        csv_lines = [
            "Chat Session,Message Date,Delivered Date,Read Date,Service,Type,"
            "Sender ID,Sender Name,Status,Replying to,Subject,Text,Attachment,"
            "Attachment type"
        ]

        for msg in conv.get("messages", []):
            sender = msg.get("sender", "")
            msg_type = "Incoming" if sender != username else "Outgoing"
            text = msg.get("text", "")
            attachment = msg.get("attachment", "")
            attach_type = "Image" if attachment.endswith((".jpg", ".png")) else ""

            csv_lines.append(
                f"{contact_name},{msg['date']},,,"
                f"iMessage,{msg_type},,{sender},Delivered,,,"
                f"{text},{attachment},{attach_type}"
            )

            # Create attachment file if present
            if attachment:
                # iMazing uses: YYYY-MM-DD HH MM SS - Contact - filename
                date_part = msg["date"].replace(":", " ")
                media_filename = f"{date_part} - {contact_name} - {attachment}"
                ext = attachment.split(".")[-1].lower()
                write_media_file(base_path / media_filename, ext)

        csv_path = base_path / f"Messages - {contact_name}.csv"
        csv_path.write_text("\n".join(csv_lines))

    return base_path
