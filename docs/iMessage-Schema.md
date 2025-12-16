# iMessage Schema

This document describes the iMessage database schema relevant to media extraction for Memoria.

## Export Sources

iMessage data can be obtained from two sources:

1. **Mac Export** (`mac-*` prefix): Direct copy of `~/Library/Messages/` from a Mac
2. **iPhone Export** (`iph*` prefix): Extracted from an iTunes/Finder backup of an iPhone

Both sources use the same SQLite database schema.

---

## Location in Export

### Mac Export Structure

```text
mac-messages-YYYYMMDD/
├── chat.db              # Main SQLite database
├── chat.db-shm          # SQLite shared memory file (can be ignored)
├── chat.db-wal          # SQLite write-ahead log (can be ignored)
└── Attachments/         # Attachment files organized by hash
    ├── 00/
    │   └── 00/
    │       └── <UUID>/
    │           └── filename.ext
    ├── 01/
    │   └── ...
    └── ff/
        └── ...
```

### iPhone Export Structure

```text
iph*-messages-YYYYMMDD/
├── SMS/
│   ├── sms.db           # Main SQLite database (same schema as chat.db)
│   └── Attachments/     # Attachment files (same structure as Mac)
│       ├── 00/
│       │   └── 00/
│       │       └── <UUID>/
│       │           └── filename.ext
│       └── ...
└── MessagesMetaData/    # Additional metadata (group photos, etc.)
```

**Key Files:**

- `chat.db` (Mac) / `sms.db` (iPhone) - Main SQLite database containing all messages, chats, and metadata
- `Attachments/` - Directory containing all media files organized by path hash

---

## Database Schema Overview

The iMessage database consists of several interconnected tables:

| Table | Description |
|-------|-------------|
| `message` | All messages with content, timestamps, and metadata |
| `chat` | Conversations (both 1:1 and group chats) |
| `handle` | Contact identifiers (phone numbers, emails, Apple IDs) |
| `attachment` | Attachment metadata (files stored on disk) |
| `chat_message_join` | Links messages to conversations |
| `chat_handle_join` | Links participants to conversations |
| `message_attachment_join` | Links attachments to messages |

### Entity Relationships

```text
                    +-------------+
                    |   message   |
                    |-------------|
                    | rowid (PK)  |
                    | guid (UK)   |
                    | text        |
                    | handle_id ──┼────────────────┐
                    | ...         |                │
                    +------┬------+                │
                           │                       │
         ┌─────────────────┼─────────────────┐     │
         │                 │                 │     │
         ▼                 ▼                 ▼     ▼
+------------------+  +------------------+  +-------------+
| chat_message_join|  | msg_attachment   |  |   handle    |
|------------------|  | _join            |  |-------------|
| chat_id (FK)     |  |------------------|  | rowid (PK)  |
| message_id (FK)  |  | message_id (FK)  |  | id (UK)     |
| message_date     |  | attachment_id(FK)|  | service     |
+--------┬---------+  +--------┬---------+  +------┬------+
         │                     │                   │
         ▼                     ▼                   ▼
+------------------+  +------------------+  +------------------+
|      chat        |  |   attachment     |  | chat_handle_join |
|------------------|  |------------------|  |------------------|
| rowid (PK)       |  | rowid (PK)       |  | chat_id (FK)     |
| guid (UK)        |  | guid (UK)        |  | handle_id (FK)   |
| chat_identifier  |  | filename         |  +------------------+
| display_name     |  | mime_type        |
+------------------+  +------------------+
```

Key relationships:

- **chat** <- `chat_message_join` -> **message**: Many-to-many (messages can be forwarded)
- **message** <- `message_attachment_join` -> **attachment**: Many-to-many (multiple attachments per message)
- **message** -> **handle**: Many-to-one via `handle_id` (sender/recipient)
- **chat** <- `chat_handle_join` -> **handle**: Many-to-many (participants in conversations)

---

## Schema: message Table

