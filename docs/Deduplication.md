# Media Deduplication

## Overview

Several processors implement content-based deduplication to eliminate duplicate files when the same media is shared across multiple conversations, albums, or exports. Instead of copying the same file multiple times, each unique file is copied once and all references point to it.

## Supported Processors

- **Google Photos**: Deduplicates photos across albums
- **iMessage**: Deduplicates across conversations and exports
- **Discord**: Deduplicates attachments across channels and DMs
- **Instagram Messages**: Deduplicates media across conversations

---

## Google Photos

The Google Photos processor eliminates duplicate files when photos appear in multiple albums. Instead of copying the same file multiple times, it copies each unique file once and tracks which albums it belongs to.

## How It Works

### Content-Based Hashing

The preprocessor computes an xxHash64 for each media file:

- **xxHash64**: ~10GB/s hashing speed (20x faster than SHA256)
- **Collision probability**: Negligible for this use case
- **Fallback**: Uses SHA256 if xxhash is not installed

### Deduplication Process

1. When processing each album, compute a hash of the file's content
2. Check if this content hash already exists
3. If duplicate:
   - Skip the copy operation
   - Add the album name to the existing file's album list
4. If unique:
   - Copy the file to output
   - Track the hash for future duplicate detection

### Metadata Structure

Files are stored with album associations:

```json
{
  "media_files": [
    {
      "filename": "IMG_001.jpg",
      "albums": ["Vacation 2023", "Best Photos", "Summer Collection"],
      "timestamp": "2023-07-15T14:30:00Z",
      "latitude": 37.7749,
      "longitude": -122.4194
    }
  ]
}
```

Each file appears once with an array of all albums it belongs to.

## Performance

- **Thread-safe**: Uses locks for concurrent album processing
- **Memory efficient**: ~50 bytes per unique file
- **Fast**: Hashing overhead is negligible (~10GB/s)

## Expected Results

For a typical Google Photos export with duplicates across albums:

```text
PREPROCESSING STATISTICS
======================================================================
Total albums processed:                 123
Total media files found:             60,204
Media files copied:                  34,338
Deduplicated files (not copied):    25,866
======================================================================

DEDUPLICATION SUMMARY:
  Unique media files:                 34,338
  Duplicate instances avoided:        25,866
  Space savings: ~222GB
======================================================================
```

## Installation

Install the xxhash dependency:

```bash
pip install xxhash>=3.0.0
```

Or install all dependencies:

```bash
pip install -r requirements.txt
```

## Technical Details

### Filename Format

Generated filenames no longer include album names:

- **Format**: `gphotos-{user}-{date}.ext`
- **Example**: `gphotos-johndoe-20230715_143000.jpg`

### EXIF Metadata

ImageDescription field stores source information without album:

- **Format**: `Source: Google Photos/{username}`
- **Example**: `Source: Google Photos/johndoe`

### Live Photos

Live Photo pairs (JPG+MOV) are deduplicated together, maintaining their association with the same sequence number.

---

## iMessage

The iMessage processor supports content-based deduplication using xxHash64, with cross-export consolidation:

- **Single export**: Deduplicates identical files sent to multiple conversations
- **Multiple exports**: Deduplicates across all exports when processed together
- **Metadata preservation**: All occurrences are tracked with `source_export` field

See [iMessage Export Guide](iMessage-Export#deduplication) for details.

---

## Discord

The Discord preprocessor implements deduplication during attachment downloads. When the same file is shared across multiple channels or DMs, it is downloaded and stored only once.

### How It Works

1. Download attachment from Discord CDN
2. Compute xxHash64 of the downloaded file
3. If hash already exists:
   - Delete the duplicate file
   - Reference the existing file in metadata
4. If unique:
   - Keep the file
   - Register the hash for future duplicate detection

### Expected Results

```text
PREPROCESSING STATISTICS
======================================================================
Total channels processed:              45
Total attachments found:            1,234
Downloads successful:               1,200
Downloads failed:                      34
======================================================================

DEDUPLICATION SUMMARY:
  Unique media files:                 890
  Duplicate instances avoided:        310
======================================================================
```

### Metadata Structure

Duplicate files reference the same filename in the output:

```json
{
  "conversations": {
    "123456789": {
      "title": "general in My Server",
      "messages": [
        {
          "id": 111111111,
          "timestamp": "2024-01-15 10:30:00 UTC",
          "media_files": ["111111111_photo.jpg"]
        }
      ]
    },
    "987654321": {
      "title": "Direct Message with user",
      "messages": [
        {
          "id": 222222222,
          "timestamp": "2024-02-20 14:00:00 UTC",
          "media_files": ["111111111_photo.jpg"]
        }
      ]
    }
  }
}
```

Both messages reference the same file because they have identical content.

---

## Instagram Messages

The Instagram Messages preprocessor implements deduplication during media file copying. When the same image is shared across multiple conversations, it is copied only once.

### How It Works

1. Build catalog of all media files in the export
2. For each referenced media file:
   - Compute xxHash64 of the source file
   - If hash already exists: reference the existing file
   - If unique: copy the file and register the hash

### Expected Results

```text
PREPROCESSING STATISTICS
======================================================================
Total conversations processed:         67
Total messages with media:            432
Total media files found:              456
Media files copied:                   312
======================================================================

DEDUPLICATION SUMMARY:
  Unique media files:                 312
  Duplicate instances avoided:        120
======================================================================
```

### Metadata Structure

Duplicate files reference the same filename:

```json
{
  "conversations": [
    {
      "conversation_id": "user1_12345",
      "conversation_title": "User One",
      "messages": [
        {
          "sender": "user1",
          "timestamp": "2024-01-15 10:30:00",
          "media_files": ["photo_123.jpg"]
        }
      ]
    },
    {
      "conversation_id": "user2_67890",
      "conversation_title": "User Two",
      "messages": [
        {
          "sender": "me",
          "timestamp": "2024-02-20 14:00:00",
          "media_files": ["photo_123.jpg"]
        }
      ]
    }
  ]
}
```

Both conversations reference the same file because the content is identical.

---

## Related Documentation

- [Google Export](Google-Export) - Google Photos export guide
- [iMessage Export](iMessage-Export) - iMessage export guide
- [Discord Export](Discord-Export) - Discord export guide
- [Instagram Export](Instagram-Export) - Instagram export guide
- [Usage](Usage) - Command-line options
