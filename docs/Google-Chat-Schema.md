# Google Chat Messages Schema

This document describes the message data schema for Google Chat as exported from Google Takeout.

## Location in Export

```text
google-username-YYYYMMDD/
└── Google Chat/
    ├── Groups/
    │   ├── DM XXXXXXXXXXXXX/
    │   │   ├── messages.json
    │   │   ├── group_info.json
    │   │   ├── File-2016-09-06.png
    │   │   ├── File-2017-01-05.jpg
    │   │   └── ...
    │   ├── Space XXXXXXXX/
    │   │   ├── messages.json
    │   │   ├── group_info.json
    │   │   ├── File-2016-07-08.png
    │   │   └── ...
    │   └── ...
    └── Users/
        └── User XXXXXXXXXXXXX/
            └── user_info.json
```

**Key Files:**

- `Google Chat/Groups/{conversation}/messages.json` - Contains message metadata for each conversation
- `Google Chat/Groups/{conversation}/group_info.json` - Contains conversation/group information
- `Google Chat/Users/{user_id}/user_info.json` - Contains export user information and membership list
- Media files stored directly in conversation folders

**Note:** Unlike some other Google exports, Google Chat data is typically NOT nested inside a `Takeout/` directory.

---

## Schema: messages.json

The `messages.json` file in each conversation folder is structured as an **object containing a `messages` array**.

**Top-Level Structure:**

```json
{
  "messages": [
    { ...message object... },
    { ...message object... },
    { ...message object... }
  ]
}
```

---

## Message Object Schema

Each message object has the following structure:

### Required Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `creator` | object | Information about the message sender | See creator object below |
| `created_date` | string | Timestamp when message was sent (formatted) | `"Thursday, November 3, 2016 at 11:59:50 AM UTC"` |
| `topic_id` | string | Unique identifier for the message thread/topic | `"LqFDi4ScZAQ"` |
| `message_id` | string | Unique identifier for the message | `"k6oxHQAAAAE/LqFDi4ScZAQ/LqFDi4ScZAQ"` |

### Optional Fields

| Field | Type | Description | Example | Notes |
|-------|------|-------------|---------|-------|
| `text` | string | Text content of the message | `"We should shoe shop this weekend"` | May be missing for media-only messages |
| `attached_files` | array | List of attached file references | `[{"export_name": "File-2016-09-06.png"}]` | May be missing if no attachments |
| `annotations` | array | Metadata for links, embeds, and rich content | See annotations section below | Contains metadata for YouTube links, URLs, Drive files, etc. |

### Example Message Object

```json
{
  "creator": {
    "name": "John Smith",
    "email": "john.smith@example.com",
    "user_type": "Human"
  },
  "created_date": "Thursday, November 3, 2016 at 11:59:50 AM UTC",
  "text": "We should shoe shop this weekend",
  "topic_id": "LqFDi4ScZAQ",
  "message_id": "k6oxHQAAAAE/LqFDi4ScZAQ/LqFDi4ScZAQ"
}
```

---

## Creator Object Schema

The `creator` object contains sender information:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `name` | string | Display name of the sender | `"John Smith"` |
| `email` | string | Email address of the sender | `"john.smith@example.com"` |
| `user_type` | string | Type of user | `"Human"` (bots may have different values) |

**Note:** The `user_type` field can be used to distinguish between messages from humans and bots.

---

## Attached Files Schema

When present, the `attached_files` array contains objects with:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `original_name` | string | Original filename of the attachment | `"2016-11-22.png"` or `"4926929847130562072?account_id=3.png"` |
| `export_name` | string | Filename of the attachment in export | `"File-2016-09-06.png"` or `"File-4926929847130562072?account_id=3.png"` |

**Note:** The actual media files are stored in the same directory as `messages.json` with filenames matching `export_name`. However, files with `?account_id=` in the JSON are saved to disk with `_account_id=` instead (e.g., `File-4926929847130562072_account_id=3.png`).

### Example Message with Attachment

```json
{
  "creator": {
    "name": "Jane Doe",
    "email": "jane.doe@example.com",
    "user_type": "Human"
  },
  "created_date": "Tuesday, November 22, 2016 at 9:46:26 PM UTC",
  "attached_files": [
    {
      "original_name": "2016-11-22.png",
      "export_name": "File-2016-11-22.png"
    }
  ],
  "topic_id": "abc123def456",
  "message_id": "k6oxHQAAAAE/abc123def456/abc123def456"
}
```

**Note:** Messages with attachments may or may not have a `text` field.

---

## Annotations Schema

The `annotations` array contains metadata for rich content in messages, such as links, YouTube videos, and Google Drive files. When present, each annotation object has the following structure:

### Common Annotation Fields

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `start_index` | integer | Character position where the annotation starts in the text | `0` |
| `length` | integer | Length of the annotated text in characters | `43` |

**Note:** Some annotations may have both `start_index` and `length` set to `0`, which typically indicates embedded content or metadata not directly tied to specific text in the message.

### Annotation Types

Annotations contain different metadata depending on the type of content:

#### YouTube Metadata

For YouTube video links:

