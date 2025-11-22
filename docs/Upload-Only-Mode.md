# Upload-Only Mode

Upload previously processed exports to Immich without re-processing.

## Usage

```bash
./memoria.py <original_export_dir> --upload-only <processed_output_dir>
```

### Arguments

- `<original_export_dir>`: Original unprocessed export (for processor detection and username extraction)
- `--upload-only <processed_output_dir>`: Path to processed output directory

### Requirements

- Valid Immich configuration (URL and API key)
- Original export directory must exist
- Processed output directory must exist
- Cannot be used with `--originals` or `--skip-upload`

## How It Works

1. Detects which processor(s) match the original export structure
2. Maps processors to their expected output subdirectories
3. Extracts username from original export directory name
4. Uploads each output directory with correct album naming

## Examples

### Basic Usage

```bash
# Original processing
./memoria.py instagram-johndoe-20251027 -o /mnt/media/processed/output1

# Later, upload-only mode
./memoria.py instagram-johndoe-20251027 --upload-only /mnt/media/processed/output1
```

### With Custom Immich Settings

```bash
./memoria.py google-janedoe-20251027 \
  --upload-only /mnt/media/processed/output2 \
  --immich-url https://immich.example.com \
  --immich-key your_api_key_here \
  --immich-concurrency 8
```

## Output Structure

Expected processed output structure:

### Instagram Export

```
processed_output/
├── messages/              # Instagram Messages
└── public-media/          # Instagram Public Media
    ├── posts/
    ├── archived_posts/
    ├── profile/
    ├── stories/
    └── reels/
```

### Google Export

```
processed_output/
├── Google Photos/         # Google Photos
├── Google Chat/           # Google Chat
└── Google Voice/          # Google Voice
```

### Snapchat Export

```
processed_output/          # Base directory used directly
├── 2024-01-15/
├── 2024-01-16/
└── media/
```

## Album Naming

Albums use the same naming scheme as normal processing:

- **Instagram**: `Instagram/{username}/{type}` (messages, posts, stories, etc.)
- **Google Photos**: `Google Photos/{username}`
- **Google Chat**: `Google Chat/{username}`
- **Google Voice**: `Google Voice/{username}`
- **Snapchat Memories**: `Snapchat/{username}/memories`
- **Snapchat Messages**: `Snapchat/{username}/messages`

## Troubleshooting

### "No processors matched original export directory"

- Ensure directory is the actual original export (not processed output)
- Verify export structure matches a supported format

### "Expected output directory does not exist"

- Processor may have failed during original processing
- Output structure may have been manually modified
- Wrong processed output directory specified

### "Upload-only mode requires valid Immich configuration"

- Set `IMMICH_INSTANCE_URL` and `IMMICH_API_KEY` in environment or `.env`
- Or provide `--immich-url` and `--immich-key` arguments
- Verify Immich server is accessible

## Related Documentation

- [Immich Upload](Immich-Upload) - Immich upload configuration
- [Upload Queuing](Upload-Queuing) - Parallel processing upload queuing
- [Usage](Usage) - Command-line options

