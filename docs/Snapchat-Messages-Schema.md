# Snapchat Messages Schema

This document describes the message data schema for Snapchat Messages as exported from the "My Data" export.

## Location in Export

```text
snapchat-username-YYYYMMDD/
└── messages/
    ├── json/
    │   ├── chat_history.json
    │   └── snap_history.json
    ├── chat_media/
    │   ├── 2019-02-12_b~EiQSFTM0SEdmSTFNRzBDdkhlelFQS1hLUhoAGgAyAXxIAlAEYAE.jpg
    │   ├── 2019-02-13_media~zip-38FDD84B-5891-4975-84BD-08763E785D32.mp4
    │   ├── 2019-02-13_overlay~zip-31FBA148-B00D-4E1D-A802-25249ADDF1A8.png
    │   ├── 2019-02-18_thumbnail~zip-C07EF7F5-9005-43FE-83C2-9CA9F173CA3E.jpg
    │   └── ...
    ├── html/
    │   ├── chat_history.html
    │   └── snap_history.html
    └── index.html
```

**Key Files:**

- `messages/json/chat_history.json` - Contains all chat messages with metadata
- `messages/json/snap_history.json` - Contains snap-specific messages
- `messages/chat_media/` - Directory containing all media files (photos, videos, overlays, thumbnails)
- `messages/html/` - HTML versions of message history (for viewing in browser)

---

## Schema: chat_history.json

The `chat_history.json` file is structured as a dictionary where **keys are conversation IDs** and **values are arrays of message objects**.

**Top-Level Structure:**

```json
{
  "conversation_id_1": [ ...messages... ],
  "conversation_id_2": [ ...messages... ],
  "conversation_id_3": [ ...messages... ]
}
```

---

## Message Object Schema

Each message in the conversation arrays has the following structure:

### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `From` | string | Username of the message sender | `"friend_username"` |
| `Media Type` | string | Type of message content | `"TEXT"`, `"PHOTO"`, `"VIDEO"`, `"MEDIA"` |
| `Created` | string | Timestamp when message was sent (UTC) | `"2024-04-20 01:29:22 UTC"` |
| `Conversation Title` | string or null | Name of the conversation (null for DMs) | `"Group Chat Name"` or `null` |
| `IsSender` | boolean | Whether the export user sent this message | `true` or `false` |
| `Created(microseconds)` | integer | Timestamp in microseconds since epoch | `1713576562686` |
| `IsSaved` | boolean | Whether the message was saved | `true` or `false` |

### Optional Fields

| Field | Type | Description | Example | Notes |
|-------|------|-------------|---------|-------|
| `Content` | string or null | Text content of the message | `"was he good?"` | May be empty string for media-only messages; rarely `null` for certain system messages |
| `Media IDs` | string | References to media files (can be multiple, pipe-separated) | `"b~EiQSFTM0SEdmSTFNRzBDdkhlelFQS1hLUhoAGgAyAXxIAlAEYAE"` or `"b~abc... \| b~def..."` | Empty string if no media; base64-encoded IDs; multiple IDs separated by ` \| ` (space-pipe-space) |

### Example Message Objects

**Text Message:**

```json
{
  "From": "friend_username",
  "Media Type": "TEXT",
  "Created": "2024-04-20 01:29:22 UTC",
  "Content": "was he good?",
  "Conversation Title": "Summer Staff Group",
  "IsSender": false,
  "Created(microseconds)": 1713576562686,
  "IsSaved": true,
  "Media IDs": ""
}
```

**System Status Message:**

```json
{
  "From": "another_friend",
  "Media Type": "STATUSERASEDMESSAGE",
  "Created": "2019-03-29 14:56:03 UTC",
  "Content": "",
  "Conversation Title": "Group Chat Name",
  "IsSender": false,
  "Created(microseconds)": 1553871363433,
  "IsSaved": true,
  "Media IDs": ""
}
```

---

## Schema: snap_history.json

The `snap_history.json` file has the same top-level structure as `chat_history.json` (conversation IDs as keys, arrays of messages as values), but the message objects have a **simpler schema**:

### Fields in snap_history.json Messages

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `From` | string | Username of the message sender | `"friend_username"` |
| `Media Type` | string | Type of snap content | `"IMAGE"`, `"VIDEO"` |
| `Created` | string | Timestamp when snap was sent (UTC) | `"2024-06-13 16:42:26 UTC"` |
| `Conversation Title` | string | Name of the conversation | `"Group Chat Name"` or `null` |
| `IsSender` | boolean | Whether the export user sent this snap | `true` or `false` |
| `Created(microseconds)` | integer | Timestamp in microseconds since epoch | `1718296946482` |

**Important differences from chat_history.json:**

- **No `Media IDs` field** - snap history does not include media ID references
- **No `Content` field** - snaps don't have text content
- **No `IsSaved` field** - not tracked for snaps
- **Different `Media Type` values** - uses `"IMAGE"` and `"VIDEO"` instead of `"PHOTO"`, `"VIDEO"`, and `"MEDIA"`

### Example snap_history.json Message Object

```json
{
  "From": "friend_username",
  "Media Type": "IMAGE",
  "Created": "2024-06-13 16:42:26 UTC",
  "Conversation Title": "Group Chat Name",
  "IsSender": false,
  "Created(microseconds)": 1718296946482
}
```

---

## Media Type Values

### chat_history.json

The `Media Type` field in chat_history.json can have the following values:

