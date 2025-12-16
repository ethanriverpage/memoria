# Google Export Guide

This guide covers processing Google Takeout exports including Google Photos, Google Chat, and Google Voice.

## Table of Contents

1. [Overview](#overview)
2. [Preparing Your Google Export](#preparing-your-google-export)
3. [Google Photos](#google-photos)
4. [Google Chat](#google-chat)
5. [Google Voice](#google-voice)
6. [Processing Your Export](#processing-your-export)

## Overview

Google provides data export through Google Takeout. Memoria can process:

- **Google Photos**: Albums, shared libraries, and photo metadata
- **Google Chat**: Group and direct message media
- **Google Voice**: Media from text conversations

---

## Important Design Decisions

> **Why album folders aren't preserved:**
>
> Google Photos exports contain massive duplication when the same photo appears in multiple albums. By flattening the structure and embedding album names in metadata, Memoria:
>
> - Saves 30-60% disk space by deduplicating
> - Speeds up processing significantly
> - Prevents confusion about which copy is "canonical"
> - Makes uploads to Immich cleaner and faster
>
> Album information is preserved in EXIF metadata (`ImageDescription` and `IPTC:Caption-Abstract` fields) and used to create Immich albums. No information is lost, just stored differently.
>
> See [Design Decisions](Design-Decisions.md#flat-directory-structure) for full rationale.

---

## Preparing Your Google Export

After downloading your Google Takeout export:

1. **Extract all archives**: If your export was split into multiple archives, extract them all to the same directory. Google Takeout names them sequentially (`takeout-001.zip`, `takeout-002.zip`, etc.).

2. **Rename the directory** to follow Memoria's naming convention:

```text
google-username-YYYYMMDD/
```

Example:

```text
google-john.doe-20250526/
```

**Note**: Use your actual Google account username (the part before @gmail.com). The date can be the export date or any date for reference.

## Google Photos

### Export Structure

```text
google-username-YYYYMMDD/
└── Takeout/
    └── Google Photos/
        ├── Photos from 2023/
        │   ├── IMG_1234.jpg
        │   ├── IMG_1234.jpg.json
        │   └── ...
        ├── Albums/
        │   ├── Summer 2023/
        │   │   ├── photo1.jpg
        │   │   ├── photo1.jpg.json
        │   │   └── ...
        │   └── ...
        └── metadata.json
```

### What Gets Processed

- Photos and videos from your library
- Album metadata (names preserved in file metadata)
- Shared library content
- Live Photos (both photo and video components)
- Motion Photos
- Metadata including:
  - Original capture timestamp
  - GPS coordinates
  - Camera settings
  - Photo descriptions
  - Faces/people tags
  - Album names

### Important: Album Organization

**Album folder structure is not preserved.** All photos are placed in a flat directory structure under `photos/`. While album names are extracted from the Google Photos export and embedded in the file metadata, the physical organization into album folders is lost.

If you need photos organized by albums in Immich or another photo management system, the album information will be available in the metadata for those systems to use, but Memoria itself outputs a flat file structure for simplicity and deduplication purposes.

### Embedded Metadata

After processing, each file has metadata embedded directly into it. For images, the `ImageDescription` and `IPTC:Caption-Abstract` fields contain:

```text
Source: Google Photos/john.doe
```

For videos, the same information is embedded in the `Comment` and `Description` fields.

For Google Chat and Google Voice, the embedded metadata includes conversation context:

```text
Source: Google Chat/john.doe
Conversation: "Family Group"
Sender: "Jane Smith"
```

This metadata is visible in most photo applications and ensures your files remain properly attributed even if moved to different systems.

### Deduplication

Google Photos exports often contain duplicates due to:

- Same photo in multiple albums
- Photos in both regular and shared libraries
- Archived vs non-archived versions

Memoria includes automatic deduplication. See [Deduplication](Deduplication) for details.

### Live Photos and Motion Photos

Google Photos exports Live Photos (iOS) and Motion Photos (Android) as separate files:

- `IMG_1234.jpg` - The photo
- `IMG_1234.mp4` - The video component

Memoria processes both components and links them with appropriate metadata.

### Common Issues

**Missing JSON files**: Some older uploads may not have JSON metadata. Memoria will fall back to EXIF data embedded in the file itself.

**Timestamp discrepancies**: If the JSON timestamp differs from EXIF data, JSON takes precedence as it represents the "taken time" in Google Photos.

## Google Chat

### Structure

```text
google-username-YYYYMMDD/
└── Takeout/
    └── Google Chat/
        ├── Groups/
        │   ├── Group Name/
        │   │   ├── messages.json
        │   │   ├── group_info.json
        │   │   ├── photo1.jpg
        │   │   ├── video1.mp4
        │   │   └── ...
        │   └── ...
        └── Users/
            ├── Person Name/
            │   ├── messages.json
            │   ├── photo1.jpg
            │   └── ...
            └── ...
```

### Processing

- Photos and videos from group chats
- Media from direct messages
- Metadata including:
  - Message timestamps
  - Sender information
  - Chat names
  - Message context

### Required Elements

- `Google Chat/` directory
- Conversation subdirectories
- `messages.json` files
- Media files within conversation folders

### Output Organization

Processed chat media is placed directly in the chat directory:

```text
output/chat/
├── gchat-username-Project_Team-20230115.jpg
├── gchat-username-Project_Team-20230116.mp4
├── gchat-username-John_Doe-20230220.jpg
└── ...
```

All files from both Groups and Users (direct messages) are organized together with filenames that include the conversation name.

### Issues

**Group name changes**: If a group was renamed, it appears under its most recent name.

**Deleted messages**: Media from deleted messages is not included in exports.

## Google Voice

### Structure

```text
google-username-YYYYMMDD/
└── Takeout/
    └── Voice/
        └── Calls/
            ├── +1234567890 - Text - 2023-01-15T14_30_00Z.html
            ├── call_recording_123.mp3
            └── ...
```

### Processing

- Media files from text conversations (photos, videos, audio)
- Metadata including:
  - Message timestamps
  - Caller/recipient phone numbers
  - Sender information
  - Conversation context

### Required Elements

- `Voice/Calls/` directory
- HTML conversation files (format: `+XXXXXXXXXX - Text - YYYY-MM-DDTHH_MM_SSZ.html`)
- Media files within the Calls directory

### Issues

**No media files**: Google Voice exports only include media files that were sent or received in text conversations. Call recordings and voicemails are not typically included unless they were shared as messages.

**HTML parsing**: Conversation metadata is extracted from HTML files, which may have formatting variations.

## Processing Your Export

### Single Google Export

```bash
./memoria.py /path/to/google-username-20250526 -o /path/to/output
```

Memoria will automatically detect which Google services are present in your export:

- Google Photos only
- Google Chat only
- Google Voice only
- Any combination of the above

### Processing Specific Services

If you have a large export and want to process only one service:

```bash
# Process only Google Photos
./memoria.py /path/to/google-export/Takeout/Google\ Photos -o /path/to/output

# Process only Google Chat
./memoria.py /path/to/google-export/Takeout/Google\ Chat -o /path/to/output
```

### Multiple Google Accounts

```text
/path/to/exports/
├── google-personal-20250526/
├── google-work-20250526/
└── google-old_account-20250526/
```

Process them all:

```bash
./memoria.py --originals /path/to/exports -o /path/to/output
```

### Output Structure

```text
/path/to/output/
├── photos/
│   ├── gphotos-username-IMG_1234.jpg
│   ├── gphotos-username-IMG_1235.mp4
│   └── ...
├── chat/
│   ├── gchat-username-Family_Chat-20230115.jpg
│   ├── gchat-username-Work_Group-20230220.mp4
│   └── ...
└── voice/
    ├── gvoice-username-+1234567890-20230115.mp3
    ├── gvoice-username-+0987654321-20230220.jpg
    └── ...
```

### Immich Albums

When uploading to Immich, Google content is organized as:

- **Google Photos**: `Google Photos/{username}`
- **Google Chat**: `Google Chat/{username}`
- **Google Voice**: `Google Voice/{username}`

See [Immich Upload](Immich-Upload) for configuration options.

## Processing Performance

Google Photos libraries can be very large. Use parallel processing:

```bash
# Process with 8 parallel workers
./memoria.py /path/to/google-export -o /path/to/output --workers 8
```

See [Parallel Processing](Parallel-Processing) for optimization tips.

## Tips

1. **Album Metadata**: If a photo appears in multiple albums, Memoria's deduplication will keep only one copy with all album names preserved in the file's metadata.

2. **Shared Libraries**: Content from shared libraries (e.g., partner sharing) appears in a separate folder but is processed the same way.

3. **Processing Time**: Large Google Photos libraries (10,000+ items) can take several hours to process. Use `--verbose` to monitor progress.

4. **Disk Space**: Ensure you have at least 2x the export size available for temporary files and output.

## Technical Reference

### Google Photos JSON Metadata Format

Each media file in a Google Photos export has a corresponding `.json` file containing metadata:

```json
{
  "title": "IMG_1234.jpg",
  "photoTakenTime": {
    "timestamp": "1673785845"
  },
  "geoData": {
    "latitude": 37.7749,
    "longitude": -122.4194
  },
  "description": "Photo description"
}
```

This JSON file is parsed by Memoria to extract timestamps, GPS coordinates, and descriptions that are then embedded into the media file as EXIF tags.

## Related Documentation

- [Getting Started](Getting-Started) - Initial setup guide
- [Usage](Usage) - Detailed usage options
- [Deduplication](Deduplication) - Google Photos deduplication details
- [Immich Upload](Immich-Upload) - Immich upload configuration
