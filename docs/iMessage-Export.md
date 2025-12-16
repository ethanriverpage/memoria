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
11. [Troubleshooting](#troubleshooting)

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

> **Contacts Recommended:**
>
> For best results, export your contacts as a vCard file (`contacts.vcf`) and place it alongside your message exports. This allows Memoria to display contact names instead of phone numbers/email addresses in the metadata.

---

## Preparing Your iMessage Export

### Directory Naming Convention

Create a directory following Memoria's naming convention:

**Mac exports:**

```
mac-messages-YYYYMMDD/
```

**iPhone exports:**

```
{device}-messages-YYYYMMDD/
```

Examples:

- `mac-messages-20251202/`
- `iphone14-messages-20251015/`
- `ipadpro-messages-20251010/`

The device identifier (e.g., `mac`, `iphone14`) is used in output filenames and Immich album names.

---

## Mac Export

### Export Structure

```
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

### Export Structure

```
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

### Required Elements

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

### Overview

When you have multiple iMessage exports (e.g., from different backups over time), Memoria can process them together to:

- **Deduplicate** identical media files across exports
- **Preserve** all conversation context from each occurrence
- **Consolidate** into a single output directory

### Example Structure

```
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

```
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

```
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

```
Source: iMessage/mac
  - John Doe in "DM with +14045551234": "Check out this photo!"
```

**Single message (Group):**

```
Source: iMessage/mac
  - Jane Smith in "Group: Family Chat": "From our trip yesterday"
```

**Merged message (same file in multiple conversations):**

```
Source: iMessage/iphone14
  - John Doe in "DM with +14045551234" [iphone14-messages-20240601]: "Photo from yesterday"
  - John Doe in "Group: Work Team" [iphone14-messages-20241001]: "Sharing here too"
```

### Live Photo Videos

Live Photo video components include a marker:

```
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

1. **Detection**: Looks for `lp_image.HEIC` files with corresponding `lp_image.MOV` sidecars
2. **Processing**: Both files are processed with linked metadata
3. **Marking**: Video components are marked with `is_live_photo_video: true`

### Output

Both components are copied:

- `imessage-mac-contact-20231015.heic` (still image)
- `imessage-mac-contact-20231015.mov` (video component)

The EXIF description for the video includes `[Live Photo Video]` marker.

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
- [iMessage Schema](iMessage-Schema) - Technical database schema details
- [vCard Schema](vCard-Schema) - Contact file format details







