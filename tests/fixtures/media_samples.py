"""
Minimal valid media file bytes for testing.

These are the smallest valid files that can be created for each format.
Using base64-encoded bytes keeps tests self-contained without external dependencies.
"""

import base64
import sqlite3
from pathlib import Path
from typing import Optional

# Minimal valid 1x1 JPEG (267 bytes)
# Created with: convert -size 1x1 xc:red minimal.jpg
MINIMAL_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkS"
    "Ew8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJ"
    "CQwLDBgNDRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
    "MjIyMjIyMjIyMjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAn/"
    "xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAAAAAAAAAAAAX/xAAUEQEAAAAAAAAAAAAAAAAAAAAA"
    "/9oADAMBAAIRAxEAPwCwAB//2Q=="
)

# Minimal valid 1x1 PNG (68 bytes)
# Created with: convert -size 1x1 xc:red minimal.png
MINIMAL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9Q"
    "DwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

# Minimal valid MP4 container (smallest possible valid MP4)
# This is a minimal ftyp box only - enough for file type detection
MINIMAL_MP4 = bytes(
    [
        0x00,
        0x00,
        0x00,
        0x14,  # Box size: 20 bytes
        0x66,
        0x74,
        0x79,
        0x70,  # Box type: 'ftyp'
        0x69,
        0x73,
        0x6F,
        0x6D,  # Major brand: 'isom'
        0x00,
        0x00,
        0x00,
        0x01,  # Minor version
        0x69,
        0x73,
        0x6F,
        0x6D,  # Compatible brand: 'isom'
    ]
)

# Minimal valid WebP (1x1 red pixel)
MINIMAL_WEBP = base64.b64decode(
    "UklGRlYAAABXRUJQVlA4IEoAAADQAQCdASoBAAEAAUAmJYgCdAEO/hOMAAD++HP/"
    "HCVse0Gg33/0HLe3/wFaPdz/4Sf/1/+D/8D/6n/7X/5n/Lf9L/v/+K/7z/2/+4/8"
    "gAAAAA=="
)


