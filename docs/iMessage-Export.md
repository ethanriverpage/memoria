# iMessage Export Guide

This guide covers processing iMessage exports from Mac and iPhone backups.

## Table of Contents

1. [Overview](#overview)
2. [Preparing Your iMessage Export](#preparing-your-imessage-export)
3. [Mac Export](#mac-export)
4. [iPhone Export](#iphone-export)
5. [Contacts File](#contacts-file)
6. [Cross-Export Consolidation](#cross-export-consolidation)
7. [Processing Your Export](#processing-your-export)
8. [Embedded Metadata](#embedded-metadata)
9. [Deduplication](#deduplication)
10. [Live Photos](#live-photos)
11. [Placeholder Attachments](#placeholder-attachments)
12. [Troubleshooting](#troubleshooting)

## Overview

Memoria can process iMessage exports from both Mac computers and iPhone backups:

- **Mac Export**: Messages stored in `chat.db` with attachments in `Attachments/`
- **iPhone Export**: Messages stored in `SMS/sms.db` with attachments in `SMS/Attachments/`

The processor extracts all media attachments with their full conversation context, embeds metadata directly into files, and supports cross-export deduplication when processing multiple exports together.

---

## Important Notes

> **Database Access Required:**
>
> iMessage exports require access to the SQLite database files (`chat.db` or `sms.db`). These are extracted from:
>
> - **Mac**: Direct copy from `~/Library/Messages/`
> - **iPhone**: iTunes/Finder backup (unencrypted or decrypted)
>
> Encrypted iPhone backups must be decrypted before processing. Tools like iMazing or open-source backup extractors can help.
>
> **Contacts Recommended:**
>
> For best results, export your contacts as a vCard file (`contacts.vcf`) and place it alongside your message exports. This allows Memoria to display contact names instead of phone numbers/email addresses in the metadata.

---

## Preparing Your iMessage Export

### Directory Naming Convention

Create a directory following Memoria's naming convention:

**Mac exports:**

```text
mac-messages-YYYYMMDD/
```

**iPhone exports:**

```text
{device}-messages-YYYYMMDD/
```

Examples:

- `mac-messages-20251202/`
- `iphone14-messages-20251015/`
- `ipadpro-messages-20251010/`

The device identifier (e.g., `mac`, `iphone14`) is used in output filenames and Immich album names.

---

## Mac Export

### Mac Export Structure

```text
mac-messages-YYYYMMDD/
├── chat.db
├── chat.db-shm (optional)
├── chat.db-wal (optional)
└── Attachments/
    ├── 00/
    │   └── 00/
    │       └── XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX/
    │           └── IMG_1234.heic
    └── ...
```

### How to Export from Mac

1. **Close Messages app** to ensure database consistency

2. **Copy the Messages folder:**

   ```bash
   cp -r ~/Library/Messages /path/to/mac-messages-YYYYMMDD
   ```

3. **Verify the structure:**
   - `chat.db` should be present at the root
   - `Attachments/` directory should contain media files

### Required Elements

For Memoria to detect a Mac iMessage export:

- `chat.db` file (SQLite database)
- `Attachments/` directory with media files

---

## iPhone Export

### iPhone Export Structure

```text
{device}-messages-YYYYMMDD/
└── SMS/
    ├── sms.db
    ├── sms.db-shm (optional)
    ├── sms.db-wal (optional)
    └── Attachments/
        ├── 00/
        │   └── 00/
        │       └── XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX/
        │           └── IMG_5678.jpeg
        └── ...
```

### How to Export from iPhone

1. **Create an unencrypted backup** using iTunes (Windows) or Finder (Mac)
   - Connect your iPhone
   - In iTunes/Finder, ensure "Encrypt local backup" is **unchecked**
   - Click "Back Up Now"

2. **Locate the backup:**
   - **Mac**: `~/Library/Application Support/MobileSync/Backup/`
   - **Windows**: `%APPDATA%\Apple Computer\MobileSync\Backup\`

3. **Extract SMS data** using a backup extractor tool:
   - Copy the `SMS/` directory from the extracted backup
   - Place it in your export directory

### iPhone Required Elements

For Memoria to detect an iPhone iMessage export:

- `SMS/sms.db` file (SQLite database)
- `SMS/Attachments/` directory with media files

---

## Contacts File

### Why Use Contacts?

The iMessage database stores sender/recipient information as phone numbers or email addresses (called "handles"). To display friendly names like "John Doe" instead of "+14045551234", Memoria can parse a contacts export.

### Export Your Contacts

1. **On Mac** (Contacts.app):
   - Open Contacts
   - Select All (Cmd+A)
   - File > Export > Export vCard
   - Save as `contacts.vcf`

2. **On iPhone**:
   - Use iCloud.com to export contacts
   - Or use a third-party contacts backup app

### Placement

Place `contacts.vcf` in one of these locations (checked in order):

1. Inside each export directory: `mac-messages-YYYYMMDD/contacts.vcf`
2. In the parent directory of exports: `exports/contacts.vcf`
3. In a common location: `/mnt/media/originals/contacts.vcf`

### What Gets Matched

The vCard parser extracts:

- **Phone numbers**: Normalized to digits (e.g., "+14045551234" or "4045551234")
- **Email addresses**: Lowercased for matching

Each phone/email is mapped to the contact's display name (FN field in vCard).

---

## Cross-Export Consolidation

### Consolidation Overview

When you have multiple iMessage exports (e.g., from different backups over time), Memoria can process them together to:

- **Deduplicate** identical media files across exports
- **Preserve** all conversation context from each occurrence
- **Consolidate** into a single output directory

### Example Structure

```text
exports/
├── mac-messages-20240601/
│   ├── chat.db
│   └── Attachments/
├── iphone14-messages-20241001/
│   └── SMS/
│       ├── sms.db
│       └── Attachments/
└── contacts.vcf
```

### Processing Multiple Exports

```bash
./memoria.py --originals /path/to/exports -o /path/to/output
```

Memoria automatically:

1. Detects all iMessage exports
2. Groups them for consolidated processing
3. Deduplicates identical files across all exports
4. Creates a single unified output

---

## Processing Your Export

### Single Export

```bash
./memoria.py /path/to/mac-messages-20251202 -o /path/to/output
```

### Multiple Exports

```bash
./memoria.py --originals /path/to/exports -o /path/to/output
```

### Output Structure

```text
/path/to/output/
└── messages/
    ├── imessage-mac-14045551234-20231015.heic
    ├── imessage-mac-14045551234-20231015_2.heic
    ├── imessage-mac-family_group-20231020.jpeg
    ├── imessage-mac-work_team-20231022.mp4
    └── ...
```

### Filename Format

Files are renamed with descriptive, sortable names:

```text
imessage-{device}-{contact_or_group}-{YYYYMMDD}.{ext}
```

Examples:

- `imessage-mac-14045551234-20231015.heic` (DM with phone number)
- `imessage-iphone14-john_doe-20231020.jpeg` (DM with resolved contact)
- `imessage-mac-family_group-20231022.mp4` (Group chat)

---

## Embedded Metadata

Memoria embeds comprehensive metadata into all processed iMessage files.

### For Images

| Tag | Content |
|-----|---------|
| `DateTimeOriginal` | Message timestamp |
| `CreateDate` | Message timestamp |
| `ModifyDate` | Message timestamp |
| `ImageDescription` | Source information with conversation context |
| `IPTC:Caption-Abstract` | Same as ImageDescription |

### For Videos

| Tag | Content |
|-----|---------|
| `creation_time` | Message timestamp |
| `Comment` | Source information with conversation context |
| `Description` | Same as Comment |

### Description Format

**Single message (DM):**

```text
Source: iMessage/mac
  - John Doe in "DM with +14045551234": "Check out this photo!"
```

**Single message (Group):**

```text
Source: iMessage/mac
  - Jane Smith in "Group: Family Chat": "From our trip yesterday"
```

**Merged message (same file in multiple conversations):**

```text
Source: iMessage/iphone14
  - John Doe in "DM with +14045551234" [iphone14-messages-20240601]: "Photo from yesterday"
  - John Doe in "Group: Work Team" [iphone14-messages-20241001]: "Sharing here too"
```

### Live Photo Videos

Live Photo video components include a marker:

```text
Source: iMessage/mac
  - me in "DM with +14045551234"
[Live Photo Video]
```

---

## Deduplication

### How It Works

Memoria uses content-based deduplication with xxHash64:

1. **Hash all files** - Each attachment file is hashed
2. **Group identical files** - Files with matching hashes are grouped
3. **Copy once** - Only one physical copy is kept
4. **Preserve all metadata** - All occurrences are tracked in metadata

### Same-Export Deduplication

Within a single export, if the same photo was sent to multiple conversations:

- One file copy is created
- Metadata includes all conversation contexts
- The oldest occurrence is used as the "primary"

### Cross-Export Deduplication

When processing multiple exports together:

- Files identical across exports are deduplicated
- The `source_export` field tracks which export each occurrence came from
- Oldest occurrence (by timestamp) becomes primary

### Metadata Structure

For duplicate files, the metadata includes a `messages` array:

```json
{
  "media_file": "IMG_1234.heic",
  "primary_created": "2023-10-15 14:30:00 UTC",
  "is_duplicate": true,
  "messages": [
    {
      "source_export": "mac-messages-20240601",
      "conversation_id": "+14045551234",
      "sender": "John Doe",
      "created": "2023-10-15 14:30:00 UTC"
    },
    {
      "source_export": "iphone14-messages-20241001",
      "conversation_id": "+14045551234",
      "sender": "John Doe",
      "created": "2023-10-15 14:30:00 UTC"
    }
  ]
}
```

---

## Live Photos

### What Are Live Photos?

Live Photos are Apple's format combining a still image (HEIC) with a short video (MOV). In iMessage, they appear as a single attachment but are stored as two files.

### How Memoria Handles Them

1. **Detection**: Looks for `lp_image.*` files (HEIC or JPEG) with corresponding `lp_image.MOV` sidecars
2. **Pairing**: A stable pairing key (derived from the attachment directory path) links the still image and video component. This key persists through preprocessing and survives cross-export deduplication, ensuring the pair stays linked even when multiple exports are consolidated.
3. **Processing**: Both files are processed with linked metadata. The video component reuses the still image's generated base filename.
4. **Marking**: Video components are marked with `is_live_photo_video: true` and both components share `live_photo_pair_keys` for pairing.

### Cross-Export Pairing

When processing multiple iMessage exports, cross-export deduplication may determine that the best quality HEIC is in one export while the MOV is in another. To handle this:

- During preprocessing, the HEIC's content hash is stored as `live_photo_image_hash` in both the HEIC and MOV metadata
- If the local HEIC is skipped (as a secondary duplicate), the processor looks up the HEIC hash in the dedup manifest to find the canonical timestamp
- The MOV then generates a matching base filename to pair with the HEIC output from the other export

This ensures Live Photo pairs stay correctly named even when their components are processed by different exports.

### Output

Both components are copied:

- `imessage-mac-contact-20231015.heic` (still image)
- `imessage-mac-contact-20231015.mov` (video component)

The EXIF description for the video includes `[Live Photo Video]` marker.

---

## Placeholder Attachments

### What Are Placeholder Attachments?

When iCloud "Optimize Storage" is enabled on a Mac or iPhone, the device may not download the full content of large attachments (especially videos). Instead, it keeps a **placeholder blob** on disk that appears to be a MOV/MP4 file but actually contains:

- Encrypted or incomplete data
- No valid video container structure (missing `ftyp` and `moov` atoms)
- High-entropy binary data that cannot be played or processed

These placeholders exist in the iMessage database with valid metadata (timestamp, sender, conversation), but the actual media content was never fully downloaded from iCloud.

### How Memoria Detects Them

Memoria uses a two-stage detection strategy:

1. **Fast header check**: Scan the first 64KB for the `ftyp` atom (a required marker in MOV/MP4 containers). If missing, the file is suspicious.

2. **Authoritative ffprobe check**: For suspicious files, run `ffprobe` to confirm. If ffprobe fails with "moov atom not found" or similar errors, the file is confirmed as a placeholder.

This approach is fast (header check takes <1ms per file) and accurate (ffprobe confirms edge cases).

### How Memoria Handles Them

Placeholder attachments are treated as **orphaned metadata** (database entry exists but media payload is missing):

- The file is **skipped** during preprocessing (not included in `metadata.json`)
- The metadata is **recorded** in `issues/failed-matching/metadata/` via FailureTracker
- The reason is logged as "Placeholder attachment (iCloud incomplete download)"

### Recovering Placeholder Content

To recover the actual media content:

1. **On Mac**: Open Messages.app, find the conversation, and click on the attachment to trigger a download from iCloud
2. **On iPhone**: Open the Messages app and tap on attachments to download them
3. **Re-export**: After downloading, create a new iMessage export

Alternatively, you can check if the same media exists in other exports (e.g., from the sender's device or a different backup).

### Statistics

When placeholders are detected during preprocessing, you'll see a log message like:

```
Detected 967 placeholder attachments (iCloud incomplete downloads)
```

These are also included in the failure report under `issues/failed-matching/metadata/`.

---

## Troubleshooting

### "No processors matched input directory"

Verify your export structure:

**Mac export** must have:

- `chat.db` at root level
- `Attachments/` directory

**iPhone export** must have:

- `SMS/sms.db`
- `SMS/Attachments/` directory

### "No attachments found to process"

Check that:

- Attachments directory contains actual media files
- Files have valid extensions (`.heic`, `.jpeg`, `.jpg`, `.png`, `.mp4`, `.mov`)
- Database has attachment records (use a SQLite browser to verify)

### Contact names not showing

Ensure:

- `contacts.vcf` is in the correct location
- vCard file uses standard format with FN (Full Name) fields
- Phone numbers in contacts match the format in messages

### Encrypted iPhone backup

iMessage exports from encrypted backups require decryption first:

- Use iMazing, iPhone Backup Extractor, or similar tools
- Extract the SMS directory after decryption
- Ensure `sms.db` is readable (not encrypted)

### "Failed to connect to database"

- Database file may be corrupted
- WAL/SHM files may be needed for consistency
- Try copying all `chat.db*` or `sms.db*` files together

### Missing messages or attachments

The iMessage database only includes:

- Messages that were on the device at backup time
- Attachments that were fully downloaded
- Messages not deleted before backup

Deleted messages and attachments cannot be recovered.

### "moov atom not found" errors in logs

This error indicates placeholder attachments from iCloud "Optimize Storage". The video files exist on disk but contain incomplete/encrypted data instead of actual video content.

**Solution**: See [Placeholder Attachments](#placeholder-attachments) for details. These files are automatically detected and tracked as orphaned metadata. To recover the actual content, open Messages.app and download the attachments from iCloud before re-exporting.

---

## Immich Albums

When uploading to Immich, iMessage content is organized as:

- **Album**: `iMessage/{device}`

Examples:

- `iMessage/mac`
- `iMessage/iphone14`

See [Immich Upload](Immich-Upload) for configuration options.

---

## Related Documentation

- [Getting Started](Getting-Started) - Initial setup guide
- [Usage](Usage) - Detailed usage options
- [Immich Upload](Immich-Upload) - Immich upload configuration
- [vCard Schema](vCard-Schema) - Contact file format details

---

## Technical Reference: Database Schema

This section describes the iMessage database schema relevant to media extraction.

### Export Sources

iMessage data can be obtained from two sources:

1. **Mac Export** (`mac-*` prefix): Direct copy of `~/Library/Messages/` from a Mac
2. **iPhone Export** (`iph*` prefix): Extracted from an iTunes/Finder backup of an iPhone

Both sources use the same SQLite database schema.

### Database Tables Overview

| Table | Description |
|-------|-------------|
| `message` | All messages with content, timestamps, and metadata |
| `chat` | Conversations (both 1:1 and group chats) |
| `handle` | Contact identifiers (phone numbers, emails, Apple IDs) |
| `attachment` | Attachment metadata (files stored on disk) |
| `chat_message_join` | Links messages to conversations |
| `chat_handle_join` | Links participants to conversations |
| `message_attachment_join` | Links attachments to messages |

### Schema: message Table

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ROWID` | INTEGER | Primary key | `12345` |
| `guid` | TEXT | Globally unique identifier | `"A4243054-B1A8-..."` |
| `text` | TEXT | Plain text content | `"Hello!"` |
| `attributedBody` | BLOB | Rich text content (NSAttributedString) | `(binary)` |
| `handle_id` | INTEGER | Foreign key to `handle` table | `42` |
| `date` | INTEGER | Timestamp in Apple Cocoa format (nanoseconds since 2001-01-01) | `731540048638000000` |
| `is_from_me` | INTEGER | 1 if sent by device owner, 0 if received | `1` or `0` |
| `cache_has_attachments` | INTEGER | Whether message has attachments | `1` or `0` |

### Schema: chat Table

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ROWID` | INTEGER | Primary key | `1` |
| `guid` | TEXT | Globally unique identifier | `"iMessage;+;chat516..."` |
| `style` | INTEGER | Chat style (43=group, 45=1:1) | `43` or `45` |
| `chat_identifier` | TEXT | Identifier for the chat | `"+14155551234"` |
| `display_name` | TEXT | User-set display name | `"Family Chat"` or `NULL` |

### Schema: attachment Table

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ROWID` | INTEGER | Primary key | `1` |
| `guid` | TEXT | Unique identifier | `"at_0_5D60152C-..."` |
| `filename` | TEXT | Path to file on disk | `"~/Library/Messages/Attachments/..."` |
| `uti` | TEXT | Uniform Type Identifier | `"public.heic"`, `"public.jpeg"` |
| `mime_type` | TEXT | MIME type | `"image/heic"` |
| `transfer_state` | INTEGER | Download/upload state (5=complete) | `5` |
| `transfer_name` | TEXT | Original filename | `"IMG_0926.HEIC"` |
| `total_bytes` | INTEGER | File size in bytes | `148605` |

**Note on `transfer_state`**: A value of 5 indicates the attachment transfer is "complete" according to the database. However, when iCloud "Optimize Storage" is enabled, the file on disk may be a placeholder blob rather than the actual media content. These placeholders pass database checks but fail container validation (see [Placeholder Attachments](#placeholder-attachments)).

### Attachment Path Structure

Attachments are stored in a hashed directory structure:

```text
~/Library/Messages/Attachments/
├── 4f/
│   └── 15/
│       └── D7CEBAED-9844-4B25-B841-F7748EA3BCAD/
│           └── IMG_4911.heic
└── ...
```

### Common File Types

| UTI | MIME Type | Description |
|-----|-----------|-------------|
| `public.heic` | `image/heic` | HEIC photos (default iPhone format) |
| `public.jpeg` | `image/jpeg` | JPEG photos |
| `com.apple.quicktime-movie` | `video/quicktime` | MOV videos |
| `public.mpeg-4` | `video/mp4` | MP4 videos |
| `com.apple.coreaudio-format` | `audio/x-caf` | Voice messages (CAF) |

### Live Photos

Live Photos are stored as paired files in the same attachment directory:

```text
~/Library/Messages/Attachments/.../
├── lp_image.HEIC    # Still image (tracked in database)
└── lp_image.MOV     # Video component (NOT in database)
```

**Important:** The MOV video component is **not tracked in the database**. Processors must scan the filesystem for `lp_image.MOV` alongside `lp_image.HEIC` files.

### Timestamp Conversion

iMessage uses Apple Cocoa timestamps (nanoseconds since 2001-01-01 00:00:00 UTC).

```python
APPLE_EPOCH_OFFSET = 978307200

def convert_timestamp(apple_timestamp: int) -> datetime:
    """Convert Apple Cocoa timestamp to Python datetime."""
    unix_timestamp = (apple_timestamp / 1_000_000_000) + APPLE_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_timestamp)
```

### Decoding attributedBody

Starting with macOS Ventura (13.0) and iOS 16, message content is often stored in `attributedBody` (NSTypedStream format) rather than the `text` column.

**Impact:**

- Mac exports (macOS Ventura+): ~99% of messages have NULL `text`
- iPhone exports: ~15-20% may need `attributedBody` extraction

The blob has a `\x04\x0bstreamtyped` header. Text is located after the `\x01+` marker with a length prefix.

### Example Query: Extract Attachments with Metadata

```sql
SELECT 
    a.filename,
    a.mime_type,
    a.transfer_name as original_filename,
    datetime(m.date/1000000000.0 + 978307200, 'unixepoch') as message_date,
    c.display_name as chat_name,
    h.id as sender_id
FROM attachment a
JOIN message_attachment_join maj ON a.ROWID = maj.attachment_id
JOIN message m ON maj.message_id = m.ROWID
JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
JOIN chat c ON cmj.chat_id = c.ROWID
LEFT JOIN handle h ON m.handle_id = h.ROWID
WHERE a.filename IS NOT NULL
  AND a.transfer_state = 5
ORDER BY m.date;
```

### Platform-Specific Notes

- **Mac export**: Paths reference `~/Library/Messages/Attachments/...`
- **iPhone export**: Paths reference `~/Library/SMS/Attachments/...`
- **Group chats**: `chat.style = 43`; Direct messages: `chat.style = 45`
- **Live Photos**: Video component not in database; must scan filesystem
