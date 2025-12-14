# Instagram Messages Schema

This document describes the message data schema for Instagram Messages as exported from Instagram's data download.

---

## Export Format Versions

Instagram has changed their export format over time. Memoria supports both versions:

| Version | Era | Messages Path | Key Differences |
|---------|-----|---------------|-----------------|
| Current | 2023+ | `your_instagram_activity/messages/inbox/` | Uses `<h2>` for sender, no comma in timestamp |
| Legacy | Pre-2023 | `messages/inbox/` | Uses `<div>` for sender, comma after year in timestamp |

Both formats use the same CSS classes and overall HTML structure. Memoria automatically detects which format is present.

---

## Location in Export

### Current Format (2023+)

```text
instagram-username-YYYYMMDD/
└── your_instagram_activity/
    └── messages/
        ├── ai_conversations/
        │   └── thread_for_mailbox{id}_{thread_id}.html
        ├── ai_conversations.html
        ├── chats.html
        ├── secret_conversations.html
        ├── your_chat_information.html
        ├── inbox/
        │   ├── username1_conversation_id/
        │   │   ├── message_1.html
        │   │   ├── photos/
        │   │   │   ├── 123456789.jpg
        │   │   │   └── 987654321.png
        │   │   ├── videos/
        │   │   │   └── 111222333.mp4
        │   │   └── audio/
        │   │       └── 444555666.mp4
        │   ├── user1user2anduser3_conversation_id/
        │   │   ├── message_1.html
        │   │   └── photos/
        │   │       └── 777888999.jpg
        │   └── instagramuser_numeric_id/
        │       └── message_1.html
        └── message_requests/
            ├── username_conversation_id/
            │   └── message_1.html
            └── ...
```

### Legacy Format (Pre-2023)

```text
instagram-username-YYYYMMDD/
├── index.html
├── messages/
│   ├── chats.html
│   ├── secret_conversations.html
│   ├── inbox/
│   │   ├── username1_conversation_id/
│   │   │   ├── message_1.html
│   │   │   ├── photos/
│   │   │   │   └── 123456789.jpg
│   │   │   └── videos/
│   │   │       └── 111222333.mp4
│   │   └── ...
│   └── message_requests/
│       └── ...
└── content/
    └── ... (media metadata for public posts)
```

**Key Path Differences:**

| Component | Current Format | Legacy Format |
|-----------|---------------|---------------|
| Entry point | `start_here.html` | `index.html` |
| Messages root | `your_instagram_activity/messages/` | `messages/` |
| Inbox | `your_instagram_activity/messages/inbox/` | `messages/inbox/` |
| AI conversations | `your_instagram_activity/messages/ai_conversations/` | Not present |
| Chat info | `your_instagram_activity/messages/your_chat_information.html` | Not present |

**Key Files:**

- `your_instagram_activity/messages/ai_conversations.html` - Index/summary of AI conversations (e.g., Meta AI)
- `your_instagram_activity/messages/chats.html` - Index/summary of all chats
- `your_instagram_activity/messages/secret_conversations.html` - Information about encrypted conversations and devices
- `your_instagram_activity/messages/your_chat_information.html` - Additional chat metadata and information
- `your_instagram_activity/messages/ai_conversations/{thread}/` - AI conversation threads (e.g., Meta AI chats)
- `your_instagram_activity/messages/inbox/{conversation}/message_N.html` - HTML files containing message data
- `your_instagram_activity/messages/inbox/{conversation}/photos/` - Directory containing photos and images
- `your_instagram_activity/messages/inbox/{conversation}/videos/` - Directory containing video files
- `your_instagram_activity/messages/inbox/{conversation}/audio/` - Directory containing audio/voice messages
- `your_instagram_activity/messages/message_requests/{conversation}/` - Message requests from non-followers
- Media files may also be at conversation folder root

---

## Schema: message_N.html Files

Instagram messages are stored as **HTML files** (not JSON). Each `message_N.html` file contains one or more messages in HTML format.

**Important:** Large conversations are split across multiple HTML files numbered sequentially: `message_1.html`, `message_2.html`, `message_3.html`, etc.

---

## HTML Structure

The HTML files use Instagram's standard HTML export format with CSS classes for structure. The message container class is identical between formats, but the sender element type differs.