- `"TEXT"` - Text-only message
- `"MEDIA"` - Generic media attachment (most common for photos and videos)
- `"STICKER"` - Sticker message
- `"SHARE"` - Shared content (e.g., links, other snaps)
- `"NOTE"` - Note message (rare)
- `"LOCATION"` - Location sharing message (rare)
- `"STATUSERASEDMESSAGE"` - System message indicating a message was erased
- `"STATUSPARTICIPANTREMOVED"` - System message indicating a participant was removed
- `"STATUSSAVETOCAMERAROLL"` - System message indicating save to camera roll
- `"PHOTO"` - Photo message (may be obsolete; not found in recent exports)
- `"VIDEO"` - Video message (may be obsolete; not found in recent exports)

**Note:** `PHOTO` and `VIDEO` media types are mentioned in some documentation but were not found in actual exports from 2018-2025. Most photos and videos use the generic `"MEDIA"` type instead.

### snap_history.json

The `Media Type` field in snap_history.json uses different values:

- `"IMAGE"` - Image snap
- `"VIDEO"` - Video snap

---

## Media ID Formats

Media files in the `chat_media/` directory use date-prefixed naming patterns. **All files** have a `YYYY-MM-DD_` prefix based on the file's timestamp:

1. **Media IDs (photos/videos)**: `2019-02-12_b~EiQSFTM0SEdmSTFNRzBDdkhlelFQS1hLUhoAGgAyAXxIAlAEYAE.jpg` (referenced in JSON as `b~EiQSFTM0SEdmSTFNRzBDdkhlelFQS1hLUhoAGgAyAXxIAlAEYAE`)
   - Common extensions: `.jpg`, `.mp4`, `.gif`, `.png`, `.webp`
   - Rare: `.unknown` (corrupted or unknown format)
2. **Media from zip (videos)**: `2019-02-13_media~zip-38FDD84B-5891-4975-84BD-08763E785D32.mp4`
   - Primarily `.mp4` files, occasionally `.jpg`
3. **Overlays from zip**: `2019-02-13_overlay~zip-31FBA148-B00D-4E1D-A802-25249ADDF1A8.png`
   - Common extensions: `.png`, `.webp`
4. **Thumbnails from zip**: `2019-02-18_thumbnail~zip-C07EF7F5-9005-43FE-83C2-9CA9F173CA3E.jpg`
   - Always `.jpg` format
5. **Metadata files**: `2016-03-04_metadata~zip-C34DC93D-0881-43E5-AD15-57A3EF600C86.unknown`
   - Always `.unknown` extension; found primarily in older exports (2016-2018)

**Note:** The preprocessor matches these automatically using timestamps and metadata references. Some older exports may have alternate naming patterns without the `~zip-` component (e.g., `overlay~<UUID>.png` or `media~<UUID>.mp4`).

### Multiple Media IDs

A single message can reference multiple media files. In this case, the `Media IDs` field contains multiple IDs separated by ` | ` (space-pipe-space). Messages can contain up to 9 or more media files:

```json
{
  "Media IDs": "b~EioSFWVueWoyb2lJbndHcEZieUMxd2V3UBoAGgAiBgipt6eSBjIBBFAEYAE | b~EioSFTdrdG5kVkMzREd1VDNnQzE4bDJ2eRoAGgAiBgipt6eSBjIBBFAEYAE | b~EioSFXdjaGVFRzNyQ2xpWFF0d1VRdllnNxoAGgAiBgipt6eSBjIBBFAEYAE"
}
```

---

## Overlay Files

Overlay files (text, drawings, stickers) are stored in `chat_media/` with:

- Date-prefixed, UUID-based filenames: `2019-02-13_overlay~zip-31FBA148-B00D-4E1D-A802-25249ADDF1A8.png`
- Pattern: `YYYY-MM-DD_overlay~zip-<UUID>.<ext>`
- Formats: Primarily `.png`, also `.webp` (common in recent exports)
- Matching based on **file modification timestamps** (no explicit links in JSON)

**Associated Files:**

- **Thumbnail files**: `YYYY-MM-DD_thumbnail~zip-<UUID>.jpg` - Thumbnail images for video snaps
- **Metadata files**: `YYYY-MM-DD_metadata~zip-<UUID>.unknown` - Additional metadata files (rare)

**Important:** Snapchat's export does not provide explicit links between overlays and media files. Memoria matches overlays to media files using file modification timestamps. This requires that timestamps be preserved during file operations.

---

## Platform-Specific Notes

- **Multiple Media IDs**: A single message can reference multiple media files separated by ` | ` (space-pipe-space). Example: up to 9 media files in one message.
- **Media File Naming**: All media files use date-prefixed naming (`YYYY-MM-DD_`) for organization
- **Overlay Matching**: Overlays matched to media by file modification timestamps (no explicit JSON links)
- **Conversation Types**: Both direct messages and group chats use the same structure
- **Media Type Ambiguity**: Most photos and videos use generic `"MEDIA"` type rather than `"PHOTO"` or `"VIDEO"`
- **System Messages**: Messages with `Media Type` starting with `"STATUS"` are system-generated messages (e.g., message erasure, participant removal, camera roll saves). These may have `null` Content in some cases.
- **Timestamp Format**: Already in standardized `YYYY-MM-DD HH:MM:SS UTC` format
- **snap_history.json Differences**: Uses `"IMAGE"` and `"VIDEO"` media types; lacks `Media IDs`, `Content`, and `IsSaved` fields
- **File Extensions**: Media ID files (`b~...`) support multiple formats: `.jpg` (most common), `.mp4` (videos), `.gif`, `.png`, `.webp`, and rarely `.unknown` for corrupted files
