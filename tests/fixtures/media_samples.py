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
MINIMAL_MP4 = bytes([
    0x00, 0x00, 0x00, 0x14,  # Box size: 20 bytes
    0x66, 0x74, 0x79, 0x70,  # Box type: 'ftyp'
    0x69, 0x73, 0x6F, 0x6D,  # Major brand: 'isom'
    0x00, 0x00, 0x00, 0x01,  # Minor version
    0x69, 0x73, 0x6F, 0x6D,  # Compatible brand: 'isom'
])

# Minimal valid WebP (1x1 red pixel)
MINIMAL_WEBP = base64.b64decode(
    "UklGRlYAAABXRUJQVlA4IEoAAADQAQCdASoBAAEAAUAmJYgCdAEO/hOMAAD++HP/"
    "HCVse0Gg33/0HLe3/wFaPdz/4Sf/1/+D/8D/6n/7X/5n/Lf9L/v/+K/7z/2/+4/8"
    "gAAAAA=="
)


def create_minimal_sqlite_db(
    db_path: Path,
    schema: Optional[str] = None,
    data: Optional[list] = None
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
        group_id TEXT
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
        created_date INTEGER
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