### Current Format (2023+)

Uses `<h2>` element for sender name:

```html
<html>
  <head>
    <title>Conversation Title</title>
    ...
  </head>
  <body>
    <div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
      <h2 class="_3-95 _2pim _a6-h _a6-i">Sender Name</h2>
      <div class="_3-95 _a6-p">
        <div>Message content...</div>
        <div>Media attachments...</div>
      </div>
      <div class="_3-94 _a6-o">Sep 22, 2017 6:33 am</div>
    </div>
    ...
  </body>
</html>
```

### Legacy Format (Pre-2023)

Uses `<div>` element for sender name (same CSS classes):

```html
<html>
  <head>
    <title>Conversation Title</title>
    ...
  </head>
  <body>
    <div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
      <div class="_3-95 _2pim _a6-h _a6-i">Sender Name</div>
      <div class="_3-95 _a6-p">
        <div>Message content...</div>
        <div>Media attachments...</div>
      </div>
      <div class="_3-94 _a6-o">Sep 22, 2017, 6:33 AM</div>
    </div>
    ...
  </body>
</html>
```

**Key Differences:**

| Element | Current Format | Legacy Format |
|---------|---------------|---------------|
| Sender element | `<h2 class="_3-95 _2pim _a6-h _a6-i">` | `<div class="_3-95 _2pim _a6-h _a6-i">` |
| Timestamp format | `Sep 22, 2017 6:33 am` | `Sep 22, 2017, 6:33 AM` |
| Timestamp comma | No comma after year | Comma after year |

---

## Extracted Message Fields

Memoria's preprocessor extracts the following information from HTML files:

### Required Fields

| Field | Extraction Method | Example |
|-------|------------------|---------|
| Conversation Title | `<title>` tag | `"John Doe"` or `"Family Group"` |
| Sender | `<h2>` or `<div>` with class `_3-95 _2pim _a6-h _a6-i` | `"your_username"` or deleted user placeholder |
| Timestamp | `<div>` with class `_3-94 _a6-o` | `"Sep 22, 2017 6:33 am"` |

**Note:** The sender field uses `<h2>` in current format exports and `<div>` in legacy exports. Memoria checks both element types.

### Optional Fields

| Field | Extraction Method | Example | Notes |
|-------|------------------|---------|-------|
| Message Text | `<div>` within `_3-95 _a6-p` container | `"Check this out!"` | May be missing for media-only messages |
| Photo Attachments | `<a>` and `<img>` tags with `href`/`src` | `photos/123456789.jpg` | Paths relative to conversation folder |
| Video Attachments | `<video>` tag with `src` attribute | `videos/123456789.mp4` | Video files stored in `videos/` folder |
| Audio Messages | `<video>` tag with `src` pointing to audio folder | `audio/123456789.mp4` | Voice messages stored as MP4 in `audio/` |
| Shared Links | `<a>` tags with `target="_blank"` | `http://example.com` | External URLs shared in messages |
| Shared Stories | `<a>` tags to `instagram.com/stories/` | `https://instagram.com/stories/username/12345` | Links to shared Instagram stories |
| Shared Posts/Reels | `<div>` with title, username, and URL | Title + username + reel URL | Rich preview for shared Instagram content |
| Reactions | `<ul class="_a6-q"><li><span>` | `❤️username` or `❤️username<span> (timestamp)</span>` | Can include timestamp or not |
| Like/React Messages | Text content: `"Liked a message"` | `"Liked a message"` | Standalone messages indicating a like/reaction was added to a previous message |
| Message Requests Header | `<h2 class="_a6-h">` | `"Mailbox thread pending"` | Only in message_requests folder |
| Group Participants | `<h2 class="_a6-h">` | `"Participants: name1, name2 and name3"` | Only in group chats |
| Group Invite Link | `<h2>` with link in content | `https://ig.me/j/...` | Optional in group chats |

---

## Example HTML Message

**Text Message:**

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <h2 class="_3-95 _2pim _a6-h _a6-i">your_username</h2>
  <div class="_3-95 _a6-p">
    <div>Check this out!</div>
  </div>
  <div class="_3-94 _a6-o">Sep 22, 2017 6:33 am</div>
