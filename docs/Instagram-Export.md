# Instagram Export Guide

This guide covers processing Instagram exports including messages, posts, and legacy formats.

## Table of Contents

1. [Overview](#overview)
2. [Downloading Your Instagram Data](#downloading-your-instagram-data)
3. [Instagram Messages](#instagram-messages)
4. [Instagram Public Media (New Format)](#instagram-public-media-new-format)
5. [Instaloader (Scrape Data)](#instaloader-scrape-data)
6. [Processing Your Export](#processing-your-export)

## Overview

Instagram provides data export through their settings. Memoria can process:

- **Instagram Messages**: Direct messages and group chat media
- **Instagram Public Media**: Posts and archived posts (new JSON-based format)
- **Instaloader**: Scrape data from the Instaloader tool (not official Instagram export)

Instagram has changed their export format over time, so you may encounter different structures depending on when you requested your export.

**Note:** Memoria also supports data scraped using [Instaloader](https://instaloader.github.io/), a third-party tool. This is distinct from Instagram's official data export.

### Supported Export Formats

Memoria supports both legacy and current Instagram export structures:

| Export Era | Metadata Location | Messages Location |
|------------|-------------------|-------------------|
| 2023+ (Current) | `your_instagram_activity/media/` | `your_instagram_activity/messages/inbox/` |
| Pre-2023 (Legacy) | `content/` | `messages/inbox/` |

The processor automatically detects which format your export uses and handles it appropriately. No manual configuration is needed.

For detailed technical comparison, see [Technical Reference: Export Format Comparison](#technical-reference-export-format-comparison) at the end of this document.

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

- Messages inbox directory (either location):
  - Current format: `your_instagram_activity/messages/inbox/`
  - Legacy format: `messages/inbox/`
- Conversation folders
- Message files in `.html` format (message_1.html, message_2.html, etc.)
- Media files within conversation folders (typically in `photos/` subdirectories)

### Output Organization

Processed messages are placed in a flat structure with conversation names embedded in filenames:

```
output/messages/
├── insta-messages-username-conversation1-20230115.jpg
├── insta-messages-username-conversation1-20230115_2.mp4
├── insta-messages-username-conversation2-20230220.jpg
└── ...
```

Files are named: `insta-messages-{username}-{conversation}-{YYYYMMDD}.{ext}` (with `_N` suffix for duplicates)

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

## Instaloader (Scrape Data)

**Important**: This section describes data scraped using [Instaloader](https://instaloader.github.io/), a third-party tool. This is NOT an official Instagram export format.

Instaloader is a command-line tool that downloads public (or accessible) Instagram profiles, posts, and stories. If you've used Instaloader to archive content, Memoria can process this data.

### Export Structure

```
instaloader-username/
├── 2023-01-15_10-30-45_UTC.jpg
├── 2023-01-15_10-30-45_UTC.txt
├── 2023-01-15_10-31-22_UTC_1.jpg
├── 2023-01-15_10-31-22_UTC_1.txt
├── 2023-02-20_14-22-11_UTC.mp4
├── 2023-02-20_14-22-11_UTC.txt
└── ...
```

Or, in some scrapes, JSON metadata may be compressed:

```
instaloader-username/
├── 2023-01-15_10-30-45_UTC.jpg
├── 2023-01-15_10-30-45_UTC.json.xz    # Compressed JSON metadata (automatically handled)
├── 2023-01-15_10-30-45_UTC.txt
└── ...
```

Both `.json` and `.json.xz` files are automatically processed during preprocessing.

### What Gets Processed

- Photos and videos with UTC timestamps in filename
- Metadata from paired `.txt` files including:
  - Post caption
  - Post date
  - Location
- Metadata from paired `.json` or `.json.xz` files (if available) including:
  - More detailed caption information
  - Precise timestamp
  - Media type

### Filename Pattern

Instaloader uses the format: `YYYY-MM-DD_HH-MM-SS_UTC[_N].{jpg|mp4|...}`

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

Each media file may have one or both metadata files:

**Text caption file:**

```
2023-01-15_10-30-45_UTC.txt
```

**JSON metadata file (if available):**

```
2023-01-15_10-30-45_UTC.json
```

Note: Some Instaloader versions create `.json.xz` (compressed) which must be extracted first (see above).

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

**Compressed JSON files**: If you see `.json.xz` files, you must extract them first using `xz -d *.json.xz` (Linux/macOS) or 7-Zip (Windows). The processor currently expects uncompressed `.json` files.

**No JSON files**: Some Instaloader configurations may use only `.txt` files for captions - this is expected.

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
│   ├── insta-messages-username-conv1-20230115.jpg
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

Note: Instaloader posts are combined with new format posts in the `Instagram/{username}/posts` album.

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

---

## Technical Reference: Messages Schema

This section describes the message data schema for Instagram Messages exports.

### Export Format Versions

Instagram has changed their export format over time. Memoria supports both versions:

| Version | Era | Messages Path | Key Differences |
|---------|-----|---------------|-----------------|
| Current | 2023+ | `your_instagram_activity/messages/inbox/` | Uses `<h2>` for sender, no comma in timestamp |
| Legacy | Pre-2023 | `messages/inbox/` | Uses `<div>` for sender, comma after year in timestamp |

Both formats use the same CSS classes and overall HTML structure. Memoria automatically detects which format is present.

### Messages Location

**Current Format (2023+):**

```text
instagram-username-YYYYMMDD/
└── your_instagram_activity/
    └── messages/
        ├── ai_conversations/
        ├── chats.html
        ├── secret_conversations.html
        ├── inbox/
        │   ├── username1_conversation_id/
        │   │   ├── message_1.html
        │   │   ├── photos/
        │   │   └── videos/
        │   └── ...
        └── message_requests/
```

**Legacy Format (Pre-2023):**

```text
instagram-username-YYYYMMDD/
├── messages/
│   ├── chats.html
│   ├── inbox/
│   │   └── ...
│   └── message_requests/
└── content/
```

### HTML Structure

Instagram messages are stored as **HTML files** (not JSON). Each `message_N.html` file contains one or more messages.

**Current Format (2023+) - Uses `<h2>` for sender:**

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <h2 class="_3-95 _2pim _a6-h _a6-i">Sender Name</h2>
  <div class="_3-95 _a6-p">
    <div>Message content...</div>
  </div>
  <div class="_3-94 _a6-o">Sep 22, 2017 6:33 am</div>
</div>
```

**Legacy Format (Pre-2023) - Uses `<div>` for sender:**

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <div class="_3-95 _2pim _a6-h _a6-i">Sender Name</div>
  <div class="_3-95 _a6-p">
    <div>Message content...</div>
  </div>
  <div class="_3-94 _a6-o">Sep 22, 2017, 6:33 AM</div>
</div>
```

### Extracted Message Fields

| Field | Extraction Method | Example |
|-------|------------------|---------|
| Conversation Title | `<title>` tag | `"John Doe"` or `"Family Group"` |
| Sender | `<h2>` or `<div>` with class `_3-95 _2pim _a6-h _a6-i` | `"your_username"` |
| Timestamp | `<div>` with class `_3-94 _a6-o` | `"Sep 22, 2017 6:33 am"` |
| Message Text | `<div>` within `_3-95 _a6-p` container | `"Check this out!"` |
| Photo Attachments | `<a>` and `<img>` tags with `href`/`src` | `photos/123456789.jpg` |
| Video Attachments | `<video>` tag with `src` attribute | `videos/123456789.mp4` |

### Timestamp Formats

| Version | Format | Example |
|---------|--------|---------|
| Current (2023+) | `Mon DD, YYYY H:MM am/pm` | `Sep 22, 2017 6:33 am` |
| Legacy (Pre-2023) | `Mon DD, YYYY, H:MM AM/PM` | `Sep 22, 2017, 6:33 AM` |

### Conversation Types

1. **Direct Messages (DM)**: Folder name `{username}_{conversation_id}/`
2. **Group Chats**: Folder name `{participant1}{participant2}and{participant3}_{conversation_id}/`
3. **AI Conversations**: In `ai_conversations/` folder (current format only)
4. **Deleted Users**: Folder name `instagramuser_{numeric_id}/`

### Platform-Specific Notes

- **HTML Parsing Required**: No JSON format; must parse HTML structure
- **CSS Class Dependencies**: Extraction relies on Instagram's CSS class names
- **Message Requests**: Separate `message_requests/` folder for non-followers
- **Missing Media**: Export excludes ephemeral content (View Once, expired messages)
- **Multiple HTML Files**: Large conversations may split across `message_1.html`, `message_2.html`, etc.

---

## Technical Reference: Export Format Comparison

Instagram changed their data export format between 2022 and 2025. Key structural changes:

| Aspect | 2022 Export | 2025 Export |
|--------|-------------|-------------|
| Entry Point | `index.html` | `start_here.html` |
| Metadata Location | `content/` | `your_instagram_activity/media/` |
| Messages Location | `messages/` | `your_instagram_activity/messages/` |
| Media Location | `media/` | `media/` + `public-media/` |

### Directory Reorganization

| Category | 2022 Location | 2025 Location |
|----------|---------------|---------------|
| Ads data | `ads_and_businesses/` | `ads_information/` |
| Contacts | `contacts/` | `connections/contacts/` |
| Followers | `followers_and_following/` | `connections/followers_and_following/` |
| Comments | `comments/` | `your_instagram_activity/comments/` |
| Likes | `likes/` | `your_instagram_activity/likes/` |

### New Content Types (2025)

- `your_instagram_activity/ai/` - AI-related activity
- `your_instagram_activity/threads/` - Threads app integration
- `media/reels/` - Reels content (separate from IGTV)
- `public-media/` - Organized public media directory

### HTML Metadata Format

Both formats use the same HTML structure for posts metadata with identical CSS classes:

| Class | Purpose |
|-------|---------|
| `pam _3-95 _2ph- _a6-g uiBoxWhite noborder` | Post container |
| `_3-95 _2pim _a6-h _a6-i` | Caption/title |
| `_3-95 _a6-p` | Media content wrapper |
| `_3-94 _a6-o` | Timestamp |

### Implications for Processing

Memoria automatically detects which format is present and handles both:
- Detection passes if media structure exists (`media/posts/` or `media/archived_posts/`)
- Preprocessing looks for metadata in both `your_instagram_activity/media/` and `content/`