The `message` table contains all message data. Fields relevant to media processing:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ROWID` | INTEGER | Primary key, auto-incrementing | `12345` |
| `guid` | TEXT | Globally unique identifier | `"A4243054-B1A8-4498-91FC-DC442E381D7B"` |
| `text` | TEXT | Plain text content of the message | `"Hello!"` |
| `attributedBody` | BLOB | Rich text content (NSAttributedString) - see decoding section below | `(binary)` |
| `handle_id` | INTEGER | Foreign key to `handle` table (sender/recipient) | `42` |
| `service` | TEXT | Service type | `"iMessage"`, `"SMS"` |
| `date` | INTEGER | Timestamp in Apple Cocoa format (nanoseconds since 2001-01-01) | `731540048638000000` |
| `is_from_me` | INTEGER | 1 if sent by device owner, 0 if received | `1` or `0` |
| `cache_has_attachments` | INTEGER | Whether message has attachments | `1` or `0` |
| `cache_roomnames` | TEXT | Cached room name(s) for group chats | `"chat516933497360157144"` |

---

## Schema: chat Table

The `chat` table contains conversation metadata.

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ROWID` | INTEGER | Primary key | `1` |
| `guid` | TEXT | Globally unique identifier | `"iMessage;+;chat516933497360157144"` |
| `style` | INTEGER | Chat style (43=group, 45=1:1) | `43` or `45` |
| `chat_identifier` | TEXT | Identifier for the chat | `"chat516933497360157144"` or `"+14155551234"` |
| `service_name` | TEXT | Service type | `"iMessage"`, `"SMS"` |
| `display_name` | TEXT | User-set display name | `"Family Chat"` or `NULL` |

### Chat Style Values

| Value | Meaning |
|-------|---------|
| `43` | Group chat (3+ participants) |
| `45` | Direct message (1:1 conversation) |

---

## Schema: handle Table

The `handle` table contains contact identifiers.

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ROWID` | INTEGER | Primary key | `1` |
| `id` | TEXT | Contact identifier (phone/email) | `"+14155551234"`, `"user@icloud.com"` |
| `service` | TEXT | Service type | `"iMessage"`, `"SMS"` |

### Handle ID Formats

- **Phone numbers**: E.164 format with `+` prefix: `"+14155551234"`
- **Email addresses**: Full email: `"user@example.com"`

---

## Schema: attachment Table

The `attachment` table contains file metadata. This is the primary table for media extraction.

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ROWID` | INTEGER | Primary key | `1` |
| `guid` | TEXT | Unique identifier | `"at_0_5D60152C-62C2-46B1-B138-FE5E44EC9C97"` |
| `created_date` | INTEGER | Creation timestamp (Apple Cocoa) | `731541191` |
| `filename` | TEXT | Path to file on disk | `"~/Library/Messages/Attachments/73/03/..."` |
| `uti` | TEXT | Uniform Type Identifier | `"public.heic"`, `"public.jpeg"` |
| `mime_type` | TEXT | MIME type | `"image/heic"`, `"image/jpeg"` |
| `transfer_state` | INTEGER | Download/upload state | `5` (complete) |
| `is_outgoing` | INTEGER | Whether sent by device owner | `1` or `0` |
| `transfer_name` | TEXT | Original filename | `"IMG_0926.HEIC"` |
| `total_bytes` | INTEGER | File size in bytes | `148605` |
| `hide_attachment` | INTEGER | Whether hidden | `1` or `0` |

### Attachment Path Structure

Attachments are stored in a hashed directory structure based on GUIDs:

```text
~/Library/Messages/Attachments/
├── 4f/
│   └── 15/
│       └── D7CEBAED-9844-4B25-B841-F7748EA3BCAD/
│           └── IMG_4911.heic
├── a2/
│   └── 3c/
│       └── F1234567-8901-2345-6789-ABCDEF012345/
│           └── video.mov
└── ...
```

The `attachment.filename` column stores the full path with `~` prefix:

```text
~/Library/Messages/Attachments/4f/15/D7CEBAED-9844-4B25-B841-F7748EA3BCAD/IMG_4911.heic
```

Where the 2-character hex directories (e.g., `4f`, `15`) are derived from the attachment GUID.

### Common File Types

| UTI | MIME Type | Description |
|-----|-----------|-------------|
| `public.heic` | `image/heic` | HEIC photos (default iPhone format) |
| `public.jpeg` | `image/jpeg` | JPEG photos |
| `public.png` | `image/png` | PNG images |
| `com.apple.quicktime-movie` | `video/quicktime` | MOV videos |
| `public.mpeg-4` | `video/mp4` | MP4 videos |
| `com.apple.coreaudio-format` | `audio/x-caf` | Voice messages (CAF) |
| `public.mpeg-4-audio` | `audio/m4a` | Voice messages (M4A) |
| `public.vcard` | `text/vcard` | Contact cards |