</div>
```

**Message with Photo:**

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <h2 class="_3-95 _2pim _a6-h _a6-i">friend_username</h2>
  <div class="_3-95 _a6-p">
    <div>Look at this</div>
    <div><div>
      <a target="_blank" href="your_instagram_activity/messages/inbox/friend_username_123/photos/123456789.jpg">
        <img src="your_instagram_activity/messages/inbox/friend_username_123/photos/123456789.jpg" class="_a6_o _3-96" />
      </a>
    </div></div>
  </div>
  <div class="_3-94 _a6-o">Oct 15, 2017 2:45 pm</div>
</div>
```

**Message with Video:**

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <h2 class="_3-95 _2pim _a6-h _a6-i">your_username</h2>
  <div class="_3-95 _a6-p">
    <div><div><div><div>
      <video src="your_instagram_activity/messages/inbox/friend_username_123/videos/1276044200006294.mp4" controls="1" class="_a6_o _3-96">
        <a target="_blank" href="your_instagram_activity/messages/inbox/friend_username_123/videos/1276044200006294.mp4">
          <div>Click for video:</div>
        </a>
      </video>
    </div></div></div></div>
  </div>
  <div class="_3-94 _a6-o">Mar 06, 2023 6:26 pm</div>
</div>
```

**Message with Reaction:**

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <h2 class="_3-95 _2pim _a6-h _a6-i">your_username</h2>
  <div class="_3-95 _a6-p">
    <div>Here's some photos if you want to use them for any promo stuff</div>
    <div><ul class="_a6-q">
      <li><span>❤️friend_username<span> (Apr 23, 2022 5:32 pm)</span></span></li>
    </ul></div>
  </div>
  <div class="_3-94 _a6-o">Apr 23, 2022 10:03 am</div>
</div>
```

**Message with Shared Story:**

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <h2 class="_3-95 _2pim _a6-h _a6-i">friend_username</h2>
  <div class="_3-95 _a6-p">
    <div>!!! Congrats!!!!!!!</div>
    <div><div><div>
      <a target="_blank" href="https://instagram.com/stories/your_username/3717145434100081006">
        https://instagram.com/stories/your_username/3717145434100081006
      </a>
    </div></div></div>
  </div>
  <div class="_3-94 _a6-o">Sep 08, 2025 5:57 am</div>
</div>
```

**Message with Shared Reel:**

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <h2 class="_3-95 _2pim _a6-h _a6-i">friend_username</h2>
  <div class="_3-95 _a6-p">
    <div>friend_username sent an attachment.</div>
    <div><div><div>
      <div>Example reel caption with #hashtags</div>
      <div>reel_creator</div>
      <div><a target="_blank" href="https://www.instagram.com/reel/ABC123/?id=1234567890_12345678">
        https://www.instagram.com/reel/ABC123/?id=1234567890_12345678
      </a></div>
    </div></div></div>
  </div>
  <div class="_3-94 _a6-o">Apr 25, 2025 10:20 am</div>
</div>
```

---

## Media File Naming

Instagram media files use numeric identifiers:

- Photos: `123456789.jpg` or `123456789.png`
- Videos: `123456789.mp4`
- Audio/Voice Messages: `123456789.mp4`
- Stored in `photos/`, `videos/`, or `audio/` subdirectories (or conversation folder root)

**Note:** The numeric identifier is unique to the media file but doesn't directly correspond to message IDs or timestamps. Unlike older exports, newer Instagram exports do NOT prefix media files with "photo_" or "video_".

---

## Timestamp Format

Instagram uses a short timestamp format that varies between export versions:

### Current Format (2023+)

- Format: `"Mon DD, YYYY H:MM am/pm"`
- Example: `"Sep 22, 2017 6:33 am"`
- No comma after the year

### Legacy Format (Pre-2023)

- Format: `"Mon DD, YYYY, H:MM AM/PM"`
- Example: `"Sep 22, 2017, 6:33 AM"`
- Comma after the year
- AM/PM may be uppercase

**Note:** Memoria's preprocessor handles both formats and converts them to `YYYY-MM-DD HH:MM:SS` format for consistency.

---

## Conversation Types

Instagram messages are organized into different types:

### 1. Direct Messages (DM)

One-on-one conversations:

- Folder name: `{username}_{conversation_id}/`
- Title: Other user's display name
- Example: `friend_username_539841500738651/`

