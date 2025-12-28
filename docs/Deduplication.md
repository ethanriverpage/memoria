# Media Deduplication

## Overview

Several processors implement content-based deduplication to eliminate duplicate files when the same media is shared across multiple conversations, albums, or exports. Instead of copying the same file multiple times, each unique file is copied once and all references point to it.

## Deduplication Modes

Memoria supports two levels of deduplication:

| Mode | Scope | Configuration |
|------|-------|---------------|
| **Within-Processor** | Duplicates within a single platform | Always enabled |
| **Cross-Processor** | Duplicates across all platforms | `CROSS_PROCESSOR_DEDUP=true` |

**Within-processor deduplication** (documented below) handles duplicates within a single export - for example, the same photo appearing in multiple Google Photos albums or shared in multiple iMessage conversations.

**Cross-processor deduplication** identifies duplicates across different platforms - for example, a photo that exists in both your Google Photos and Instagram. See [Cross-Processor Deduplication](#cross-processor-deduplication) below for details.

## Supported Processors

- **Google Photos**: Deduplicates photos across albums
- **iMessage**: Deduplicates across conversations and exports
- **Discord**: Deduplicates attachments across channels and DMs
- **Instagram Messages**: Deduplicates media across conversations
- **Snapchat Messages**: Deduplicates media across conversations

---

## Implementation: PreprocessorBase

Within-processor deduplication is implemented via the `PreprocessorBase` class in `processors/preprocess_base.py`. All preprocessors that extend this base class automatically get:

- **Content-based hashing**: xxHash64 for fast, reliable duplicate detection
- **Hash caching**: File hashes are cached in SQLite for faster repeated processing
- **Context merging**: When duplicates are found, metadata from all occurrences is merged
- **Thread-safe operations**: Safe for parallel processing

### Key Methods

```python
from processors.preprocess_base import PreprocessorBase, CopyTask, CopyResult

class MyPreprocessor(PreprocessorBase):
    def process(self) -> bool:
        # Build copy tasks with context
        tasks = [
            CopyTask(
                source_path=file_path,
                dest_filename="output_name.jpg",
                context={"conversation": "Group Chat", "sender": "user"}
            )
            for file_path in media_files
        ]
        
        # Set context merger for handling duplicates
        self.set_context_merger(self._merge_message_context)
        
        # Hash with automatic deduplication (no file copying)
        results = self.hash_with_dedup(tasks)
        
        # Results map source_path -> HashResult with:
        # - source_path: original file location for direct processing
        # - dest_filename: unique filename for output
        # - content_hash: xxHash64 of file
        # - is_duplicate: True if this was a duplicate
        # - primary_source: source path of the primary file for this hash
```

### Context Merging

When duplicates are found, the context merger function combines metadata:

```python
def _merge_message_context(self, existing: Dict, new: Dict) -> None:
    """Merge metadata from duplicate into existing context."""
    # Accumulate messages list for multi-occurrence tracking
    if "messages" not in existing:
        existing["messages"] = []
    existing["messages"].append(new)
```

This ensures that when the same file appears in multiple conversations, all conversation contexts are preserved in the metadata.

---

## Google Photos

The Google Photos processor eliminates duplicate files when photos appear in multiple albums. Instead of copying the same file multiple times, it copies each unique file once and tracks which albums it belongs to.

## How It Works

### Content-Based Hashing

The preprocessor computes an xxHash64 for each media file:

- **xxHash64**: ~10GB/s hashing speed (20x faster than SHA256)
- **Collision probability**: Negligible for this use case

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

- **Format**: `Source: Google/{username}`
- **Example**: `Source: Google/johndoe`

### Live Photos

Live Photo pairs (image + video) are deduplicated atomically. When the deduplication service analyzes catalogs, it builds a mapping of Live Photo video paths to their associated image content hashes. During deduplication filtering:

1. **Video component lookup**: When processing a video file, the service checks if it's a known Live Photo video
2. **Follow image decision**: If found, the video follows the same primary/secondary decision as its image
3. **Atomic pairing**: If the image is primary for a processor, the video is also kept; if secondary, the video is skipped

This ensures Live Photo pairs remain intact across cross-processor deduplication. The `live_photo_video` metadata field in catalog entries enables this linking.

---

## iMessage

The iMessage processor supports content-based deduplication using xxHash64, with cross-export consolidation:

- **Single export**: Deduplicates identical files sent to multiple conversations
- **Multiple exports**: Deduplicates across all exports when processed together
- **Metadata preservation**: All occurrences are tracked with `source_export` field

See [iMessage Export Guide](iMessage-Export#deduplication) for details.

---

## Discord

The Discord preprocessor implements deduplication during attachment downloads using the `PreprocessorBase.register_downloaded_file()` method. When the same file is shared across multiple channels or DMs, it is downloaded and stored only once.

### How It Works

1. Download attachment from Discord CDN
2. Call `register_downloaded_file()` which:
   - Computes xxHash64 of the downloaded file
   - Checks the hash registry for duplicates
   - If duplicate: deletes the file and merges metadata into the existing entry
   - If unique: registers the file and its metadata
3. Build final metadata with deduplicated file references

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

The Instagram Messages preprocessor implements deduplication using `PreprocessorBase.hash_with_dedup()`. When the same image is shared across multiple conversations, it is hashed only once and processed once during the processing phase.

### How It Works

1. Build catalog of all media files in the export with conversation context
2. Create `CopyTask` objects for each file with metadata
3. Call `hash_with_dedup()` which:
   - Hashes all files in parallel using xxHash64
   - Groups files by content hash
   - Merges conversation metadata for duplicates
   - Records source_path for each unique file (no intermediate copying)

### Expected Results

```text
PREPROCESSING STATISTICS
======================================================================
Total conversations processed:         67
Total messages with media:            432
Total media files found:              456
Unique media files:                   312
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

## Cross-Processor Deduplication

Cross-processor deduplication identifies and eliminates duplicate files across different platform exports. The same photo might exist in your Google Photos, Instagram stories, iMessage conversations, and Discord channels. This feature ensures each unique file is stored only once, with metadata merged from all sources.

### When to Use Cross-Processor Deduplication

This feature is ideal when:

- Processing exports from multiple platforms that may contain the same media
- You've shared the same photos across different services
- You want to consolidate media from multiple sources into a single library
- Storage space is a concern

### Quick Start

Enable cross-processor deduplication by setting the environment variable:

```bash
CROSS_PROCESSOR_DEDUP=true ./memoria.py --originals /path/to/exports -o /output
```

Or add to your `.env` file:

```bash
CROSS_PROCESSOR_DEDUP=true
```

### How It Works

When enabled, Memoria processes exports in three phases:

```
Phase 1: CATALOG
  - Scan all exports without copying files
  - Compute content hashes (xxHash64) for each media file
  - Optionally compute perceptual hashes for fuzzy matching
  - Build catalog of all files with metadata

Phase 2: DEDUP ANALYSIS
  - Group files by exact content hash
  - For unmatched files, check perceptual similarity
  - Validate matches using metadata (GPS, timestamp, dimensions)
  - Select highest quality version as primary
  - Generate dedup_manifest.json

Phase 3: PROCESS
  - Process each export using the manifest
  - Primary files: copy and embed merged metadata
  - Secondary files: skip copying, record reference only
```

### Duplicate Detection Strategy

The system uses a hybrid matching strategy:

1. **Exact Hash (xxHash64)**: Files with identical content have identical hashes
2. **Perceptual Hash (dhash)**: Catches re-encoded or slightly modified versions
3. **Metadata Validation**: Confirms matches using GPS, timestamp, and dimensions

### What Counts as a Duplicate

| Scenario | Duplicate? |
|----------|-----------|
| Exact same file shared on multiple platforms | Yes |
| Same photo re-encoded (HEIC to JPG) | Maybe (perceptual match) |
| Photo with filter applied | No |
| Significantly cropped version | No |
| Screenshot containing the photo | No |

### Configuration

#### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CROSS_PROCESSOR_DEDUP` | `false` | Enable cross-processor deduplication |
| `DEDUP_USE_PERCEPTUAL` | `true` | Enable perceptual hashing for fuzzy matching |
| `DEDUP_PERCEPTUAL_THRESHOLD` | `0.92` | Similarity threshold (0.0-1.0) |
| `DEDUP_DATABASE_PATH` | `~/.memoria/dedup.db` | SQLite database for hash persistence |
| `DEDUP_DESC_MAX_PER_SOURCE` | `250` | Max chars per source in ImageDescription |
| `DEDUP_DESC_MAX_TOTAL` | `1500` | Max total chars for ImageDescription |

#### Validation Thresholds

| Variable | Default | Description |
|----------|---------|-------------|
| `DEDUP_GPS_DISTANCE` | `500` | Max GPS distance (meters) for duplicate validation |
| `DEDUP_ASPECT_RATIO` | `0.10` | Max aspect ratio difference (10%) |

### Supported Processors

| Processor | Cross-Processor Dedup |
|-----------|----------------------|
| Google Photos | Yes |
| iMessage | Yes |
| Discord | Yes |
| Instagram Messages | Yes |
| Instagram Public Media | Yes |
| Instaloader | Yes |
| Snapchat Memories | Yes |
| Snapchat Messages | Yes |
| Google Chat | Yes |
| Google Voice | Yes |

### Output Structure with Cross-Processor Deduplication

Output uses the `Platform/User/Service` structure:

```
/output/
├── Google/
│   └── username/
│       └── photos/
│           └── IMG_001.jpg      # Primary copy (highest quality)
├── Instagram/
│   └── username/
│       └── messages/
│           └── unique_to_ig.jpg # Only files unique to Instagram
├── iMessage/
│   └── device_id/
│       └── ...                  # iMessage files
└── dedup_manifest.json          # Global deduplication state
```

### Metadata Merging

When duplicates are found, metadata from all sources is merged:

| Field | Strategy |
|-------|----------|
| Timestamp | Earliest with timezone preference |
| GPS | First non-zero coordinates |
| Camera info | Most detailed |
| People tags | Union of all tags |
| Albums | All albums with source attribution |
| Provenance | All sources recorded |

### ImageDescription Format

The merged description includes all sources:

```
Source: Google/johndoe | Vacation 2023, Summer Highlights
+Instagram/johndoe: Story "Beach day with the squad!..." (2024-01-15)
+iMessage/iph13: John [Family] "Everyone needs to see this!" (2024-01-16)
```

### Performance

| Operation | Speed |
|-----------|-------|
| xxHash64 | ~10 GB/s |
| dhash (image) | ~5-20 ms |
| ffmpeg frame extract | ~50-100 ms |

For 10,000 files (~100GB):

- Catalog phase: ~70 seconds
- Analysis phase: ~10-30 seconds

### Limitations

1. **Format Conversion**: HEIC to JPG conversions may not match (different content)
2. **Edited Photos**: Filters, crops, and edits create different files
3. **Re-encoded Videos**: Transcoded videos have different hashes
4. **Metadata Conflicts**: When sources disagree, earliest/most detailed wins

### Troubleshooting Cross-Processor Deduplication

**"No dedup-capable processors found"**

- Ensure exports are from supported platforms
- Check that export directories are detected correctly

**High false positive rate**

- Lower `DEDUP_PERCEPTUAL_THRESHOLD` (e.g., 0.95)
- Increase validation thresholds for GPS/timestamp

**Missing duplicates**

- Enable perceptual hashing: `DEDUP_USE_PERCEPTUAL=true`
- Lower threshold if files are similar but not identical

---

## Related Documentation

- [Google Export](Google-Export) - Google Photos export guide
- [iMessage Export](iMessage-Export) - iMessage export guide
- [Discord Export](Discord-Export) - Discord export guide
- [Instagram Export](Instagram-Export) - Instagram export guide
- [Usage](Usage) - Command-line options