### Live Photos

Live Photos sent via iMessage are stored as paired files in the same attachment directory:

```text
~/Library/Messages/Attachments/0b/11/4AE5AABC-68ED-4EB1-BAA8-16DB792575E9/
├── lp_image.HEIC    # Still image (tracked in database)
└── lp_image.MOV     # Video component (sidecar file, NOT in database)
```

**Key characteristics:**

| Aspect | Details |
|--------|---------|
| Image filename | Always `lp_image.HEIC` |
| Video filename | Always `lp_image.MOV` |
| Database tracking | Only the HEIC file has a record in the `attachment` table |
| Video discovery | Must scan filesystem for `lp_image.MOV` in same directory |
| Prevalence | Analysis of real exports shows ~24-34 Live Photos per export |

**Important:** The MOV video component is **not tracked in the database**. Processors must:

1. Query the database for `lp_image.HEIC` attachments
2. Check the parent directory on disk for a matching `lp_image.MOV` file
3. Process both files with linked metadata

**Example query to find Live Photos:**

```sql
SELECT 
    a.ROWID,
    a.filename,
    a.transfer_name,
    a.total_bytes
FROM attachment a
WHERE a.transfer_name = 'lp_image.HEIC'
  AND a.transfer_state = 5;
```

Then for each result, check if `Path(filename).parent / 'lp_image.MOV'` exists on disk.

---

## Schema: Join Tables

### `chat_message_join`

Links messages to conversations.

| Field | Type | Description |
|-------|------|-------------|
| `chat_id` | INTEGER | Foreign key to `chat.ROWID` |
| `message_id` | INTEGER | Foreign key to `message.ROWID` |
| `message_date` | INTEGER | Denormalized timestamp for query optimization |

### `chat_handle_join`

Links participants to conversations.

| Field | Type | Description |
|-------|------|-------------|
| `chat_id` | INTEGER | Foreign key to `chat.ROWID` |
| `handle_id` | INTEGER | Foreign key to `handle.ROWID` |

### `message_attachment_join`

Links attachments to messages.

| Field | Type | Description |
|-------|------|-------------|
| `message_id` | INTEGER | Foreign key to `message.ROWID` |
| `attachment_id` | INTEGER | Foreign key to `attachment.ROWID` |

---

## Timestamp Conversion

iMessage uses Apple Cocoa timestamps (nanoseconds since 2001-01-01 00:00:00 UTC).

### Conversion Formula

```text
unix_timestamp = (apple_timestamp / 1,000,000,000) + 978307200
```

Where `978307200` is the number of seconds between Unix epoch (1970-01-01) and Apple Cocoa epoch (2001-01-01).

### Python Example

```python
from datetime import datetime

APPLE_EPOCH_OFFSET = 978307200

def convert_timestamp(apple_timestamp: int) -> datetime:
    """Convert Apple Cocoa timestamp to Python datetime."""
    unix_timestamp = (apple_timestamp / 1_000_000_000) + APPLE_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_timestamp)
```

---

## Decoding attributedBody (NSTypedStream Format)

Starting with macOS Ventura (13.0) and iOS 16, Apple changed the Messages database schema so that the `text` column is frequently NULL, with message content stored exclusively in the `attributedBody` column. This affects outgoing messages most significantly.

### Background

The `attributedBody` column contains a serialized `NSMutableAttributedString` (or `NSAttributedString`) encoded using Apple's **NSTypedStream** format (the older serialization format used by `NSArchiver`, not the newer `NSKeyedArchiver`).

**Impact:**

- Mac exports (macOS Ventura+): ~99% of messages have NULL `text` but populated `attributedBody`
- iPhone exports: ~15-20% of messages may need `attributedBody` extraction
- The `text` column may still be populated for older messages or received messages

### NSTypedStream Format Structure

The blob has the following structure:

```text
Header:     \x04\x0bstreamtyped   (magic bytes identifying NSTypedStream format)
Version:    \x81\xe8\x03          (version/flags)
Classes:    Class hierarchy definitions
Content:    Encoded string data with length prefix
Attributes: Rich text attributes (mentions, links, etc.)
```

