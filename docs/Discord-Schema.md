# Discord Schema

This document describes the Discord data package schema relevant to media extraction for Memoria.

## Export Source

Discord data is obtained via the "Request My Data" feature in User Settings > Privacy & Safety. The export is delivered as a ZIP file containing JSON files organized by category.

**Note:** Discord exports only include messages sent by the requesting user. Received messages from other users are NOT included.

---

## Location in Export

```text
discord-username-YYYYMMDD/
├── README.txt                    # Export overview and folder descriptions
├── Account/
│   ├── avatar.png                # User's avatar image
│   ├── user.json                 # Account information (large file, see note)
│   └── applications/             # Bot/application configurations
│       └── {app_id}/
│           └── application.json
├── Activities/
│   ├── Activities_1/
│   │   ├── poker/
│   │   │   └── poker.json       # Poker activity data
│   │   └── users/
│   │       └── user.json        # Activity user profile
│   └── Activities_2/
│       ├── message.txt
│       └── user_data.json       # Activity-specific user data
├── Activity/
│   ├── reporting/
│   │   └── events-YYYY-00000-of-00001.json  # Event logs (very large)
│   └── tns/
│       └── events-YYYY-00000-of-00001.json  # Trust & Safety events
├── Ads/
│   ├── quests_user_status.json  # Quest/promotion status
│   └── traits.json              # Ad targeting traits
├── Messages/
│   ├── index.json               # Channel ID to name mapping
│   └── c{channel_id}/           # One folder per channel
│       ├── channel.json         # Channel metadata
│       └── messages.json        # Array of message objects
├── Servers/
│   ├── index.json               # Server ID to name mapping
│   └── {server_id}/
│       ├── audit-log.json       # Server audit log (often empty)
│       └── guild.json           # Server metadata
└── Support_Tickets/             # Zendesk support tickets (if any)
```

**Key Files for Media Extraction:**

- `Messages/c{channel_id}/messages.json` - Message content with attachment URLs
- `Messages/c{channel_id}/channel.json` - Channel type and context (server name, DM participants)
- `Messages/index.json` - Human-readable channel names
- `Servers/index.json` - Server ID to name mapping

---

## Schema: messages.json

Each `messages.json` file contains an array of message objects sent by the exporting user.

### Message Object Schema

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `ID` | integer | Discord snowflake ID (unique message identifier) | `897870239797215243` |
| `Timestamp` | string | UTC timestamp in `YYYY-MM-DD HH:MM:SS` format | `"2021-10-13 15:35:46"` |
| `Contents` | string | Text content of the message | `"Check this out!"` |
| `Attachments` | string | Space-separated CDN URLs for attachments | `"https://cdn.discordapp.com/..."` |

### Example Message Objects

**Text-Only Message:**

```json
{
  "ID": 897870239797215243,
  "Timestamp": "2021-10-13 15:35:46",
  "Contents": "my only 10/10 i've watched so far",
  "Attachments": ""
}
```

**Message with Single Attachment:**

```json
{
  "ID": 1419422247004934226,
  "Timestamp": "2025-09-21 20:37:15",
  "Contents": "",
  "Attachments": "https://cdn.discordapp.com/attachments/458102099301892108/1419422246845288670/File-2016-06-075.jpg?ex=0&is=6931a2d3&hm=..."
}
```

**Message with Text and Attachment:**

```json
{
  "ID": 1183589260452962364,
  "Timestamp": "2023-12-11 02:01:09",
  "Contents": "4",
  "Attachments": "https://cdn.discordapp.com/attachments/458102099301892108/1183589260192907374/fit_2048.png?ex=0&is=6931a0fe&hm=..."
}
```

**Message with Multiple Attachments:**

```json
{
  "ID": 934944769845637140,
  "Timestamp": "2022-01-23 22:56:43",
  "Contents": "",
  "Attachments": "https://cdn.discordapp.com/.../IMG_5608.jpg?... https://cdn.discordapp.com/.../IMG_5607.jpg?... https://cdn.discordapp.com/.../IMG_5606.jpg?..."
}
```

**Note:** Multiple attachments are separated by a single space character.

---

## Schema: channel.json

Each channel folder contains a `channel.json` file describing the channel type and context.

### Channel Types

| Type | Description | Example Context |
|------|-------------|-----------------|
| `DM` | Direct message (1:1) | Two recipient user IDs |
| `GROUP_DM` | Group direct message | Multiple recipient user IDs |
| `GUILD_TEXT` | Server text channel | Server (guild) ID and name |
| `PUBLIC_THREAD` | Public thread in a channel | Thread name and parent server |
| `PRIVATE_THREAD` | Private thread in a channel | Thread name and parent server |

