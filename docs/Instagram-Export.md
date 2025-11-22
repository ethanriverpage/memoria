# Instagram Export Guide

This guide covers processing Instagram exports including messages, posts, and legacy formats.

## Table of Contents

1. [Overview](#overview)
2. [Downloading Your Instagram Data](#downloading-your-instagram-data)
3. [Instagram Messages](#instagram-messages)
4. [Instagram Public Media (New Format)](#instagram-public-media-new-format)
5. [Instagram Old Format](#instagram-old-format)
6. [Processing Your Export](#processing-your-export)

## Overview

Instagram provides data export through their settings. Memoria can process:

- **Instagram Messages**: Direct messages and group chat media
- **Instagram Public Media**: Posts and archived posts (new JSON-based format)
- **Instagram Old Format**: Legacy exports with UTC-timestamped filenames

Instagram has changed their export format over time, so you may encounter different structures depending on when you requested your export.

---

## Important Caveats

> **Expired and temporary media is not included:**
>
> Instagram's export only contains media that still exists in their system. You will NOT find:
>
> - "View Once" photos and videos (designed to be ephemeral)
> - Expired temporary messages
> - Deleted content
> - 24-hour stories that weren't saved to archive
>
> This is a limitation of Instagram's export system, not Memoria. If you're missing media from conversations, this is likely why.

---

## Downloading Your Instagram Data

### Step 1: Request Your Export

1. Open Instagram app or web
2. Go to **Settings** > **Account Center** > **Your Information and Permissions**
3. Select **Download Your Information**
4. Choose **Request a Download**
5. Select options:
   - **Account**: Choose the account to export
   - **Date range**: All time (or custom range)
   - **Format**: JSON (recommended) or HTML
   - **Media quality**: High (recommended)
6. Click **Create Files**

### Step 2: Download and Extract

1. Wait for the download link email (can take hours or days)
2. Download the archive
3. Extract to a directory
4. Rename following Memoria's naming convention:

```
instagram-username-YYYYMMDD/
```

Example:

```
instagram-jane_doe-20251007/
```

**Note**: Use the actual Instagram username, not your display name.

## Instagram Messages

### Export Structure

```
instagram-username-YYYYMMDD/
└── your_instagram_activity/
    └── messages/
        └── inbox/
            ├── conversation_1/
            │   ├── message_1.html
            │   ├── message_2.html
            │   ├── photos/
            │   │   ├── photo_123456789.jpg
            │   │   └── video_987654321.mp4
            │   └── ...
            ├── conversation_2_groupname/
            │   ├── message_1.html
            │   └── ...
            └── ...
```

### What Gets Processed

- Photos and videos from direct messages
- Media from group conversations
- Metadata including:
  - Message timestamps
  - Sender information
  - Conversation names
  - Participant lists (for groups)

### Required Elements

- `your_instagram_activity/messages/inbox/` directory path
- Conversation folders
- Message files in `.html` format (message_1.html, message_2.html, etc.)
- Media files within conversation folders (typically in `photos/` subdirectories)

### Output Organization

Processed messages are placed in a flat structure with conversation names embedded in filenames:

```
output/messages/
├── instagram-messages-username-conversation1-20230115_1.jpg
├── instagram-messages-username-conversation1-20230115_2.mp4
├── instagram-messages-username-conversation2-20230220_1.jpg
└── ...
```

Files are named: `instagram-messages-{username}-{conversation}-{YYYYMMDD}_{sequence}.{ext}`

### Embedded Metadata

For images, the following metadata is embedded:

```
ImageDescription: Source: Instagram/username/messages
Conversation: "Conversation Name"
Sender: "Sender Name"

IPTC:Caption-Abstract: Source: Instagram/username/messages
Conversation: "Conversation Name"
Sender: "Sender Name"
```

For videos, equivalent metadata is embedded in the `Comment` and `Description` fields.

### Common Issues

**Missing media**: Instagram only includes media that hasn't expired. Temporary photos/videos sent via "View Once" or that expired won't be in the export.

**Conversation names**: Group chat names may not exactly match what you see in the app if they were changed after messages were sent.

## Instagram Public Media (New Format)

### Export Structure

```
instagram-username-YYYYMMDD/
└── media/
    ├── posts/
    │   ├── 202301/        # YYYYMM date folders
    │   │   ├── photo_123.jpg
    │   │   ├── photo_123.json
    │   │   ├── video_456.mp4
    │   │   └── video_456.json
    │   ├── 202302/
    │   │   └── ...
    │   └── ...
    ├── archived_posts/
    │   └── 202212/
    │       ├── photo_789.jpg
    │       ├── photo_789.json
    │       └── ...
    └── stories/
        └── 202301/
            └── ...
```

### What Gets Processed

- Published posts
- Archived posts
- Stories (if included in export)
- Metadata from JSON files including:
  - Post timestamp
  - Caption
  - Location (if added)
  - Hashtags
  - Tagged users

### Required Elements

- `media/posts/` and/or `media/archived_posts/` directories
- YYYYMM date-organized subfolders
- JSON metadata files alongside each media file

### Date Organization

Instagram's new format organizes content into YYYYMM folders:

- `202301` = January 2023
- `202212` = December 2022
- etc.

### Embedded Metadata

For images, the following metadata is embedded:

```
ImageDescription: Source: Instagram/username/posts
Caption: "Your post caption here with hashtags #example"

IPTC:Caption-Abstract: Source: Instagram/username/posts
Caption: "Your post caption here with hashtags #example"
```

For videos, equivalent metadata is embedded in the `Comment` and `Description` fields. The media type in the source path varies based on the content type (e.g., `posts`, `archived_posts`, `stories`, `reels`).

### Common Issues

**Split across folders**: Multi-photo posts may have images in the same month folder but with sequential numbering.

**No stories**: Stories are only included if you specifically selected them in the export options, and only stories saved to your archive.

## Instagram Old Format

### Export Structure

```
instagram-username-YYYYMMDD/
├── 2023-01-15_10-30-45_UTC.jpg
├── 2023-01-15_10-30-45_UTC.txt
├── 2023-01-15_10-31-22_UTC_1.jpg
├── 2023-01-15_10-31-22_UTC_1.txt
├── 2023-02-20_14-22-11_UTC.mp4
├── 2023-02-20_14-22-11_UTC.txt
└── ...
```

### What Gets Processed

- Photos and videos with UTC timestamps in filename
- Metadata from paired `.txt` files including:
  - Post caption
  - Post date
  - Location

### Filename Pattern

Format: `YYYY-MM-DD_HH-MM-SS_UTC[_N].{jpg|mp4|...}`

Components:

- Date and time in UTC
- Optional `_N` suffix for multi-photo posts (carousel posts)
- Media extension

Example:

```
2023-01-15_10-30-45_UTC.jpg       # Single photo post
2023-01-15_10-31-22_UTC_1.jpg     # First photo in carousel
2023-01-15_10-31-22_UTC_2.jpg     # Second photo in carousel
```

### Metadata Files

Each media file has a corresponding `.txt` file:

```
2023-01-15_10-30-45_UTC.txt
```

Contains post caption and metadata.

### Required Elements

- Files directly in export root directory (or immediate subdirectory)
- Filename pattern matching `YYYY-MM-DD_HH-MM-SS_UTC.*`
- Paired `.txt` files for metadata

### Embedded Metadata

For images, the following metadata is embedded:

```
ImageDescription: Source: Instagram/username/posts
Caption: "Your post caption from the .txt file"

IPTC:Caption-Abstract: Source: Instagram/username/posts
Caption: "Your post caption from the .txt file"
```

For videos, equivalent metadata is embedded in the `Comment` and `Description` fields.

### Common Issues

**No JSON files**: Old format uses `.txt` files instead of JSON - this is expected.

**Carousel numbering**: Multi-photo posts have `_1`, `_2`, etc. suffixes. All are processed individually.

## Processing Your Export

### Single Instagram Export

```bash
./memoria.py /path/to/instagram-username-20251007 -o /path/to/output
```

The processor will automatically detect which format(s) your export contains and process accordingly.

### Multiple Formats in One Export

Some exports may contain multiple formats:

- Messages in `your_instagram_activity/messages/`
- Posts in `media/posts/`
- Old format files at root

Memoria will detect and process all formats found.

### Multiple Instagram Accounts

```
/path/to/exports/
├── instagram-personal_account-20251007/
├── instagram-business_page-20251007/
└── instagram-old_account-20251007/
```

Process them all:

```bash
./memoria.py --originals /path/to/exports -o /path/to/output
```

### Output Structure

Processed Instagram content is organized by type:

```
/path/to/output/
├── messages/              # Instagram Messages (flat structure)
│   ├── instagram-messages-username-conv1-20230115_1.jpg
│   └── ...
└── public-media/          # Instagram Public Media (new & old format)
    ├── posts/
    │   ├── insta-posts-username-20230115.jpg
    │   └── ...
    ├── archived_posts/
    │   └── ...
    ├── stories/
    │   └── ...
    └── reels/
        └── ...
```

### Immich Albums

When uploading to Immich, Instagram content is organized as:

- **Messages**: `Instagram/{username}/messages`
- **Posts**: `Instagram/{username}/posts`
- **Archived Posts**: `Instagram/{username}/archived_posts`
- **Stories**: `Instagram/{username}/stories`
- **Reels**: `Instagram/{username}/reels`
- **Profile Photos**: `Instagram/{username}/profile`

Note: Old format posts are combined with new format posts in the `Instagram/{username}/posts` album.

See [Immich Upload](Immich-Upload) for configuration options.

## Tips

1. **Export Format**: Request JSON format when downloading - it provides richer metadata than HTML.

2. **Media Quality**: Choose "High" quality to preserve original resolution.

3. **Multiple Exports**: If you have exports from different dates, Memoria will process them separately (no deduplication between Instagram exports currently).

4. **Conversation Privacy**: Message media includes sender information in metadata, which may be relevant for privacy when sharing or uploading.

5. **Stories**: To include stories, make sure to select "Stories Archive" in your export request. Only saved stories will be included.

6. **Business Accounts**: Business account exports may include additional analytics data (not processed by Memoria).

## Related Documentation

- [Getting Started](Getting-Started) - Initial setup guide
- [Usage](Usage) - Detailed usage options
- [Immich Upload](Immich-Upload) - Immich upload configuration