```json
{
  "start_index": 0,
  "length": 43,
  "youtube_metadata": {
    "id": "dQw4w9WgXcQ",
    "start_time": 0
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | YouTube video ID |
| `start_time` | integer | Start time in seconds (for timestamped links) |

#### URL Metadata

For general web links (including location sharing):

```json
{
  "start_index": 51,
  "length": 27,
  "url_metadata": {
    "title": "",
    "snippet": "",
    "image_url": "",
    "url": {
      "private_do_not_access_or_else_safe_url_wrapped_value": "https://hangouts.google.com"
    }
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Page title (may be empty) |
| `snippet` | string | Page description/snippet (may be empty) |
| `image_url` | string | Preview image URL (may be empty) |
| `url` | object | Contains the actual URL in a nested field |

**Note:** The URL is stored in a field with the unusual name `private_do_not_access_or_else_safe_url_wrapped_value`. This appears to be Google's internal format.

#### Google Drive Metadata

For Google Drive file/folder links:

```json
{
  "start_index": 0,
  "length": 72,
  "drive_metadata": {
    "id": "1u3O4ravpOpkepKtqOOvgio-l0-MH_Btb",
    "title": "",
    "thumbnail_url": ""
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Google Drive file/folder ID |
| `title` | string | File/folder title (may be empty) |
| `thumbnail_url` | string | Thumbnail preview URL (may be empty) |

### Example Message with Annotations

```json
{
  "creator": {
    "name": "Jane Doe",
    "email": "jane.doe@example.com",
    "user_type": "Human"
  },
  "created_date": "Monday, September 5, 2016 at 6:46:08 PM UTC",
  "text": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "annotations": [
    {
      "start_index": 0,
      "length": 43,
      "youtube_metadata": {
        "id": "dQw4w9WgXcQ",
        "start_time": 0
      }
    }
  ],
  "topic_id": "b4J2xIBNDZk",
  "message_id": "h5fzzIAAAAE/b4J2xIBNDZk/b4J2xIBNDZk"
}
```

**Note:** Annotations are particularly useful for:

- Extracting YouTube video IDs from shared links
- Identifying location sharing messages
- Detecting Google Drive file shares
- Preserving link metadata for archival purposes

---

## File Naming Patterns

Google Chat exports use several naming patterns for media files:

1. **Date-based**: `File-2016-09-06.png`, `File-2017-01-05(1).png` (with counter for same-day files)
2. **UUID-based**: `File-03b06911-def0-4c33-ad50-03f90084a158.mov`, `File-624c5724-ae3d-48a0-b2ce-ac04d8de43db.mov`
3. **Account ID-based**: `File-284346614161283602_account_id=3.jpg`, `File-4709807525838059701_account_id=1.jpg`
   - In JSON: stored as `File-284346614161283602?account_id=3.jpg` (with `?` and URL-encoded `=` as `\u003d`)
   - On disk: saved as `File-284346614161283602_account_id=3.jpg` (with `_`)
4. **Custom names**: `File-k8.png`, `File-7QxRoLS.jpg`, `File-eZvU4ZP.jpg`, `File-Callouts_Internet_RAD.png`

When processing these files, match media files to messages using the `export_name` field from the `attached_files` array, accounting for the `?` to `_` conversion for account_id files.

---

## Timestamp Format

Google Chat uses a verbose timestamp format:

- Format: `"DayOfWeek, Month DD, YYYY at HH:MM:SS AM/PM UTC"`
- Example: `"Thursday, November 3, 2016 at 11:59:50 AM UTC"`

**Note:** When processing timestamps, you may want to convert this verbose format to a standardized format like `YYYY-MM-DD HH:MM:SS` or ISO 8601.

---

## Conversation Organization

### Groups vs Direct Messages

- **Groups/Spaces**: Stored in `Google Chat/Groups/Space XXXXXXXXXX/`
  - Contains `group_info.json` with group name and participants
  - Folder names include "Space" prefix

- **Direct Messages**: Stored in `Google Chat/Groups/DM XXXXXXXXXX/`
  - Contains `group_info.json` with participant information
  - Folder names include "DM" prefix

### group_info.json

Each conversation folder contains a `group_info.json` file with conversation metadata:

**For Spaces (Group Chats):**

```json
{
  "name": "Group Chat",
  "members": [
    {
      "name": "User 1",
      "email": "user1@example.com",
      "user_type": "Human"
    },
    {
      "name": "User 2",
      "email": "user2@example.com",
      "user_type": "Human"
    }
  ]
}
```

**For Direct Messages (DMs):**

```json
{
  "members": [
    {
      "name": "User 1",
      "email": "user1@example.com",
      "user_type": "Human"
    },
    {
      "name": "User 2",
      "email": "user2@example.com",
      "user_type": "Human"
    }
  ]
}
```

**Note:** The `name` field is typically present for Spaces but absent for DMs. All members include the `user_type` field (usually `"Human"`).

### user_info.json

The `Users/User {user_id}/user_info.json` file contains information about the export user:

```json
{
  "user": {
    "name": "John Smith",
    "email": "john.smith@example.com",
    "user_type": "Human"
  },
  "membership_info": [
    {
      "group_id": "DM k6oxHQAAAAE",
      "membership_state": "MEMBER_JOINED"
    },
    {
      "group_name": "Project Team",
      "group_id": "Space AAAAFyOfy4w",
      "membership_state": "MEMBER_JOINED"
    }
  ]
}
```

**Fields:**

- `user`: Information about the account owner
- `membership_info`: Array of all conversations the user is/was a member of
  - `group_id`: The conversation folder name (e.g., `"DM k6oxHQAAAAE"`, `"Space AAAAFyOfy4w"`)
  - `group_name`: Optional, present for named Spaces
  - `membership_state`: Usually `"MEMBER_JOINED"`

---

## Platform-Specific Notes

- **Spaces vs DMs**: Groups labeled as "Space" or "DM" in folder names
- **File Naming Complexity**: Multiple naming schemes (date, UUID, account ID)
- **Bot Messages**: `user_type` field distinguishes human vs bot senders
- **Topic IDs**: Messages organized by `topic_id` (threaded conversations)
- **Same-Day Files**: Date-based filenames include counters `(1)`, `(2)`, etc. for multiple files on same day
- **Timestamp Verbosity**: Timestamps include full day-of-week and formatted time
