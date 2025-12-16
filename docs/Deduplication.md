# Google Photos Deduplication

## Overview

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

## Other Processors with Deduplication

### iMessage

The iMessage processor also supports content-based deduplication using xxHash64, with cross-export consolidation:

- **Single export**: Deduplicates identical files sent to multiple conversations
- **Multiple exports**: Deduplicates across all exports when processed together
- **Metadata preservation**: All occurrences are tracked with `source_export` field

See [iMessage Export Guide](iMessage-Export#deduplication) for details.

## Related Documentation

- [Google Export](Google-Export) - Google Photos export guide
- [iMessage Export](iMessage-Export) - iMessage export guide
- [Usage](Usage) - Command-line options
