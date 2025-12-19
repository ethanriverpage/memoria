# Cross-Processor Deduplication

## Overview

Cross-processor deduplication identifies and eliminates duplicate files across different platform exports. The same photo might exist in your Google Photos, Instagram stories, iMessage conversations, and Discord channels. This feature ensures each unique file is stored only once, with metadata merged from all sources.

## When to Use

Cross-processor deduplication is ideal when:

- Processing exports from multiple platforms that may contain the same media
- You've shared the same photos across different services
- You want to consolidate media from multiple sources into a single library
- Storage space is a concern

## Quick Start

Enable cross-processor deduplication by setting the environment variable:

```bash
CROSS_PROCESSOR_DEDUP=true ./memoria.py --originals /path/to/exports -o /output
```

Or add to your `.env` file:

```bash
CROSS_PROCESSOR_DEDUP=true
```

## How It Works

### Phased Processing

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
  - Determine primary source (earliest timestamp)
  - Generate dedup_manifest.json

Phase 3: PROCESS
  - Process each export using the manifest
  - Primary files: copy and embed merged metadata
  - Secondary files: skip copying, record reference only
```

### Duplicate Detection

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

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CROSS_PROCESSOR_DEDUP` | `false` | Enable cross-processor deduplication |
| `DEDUP_USE_PERCEPTUAL` | `true` | Enable perceptual hashing for fuzzy matching |
| `DEDUP_PERCEPTUAL_THRESHOLD` | `0.92` | Similarity threshold (0.0-1.0) |
| `DEDUP_DATABASE_PATH` | `~/.memoria/dedup.db` | SQLite database for hash persistence |
| `DEDUP_DESC_MAX_PER_SOURCE` | `250` | Max chars per source in ImageDescription |
| `DEDUP_DESC_MAX_TOTAL` | `1500` | Max total chars for ImageDescription |
| `DEDUP_MSG_TRUNCATE` | `200` | Message content truncation length |

### Validation Thresholds

| Variable | Default | Description |
|----------|---------|-------------|
| `DEDUP_GPS_DISTANCE` | `500` | Max GPS distance (meters) for duplicate validation |
| `DEDUP_TIMESTAMP_DELTA` | `3600` | Max timestamp difference (seconds) |
| `DEDUP_ASPECT_RATIO` | `0.10` | Max aspect ratio difference (10%) |

### Example Configuration

```bash
# .env file
CROSS_PROCESSOR_DEDUP=true
DEDUP_USE_PERCEPTUAL=true
DEDUP_PERCEPTUAL_THRESHOLD=0.92
DEDUP_DATABASE_PATH=/path/to/custom/dedup.db
```

## Supported Processors

The following processors support cross-processor deduplication:

| Processor | Support | Notes |
|-----------|---------|-------|
| Google Photos | Yes | Photos and videos across albums |
| iMessage | Yes | Media from conversations |
| iMessage (iMazing) | Yes | Media from iMazing exports |
| Discord | Yes | Attachments from channels/DMs |
| Instagram Messages | Yes | Media from conversations |
| Instagram Public Media | Yes | Posts, stories, reels |
| Snapchat Memories | Yes | Saved snaps and stories |
| Snapchat Messages | Yes | Media from conversations |
| Google Chat | No | Text-focused, minimal media |
| Google Voice | No | SMS/call records |

## Output Structure

### With Deduplication Enabled

```
/output/
├── google_photos/
│   └── media/
│       └── IMG_001.jpg          # Primary copy (highest quality)
├── instagram/
│   └── media/
│       └── unique_to_insta.jpg  # Only files unique to Instagram
├── imessage/
│   └── metadata.json            # References to deduplicated files
├── discord/
│   └── metadata.json            # References to deduplicated files
└── dedup_manifest.json          # Global deduplication state
```

### Dedup Manifest

The manifest tracks all duplicate relationships:

```json
{
  "version": "1.0",
  "created": "2024-01-20T10:00:00Z",
  "statistics": {
    "total_files_scanned": 15000,
    "unique_files": 12000,
    "duplicates_found": 3000,
    "space_saved_bytes": 5368709120
  },
  "duplicates": {
    "abc123def456...": {
      "primary": {
        "processor": "Google Photos",
        "source_path": "/exports/google/photo.jpg",
        "output_path": "/output/google_photos/media/photo.jpg"
      },
      "secondary": [
        {
          "processor": "Instagram",
          "source_path": "/exports/instagram/story.jpg",
          "context": {"type": "story", "posted": "2024-01-15"}
        }
      ],
      "merged_metadata": {
        "timestamp": "2024-01-14T10:30:00Z",
        "gps": {"lat": 37.7749, "lon": -122.4194},
        "albums": ["Vacation 2024", "Best Photos"],
        "people": ["John", "Jane"]
      }
    }
  }
}
```

## Metadata Merging

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
Source: Google Photos/johndoe | Vacation 2023, Summer Highlights
+Instagram/johndoe: Story "Beach day with the squad!..." (2024-01-15)
+iMessage/iph13: John [Family] "Everyone needs to see this!" (2024-01-16)
```

## Performance

### Hash Performance

| Operation | Speed |
|-----------|-------|
| xxHash64 | ~10 GB/s |
| dhash (image) | ~5-20 ms |
| ffmpeg frame extract | ~50-100 ms |

### Typical Results

For 10,000 files (~100GB):

- Catalog phase: ~70 seconds
- Analysis phase: ~10-30 seconds
- Total overhead: ~2 minutes

### Memory Usage

- Hash database: ~50 bytes per unique file
- In-memory catalog: ~500 bytes per file during analysis

## Database Persistence

The SQLite database stores hashes for incremental processing:

```sql
-- Schema
CREATE TABLE files (
    id INTEGER PRIMARY KEY,
    content_hash TEXT NOT NULL,
    perceptual_hash TEXT,
    processor TEXT NOT NULL,
    source_path TEXT NOT NULL,
    is_primary BOOLEAN DEFAULT FALSE,
    metadata JSON,
    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(content_hash, source_path)
);
```

Benefits:
- Skip re-hashing files from previous runs
- Track duplicate relationships across sessions
- Support incremental processing of new exports

## Limitations

1. **Format Conversion**: HEIC to JPG conversions may not match (different content)
2. **Edited Photos**: Filters, crops, and edits create different files
3. **Re-encoded Videos**: Transcoded videos have different hashes
4. **Metadata Conflicts**: When sources disagree, earliest/most detailed wins

## Troubleshooting

### Common Issues

**"No dedup-capable processors found"**
- Ensure exports are from supported platforms
- Check that export directories are detected correctly

**High false positive rate**
- Lower `DEDUP_PERCEPTUAL_THRESHOLD` (e.g., 0.95)
- Increase validation thresholds for GPS/timestamp

**Missing duplicates**
- Enable perceptual hashing: `DEDUP_USE_PERCEPTUAL=true`
- Lower threshold if files are similar but not identical

### Debug Mode

Enable verbose logging to see deduplication decisions:

```bash
CROSS_PROCESSOR_DEDUP=true ./memoria.py --originals /exports -o /output --verbose
```

## Related Documentation

- [Deduplication](Deduplication) - Within-processor deduplication
- [Usage](Usage) - Command-line options and environment variables
- [Parallel Processing](Parallel-Processing) - Processing multiple exports
- [Design Decisions](Design-Decisions) - Architecture rationale