### 2. Group Chats

Multi-participant conversations:

- Folder name: `{participant1}{participant2}and{participant3}_{conversation_id}/`
- Title: Comma-separated participant list (e.g., `"alice, bob and charlie"`)
- Participant list: Separate `<h2>` header with `Participants: name1, name2, name3 and name4`
- Group invite link: Optional `<h2>` header with group invite URL
- Example folder: `alicebobandcharlie_9520404464722127/`

**Note:** Group chat folders concatenate participant usernames without separators (except "and"), while the title displays them comma-separated for readability.

### 3. AI Conversations

Conversations with AI assistants (e.g., Meta AI):

- Folder location: `your_instagram_activity/messages/ai_conversations/`
- Folder name: `thread_for_mailbox{mailbox_id}_{thread_id}/`
- Title: `"thread for mailbox{mailbox_id}"` or AI assistant name
- Participants: AI assistant name (e.g., "Meta AI")
- Example folder: `thread_for_mailbox416964671347609_27943376068609402.html`
- Index file: `your_instagram_activity/messages/ai_conversations.html`

**Note:** AI conversation HTML files use a different structure with nested tables rather than the standard message div format used in regular conversations.

### 4. Deleted Users

Conversations with deleted accounts:

- Folder name: `instagramuser_{numeric_id}/`
- Title in HTML: `"Instagram User"`
- Memoria assigns friendly names: `deleted_1`, `deleted_2`, etc.
- Example: `instagramuser_1013271226733911/`

**Note:** Deleted user names are assigned sequentially during preprocessing to make them more human-readable.

---

## Message Requests

Instagram separates message requests (from non-followers or non-connections) into a separate folder:

**Location:** `your_instagram_activity/messages/message_requests/`

**Structure:** Same as inbox conversations, with these differences:

- Header includes: `<h2 class="_a6-h">Mailbox thread pending</h2>`
- Follows same HTML structure and media organization as inbox messages
- Same conversation folder naming conventions apply

**Note:** Message requests that were accepted are moved to the inbox folder in the actual Instagram app, but the export preserves them in the message_requests folder.

---

## Additional Message Files

Instagram exports include several additional HTML files at the messages root level:

### Chats Index (`chats.html`)

A comprehensive index or summary file of all chats. This is a large HTML file containing metadata and overview information about all conversations.

### AI Conversations Index (`ai_conversations.html`)

An index file listing all AI conversation threads (e.g., conversations with Meta AI). Contains links to individual AI conversation HTML files.

### Secret Conversations (`secret_conversations.html`)

Information about encrypted/secret conversations feature:

- List of devices where the secret conversations feature is enabled
- Device details: manufacturer, model, type, OS version, IP address
- Connection/disconnection timestamps for each device

**Example content:**

- Device manufacturer (e.g., "Apple", "Windows", "Mac OS X")
- Device model (e.g., "iPhone18,2", "Chrome")
- Device type (e.g., "Igd:iPhone-400.1.0", "Igd:Web-302.0.1027102511")
- OS version and IP address
- Disconnection time

### Your Chat Information (`your_chat_information.html`)

Additional chat metadata and information about message settings, preferences, and other chat-related data. This is a large file with detailed information about the user's messaging activity.

---

## Special Cases

### Messages Without Text

Some messages contain only media or shared content:

```html
<div>
  <div>username sent an attachment.</div>
  <div>Link preview or media reference...</div>
</div>
```

The text "username sent an attachment." is Instagram's placeholder for media-only messages.

### Reaction-Only Messages

Messages that are just reactions to previous messages:

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <h2 class="_3-95 _2pim _a6-h _a6-i">friend_username</h2>
  <div class="_3-95 _a6-p">
    <div>Reacted ❤️ to your message </div>
  </div>
  <div class="_3-94 _a6-o">Nov 25, 2022 11:44 am</div>
</div>
```

These are standalone messages indicating a reaction was added, separate from reactions shown within other messages.

### Like Messages

Simple like/reaction messages without text content:

```html
<div class="pam _3-95 _2ph- _a6-g uiBoxWhite noborder">
  <h2 class="_3-95 _2pim _a6-h _a6-i">friend_username</h2>
  <div class="_3-95 _a6-p">
    <div>Liked a message</div>
  </div>
  <div class="_3-94 _a6-o">Sep 20, 2025 12:32 pm</div>