def create_minimal_sqlite_db(
    db_path: Path, schema: Optional[str] = None, data: Optional[list] = None
) -> Path:
    """Create a minimal valid SQLite database.

    Args:
        db_path: Path where to create the database
        schema: Optional SQL schema to execute
        data: Optional list of (sql, params) tuples to execute

    Returns:
        Path to the created database
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)

    if schema:
        conn.executescript(schema)

    if data:
        cursor = conn.cursor()
        for sql, params in data:
            cursor.execute(sql, params)

    conn.commit()
    conn.close()
    return db_path


def create_imessage_chat_db(db_path: Path) -> Path:
    """Create a minimal iMessage chat.db with required schema.

    Args:
        db_path: Path where to create the database

    Returns:
        Path to the created database
    """
    schema = """
    CREATE TABLE IF NOT EXISTS message (
        ROWID INTEGER PRIMARY KEY,
        guid TEXT UNIQUE,
        text TEXT,
        attributedBody BLOB,
        handle_id INTEGER,
        date INTEGER,
        is_from_me INTEGER DEFAULT 0,
        cache_has_attachments INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS handle (
        ROWID INTEGER PRIMARY KEY,
        id TEXT UNIQUE,
        service TEXT
    );

    CREATE TABLE IF NOT EXISTS chat (
        ROWID INTEGER PRIMARY KEY,
        guid TEXT UNIQUE,
        chat_identifier TEXT,
        display_name TEXT,
        group_id TEXT,
        style INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS chat_message_join (
        chat_id INTEGER,
        message_id INTEGER,
        PRIMARY KEY (chat_id, message_id)
    );

    CREATE TABLE IF NOT EXISTS chat_handle_join (
        chat_id INTEGER,
        handle_id INTEGER,
        PRIMARY KEY (chat_id, handle_id)
    );

    CREATE TABLE IF NOT EXISTS attachment (
        ROWID INTEGER PRIMARY KEY,
        guid TEXT UNIQUE,
        filename TEXT,
        mime_type TEXT,
        transfer_name TEXT,
        created_date INTEGER,
        total_bytes INTEGER,
        is_outgoing INTEGER DEFAULT 0,
        transfer_state INTEGER DEFAULT 5
    );

    CREATE TABLE IF NOT EXISTS message_attachment_join (
        message_id INTEGER,
        attachment_id INTEGER,
        PRIMARY KEY (message_id, attachment_id)
    );
    """
    return create_minimal_sqlite_db(db_path, schema)


def write_media_file(path: Path, media_type: str = "jpeg") -> Path:
    """Write a minimal media file to the given path.

    Args:
        path: Path where to write the file
        media_type: Type of media file to write (jpeg, png, mp4, webp)

    Returns:
        Path to the written file
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    media_bytes = {
        "jpeg": MINIMAL_JPEG,
        "jpg": MINIMAL_JPEG,
        "png": MINIMAL_PNG,
        "mp4": MINIMAL_MP4,
        "mov": MINIMAL_MP4,  # Use MP4 bytes for MOV as well
        "webp": MINIMAL_WEBP,
    }

    content = media_bytes.get(media_type.lower(), MINIMAL_JPEG)
    path.write_bytes(content)
    return path


# ============================================================================
# Runtime-generated realistic samples
# ============================================================================


def write_real_mp4_if_available(path: Path, duration_seconds: float = 0.1) -> bool:
    """Generate a real playable MP4 file using ffmpeg if available.

    This creates a minimal but fully valid MP4 with a video stream,
    suitable for tests that use ffprobe to inspect video properties.

    Args:
        path: Path where to write the file
        duration_seconds: Duration of the video (default 0.1s for speed)

    Returns:
        True if a real MP4 was created, False if ffmpeg not available
        (falls back to MINIMAL_MP4 in that case)
    """
    import shutil
    import subprocess

    if shutil.which("ffmpeg") is None:
        # Fallback to minimal MP4
        write_media_file(path, "mp4")
        return False

    path.parent.mkdir(parents=True, exist_ok=True)

    # Generate a 1x1 red video with no audio
    cmd = [
        "ffmpeg",
        "-y",  # Overwrite
        "-f",
        "lavfi",
        "-i",
        f"color=red:s=16x16:d={duration_seconds}",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        "-an",  # No audio
        str(path),
    ]

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            check=True,
            timeout=10,
        )
        return True
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        # Fallback to minimal MP4
        write_media_file(path, "mp4")
        return False


def write_scof_wrapped_png(
    path: Path, overlay_png_bytes: Optional[bytes] = None
) -> Path:
    """Create a Snapchat SCOF-wrapped overlay file.

    SCOF (Snapchat Overlay Format) files have:
    - 136 bytes of header
    - Embedded PNG payload at offset 136

    Per docs/Snapchat-Export.md, this is the format used for overlay files.

    Args:
        path: Path where to write the file
        overlay_png_bytes: Optional PNG bytes to embed (defaults to MINIMAL_PNG)

    Returns:
        Path to the written file
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    if overlay_png_bytes is None:
        overlay_png_bytes = MINIMAL_PNG

    # SCOF header: 136 bytes of padding/header data
    # The actual SCOF format has specific header fields, but for testing
    # we just need the PNG to be at offset 136
    scof_header = b"SCOF" + b"\x00" * 132  # 4 byte magic + 132 bytes padding

    scof_data = scof_header + overlay_png_bytes
    path.write_bytes(scof_data)
    return path


def extract_png_from_scof(scof_path: Path) -> bytes:
    """Extract PNG data from a SCOF-wrapped overlay file.

    Args:
        scof_path: Path to the SCOF file

    Returns:
        PNG bytes extracted from offset 136
    """
    data = scof_path.read_bytes()
    # PNG starts at offset 136
    return data[136:]


def write_heic_placeholder(path: Path) -> Path:
    """Write a placeholder HEIC file.

    Note: This is NOT a valid HEIC file. It's a JPEG with HEIC extension
    for tests that don't actually decode the image content.

    For tests requiring valid HEIC parsing, use real HEIC fixtures or
    skip with @pytest.mark.skipif.

    Args:
        path: Path where to write the file

    Returns:
        Path to the written file
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # HEIC files use JPEG-like ftyp box structure
    # This is a minimal valid HEIC-like ftyp
    heic_ftyp = bytes(
        [
            0x00,
            0x00,
            0x00,
            0x18,  # Box size: 24 bytes
            0x66,
            0x74,
            0x79,
            0x70,  # Box type: 'ftyp'
            0x68,
            0x65,
            0x69,
            0x63,  # Major brand: 'heic'
            0x00,
            0x00,
            0x00,
            0x00,  # Minor version
            0x6D,
            0x69,
            0x66,
            0x31,  # Compatible brand: 'mif1'
            0x68,
            0x65,
            0x69,
            0x63,  # Compatible brand: 'heic'
        ]
    )
    path.write_bytes(heic_ftyp)
    return path
