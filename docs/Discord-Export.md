# Discord Export Guide

This guide covers processing Discord data exports, including downloading attachments and embedding metadata.

## Table of Contents

1. [Overview](#overview)
2. [Downloading Your Discord Data](#downloading-your-discord-data)
3. [Export Structure](#export-structure)
4. [Processing Your Export](#processing-your-export)
5. [Embedded Metadata](#embedded-metadata)
6. [Attachment Downloads](#attachment-downloads)
7. [Troubleshooting](#troubleshooting)

## Overview

Memoria can process Discord data exports obtained via Discord's "Request My Data" feature. The processor:

- Downloads media attachments from Discord CDN URLs
- Extracts message context (channel, server, timestamp)
- Embeds metadata directly into files
- Organizes by channel and conversation type

---

## Important Notes

> **Sent Messages Only:**
>
> Discord exports only include messages **sent by you**. Received messages from other users are NOT included in the export. This is a limitation of Discord's export system, not Memoria.

> **Attachment URLs May Expire:**
>
> Discord CDN URLs may expire over time. Download your export and process it promptly after receiving it. If URLs have expired, those attachments cannot be downloaded.

> **Preprocessing Required:**
>
> Discord exports require a preprocessing step to download attachments from CDN URLs. This happens automatically during processing, but may take time depending on the number of attachments and your internet connection speed.

---

## Downloading Your Discord Data

### Step 1: Request Your Export

1. Open Discord (app or web)
2. Go to **User Settings** (gear icon)
3. Navigate to **Privacy & Safety**
4. Scroll to **Data & Privacy**
5. Click **Request My Data**
6. Select what to include:
   - **Messages** (required for media extraction)
   - **Servers** (optional, for server context)
7. Click **Submit Request**

### Step 2: Wait for Export

- Discord will email you when your data package is ready
- This can take several hours or days depending on account size
- You'll receive a download link in the email

### Step 3: Download and Extract

1. Download the ZIP file from the email link
2. Extract the archive to a directory
3. Rename following Memoria's naming convention:

```
discord-username-YYYYMMDD/
```

Example:

```
discord-johndoe-20251215/
```

**Note**: Use your Discord username (not display name) and the date you downloaded the export.

---

## Export Structure

### Required Structure

```
discord-username-YYYYMMDD/
├── Messages/
│   ├── index.json              # Channel ID to name mapping
│   └── c{channel_id}/          # One folder per channel
│       ├── channel.json        # Channel metadata
│       └── messages.json       # Array of message objects
├── Servers/                    # Optional
│   ├── index.json              # Server ID to name mapping
│   └── {server_id}/
│       └── guild.json          # Server metadata
└── README.txt                  # Export overview
```

### Key Files

- **`Messages/index.json`**: Maps channel IDs to human-readable names
- **`Messages/c{channel_id}/messages.json`**: Contains your sent messages with attachment URLs
- **`Messages/c{channel_id}/channel.json`**: Channel type and context (DM, group DM, or server channel)

### Channel Types

Discord exports include three types of channels:

1. **Direct Messages (DM)**: 1:1 conversations
2. **Group DMs**: Multi-person direct message groups
3. **Server Channels**: Text channels in Discord servers

---

## Processing Your Export

### Single Export

```bash
./memoria.py /path/to/discord-username-20251215 -o /path/to/output
```

### Output Structure

```
/path/to/output/
└── messages/
    ├── discord-johndoe-general-20231015.jpg
    ├── discord-johndoe-general-20231015_2.mp4
    ├── discord-johndoe-john_doe-20231020.png
    └── ...
```

### Filename Format

Files are renamed with descriptive, sortable names:

```
discord-{username}-{channel}-{YYYYMMDD}[-{seq}].{ext}
```

Examples:

- `discord-johndoe-general-20231015.jpg` (server channel)
- `discord-johndoe-john_doe-20231020.png` (DM with contact name)
- `discord-johndoe-group-20231022.mp4` (group DM)
- `discord-johndoe-general-20231015_2.jpg` (sequence number for same-day duplicates)

---

## Embedded Metadata

Memoria embeds comprehensive metadata into all processed Discord files.

### For Images

| Tag | Content |
|-----|---------|
| `DateTimeOriginal` | Message timestamp |
| `CreateDate` | Message timestamp |
| `ModifyDate` | Message timestamp |
| `ImageDescription` | Source information with channel context |
| `IPTC:Caption-Abstract` | Same as ImageDescription |

### For Videos

| Tag | Content |
|-----|---------|
| `creation_time` | Message timestamp |
| `Comment` | Source information with channel context |
| `Description` | Same as Comment |

### Description Format

**Server Channel:**

```
Source: Discord/johndoe
  - johndoe in "general in ServerName": "Check this out!"
```

**Direct Message:**

```
Source: Discord/johndoe
  - johndoe in "Direct Message with friend#1234": "Here's the photo"
```

**Group DM:**

```
Source: Discord/johndoe
  - johndoe in "Group DM": "Shared with the group"
```

---

## Attachment Downloads

### How It Works

During preprocessing, Memoria:

1. **Parses messages.json** files to extract attachment URLs
2. **Downloads attachments** from Discord CDN in parallel
3. **Saves to media/** directory with unique filenames
4. **Creates metadata.json** linking files to messages

### Download Process

- Downloads happen automatically during preprocessing
- Uses parallel workers (configurable with `--workers`)
- Retries failed downloads up to 3 times
- Skips non-media files (PDFs, archives, etc.)

### Supported Media Types

- **Images**: `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`
- **Videos**: `.mp4`, `.webm`, `.mov`
- **Audio**: `.mp3`, `.wav`, `.ogg`, `.flac`

### Download Statistics

After preprocessing, you'll see statistics:

```
PREPROCESSING STATISTICS
======================================================================
Total channels processed:              12
Total messages scanned:             1234
Messages with attachments:            456
Total attachments found:             789
Downloads successful:                745
Downloads failed:                     44
Downloads skipped (non-media):        12
Banned files skipped:                  0
======================================================================
```

### Failed Downloads

Failed downloads are logged in `preprocessing.log` and tracked in the failure tracker. Common reasons:

- **URL expired**: Discord CDN URLs may expire over time
- **404 Not Found**: Attachment was deleted from Discord
- **403 Forbidden**: URL authentication failed
- **Network timeout**: Connection issues during download

---

## Troubleshooting

### "No processors matched input directory"

Verify your export structure:

- `Messages/` directory must exist
- `Messages/index.json` must be present
- At least one `Messages/c{channel_id}/` folder with `messages.json` must exist

### "No media files found to process"

Possible causes:

- All attachment URLs expired (download export promptly)
- No messages with attachments in your export
- All attachments were non-media files (skipped)

Check the preprocessing log for details.

### Downloads failing

If many downloads fail:

1. **Check internet connection**: Downloads require active internet
2. **URLs may be expired**: Discord CDN URLs can expire; process export soon after receiving it
3. **Rate limiting**: Discord may rate-limit rapid downloads; reduce `--workers` count
4. **Check preprocessing.log**: Detailed error messages for each failed download

### "Failed to download: File not found (404)"

This means the Discord CDN URL has expired or the attachment was deleted. These cannot be recovered. Process your export promptly after receiving it to minimize expired URLs.

### Channel names showing as "Unknown channel"

If a server was left or channel was deleted, Discord may not include full metadata. The channel will appear as "Unknown channel" or "Unknown channel in {server_name}".

### Processing is slow

Discord preprocessing involves:

- Downloading attachments from the internet (I/O bound)
- Parsing JSON files
- Creating metadata structure

To speed up:

- Use `--workers N` to parallelize downloads (default: CPU count - 1)
- Ensure stable internet connection
- Process on a machine with good network bandwidth

### Banned files skipped

Memoria automatically skips files matching banned patterns (system files, thumbnails, etc.). This is normal and expected.

---

## Immich Albums

When uploading to Immich, Discord content is organized as:

- **Album**: `Discord/{username}`

Example:

- `Discord/johndoe`

See [Immich Upload](Immich-Upload) for configuration options.

---

## Related Documentation

- [Getting Started](Getting-Started) - Initial setup guide
- [Usage](Usage) - Detailed usage options
- [Immich Upload](Immich-Upload) - Immich upload configuration
- [Discord Schema](Discord-Schema) - Technical export structure and format details