### Text Extraction

The message text is stored after the `NSString` class marker. The pattern to locate text:

1. Find the marker `\x01+` which precedes the string content
2. Read the length byte(s) immediately after
3. Extract that many bytes as UTF-8 text

**Length Encoding:**

| First Byte | Meaning | Format |
|------------|---------|--------|
| `0x00` | Empty string | N/A |
| `0x01-0x7F` | Direct length (1-127 chars) | First byte is the length |
| `0x81` | Extended length (see below) | Variable format based on second byte |
| `0x82` | 2-byte length | Next 2 bytes, big-endian |
| `0x84` | 4-byte length | Next 4 bytes, big-endian |

**`0x81` Extended Length Encoding:**

The `0x81` prefix has two sub-formats depending on the second byte:

| Second Byte | Format | Description |
|-------------|--------|-------------|
| `< 0x80` | `0x81 + low_byte + high_byte` | 2-byte little-endian length (256-32767) |
| `>= 0x80` | `0x81 + length_byte + 0x00` | Single byte length (128-255) with separator |

**Example Blob (hex):**

```text
04 0b 73 74 72 65 61 6d 74 79 70 65 64  <- "streamtyped" header
81 e8 03                                  <- version
84 01 40 84 84 84 12                      <- flags
4e 53 41 74 74 72 69 62 75 74 65 64 53 74 72 69 6e 67  <- "NSAttributedString"
00 84 84 08                               <- separator
4e 53 4f 62 6a 65 63 74                   <- "NSObject"
00 85 92 84 84 84 08                      <- separator
4e 53 53 74 72 69 6e 67                   <- "NSString"
01 94 84 01 2b                            <- marker bytes ending with \x01+
0d                                        <- length: 13 bytes
48 65 6c 6c 6f 2c 20 77 6f 72 6c 64 21   <- "Hello, world!" (UTF-8)
86 ...                                    <- attributes follow
```

### Python Decoder Implementation

```python
from typing import Optional, Tuple

def decode_attributed_body(blob: bytes) -> Tuple[Optional[str], dict]:
    """
    Decode text from NSTypedStream-encoded NSAttributedString blob.
    
    Args:
        blob: Raw bytes from attributedBody column
        
    Returns:
        Tuple of (text, metadata) where:
        - text: Decoded UTF-8 string, empty string for empty messages, 
                or None if decoding failed
        - metadata: Dict with rich text attributes found in the blob
    """
    if not blob or len(blob) < 10:
        return None, {}
    
    # Verify NSTypedStream header
    if not blob.startswith(b'\x04\x0bstreamtyped'):
        return None, {'error': 'Invalid NSTypedStream header'}
    
    # Find the text marker
    idx = blob.find(b'\x01+')
    if idx == -1:
        return None, {'error': 'No text marker found'}
    
    length_start = idx + 2
    if length_start >= len(blob):
        return None, {'error': 'Truncated blob'}
    
    # Decode length
    first_byte = blob[length_start]
    
    if first_byte == 0:
        return '', {}  # Empty string
    
    if first_byte < 0x80:
        # Single byte length (1-127)
        text_length = first_byte
        text_start = length_start + 1
    elif first_byte == 0x81:
        # Extended length encoding - format depends on second byte
        if length_start + 2 >= len(blob):
            return None, {'error': 'Truncated length'}
        second_byte = blob[length_start + 1]
        if second_byte < 0x80:
            # 2-byte little-endian length (for lengths >= 256)
            # Format: 0x81 + low_byte + high_byte
            if length_start + 3 > len(blob):
                return None, {'error': 'Truncated length'}
            third_byte = blob[length_start + 2]
            text_length = second_byte | (third_byte << 8)
            text_start = length_start + 3
        else:
            # Single byte length (128-255) followed by 0x00 separator
            # Format: 0x81 + length_byte + 0x00
            text_length = second_byte
            text_start = length_start + 3  # Skip the 0x00 separator
    elif first_byte == 0x82:
        # Extended: 0x82 + 2 byte big-endian length
        if length_start + 2 >= len(blob):
            return None, {'error': 'Truncated length'}
        text_length = (blob[length_start + 1] << 8) | blob[length_start + 2]
        text_start = length_start + 3
    elif first_byte == 0x84:
        # Extended: 0x84 + 4 byte big-endian length
        if length_start + 4 >= len(blob):
            return None, {'error': 'Truncated length'}
        text_length = (
            (blob[length_start + 1] << 24) | 
            (blob[length_start + 2] << 16) |
            (blob[length_start + 3] << 8) | 
            blob[length_start + 4]
        )
        text_start = length_start + 5
    else:
        return None, {'error': f'Unknown length encoding: 0x{first_byte:02x}'}
    
    text_end = text_start + text_length
    if text_end > len(blob):
        return None, {'error': 'Text extends beyond blob'}
    
    # Decode UTF-8 text
    try:
        text = blob[text_start:text_end].decode('utf-8')
    except UnicodeDecodeError:
        text = blob[text_start:text_end].decode('utf-8', errors='replace')
    
    # Extract metadata about rich attributes
    metadata = {}
    if b'__kIMMentionConfirmedMention' in blob:
        metadata['has_mentions'] = True
    if b'__kIMFileTransferGUIDAttributeName' in blob:
        metadata['has_inline_attachment'] = True
    
    # Object replacement character indicates inline attachment placeholder
    if '\ufffc' in text:
        metadata['has_object_replacement'] = True
    
    return text, metadata
```