</div>
```

These standalone messages indicate that a user liked a previous message in the conversation.

### Empty Messages

Some messages appear with completely empty content sections:

```html
<div class="_3-95 _a6-p">
  <div><div></div><div></div><div></div><div></div></div>
</div>
```

These typically represent deleted messages, expired media, or unsupported message types.

### Deleted Messages

Deleted messages may appear as empty containers or with placeholder text in the HTML. These are typically skipped during preprocessing.

### View Once / Temporary Media

**Important:** "View Once" photos and expired temporary messages are **not included** in the export. Instagram only exports saved/persistent media.

This is a platform limitation, not a Memoria limitation.

### Multiple HTML Files

**Note:** In recent Instagram exports (2020+), most conversations appear as a single `message_1.html` file. However, very large conversations with thousands of messages MAY still be split across multiple HTML files:

- `message_1.html` - First batch of messages
- `message_2.html` - Second batch of messages
- `message_3.html` - Third batch of messages
- etc.

Memoria's preprocessor automatically processes all HTML files in each conversation folder, whether there is one or multiple files.

---

## CSS Class Dependencies

Instagram's HTML export relies on specific CSS classes for structure. These classes are consistent across both current and legacy export formats:

| CSS Class | Purpose | HTML Element |
|-----------|---------|--------------|
| `_3-95 _2pim _a6-h _a6-i` | Sender name | `<h2>` (current) or `<div>` (legacy) |
| `_3-95 _a6-p` | Message content container | `<div>` |
| `_3-94 _a6-o` | Timestamp | `<div>` |
| `pam _3-95 _2ph- _a6-g uiBoxWhite noborder` | Message container | `<div>` |

**Note:** While CSS classes are identical between formats, the sender name uses different HTML elements (`<h2>` vs `<div>`). Memoria handles both.

**Warning:** If Instagram changes these CSS classes in future exports, the preprocessor may need updates. However, Instagram has maintained this class structure consistently since at least 2017.

---

## Platform-Specific Notes

### Format Differences

- **Two Export Versions**: Instagram changed export structure around 2023; Memoria supports both
- **Path Differences**: Current format uses `your_instagram_activity/messages/`, legacy uses `messages/`
- **Element Differences**: Sender name uses `<h2>` (current) or `<div>` (legacy) with same CSS classes
- **Timestamp Differences**: Legacy format includes comma after year (`Sep 22, 2017, 6:33 AM`)

### General Notes

- **HTML Parsing Required**: No JSON format; must parse HTML structure
- **CSS Class Dependencies**: Extraction relies on Instagram's CSS class names (consistent across versions)
- **Deleted Users**: Special handling for `instagramuser_{id}` folders (display as "Instagram User" in HTML)
- **Message Requests**: Separate `message_requests/` folder for messages from non-followers
- **AI Conversations**: Separate `ai_conversations/` folder with different HTML structure (current format only)
- **Additional Index Files**: Root-level HTML files (`chats.html`, `secret_conversations.html`, etc.) provide metadata
- **Secret Conversations**: Export includes device information for encrypted conversations
- **Missing Media**: Export excludes ephemeral content (View Once, expired messages)
- **Multiple HTML Files**: Most exports use single `message_1.html` per conversation; very large ones may split
- **Media Organization**: Media stored in separate `photos/`, `videos/`, and `audio/` subdirectories
- **Media File Naming**: Numeric identifiers WITHOUT "photo_" or "video_" prefix (unlike older exports)
- **Shared Content**: Instagram stories, posts, and reels are preserved as links (media not included)
- **Reactions**: Can appear both inline with messages and as standalone reaction messages
- **Like Messages**: Standalone "Liked a message" messages indicate likes on previous messages
- **Group Chats**: Include participant list and optional group invite link in HTML
- **Audio Messages**: Voice messages stored as MP4 files in `audio/` folder

---

## Related Documentation

- [Instagram Export Format Comparison](Instagram-Export-Format-Comparison.md) - Detailed comparison of 2022 vs 2025 export structures
- [Instagram Export Guide](Instagram-Export.md) - How to download and process Instagram exports
