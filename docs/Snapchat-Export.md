# Snapchat Export Guide

This guide covers processing Snapchat exports including Memories and Messages.

## Table of Contents

1. [Overview](#overview)
2. [Preparing Your Snapchat Export](#preparing-your-snapchat-export)
3. [Snapchat Memories](#snapchat-memories)
4. [Snapchat Messages](#snapchat-messages)
5. [Orphaned Media](#orphaned-media)
6. [Ambiguous Overlay Matching](#ambiguous-overlay-matching)
7. [Overlay Embedding](#overlay-embedding)
8. [Processing Your Export](#processing-your-export)

## Overview

Snapchat provides data export through "My Data". Memoria can process:

- **Snapchat Memories**: Saved snaps and stories with overlay embedding support
- **Snapchat Messages**: Media from chat conversations

---

## Important Limitations

> **Manual memory download required:**
>
> Snapchat provides a large HTML file with links to download each memory individually. Memoria does not yet automate this download process. You must:
>
> 1. Open the HTML file in your browser
> 2. Download all memories (can be hundreds or thousands)
> 3. Organize them into the expected structure (`media/`, `overlays/`, `metadata.json`)
>
> This is tedious for large memory collections. Automation is planned but not yet implemented. Consider whether the manual effort is worth it for your use case before starting.
>
> See the [Snapchat Memories](#snapchat-memories) section below for required structure details.

---

## Preparing Your Snapchat Export

After downloading your Snapchat data export from "My Data":

1. Extract the downloaded archive (usually a ZIP file)
2. Create a new directory following Memoria's naming convention:

`snapchat-username-YYYYMMDD/`

3. Inside this directory, create subdirectories based on what you exported:
   - Create `memories/` if you exported Memories
   - Create `messages/` if you exported Chat History

4. Move the contents of your extracted export into the appropriate subdirectory so the structure matches what the processor expects (see Export Structure sections below)

**Note**: Use your actual Snapchat username and the date you downloaded the export.

## Snapchat Memories

### Export Structure

Snapchat memories have the following structure:

```
snapchat-username-YYYYMMDD/
└── memories/
    ├── media/
    │   ├── photo.jpg
    │   ├── video.mp4
    │   └── ...
    ├── overlays/
    │   ├── overlay_123.png
    │   ├── overlay_456.png
    │   └── ...
    └── metadata.json
```

**Important Note**: Snapchat does not provide memories directly in this structure. Instead, the export contains an HTML file with links to download your memories. You must manually download all memories and organize them into the structure shown above. There are plans to implement automatic downloading and organization of memories from the HTML file, but this is not yet available. Proceed at your own risk when manually organizing your export.

### What Gets Processed

- Photos and videos from saved memories
- Overlays (text, drawings, stickers) are embedded directly into media
- Metadata including:
  - Capture timestamp
  - Memory type (snap, story)
  - Associated overlays
  - Download source (if re-saved from chat)

### Required Elements

For Memoria to detect a Snapchat Memories export, you must have:

- `media/` directory containing photos and videos
- `overlays/` directory (can be empty if you have no overlays)
- `metadata.json` file with memory information (must be an array of memory objects)

**Structure Flexibility**: These can be:

- Directly at the export root level, OR
- Inside a `memories/` subdirectory

Both structures are automatically detected and processed.

### Metadata Structure

The `metadata.json` file is an array of memory objects with the following structure:

```json
[
  {
    "date": "2023-01-15 10:30:45 UTC",
    "media_type": "PHOTO",
    "media_filename": "photo_12345.jpg",
    "overlay_filename": "overlay_12345.png"
  },
  {
    "date": "2023-02-20 14:22:10 UTC",
    "media_type": "VIDEO",
    "media_filename": "video_67890.mp4",
    "overlay_filename": "overlay_67890.png"
  }
]
```

**Note**: The `overlay_filename` field is optional. Memories without overlays will not have this field.

### Embedded Metadata

Memoria embeds comprehensive metadata into all processed Snapchat Memories files:

**For Images:**

- `DateTimeOriginal`, `CreateDate`, `ModifyDate`: Original capture timestamp
- `GPSLatitude`, `GPSLongitude`: Location data (when available)
- `ImageDescription`: Source information
- `IPTC:Caption-Abstract`: Source information (duplicate for compatibility)

**For Videos:**

- `creation_time`: Original capture timestamp
- GPS metadata (when available)
- `comment`: Source information (same as ImageDescription for images)
- `description`: Source information (same as ImageDescription for images)

**Example Description for Memories:**

```
Source: Snapchat/username/memories
```

This simple format identifies the source platform, account, and content type.

### Common Issues

**Missing overlays**: If a memory doesn't have an overlay, the base media is processed normally.

**Video overlays**: Embedding overlays in videos requires re-encoding, which may take time for large files.

**Resolution mismatches**: Rarely, overlay resolution may not match media - Memoria handles this by scaling appropriately.

## Snapchat Messages

### Export Structure

Snapchat messages exports from "My Data" have the following structure:

```
snapchat-username-YYYYMMDD/
└── messages/
    └── json/
        ├── chat_history.json
        ├── snap_history.json
        └── chat_media/
            ├── photo_media-id-123.jpg
            ├── video_media-id-456.mp4
            ├── overlay_uuid-abc.png
            └── ...
```

The raw export contains media files with various naming patterns (media IDs, UUIDs, or hashes) in the `chat_media/` directory. Memoria's preprocessor automatically organizes these files and matches them with message metadata.

### What Gets Processed

- Photos and videos from chat conversations
- Media from both sent and received messages
- Metadata including:
  - Message timestamp
  - Conversation participants
  - Message type (snap, chat media)
  - Sender information

### Required Elements

- `json/` directory containing:
  - `chat_history.json`: Chat messages with metadata
  - `snap_history.json`: Snap-specific messages
  - `chat_media/`: Directory with all media files and overlays
- Can be in `messages/` subdirectory OR at root level

### Raw Export Format

The raw Snapchat export's `chat_history.json` contains message entries in a dictionary format where keys are conversation IDs and values are arrays of messages. Each message includes fields like:

- `Media Type`: Type of media (e.g., "PHOTO", "VIDEO", "MEDIA")
- `Media IDs`: Reference to media file(s) in chat_media directory
- `Created`: Timestamp when the message was sent
- `From`: Sender username
- `IsSender`: Boolean indicating if you sent it

**Note**: Memoria automatically preprocesses the raw export to organize files and create a cleaned metadata structure for processing. The raw chat_history.json format is complex and varies - the preprocessor handles all these variations automatically.

### Embedded Metadata

Memoria embeds rich contextual metadata into all processed Snapchat Messages files:

**For Images:**

- `DateTimeOriginal`, `CreateDate`, `ModifyDate`: Message timestamp
- `ImageDescription`: Multi-line description with conversation context
- `IPTC:Caption-Abstract`: Same description (duplicate for compatibility)

**For Videos:**

- `creation_time`: Message timestamp
- `comment`: Multi-line description with conversation context (same as ImageDescription for images)
- `description`: Same description

**Example Description for Messages (DM):**

```
Source: Snapchat/username/messages
  - friend_username in "DM with friend_username": "Check this out!"
```

**Example Description for Messages (Group Chat):**

```
Source: Snapchat/username/messages
  - friend_username in "Weekend Trip Planning": "Here's the photo from yesterday"
```

**Example for Messages without content text:**

```
Source: Snapchat/username/messages
  - friend_username in "DM with friend_username"
```

**Example for Merged Messages:**

When the same media appears in multiple conversations, Memoria creates a single file with consolidated metadata listing all occurrences:

```
Source: Snapchat/username/messages
  - friend_username in "DM with friend_username": "Check this out!"
  - friend_username in "Family Group": "Sharing this here too"
```

This rich metadata makes it easy to search for and identify photos based on who sent them, which conversation they came from, and any accompanying text.

### Common Issues

**Expired snaps**: Snaps that weren't saved or have expired won't be in the export.

**Deleted users**: Messages from users who have deleted their accounts may show placeholder usernames in the metadata.

**Ambiguous overlay matching**: See the [Ambiguous Overlay Matching](#ambiguous-overlay-matching) section below for details on the `needs_matching/` directory.

**Orphaned media**: See the [Orphaned Media](#orphaned-media) section below for details on the `orphaned_media/` and `issues/` directories.

## Orphaned Media

**The Problem**: Snapchat's export sometimes contains media files in `chat_media/` that have no corresponding entry in `chat_history.json`, or vice versa - metadata references that point to missing files. This mismatch occurs when:

- Files were deleted from Snapchat after being sent but before export
- Media files use UUID/hash naming that couldn't be matched by timestamp
- Export glitches or incomplete downloads
- Messages were deleted but media files remained in the export

### How Memoria Handles This

**Orphaned Media Files** (files without metadata):

- Processed and organized into `orphaned_media/` directory in the output
- Timestamped using file modification time (best guess)
- If exactly 1 overlay matches the timestamp, it's automatically applied
- Still searchable and viewable, just without conversation context

**Orphaned Metadata** (missing files):

- References to non-existent files are tracked in the failure report
- Saved as JSON entries in `issues/failed-matching/metadata/`
- Helps identify what content may be missing from your export

### Output Structure

```
Snapchat Messages/
└── username/
    ├── conversation_1/
    ├── orphaned_media/           # Media files without matching messages
    │   ├── photo_unknown_001.jpg
    │   └── video_unknown_002.mp4
    └── issues/
        ├── failure-report.json   # Comprehensive failure tracking
        └── failed-matching/
            ├── media/            # Copy of orphaned media files
            └── metadata/         # References to missing files
```

The `failure-report.json` includes detailed statistics and context for each orphaned file, helping you understand what couldn't be matched and why.

**Note**: Orphaned files are still fully accessible and can be manually organized based on their timestamps or content.

## Ambiguous Overlay Matching

**The Problem**: Snapchat's export doesn't specify which overlay belongs to which media file. Memoria matches overlays to videos using **file modification timestamps**, but when a message contains multiple videos with multiple overlays at the same timestamp, it's impossible to determine the correct pairing programmatically.

**How Overlay Matching Works**:

Snapchat exports provide media files and overlay files (PNG images) separately, without explicit links between them. Memoria uses file modification timestamps to match them:

1. Read the modification timestamp from each media file (photo/video)
2. Read the modification timestamp from each overlay file (PNG)
3. Match overlays to media files based on timestamp proximity
4. If exactly 1 overlay matches 1 media file at a given timestamp, auto-match them
5. If multiple media files have multiple overlays at the same timestamp, flag as "ambiguous"

**Critical**: This matching relies on **filesystem modification timestamps** being preserved from the original Snapchat export. If these timestamps are modified during file operations (copy, move, etc.), the matching will fail completely because timestamps won't align.

**Example**: A message sent at 10:30:45 contains 2 videos and 2 overlays, all timestamped 10:30:45 - which overlay goes with which video?

### When This Happens

**Handled Automatically** (1:1 matching):

- 1 video + 1 overlay at same timestamp
- Videos without overlays
- Images (never have overlays in Snapchat)

**Requires Manual Review** (saved to `needs_matching/`):

- 2+ videos with 2+ overlays at same timestamp
- Multiple overlays even with 1 video

**Note**: Ambiguous cases are relatively rare - most messages contain only 1 video or don't have overlays.

### Output Structure

Each ambiguous case gets a timestamped folder:

```
needs_matching/
└── 2023-01-15_10-30-45_UTC/
    ├── media/              # Videos from this message
    ├── overlays/           # Potential overlay matches
    └── match_info.json     # Message context and file details
```

The `match_info.json` file includes:

- Message metadata (conversation, sender, content, timestamp)
- List of media files with IDs and types
- List of overlay files with UUIDs
- Analysis hints (e.g., "2 videos, 2 overlays")

### Resolution

**Current**: Manually review the message context in `match_info.json` and compare video content with overlay designs to determine correct matches. Videos without matched overlays are processed normally.

**Planned Feature**: Automated interactive matching tool to simplify the manual matching process.

## Overlay Embedding

**Special Feature**: Memoria automatically embeds Snapchat overlays (text, drawings, stickers) directly onto the base media for both Memories and Messages.

### Process for Snapchat Memories

1. Read `metadata.json` which explicitly links media files to overlay files
2. Base media file is loaded
3. Corresponding overlay is identified from metadata
4. Overlay is composited onto the media
5. Result is saved with original quality

### Process for Snapchat Messages

Messages don't have a metadata file linking overlays to media, so matching uses **file modification timestamps**:

1. **CRITICAL FIRST STEP**: Read and store filesystem modification timestamps from all media and overlay files BEFORE any file operations
2. Match overlay files to media files based on timestamp alignment
3. When exactly 1 overlay matches 1 media file at a timestamp, auto-apply the overlay
4. Load base media file and composite the matched overlay
5. Save result with original quality

**Why Timestamp Preservation Matters**:

If file modification timestamps are changed during processing (e.g., by copying files before reading timestamps), overlay matching breaks:

- Original state: `video.mp4` (modified: 2023-01-15 10:30:45), `overlay.png` (modified: 2023-01-15 10:30:45) → Match ✓
- After timestamp corruption: `video.mp4` (modified: 2024-11-22 15:00:00), `overlay.png` (modified: 2024-11-22 15:00:00) → Still match, but at wrong time
- Worse case: Files copied at different moments: `video.mp4` (modified: 2024-11-22 15:00:01), `overlay.png` (modified: 2024-11-22 15:00:05) → No match ✗

Memoria's preprocessor reads all timestamps BEFORE any file operations to prevent this.

**Result**: This preserves the complete "memory" or "snap" as it appeared in Snapchat, including all text, drawings, stickers, and decorations.

**Technical Details**:

- Overlays are PNG or WebP files with transparency
- Composite operation preserves original media resolution
- Photos: Overlays are composited onto the image using PIL/Pillow
- Videos: Converted to multi-track MKV format with dual video tracks:
  - **Track 0 (default)**: Video with overlay embedded on every frame
  - **Track 1**: Original video without overlay
- Video rotation metadata is automatically detected and physically applied
- Output videos have correct orientation without rotation metadata flags

**Performance Considerations**:

- **Photos**: Fast (typically < 1 second per photo)
- **Videos**: Processing time depends on video length, resolution, and available hardware
- Videos with overlays require multi-pass encoding:
  1. Rotation correction (if needed)
  2. Overlay embedding
  3. Dual-track MKV creation
  4. Metadata embedding
- Hardware acceleration is automatically detected and used when available (NVIDIA NVENC, AMD AMF, Intel QSV)
- Software encoding (libx264/libx265) is used as fallback if hardware acceleration is unavailable

**Dual-Track Videos**:

Videos with embedded overlays are saved as multi-track MKV files. This format allows you to:

- View the default track with the overlay embedded
- Switch to the original video track without the overlay in compatible players

**Switching tracks in VLC Media Player:**

1. Open the video file
2. Go to: `Video` > `Video Track` > Select track
3. Choose Track 0 (with overlay) or Track 1 (original)

**Common Issues**:

**Missing overlays**: If a memory or message doesn't have an associated overlay file, the base media is processed normally without overlay embedding.

**Video overlay embedding fails**: If overlay embedding fails, Memoria automatically falls back to copying the original video file with its original extension.

**Hardware acceleration errors**: If hardware-accelerated encoding fails, Memoria automatically falls back to software encoding. Check the logs for details.

## Processing Your Export

### Single Snapchat Export

```bash
./memoria.py /path/to/snapchat-username-20251007 -o /path/to/output
```

Memoria will automatically:

1. Detect whether your export contains memories, messages, or both
2. For messages: Preprocess the raw export to organize files and match media to conversations
3. Process all media with overlay embedding and metadata writing
4. Organize output files by conversation or memory type

The processing is fully automatic - just point Memoria at your export directory.

### Output Structure

```
/path/to/output/
├── Snapchat Memories/
│   └── username/
│       └── memories/
│           ├── photo_with_overlay_001.jpg
│           ├── video_with_overlay_002.mkv    # Videos with overlays
│           ├── video_no_overlay_003.mp4      # Videos without overlays
│           └── ...
└── Snapchat Messages/
    └── username/
        ├── conversation_1/
        │   ├── photo_001.jpg
        │   ├── video_002.mkv    # Video with overlay
        │   └── ...
        ├── conversation_2/
        │   └── ...
        ├── orphaned_media/       # Media not matched to any conversation
        │   └── ...
        ├── needs_matching/       # Ambiguous overlay-video matching cases
        │   └── 2023-01-15_10-30-45_UTC/
        │       ├── media/
        │       ├── overlays/
        │       └── match_info.json
        └── issues/               # Detailed failure tracking
            ├── failure-report.json
            └── failed-matching/
                ├── media/
                └── metadata/
```

**Note**: Videos with overlays are converted to MKV format with dual video tracks. Videos without overlays retain their original format.

### Multiple Snapchat Accounts

```
/path/to/exports/
├── snapchat-personal_user-20251007/
└── snapchat-old_account-20251007/
```

Process them all:

```bash
./memoria.py --originals /path/to/exports -o /path/to/output
```

### Immich Albums

When uploading to Immich, Snapchat content is organized as:

- **Memories**: `Snapchat/{username}/memories`
- **Messages**: `Snapchat/{username}/messages`

See [Immich Upload](Immich-Upload) for configuration options.

## Processing Performance

Use `--workers` to control parallelism:

```bash
# Process with 8 parallel workers
./memoria.py /path/to/snapchat-export -o /path/to/output --workers 8
```

For details on overlay embedding performance, see the [Overlay Embedding](#overlay-embedding) section.

## Tips

1. **Request Complete Data**: When submitting your data request to Snapchat, select all available options (Memories, Chat History) to get a complete export.

2. **Overlay Quality**: Snapchat provides overlays at the resolution they were created, which generally matches the media resolution.

3. **Processing Time**:
   - Photos process quickly (< 1 second each)
   - Videos with overlays require multi-pass encoding and take significantly longer
   - Use `--workers` to parallelize processing across multiple CPU cores
   - Hardware acceleration (if available) speeds up video processing considerably

4. **Temporary Files**: Video overlay embedding creates temporary files during processing - these are automatically cleaned up after successful processing.

5. **Messages Preprocessing**: For message exports, Memoria automatically:
   - Matches media files to conversations based on Media IDs, UUIDs, or timestamps
   - Handles duplicate media (same file shared in multiple conversations)
   - Creates an `orphaned_media/` folder for files that couldn't be matched
   - Creates a `needs_matching/` folder for ambiguous overlay-to-video matching cases
   - Generates a preprocessing log with details about any matching issues

6. **Multiple Exports**: If you request multiple Snapchat exports over time, each will be processed independently. Deduplication across exports is not currently implemented.

7. **Storage Space**: Ensure you have sufficient disk space:
   - Video processing requires temporary space roughly 2-3x the video size
   - MKV files with dual tracks are slightly larger than original videos
   - The output directory will contain all processed media files

8. **Memories Download**: The Snapchat "My Data" export for memories contains an HTML file with download links rather than the actual media files. You'll need to manually download the memories and organize them into the expected structure. See the Export Structure section for details.

## Troubleshooting

### "No overlays directory found" (Memories)

For Snapchat Memories, both `media/` and `overlays/` directories are required for detection, even if no overlays exist. Create an empty `overlays/` directory if you don't have any overlay files.

### Video overlay embedding fails

If video processing fails, check:

- **ffmpeg is installed**: Run `ffmpeg -version` to verify
- **Sufficient disk space**: Video processing needs 2-3x the video size in temporary space
- **Video file integrity**: Try playing the video in VLC to check if it's corrupted
- **Check logs**: Look for specific error messages in the console output or log files

Memoria automatically falls back to copying the original video file if overlay embedding fails, so processing will continue.

### Messages: Media files not matching to conversations

If you see files in the `orphaned_media/` or `issues/` folders:

- **`orphaned_media/`**: Media files successfully processed but without conversation context
- **`issues/failed-matching/media/`**: Additional copy of orphaned files for review
- **`issues/failure-report.json`**: Detailed report explaining why files couldn't be matched

Check the failure report for specific reasons:

- Files deleted from Snapchat after being sent but before export
- Media IDs in `chat_history.json` don't match actual filenames  
- Export glitches or incomplete data

See the [Orphaned Media](#orphaned-media) section for detailed information.

### Messages: Ambiguous overlay matching

If you see a `needs_matching/` folder in your output:

- This contains cases where multiple videos have multiple overlays at the same timestamp
- Review the `match_info.json` file in each timestamped subfolder
- Use the message context (conversation, sender, content) to help identify correct matches
- Manually match and process these files as needed

See the [Ambiguous Overlay Matching](#ambiguous-overlay-matching) section for detailed information.

### Hardware acceleration errors

If you see hardware acceleration errors but processing continues:

- This is normal - Memoria automatically falls back to software encoding
- Software encoding is slower but produces identical quality
- To force software encoding from the start, you can modify encoder detection (advanced)

### EXIF metadata corruption

If you see messages about rebuilding EXIF structures:

- This is normal for some files exported from Snapchat
- Memoria automatically detects and rebuilds corrupted EXIF data
- The final files will have proper EXIF metadata embedded

## Related Documentation

- [Getting Started](Getting-Started) - Initial setup guide
- [Usage](Usage) - Detailed usage options
- [Immich Upload](Immich-Upload) - Immich upload configuration
