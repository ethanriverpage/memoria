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
- Uses parallel threads (configurable with `--threads`)
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
3. **Rate limiting**: Discord may rate-limit rapid downloads; reduce `--threads` count
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

- Use `--threads N` to parallelize downloads (default: auto-calculated)
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

---

## Technical Reference: Export Schema

This section describes the Discord data package schema relevant to media extraction.

### Export Source

Discord data is obtained via the "Request My Data" feature in User Settings > Privacy & Safety. The export is delivered as a ZIP file containing JSON files organized by category.

**Note:** Discord exports only include messages sent by the requesting user. Received messages from other users are NOT included.

### Full Export Structure

```text
discord-username-YYYYMMDD/
├── README.txt                    # Export overview and folder descriptions
├── Account/
│   ├── avatar.png                # User's avatar image
│   ├── user.json                 # Account information (large file)
│   └── applications/             # Bot/application configurations
├── Activities/                   # Discord activities data
├── Activity/
│   ├── reporting/
│   │   └── events-YYYY-00000-of-00001.json  # Event logs
│   └── tns/
│       └── events-YYYY-00000-of-00001.json  # Trust & Safety events
├── Ads/
│   ├── quests_user_status.json
│   └── traits.json               # Ad targeting traits
├── Messages/
│   ├── index.json                # Channel ID to name mapping
│   └── c{channel_id}/            # One folder per channel
│       ├── channel.json          # Channel metadata
│       └── messages.json         # Array of message objects
├── Servers/
│   ├── index.json                # Server ID to name mapping
│   └── {server_id}/
│       ├── audit-log.json
│       └── guild.json            # Server metadata
└── Support_Tickets/              # Zendesk support tickets (if any)
```

### Schema: messages.json

Each `messages.json` file contains an array of message objects sent by the exporting user.

#### Message Object Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ID` | integer | Discord snowflake ID (unique message identifier) | `897870239797215243` |
| `Timestamp` | string | UTC timestamp in `YYYY-MM-DD HH:MM:SS` format | `"2021-10-13 15:35:46"` |
| `Contents` | string | Text content of the message | `"Check this out!"` |
| `Attachments` | string | Space-separated CDN URLs for attachments | `"https://cdn.discordapp.com/..."` |

#### Example Message Objects

**Text-Only Message:**

```json
{
  "ID": 897870239797215243,
  "Timestamp": "2021-10-13 15:35:46",
  "Contents": "my only 10/10 i've watched so far",
  "Attachments": ""
}
```

**Message with Attachment:**

```json
{
  "ID": 1419422247004934226,
  "Timestamp": "2025-09-21 20:37:15",
  "Contents": "",
  "Attachments": "https://cdn.discordapp.com/attachments/458102099301892108/1419422246845288670/File.jpg?ex=0&is=6931a2d3&hm=..."
}
```

**Multiple Attachments:** Separated by a single space character.

### Schema: channel.json

Each channel folder contains a `channel.json` file describing the channel type and context.

#### Channel Types

| Type | Description | Example Context |
|------|-------------|-----------------|
| `DM` | Direct message (1:1) | Two recipient user IDs |
| `GROUP_DM` | Group direct message | Multiple recipient user IDs |
| `GUILD_TEXT` | Server text channel | Server (guild) ID and name |
| `PUBLIC_THREAD` | Public thread in a channel | Thread name and parent server |
| `PRIVATE_THREAD` | Private thread in a channel | Thread name and parent server |

#### DM Channel Example

```json
{
  "id": "502291754930929684",
  "type": "DM",
  "recipients": ["247424274304991233", "192775384276664320"]
}
```

#### Guild (Server) Text Channel Example

```json
{
  "id": "184324186284621824",
  "type": "GUILD_TEXT",
  "name": "offtopic",
  "guild": {
    "id": "184315303323238400",
    "name": "homelab."
  }
}
```

### Schema: index.json (Messages)

Maps channel IDs to human-readable names:

```json
{
  "386213310258741249": "Unknown channel in Clone Hero",
  "502291754930929684": "Direct Message with username#0",
  "184324186284621824": "offtopic in homelab.",
  "623392507828371479": "general in Tdarr"
}
```

### Attachment URL Structure

Discord attachment URLs follow this pattern:

```text
https://cdn.discordapp.com/attachments/{channel_id}/{attachment_id}/{filename}?ex={expiry}&is={issued}&hm={hash}
```

The query parameters (`ex`, `is`, `hm`) are for authentication and may expire.

### Discord Snowflake IDs

Discord uses snowflake IDs which encode creation timestamps:

```python
def snowflake_to_timestamp(snowflake: int) -> datetime:
    """Convert Discord snowflake ID to datetime."""
    DISCORD_EPOCH = 1420070400000  # 2015-01-01 00:00:00 UTC in ms
    timestamp_ms = (snowflake >> 22) + DISCORD_EPOCH
    return datetime.utcfromtimestamp(timestamp_ms / 1000)
```

### Platform-Specific Notes

- **Sent Messages Only**: Export includes ONLY messages sent by the requesting user
- **No Reactions Data**: Message reactions are not included
- **No Edit History**: Only the final version of edited messages
- **Attachment Expiry**: CDN URLs may expire; download promptly
- **Multiple Attachments**: Separated by single space in the `Attachments` field
- **Unknown Channels**: Some channels show as "Unknown channel" if server was left
- **Spoiler Media**: Filename prefixed with `SPOILER_` for spoilered attachments