### Processing Strategy

When processing iMessage exports:

1. **Prefer `text` column** if it contains meaningful text
2. **Fall back to `attributedBody`** when `text` is NULL, empty, or contains only placeholder characters
3. **Handle empty messages** (`is_empty=1` has a 76-byte blob with zero-length string)

**Meaningful Text Check:**

Not all non-empty text is meaningful. The `text` column may contain only placeholder characters:

- Object Replacement Character (`\ufffc` / U+FFFC) - placeholder for inline attachments
- Unicode Replacement Character (`\ufffd` / U+FFFD) - indicates invalid/undecodable bytes

```python
def has_meaningful_text(text: Optional[str]) -> bool:
    """Check if text contains actual content, not just placeholders."""
    if not text:
        return False
    # Strip whitespace and placeholder characters
    stripped = text.replace('\ufffc', '').replace('\ufffd', '').strip()
    return len(stripped) > 0

def get_message_text(row):
    """Extract message text from row with full fallback chain."""
    # Prefer text column if it has meaningful content
    if has_meaningful_text(row['text']):
        return row['text']
    
    # Try attributedBody
    if row['attributedBody']:
        text, metadata = decode_attributed_body(row['attributedBody'])
        if has_meaningful_text(text):
            return text
    
    return None
```

### Special Characters

- **Object Replacement Character** (`\ufffc` / U+FFFC): Placeholder for inline attachments within text. The actual attachment is referenced via `__kIMFileTransferGUIDAttributeName`.
- **Zero-Width Spaces**: May appear around mentions or links

---

## Platform-Specific Notes

### Attachment Path Handling

- **Mac export**: Paths reference `~/Library/Messages/Attachments/...`
  - Replace `~` with export root path
- **iPhone export**: Paths reference `~/Library/SMS/Attachments/...`
  - Files located at `<export>/SMS/Attachments/...`

### Group Chats

- Group chats have `chat.style = 43`
- Direct messages have `chat.style = 45`
- `chat.display_name` contains user-set group name
- `chat_handle_join` contains all participants

---

## Example Query: Extract Attachments with Metadata

```sql
SELECT 
    a.ROWID as attachment_id,
    a.filename,
    a.mime_type,
    a.transfer_name as original_filename,
    a.total_bytes,
    a.is_outgoing,
    datetime(m.date/1000000000.0 + 978307200, 'unixepoch') as message_date,
    m.is_from_me,
    m.text as message_text,
    c.display_name as chat_name,
    c.chat_identifier,
    c.style as chat_style,
    h.id as sender_id
FROM attachment a
JOIN message_attachment_join maj ON a.ROWID = maj.attachment_id
JOIN message m ON maj.message_id = m.ROWID
JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
JOIN chat c ON cmj.chat_id = c.ROWID
LEFT JOIN handle h ON m.handle_id = h.ROWID
WHERE a.filename IS NOT NULL
  AND a.transfer_state = 5  -- Complete transfers only
ORDER BY m.date;
```

---

## Related Documentation

- [vCard Schema](vCard-Schema.md) - Contact file format used for matching iMessage handles to names