### DM Channel

```json
{
  "id": "502291754930929684",
  "type": "DM",
  "recipients": ["247424274304991233", "192775384276664320"]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Channel ID |
| `type` | string | Always `"DM"` |
| `recipients` | array | Array of two user ID strings |

### Group DM Channel

```json
{
  "id": "446865271341187072",
  "type": "GROUP_DM"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Channel ID |
| `type` | string | Always `"GROUP_DM"` |

**Note:** Group DMs may not include recipient lists.

### Guild (Server) Text Channel

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

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Channel ID |
| `type` | string | `"GUILD_TEXT"` for text channels |
| `name` | string | Channel name (without `#` prefix) |
| `guild` | object | Parent server information |
| `guild.id` | string | Server ID |
| `guild.name` | string | Server name |

### Public Thread

```json
{
  "id": "1025026715212329000",
  "type": "PUBLIC_THREAD",
  "name": "Game errors & dupes.",
  "guild": {
    "id": "488621078302949377",
    "name": "SteamGridDB"
  }
}
```

### Minimal Guild Channel (Deleted/Inaccessible)

Some channels may have minimal metadata if the server was left or channel was deleted:

```json
{
  "id": "1092551226622750737",
  "type": "GUILD_TEXT"
}
```

---

## Schema: index.json (Messages)

The `Messages/index.json` file maps channel IDs to human-readable names.

```json
{
  "386213310258741249": "Unknown channel in Clone Hero",
  "502291754930929684": "Direct Message with username#0",
  "184324186284621824": "offtopic in homelab.",
  "623392507828371479": "general in Tdarr"
}
```

| Key | Value Format |
|-----|--------------|
| Channel ID | `"{channel_name} in {server_name}"` for guild channels |
| Channel ID | `"Direct Message with {username}#{discriminator}"` for DMs |
| Channel ID | `"Unknown channel"` or `"Unknown channel in {server_name}"` for inaccessible channels |
| Channel ID | `"None"` for some system channels |

---

## Schema: Servers/index.json

Maps server IDs to server names.

```json
{
  "184315303323238400": "homelab.",
  "372530698168303620": "Gooncord",
  "618712310185197588": "WinAdmins"
}
```

---

## Schema: Servers/{server_id}/guild.json

Contains server metadata.

```json
{
  "id": "122900397965705216",
  "name": "Pterodactyl"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Server ID |
| `name` | string | Server name |

---

## Schema: Servers/{server_id}/audit-log.json

Contains server audit log entries. Often an empty array for non-admin users.

```json
[]
```

---

## Attachment URL Structure

Discord attachment URLs follow this pattern:

```text
https://cdn.discordapp.com/attachments/{channel_id}/{attachment_id}/{filename}?ex={expiry}&is={issued}&hm={hash}&uc={upload_context}
```

| Component | Description | Example |
|-----------|-------------|---------|
| `channel_id` | Channel where attachment was uploaded | `458102099301892108` |
| `attachment_id` | Unique attachment ID | `1419422246845288670` |
| `filename` | Original filename | `File-2016-06-075.jpg` |
| `ex` | Expiry parameter | `0` |
| `is` | Issued timestamp | `6931a2d3` |
| `hm` | Hash/signature | `9d7f718b...` |
| `uc` | Upload context | `dp` |

**Note:** The query parameters (`ex`, `is`, `hm`, `uc`) are for authentication. URLs may expire and require refreshing for download.

### Common File Types

| Extension | Description |
|-----------|-------------|
| `.jpg`, `.jpeg` | JPEG images |
| `.png` | PNG images (may include `SPOILER_` prefix) |
| `.gif` | GIF images/animations |
| `.webp` | WebP images |
| `.mp4` | MP4 videos |
| `.webm` | WebM videos |
| `.mov` | QuickTime videos |
| `.mp3` | MP3 audio |
| `.wav` | WAV audio |
| `.ogg` | OGG audio |
| `.ttf`, `.otf` | Font files |
| `.pdf` | PDF documents |
| `.zip`, `.rar` | Archives |

### Spoiler Attachments

Spoilered attachments have `SPOILER_` prefixed to the filename:

```text
https://cdn.discordapp.com/attachments/.../SPOILER_Fibromyalgia.mp3?...
```

---

## Timestamp Format

Discord exports use a standardized timestamp format:

- **Format:** `YYYY-MM-DD HH:MM:SS`
- **Timezone:** UTC
- **Example:** `"2021-10-13 15:35:46"`

Already in a standardized format suitable for direct parsing.

---

## Discord Snowflake IDs

Discord uses snowflake IDs which encode creation timestamps:

```text
Snowflake: 897870239797215243

Timestamp bits (bits 22+): 682345879797
Discord Epoch offset: 1420070400000 ms (2015-01-01)
Creation time: 2021-10-13 15:35:46 UTC
```

The snowflake can be used to derive the message creation timestamp if needed:

```python
def snowflake_to_timestamp(snowflake: int) -> datetime:
    """Convert Discord snowflake ID to datetime."""
    DISCORD_EPOCH = 1420070400000  # 2015-01-01 00:00:00 UTC in ms
    timestamp_ms = (snowflake >> 22) + DISCORD_EPOCH
    return datetime.utcfromtimestamp(timestamp_ms / 1000)
```

---

## Account Data

### Account/user.json

Large file containing comprehensive account data. May be too large to parse easily (60,000+ tokens).

### Ads/traits.json

Ad targeting data with user traits and activity:

```json
{
  "day_pt": "2025-12-02T00:00:00",
  "game_names_clean_l365": [],
  "game_names_clean_l90": [],
  "primary_platform_l30": "desktop",
  "reg_country_code": "US",
  "reg_region": "US/Canada",
  "user_id": "247424274304991233",
  "has_active_mobile_subscription": false,
  "game_ids_l365": ["1402418239342120960", ...],
  "has_active_subscription": false,
  "age_group": "18-24",
  "theme_ids_l90": ["2", "13", "1", ...]
}
```

---

## Activities Data

### Activities/Activities_1/users/user.json

Activity service user profile:

```json
{
  "id": "b81bd5c7-7e52-4f12-ab1e-ead0ebed60a6",
  "username": "username",
  "discord_id": "247424274304991233",
  "discriminator": "0001",
  "avatar": "0f4660af67a5df1952f7f915d0964d25",
  "premium_type": 2,
  "created_at": 1649890890
}
```

### Activities/Activities_1/poker/poker.json

Example activity-specific data (Discord Poker Night):

```json
{
  "global_stats": {
    "all_ins": 1,
    "biggest_pot": 150,
    "games_played": 1,
    "games_won": 1,
    "play_time": 56.54
  },
  "seasons": {...},
  "inventory": {...}
}
```

---

## Bot Applications

### Account/applications/{app_id}/application.json

Bot/application configuration for created Discord bots:

```json
{
  "id": "1041760652089364582",
  "name": "SmartBot-discord",
  "description": "",
  "is_monetized": false,
  "bot_public": true,
  "flags": ["GATEWAY_PRESENCE_LIMITED", ...],
  "bot": {
    "id": "1041760652089364582",
    "username": "SmartBot-",
    "discriminator": "7171",
    "bot": true
  }
}
```

---

## Special Cases

### Empty Messages

Some messages have empty `Contents` and empty `Attachments`:

```json
{
  "ID": 662784231751286786,
  "Timestamp": "2020-01-03 22:27:54",
  "Contents": "",
  "Attachments": ""
}
```

These may represent:
- Deleted messages (content removed)
- Embed-only messages (links that generated previews)
- System messages
- Messages with only reactions

### Markdown in Contents

Message contents preserve Discord markdown:

```json
{
  "Contents": "> -# *Reacted to username's activity*\n> Playing Counter-Strike 2\n"
}
```

Markdown elements include:
- `>` - Block quotes
- `-#` - Subheader formatting
- `*text*` - Italics
- `**text**` - Bold
- `||text||` - Spoilers
- `\n` - Newlines

### URL-Only Messages

Many messages contain URLs as content (shared links):

```json
{
  "Contents": "https://www.youtube.com/watch?v=vWYv8x0yEIs",
  "Attachments": ""
}
```

---

## Platform-Specific Notes

- **Sent Messages Only**: Export includes ONLY messages sent by the requesting user, not received messages
- **No Reactions Data**: Message reactions are not included in the export
- **No Edit History**: Only the final version of edited messages is included
- **Deleted Messages Excluded**: Manually deleted messages are not in the export
- **Attachment Expiry**: CDN URLs may expire; download promptly after export
- **Multiple Attachments**: Separated by single space in the `Attachments` field
- **Channel Context**: Use `channel.json` to determine if message was in DM, group DM, or server channel
- **Unknown Channels**: Some channels show as "Unknown channel" if server was left or channel deleted
- **Large Files**: `user.json` and `events-*.json` can be very large (100MB+)
- **Snowflake IDs**: Can be converted to timestamps for additional verification
- **Spoiler Media**: Filename prefixed with `SPOILER_` for spoilered attachments

---

## Related Documentation

- [Discord Help Article](https://support.discord.com/hc/articles/360004957991) - Official Discord data package documentation

