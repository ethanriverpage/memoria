#!/usr/bin/env python3
"""
Synthetic test dataset generator.

Creates small, deterministic exports for all processors, with edge cases that are
hard to guarantee via random sampling from /mnt/media/originals.

Design goals:
- No PII (use placeholder usernames/names)
- Minimal size (tiny media fixtures)
- Deterministic (no randomness)
- Exercise processor edge cases and cross-processor dedup where possible
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SyntheticDatasetStats:
    exports_created: int
    files_written: int


_TINY_PNG_1X1 = base64.b64decode(
    # 1x1 transparent PNG
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB"
    "gC0n2QAAAABJRU5ErkJggg=="
)

_TINY_JPEG_1X1 = base64.b64decode(
    # 1x1 JPEG (baseline)
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wCEAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/2wCEAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQH/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAA"
    "AAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJx"
    "FDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW"
    "V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2"
    "t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEB"
    "AQEBAQEBAQAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSEx"
    "BhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNE"
    "RUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOk"
    "paanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6"
    "/9oADAMBAAIRAxEAPwD0gA//2Q=="
)


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    _write_text(path, json.dumps(obj, indent=2, ensure_ascii=False))


def _utc(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _apple_ns(dt: datetime) -> int:
    # iMessage uses nanoseconds since 2001-01-01 00:00:00 UTC
    apple_epoch = datetime(2001, 1, 1, tzinfo=timezone.utc)
    seconds = (dt.replace(tzinfo=timezone.utc) - apple_epoch).total_seconds()
    return int(seconds * 1_000_000_000)


def _maybe_make_mp4(path: Path, seconds: int = 1, size: str = "320x240") -> None:
    """
    Create a tiny MP4 using ffmpeg if available.
    If ffmpeg is missing, raise a clear error (video fixtures are required for
    Snapchat overlay coverage).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={size}:d={seconds}",
        "-pix_fmt",
        "yuv420p",
        str(path),
    ]
    try:
        subprocess.run(
            cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except FileNotFoundError as e:
        raise RuntimeError(
            "ffmpeg is required to generate synthetic video fixtures"
        ) from e


def _touch_mtime(path: Path, dt: datetime) -> None:
    ts = dt.replace(tzinfo=timezone.utc).timestamp()
    os.utime(path, (ts, ts))


def create_synthetic_exports(
    output_root: Path, dry_run: bool = False
) -> SyntheticDatasetStats:
    """
    Create synthetic exports directly under output_root.
    """
    exports_created = 0
    files_written = 0

    def write_bytes(path: Path, data: bytes) -> None:
        nonlocal files_written
        if dry_run:
            return
        _write_bytes(path, data)
        files_written += 1

    def write_text(path: Path, text: str) -> None:
        nonlocal files_written
        if dry_run:
            return
        _write_text(path, text)
        files_written += 1

    def write_json(path: Path, obj: Any) -> None:
        nonlocal files_written
        if dry_run:
            return
        _write_json(path, obj)
        files_written += 1

    # Shared fixtures for cross-processor dedup
    fixtures_dir = output_root / "_synthetic_fixtures"
    shared_jpg = fixtures_dir / "shared_dup.jpg"
    shared_png = fixtures_dir / "shared_overlay.png"

    if not dry_run:
        fixtures_dir.mkdir(parents=True, exist_ok=True)
    write_bytes(shared_jpg, _TINY_JPEG_1X1)
    write_bytes(shared_png, _TINY_PNG_1X1)

    # -------------------------------------------------------------------------
    # Google export (Photos + Chat + Voice)
    # -------------------------------------------------------------------------
    google_root = output_root / "google-synthetic-20250101"
    exports_created += 1
    if not dry_run:
        google_root.mkdir(parents=True, exist_ok=True)

    # Google Photos (duplicates across Albums)
    gp_base = google_root / "Takeout" / "Google Photos"
    write_bytes(gp_base / "Photos from 2025" / "IMG_0001.jpg", _TINY_JPEG_1X1)
    write_json(
        gp_base / "Photos from 2025" / "IMG_0001.jpg.json",
        {"title": "IMG_0001.jpg", "photoTakenTime": {"timestamp": "1735689600"}},
    )

    # Duplicate file in two albums (exact byte-for-byte copy)
    for album in ["Albums/Album A", "Albums/Album B"]:
        album_path = gp_base / album
        if not dry_run:
            album_path.mkdir(parents=True, exist_ok=True)
        if not dry_run:
            shutil.copy2(shared_jpg, album_path / "shared_dup.jpg")
            files_written += 1
        write_json(album_path / "shared_dup.jpg.json", {"title": "shared_dup.jpg"})

    # Google Chat (one with media, one without)
    gc_base = google_root / "Google Chat"
    group_dir = gc_base / "Groups" / "Space 00000001"
    dm_dir = gc_base / "Users" / "User 00000002"
    write_json(
        group_dir / "group_info.json", {"name": "Synthetic Space", "members": []}
    )
    write_json(dm_dir / "user_info.json", {"user": {"email": "synthetic@example.com"}})
    write_bytes(group_dir / "File-2016-09-06.png", _TINY_PNG_1X1)
    write_json(
        group_dir / "messages.json",
        {
            "messages": [
                {
                    "creator": {
                        "name": "Sender A",
                        "email": "a@example.com",
                        "user_type": "Human",
                    },
                    "created_date": "Thursday, November 3, 2016 at 11:59:50 AM UTC",
                    "text": "Check this out",
                    "attached_files": [
                        {"original_name": "x.png", "export_name": "File-2016-09-06.png"}
                    ],
                    "topic_id": "topic1",
                    "message_id": "msg1",
                }
            ]
        },
    )
    write_json(dm_dir / "messages.json", {"messages": []})

    # Google Voice (Takeout/Voice/Calls) with fuzzy match edge cases
    gv_base = google_root / "Takeout" / "Voice" / "Calls"
    # Real file on disk includes "(1)", HTML references the stripped version
    write_bytes(gv_base / "File-2017-01-05(1).png", _TINY_PNG_1X1)
    # HTML references "photo-1" but file is "photo.jpg" (preprocessor strips -1)
    write_bytes(gv_base / "photo.jpg", _TINY_JPEG_1X1)
    voice_html = """<!doctype html>
<html>
  <head><title>Me to +15551234567</title></head>
  <body>
    <div class="participants"><span class="fn">Me</span><span class="fn">+15551234567</span></div>
    <div class="message">
      <cite class="sender"><a class="tel" href="tel:+15551234567"><span class="fn">+15551234567</span></a></cite>
      <abbr class="dt" title="2025-01-01T00:00:00.000Z"></abbr>
      <img src="photo-1" />
      <img src="File-2017-01-05.png" />
    </div>
  </body>
</html>
"""
    write_text(gv_base / "+15551234567 - Text - 2025-01-01T00_00_00Z.html", voice_html)

    # -------------------------------------------------------------------------
    # Discord export (raw) with file:// attachments (offline)
    # -------------------------------------------------------------------------
    discord_root = output_root / "discord-synthetic-20250101"
    exports_created += 1
    messages_dir = discord_root / "Messages"
    channel_dir = messages_dir / "c1234567890"
    local_dir = discord_root / "LocalAttachments"

    if not dry_run:
        local_dir.mkdir(parents=True, exist_ok=True)
    # Local attachment and a missing attachment to exercise failure tracking
    write_bytes(
        local_dir / "shared_dup.jpg", shared_jpg.read_bytes() if not dry_run else b""
    )
    write_bytes(local_dir / "note.png", _TINY_PNG_1X1)

    # Build file:// URLs with absolute paths to the synthetic dataset
    shared_url = f"file://{(local_dir / 'shared_dup.jpg').absolute()}"
    note_url = f"file://{(local_dir / 'note.png').absolute()}"
    missing_url = f"file://{(local_dir / 'missing.jpg').absolute()}"

    write_json(
        messages_dir / "index.json", {"1234567890": "Direct Message with synthetic#0"}
    )
    write_json(
        channel_dir / "channel.json",
        {"id": "1234567890", "type": "DM", "recipients": ["1", "2"]},
    )
    write_json(
        channel_dir / "messages.json",
        [
            {
                "ID": 111,
                "Timestamp": "2025-01-01 00:00:00",
                "Contents": "Two attachments, one duplicate",
                "Attachments": f"{shared_url} {note_url}",
            },
            {
                "ID": 112,
                "Timestamp": "2025-01-01 00:00:01",
                "Contents": "Same attachment again + missing",
                "Attachments": f"{shared_url} {missing_url}",
            },
        ],
    )

    # -------------------------------------------------------------------------
    # Instagram Messages (current format) + Public Media (new format)
    # -------------------------------------------------------------------------
    insta_root = output_root / "instagram-synthetic-20250101"
    exports_created += 1

    inbox_dir = insta_root / "your_instagram_activity" / "messages" / "inbox"
    conv_a = inbox_dir / "user_a_0001"
    conv_b = inbox_dir / "user_b_0002"
    # Same media filename in two conversations to exercise within-processor dedup
    write_bytes(
        conv_a / "photos" / "shared_dup.jpg",
        shared_jpg.read_bytes() if not dry_run else b"",
    )
    write_bytes(
        conv_b / "photos" / "shared_dup.jpg",
        shared_jpg.read_bytes() if not dry_run else b"",
    )

    msg_html = """<!doctype html>
<html>
  <head><title>Synthetic Conversation</title></head>
  <body>
    <div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
      <h2 class="_3-95 _2pim _a6-h _a6-i">Sender A</h2>
      <div class="_3-95 _a6-p"><div>Check this out!</div></div>
      <a href="./photos/shared_dup.jpg">photo</a>
      <div class="_3-94 _a6-o">Jan 1, 2025 12:00 am</div>
    </div>
  </body>
</html>
"""
    write_text(conv_a / "message_1.html", msg_html)
    write_text(conv_b / "message_1.html", msg_html)

    # Instagram Public Media (HTML index + media/posts files)
    pub_html_dir = insta_root / "your_instagram_activity" / "media"
    media_posts_dir = insta_root / "media" / "posts" / "202501"
    write_bytes(media_posts_dir / "post_0001.jpg", _TINY_JPEG_1X1)
    posts_html = """<!doctype html>
<html><head><title>Posts</title></head>
<body>
  <a href="/media/posts/202501/post_0001.jpg">post</a>
  <div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
    <div class="_3-94 _a6-o">Jan 1, 2025 12:00 am</div>
  </div>
</body></html>
"""
    write_text(pub_html_dir / "posts_1.html", posts_html)

    # -------------------------------------------------------------------------
    # Instaloader scrape export
    # -------------------------------------------------------------------------
    instaloader_root = output_root / "instaloader-synthetic"
    exports_created += 1

    base = "2025-01-01_00-00-00_UTC"
    write_bytes(instaloader_root / f"{base}.jpg", _TINY_JPEG_1X1)
    write_text(
        instaloader_root / f"{base}.txt", "Synthetic caption with unicode’s apostrophe"
    )
    write_json(
        instaloader_root / f"{base}.json",
        {
            "node": {
                "owner": {"username": "synthetic"},
                "taken_at_timestamp": 1735689600,
                "__typename": "GraphImage",
            }
        },
    )

    # -------------------------------------------------------------------------
    # Snapchat export (Memories + Messages)
    # -------------------------------------------------------------------------
    snap_root = output_root / "snapchat-synthetic-20250101"
    exports_created += 1

    # Memories (explicit overlay mapping)
    memories_root = snap_root / "memories"
    mem_media = memories_root / "media"
    mem_overlays = memories_root / "overlays"
    write_bytes(mem_media / "memory_photo.jpg", _TINY_JPEG_1X1)
    write_bytes(mem_overlays / "memory_overlay.png", _TINY_PNG_1X1)
    write_json(
        memories_root / "metadata.json",
        [
            {
                "date": "2025-01-01 00:00:00 UTC",
                "media_type": "PHOTO",
                "media_filename": "memory_photo.jpg",
                "overlay_filename": "memory_overlay.png",
            },
            {
                "date": "2025-01-01 00:00:01 UTC",
                "media_type": "PHOTO",
                "media_filename": "shared_dup.jpg",
            },
        ],
    )
    if not dry_run:
        shutil.copy2(shared_jpg, mem_media / "shared_dup.jpg")
        files_written += 1

    # Messages (overlay matching via mtime + ambiguous matching case)
    messages_root = snap_root / "messages"
    json_dir = messages_root / "json"
    chat_media = messages_root / "chat_media"
    if not dry_run:
        chat_media.mkdir(parents=True, exist_ok=True)

    # One auto-matchable video+overlay at exact timestamp
    t1 = datetime(2025, 1, 1, 0, 0, 10, tzinfo=timezone.utc)
    v1 = chat_media / "2025-01-01_media~zip-AAAA1111-1111-1111-1111-111111111111.mp4"
    o1 = chat_media / "2025-01-01_overlay~zip-BBBB2222-2222-2222-2222-222222222222.png"
    if not dry_run:
        _maybe_make_mp4(v1)
        files_written += 1
        shutil.copy2(shared_png, o1)
        files_written += 1
        _touch_mtime(v1, t1)
        _touch_mtime(o1, t1)

    # Ambiguous case: 2 videos + 2 overlays at same timestamp
    t2 = datetime(2025, 1, 1, 0, 0, 20, tzinfo=timezone.utc)
    v2a = chat_media / "2025-01-01_media~zip-CCCC3333-3333-3333-3333-333333333333.mp4"
    v2b = chat_media / "2025-01-01_media~zip-DDDD4444-4444-4444-4444-444444444444.mp4"
    o2a = chat_media / "2025-01-01_overlay~zip-EEEE5555-5555-5555-5555-555555555555.png"
    o2b = chat_media / "2025-01-01_overlay~zip-FFFF6666-6666-6666-6666-666666666666.png"
    if not dry_run:
        _maybe_make_mp4(v2a)
        files_written += 1
        _maybe_make_mp4(v2b)
        files_written += 1
        shutil.copy2(shared_png, o2a)
        files_written += 1
        shutil.copy2(shared_png, o2b)
        files_written += 1
        for p in [v2a, v2b, o2a, o2b]:
            _touch_mtime(p, t2)

    # Orphaned media (no metadata)
    write_bytes(chat_media / "2025-01-01_b~ORPHAN.jpg", _TINY_JPEG_1X1)

    # chat_history.json includes:
    # - one message referencing the auto-matchable video via timestamp
    # - one message creating an ambiguous overlay match (2 media ids)
    # - one orphaned metadata reference
    write_json(
        json_dir / "chat_history.json",
        {
            "conv1": [
                {
                    "From": "friend_username",
                    "Media Type": "MEDIA",
                    "Created": _utc(t1),
                    "Conversation Title": None,
                    "IsSender": False,
                    "Content": "one video",
                    "Media IDs": "b~doesnotmatter",
                },
                {
                    "From": "friend_username",
                    "Media Type": "MEDIA",
                    "Created": _utc(t2),
                    "Conversation Title": "Group Chat Name",
                    "IsSender": False,
                    "Content": "two videos two overlays",
                    "Media IDs": "b~x | b~y",
                },
                {
                    "From": "friend_username",
                    "Media Type": "MEDIA",
                    "Created": "2025-01-01 00:00:30 UTC",
                    "Conversation Title": None,
                    "IsSender": False,
                    "Content": "missing media id",
                    "Media IDs": "b~MISSING",
                },
            ]
        },
    )
    write_json(json_dir / "snap_history.json", {})

    # -------------------------------------------------------------------------
    # iMessage (Mac + iPhone) exports (SQLite + attachments)
    # -------------------------------------------------------------------------
    def build_imessage_db(
        db_path: Path, attachments_root: Path, is_phone: bool
    ) -> None:
        nonlocal files_written
        if dry_run:
            return

        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.executescript(
            """
            CREATE TABLE attachment (
                ROWID INTEGER PRIMARY KEY,
                guid TEXT,
                filename TEXT,
                mime_type TEXT,
                transfer_name TEXT,
                total_bytes INTEGER,
                is_outgoing INTEGER,
                created_date INTEGER,
                transfer_state INTEGER
            );
            CREATE TABLE message (
                ROWID INTEGER PRIMARY KEY,
                guid TEXT,
                text TEXT,
                attributedBody BLOB,
                date INTEGER,
                is_from_me INTEGER,
                handle_id INTEGER
            );
            CREATE TABLE chat (
                ROWID INTEGER PRIMARY KEY,
                guid TEXT,
                display_name TEXT,
                chat_identifier TEXT,
                style INTEGER
            );
            CREATE TABLE handle (
                ROWID INTEGER PRIMARY KEY,
                id TEXT
            );
            CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
            CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
            """
        )

        # Handles / chats
        cur.execute("INSERT INTO handle(ROWID,id) VALUES(?,?)", (1, "+15551234567"))
        cur.execute(
            "INSERT INTO chat(ROWID,guid,display_name,chat_identifier,style) VALUES(?,?,?,?,?)",
            (1, "chat1", None, "+15551234567", 45),
        )
        cur.execute(
            "INSERT INTO chat(ROWID,guid,display_name,chat_identifier,style) VALUES(?,?,?,?,?)",
            (2, "chat2", "Synthetic Group", "group1", 43),
        )

        # Messages: one uses attributedBody (text NULL)
        dt_msg = datetime(2025, 1, 1, 0, 1, 0, tzinfo=timezone.utc)
        attributed = b"\x04\x0bstreamtyped\x01+%b" % b"\x0fSynthetic text"
        cur.execute(
            "INSERT INTO message(ROWID,guid,text,attributedBody,date,is_from_me,handle_id) VALUES(?,?,?,?,?,?,?)",
            (1, "m1", None, attributed, _apple_ns(dt_msg), 0, 1),
        )
        cur.execute(
            "INSERT INTO message(ROWID,guid,text,attributedBody,date,is_from_me,handle_id) VALUES(?,?,?,?,?,?,?)",
            (2, "m2", "Also in group", None, _apple_ns(dt_msg), 0, 1),
        )

        # Attachment file paths inside export
        # Use iMessage-style "~/Library/..." paths; preprocessor resolves into export dirs.
        rel_dir = Path("00/00/AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE")
        att_dir = attachments_root / rel_dir
        att_dir.mkdir(parents=True, exist_ok=True)

        # Shared dup (cross-export + within-export)
        shared_name = "shared_dup.jpg"
        shared_path = att_dir / shared_name
        shutil.copy2(shared_jpg, shared_path)
        files_written += 1

        # Live photo image + sidecar MOV
        lp_img = att_dir / "lp_image.jpeg"
        lp_mov = att_dir / "lp_image.MOV"
        shutil.copy2(shared_jpg, lp_img)
        files_written += 1
        _maybe_make_mp4(lp_mov)
        files_written += 1

        # Missing attachment referenced by DB (orphaned metadata)
        missing_rel = "~/Library/Messages/Attachments/00/00/MISSING/missing.jpg"

        # attachment rows
        def insert_attachment(
            rowid: int, transfer_name: str, filename: str, mime: str
        ) -> None:
            size = (
                (att_dir / transfer_name).stat().st_size
                if (att_dir / transfer_name).exists()
                else 0
            )
            cur.execute(
                "INSERT INTO attachment(ROWID,guid,filename,mime_type,transfer_name,total_bytes,is_outgoing,created_date,transfer_state) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    rowid,
                    f"att{rowid}",
                    filename,
                    mime,
                    transfer_name,
                    size,
                    0,
                    0,
                    5,
                ),
            )

        prefix = (
            "~/Library/SMS/Attachments"
            if is_phone
            else "~/Library/Messages/Attachments"
        )
        insert_attachment(
            1,
            shared_name,
            f"{prefix}/{rel_dir.as_posix()}/{shared_name}",
            "image/jpeg",
        )
        insert_attachment(
            2,
            "lp_image.jpeg",
            f"{prefix}/{rel_dir.as_posix()}/lp_image.jpeg",
            "image/jpeg",
        )
        # Missing attachment row
        cur.execute(
            "INSERT INTO attachment(ROWID,guid,filename,mime_type,transfer_name,total_bytes,is_outgoing,created_date,transfer_state) VALUES(?,?,?,?,?,?,?,?,?)",
            (3, "att3", missing_rel, "image/jpeg", "missing.jpg", 123, 0, 0, 5),
        )

        # Joins: same shared attachment appears in two messages (dedup within export)
        for msg_id in [1, 2]:
            cur.execute(
                "INSERT INTO message_attachment_join(message_id,attachment_id) VALUES(?,?)",
                (msg_id, 1),
            )
        # Live photo image attached to first message
        cur.execute(
            "INSERT INTO message_attachment_join(message_id,attachment_id) VALUES(?,?)",
            (1, 2),
        )
        # Missing attachment referenced
        cur.execute(
            "INSERT INTO message_attachment_join(message_id,attachment_id) VALUES(?,?)",
            (1, 3),
        )

        # Chat joins
        cur.execute(
            "INSERT INTO chat_message_join(chat_id,message_id) VALUES(?,?)", (1, 1)
        )
        cur.execute(
            "INSERT INTO chat_message_join(chat_id,message_id) VALUES(?,?)", (2, 2)
        )

        conn.commit()
        conn.close()

    mac_root = output_root / "mac-messages-20250101"
    exports_created += 1
    build_imessage_db(
        db_path=mac_root / "chat.db",
        attachments_root=mac_root / "Attachments",
        is_phone=False,
    )

    phone_root = output_root / "iphone14-messages-20250102"
    exports_created += 1
    build_imessage_db(
        db_path=phone_root / "SMS" / "sms.db",
        attachments_root=phone_root / "SMS" / "Attachments",
        is_phone=True,
    )

    # -------------------------------------------------------------------------
    # iMessage iMazing export (flat files + CSV + Device-Info)
    # -------------------------------------------------------------------------
    imazing_root = output_root / "iph14-messages-20250103"
    exports_created += 1
    write_text(
        imazing_root / "Device-Info.txt", "Exported by iMazing (DigiDNA)\nName: iph14\n"
    )
    # A valid iMazing media filename
    imazing_media_name = "2025-01-01 00 02 00 - Contact A - shared_dup.jpg"
    if not dry_run:
        imazing_root.mkdir(parents=True, exist_ok=True)
        shutil.copy2(shared_jpg, imazing_root / imazing_media_name)
        files_written += 1
    write_text(
        imazing_root / "Messages - Contact A.csv",
        "Chat Session,Message Date,Attachment,Type,Sender Name,Text\n"
        "Contact A,2025-01-01 00:02:00,shared_dup.jpg,Incoming,Contact A,hello\n"
        "Contact A,2025-01-01 00:03:00,missing.jpg,Incoming,Contact A,missing\n",
    )

    return SyntheticDatasetStats(
        exports_created=exports_created, files_written=files_written
    )
