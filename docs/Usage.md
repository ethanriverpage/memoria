# Usage Guide

Complete reference for using Memoria to process social media exports.

## Quick Reference

| Task | Command |
|------|---------|
| Process single export | `./memoria.py /path/to/export` |
| Process with custom output | `./memoria.py /path/to/export -o /output` |
| Process multiple exports | `./memoria.py --originals /path/to/parent` |
| Process without upload | `./memoria.py /path/to/export --skip-upload` |
| Upload only (no processing) | `./memoria.py /original --upload-only /processed` |
| See available processors | `./memoria.py --list-processors` |
| Enable verbose logging | `./memoria.py /path/to/export --verbose` |
| Control parallelism | `./memoria.py /path/to/export --workers 8` |
| Process exports in parallel | `./memoria.py --originals /exports --parallel-exports 4` |

## Table of Contents

1. [Basic Usage](#basic-usage)
2. [Command-Line Options](#command-line-options)
3. [Processing Modes](#processing-modes)
4. [Output Organization](#output-organization)
5. [Parallel Processing](#parallel-processing)
6. [Immich Integration](#immich-integration)
7. [Environment Variables](#environment-variables)
8. [Advanced Usage](#advanced-usage)

## Basic Usage

### Single Export

Process a single export directory:

```bash
./memoria.py /path/to/export
```

With custom output directory:

```bash
./memoria.py /path/to/export -o /path/to/output
```

### Multiple Exports

Process multiple exports from a parent directory:

```bash
./memoria.py --originals /path/to/all-exports -o /path/to/output
```

This will process all subdirectories in `/path/to/all-exports` that match supported formats.

## Command-Line Options

### Required Arguments

**Path to export** (positional argument):

```bash
./memoria.py /path/to/export
```

OR

**Multiple exports** (using `--originals`):

```bash
./memoria.py --originals /path/to/parent-directory
```

### Output Options

**`-o, --output PATH`**
Specify output directory for processed files. If not provided, defaults to `./output`.

```bash
./memoria.py /path/to/export -o /path/to/output
```

### Processing Options

**`-v, --verbose`**
Enable verbose logging with DEBUG output and log file creation.

```bash
./memoria.py /path/to/export --verbose
```

See [Logging](Logging) for details.

**`-w, --workers N`**
Number of parallel workers for processing media files within a single export.

Default: CPU count - 1

```bash
./memoria.py /path/to/export --workers 8
```

**`--parallel-exports N`**
Process N exports simultaneously when using `--originals`.

```bash
./memoria.py --originals /path/to/exports --parallel-exports 2
```

See [Parallel Processing](Parallel-Processing) for details.

**`--list-processors`**
List all available processors and exit.

```bash
./memoria.py --list-processors
```

### Upload Options

**`--skip-upload`**
Process files but don't upload to Immich (even if configured).

```bash
./memoria.py /path/to/export --skip-upload
```

**`--upload-only PROCESSED_DIR`**
Skip processing and only upload previously processed files.

```bash
./memoria.py /path/to/original/export --upload-only /path/to/processed/output
```

See [Upload Only Mode](Upload-Only-Mode) for details.

**`--immich-url URL`**
Immich server URL (alternative to environment variable).

```bash
./memoria.py /path/to/export --immich-url https://immich.example.com
```

**`--immich-key KEY`**
Immich API key (alternative to environment variable).

```bash
./memoria.py /path/to/export --immich-key your_api_key_here
```

**`--immich-concurrency N`**
Number of concurrent uploads to Immich.

Default: 4

```bash
./memoria.py /path/to/export --immich-concurrency 8
```

## Processing Modes

### Auto-Detection Mode (Default)

Memoria automatically detects which processors match your export structure:

```bash
./memoria.py /path/to/export
```

The unified processor:

1. Scans the input directory
2. Identifies applicable processors based on directory structure
3. Runs all matching processors
4. Creates separate output directories for each processor

### List Available Processors

See which processors are available:

```bash
./memoria.py --list-processors
```

Output example:

```
Available processors:
  - GooglePhotosProcessor (priority: 100)
  - GoogleChatProcessor (priority: 90)
  - GoogleVoiceProcessor (priority: 85)
  - DiscordProcessor (priority: 70)
  - InstagramMessagesProcessor (priority: 80)
  - InstagramPublicMediaProcessor (priority: 75)
  - InstagramOldPublicMediaProcessor (priority: 70)
  - SnapchatMemoriesProcessor (priority: 65)
  - SnapchatMessagesProcessor (priority: 60)
```

### Upload-Only Mode

Process exports separately from uploading:

1. **First**: Process without uploading

```bash
./memoria.py /path/to/export -o /path/to/output --skip-upload
```

2. **Later**: Upload processed files

```bash
./memoria.py /path/to/export --upload-only /path/to/output
```

This is useful for:

- Processing on one machine, uploading from another
- Processing multiple exports before uploading
- Re-uploading after Immich configuration changes

## Output Organization

### Single Export Output

When processing a single export:

```
/path/to/output/
├── Google Photos/          # If Google Photos found
│   └── username/
│       └── processed files...
├── Google Chat/            # If Google Chat found
│   └── username/
│       └── processed files...
├── messages/               # If Instagram Messages found
│   └── username/
│       └── conversations...
└── ...
```

Each processor creates its own subdirectory to prevent conflicts.

### Multiple Exports Output

When using `--originals`:

```
/path/to/output/
├── google-user1-20250526/
│   ├── Google Photos/
│   │   └── user1/
│   └── Google Chat/
│       └── user1/
├── instagram-user2-20251007/
│   └── messages/
│       └── user2/
└── snapchat-user3-20251007/
    ├── Snapchat Memories/
    │   └── user3/
    └── Snapchat Messages/
        └── user3/
```

Each export gets its own directory, maintaining the per-processor organization within.

### Processed Filenames

Files are renamed to include metadata:

**Format**: `{platform}_{username}_{original_filename}_{timestamp}.{ext}`

Examples:

```
google-photos_john.doe_IMG_1234_20230115_103045.jpg
instagram_jane_doe_photo_20230220_142211.jpg
snapchat_user123_memory_20230310_151530.mp4
```

## Parallel Processing

### Within-Export Parallelism

Control workers processing files within a single export:

```bash
./memoria.py /path/to/export --workers 8
```

Higher values = faster processing but more CPU/memory usage.

**Recommendations**:

- Default (CPU count - 1): Good for most cases
- Lower (2-4): For systems with limited resources or during other tasks
- Higher (8-16): For powerful systems processing large exports

### Multi-Export Parallelism

Process multiple exports simultaneously:

```bash
./memoria.py --originals /path/to/exports --parallel-exports 2
```

This processes 2 exports at once, each using its own worker pool.

**Recommendations**:

- Sequential (default): Safest, lowest memory usage
- 2-3 parallel: Good balance for most systems
- 4+ parallel: Only for powerful systems with lots of RAM

See [Parallel Processing](Parallel-Processing) for detailed guidance.

## Immich Integration

### Configuration

Configure Immich via environment variables in `.env`:

```bash
IMMICH_INSTANCE_URL=https://immich.example.com
IMMICH_API_KEY=your_api_key_here
IMMICH_UPLOAD_CONCURRENCY=4
IMMICH_IGNORE_PATTERNS=**/issues/**,**/needs matching/**
```

Or via command-line arguments:

```bash
./memoria.py /path/to/export \
  --immich-url https://immich.example.com \
  --immich-key your_api_key_here \
  --immich-concurrency 8
```

### Getting an Immich API Key

1. Log into your Immich instance
2. Go to **Account Settings** > **API Keys**
3. Click **New API Key**
4. Give it a name (e.g., "Memoria")
5. Copy the key and save it

### Album Organization

Uploads are automatically organized into albums:

| Platform | Album Path |
|----------|-----------|
| Discord | `Discord/{username}` |
| Google Photos | `Google Photos/{username}` |
| Google Chat | `Google Chat/{username}` |
| Google Voice | `Google Voice/{username}` |
| iMessage | `iMessage/{device}` |
| Instagram Messages | `Instagram/{username}/messages` |
| Instagram Posts | `Instagram/{username}/posts` |
| Snapchat Memories | `Snapchat/{username}/memories` |
| Snapchat Messages | `Snapchat/{username}/messages` |

### Ignore Patterns

Exclude certain files from upload using glob patterns:

```bash
IMMICH_IGNORE_PATTERNS=**/issues/**,**/needs matching/**,**/drafts/**
```

Default patterns:

- `**/issues/**`: Files in "issues" directories
- `**/needs matching/**`: Files needing manual review

See [Immich Upload](Immich-Upload) for details.

## Environment Variables

Create a `.env` file in the project root:

```bash
# Immich Configuration
IMMICH_INSTANCE_URL=https://immich.example.com
IMMICH_API_KEY=your_api_key_here
IMMICH_UPLOAD_CONCURRENCY=4
IMMICH_IGNORE_PATTERNS=**/issues/**,**/needs matching/**

# Processing Configuration
TEMP_DIR=../pre
DISABLE_TEMP_CLEANUP=0
```

### Available Variables

**`IMMICH_INSTANCE_URL`**
Your Immich server URL.

**`IMMICH_API_KEY`**
Your Immich API key.

**`IMMICH_UPLOAD_CONCURRENCY`**
Number of concurrent uploads (default: 4).

**`IMMICH_IGNORE_PATTERNS`**
Comma-separated glob patterns to exclude from upload.

**`TEMP_DIR`**
Directory for temporary preprocessing files (default: `../pre`).

**`DISABLE_TEMP_CLEANUP`**
Set to `1`, `true`, or `yes` to preserve temporary files after processing (useful for debugging).

## Advanced Usage

### Debugging

Enable verbose logging and preserve temporary files:

```bash
DISABLE_TEMP_CLEANUP=1 ./memoria.py /path/to/export --verbose
```

This creates:

- Detailed log file in `logs/` directory
- Preserves temporary preprocessing data
- Shows DEBUG-level console output

### Custom Temporary Directory

Use a different location for temporary files:

```bash
TEMP_DIR=/mnt/fast-storage/temp ./memoria.py /path/to/export
```

Useful for:

- Using faster storage (SSD) for temp files
- Separating temp data from main storage
- Systems with limited space on default temp location

### Processing Specific Subdirectories

If you have a mixed export structure, you can process specific subdirectories:

```bash
# Process only Google Photos from a full Google Takeout
./memoria.py /path/to/google-export/Google\ Photos -o /path/to/output
```

### Batch Processing Script

Process multiple exports with different settings:

```bash
#!/bin/bash

# Process each export with custom settings
./memoria.py /exports/google-user1-20250526 -o /output --workers 8
./memoria.py /exports/instagram-user2-20251007 -o /output --workers 4
./memoria.py /exports/snapchat-user3-20251007 -o /output --workers 6

# Upload all at once
./memoria.py /exports --upload-only /output
```

### Dry Run (Checking What Would Be Processed)

Use `--list-processors` with verbose mode to see detection:

```bash
./memoria.py /path/to/export --list-processors --verbose
```

While there's no formal dry-run mode, you can:

1. Process with `--skip-upload` first
2. Review the output
3. Delete and reprocess if needed

## Performance Tips

1. **Workers**: Start with default, increase if CPU usage is low
2. **Temp Directory**: Use SSD storage for `TEMP_DIR` if available
3. **Upload Concurrency**: Increase for faster uploads if network can handle it
4. **Parallel Exports**: Only use if you have sufficient RAM (2-4 GB per export)
5. **Disk Space**: Ensure 2x the export size is available for temporary files

## Common Workflows

### First-Time Processing

```bash
# 1. List processors to verify detection
./memoria.py /path/to/export --list-processors

# 2. Process with verbose logging
./memoria.py /path/to/export -o /output --verbose

# 3. Review output and logs
ls -R /output
cat logs/memoria_*.log
```

### Batch Processing Multiple Accounts

```bash
# Process all exports in parallel
./memoria.py --originals /all-exports -o /output --parallel-exports 2
```

### Separate Processing and Uploading

```bash
# Process everything first
./memoria.py --originals /exports -o /output --skip-upload

# Review and organize output
# ...

# Upload when ready
./memoria.py /exports --upload-only /output
```

## Related Documentation

- [Getting Started](Getting-Started) - Initial setup guide
- [Logging](Logging) - Logging configuration and verbose mode
- [Parallel Processing](Parallel-Processing) - Parallel processing guide
- [Upload Only Mode](Upload-Only-Mode) - Upload-only mode details
- [Upload Queuing](Upload-Queuing) - Upload queuing for parallel processing
- [Immich Upload](Immich-Upload) - Immich upload configuration
- [Google Export](Google-Export) - Google-specific guide
- [iMessage Export](iMessage-Export) - iMessage-specific guide
- [Discord Export](Discord-Export) - Discord-specific guide
- [Instagram Export](Instagram-Export) - Instagram-specific guide
- [Snapchat Export](Snapchat-Export) - Snapchat-specific guide
