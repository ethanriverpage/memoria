## Immich Upload Integration

This project can automatically upload processed output to Immich using the Immich CLI. Uploads are enabled by default; pass `--skip-upload` to disable.

### Requirements

- Immich CLI installed (`npm i -g @immich/cli`) or Docker variant
- Immich instance URL and API key

See the official documentation for CLI features and flags: [Immich CLI](https://docs.immich.app/features/command-line-interface/)

### Configuration

Values can come from, in order of precedence:

1. CLI flags
2. Environment variables
3. `.env` file (loaded from `./.env` or `--env-file` path)

Supported keys:

- `--immich-url` / `IMMICH_INSTANCE_URL`
- `--immich-key` / `IMMICH_API_KEY`
- `--immich-concurrency` / `IMMICH_UPLOAD_CONCURRENCY` (default 4)
- `IMMICH_SKIP_HASH` (boolean, default true) - Skip hash computation during upload
- `IMMICH_IGNORE_PATTERNS` (comma-separated glob patterns) - Folders/files to exclude from upload
- `--immich-tag-platforms` / `--no-immich-tag-platforms` / `IMMICH_TAG_PLATFORMS` (boolean, default true when Immich enabled) - Apply platform tags to uploaded assets

Example `.env`:

```
IMMICH_INSTANCE_URL=http://192.168.1.216:2283/api
IMMICH_API_KEY=YOUR_API_KEY
IMMICH_UPLOAD_CONCURRENCY=4
IMMICH_SKIP_HASH=true
IMMICH_IGNORE_PATTERNS=issues/**,**/*matching/**
IMMICH_TAG_PLATFORMS=true
```

### Default Ignore Patterns

By default, the following folders are automatically excluded from uploads:

- `issues/**` - The top-level "issues" folder
- `**/*matching/**` - Folders ending with "matching" (e.g., "needs matching")

You can override these defaults by setting `IMMICH_IGNORE_PATTERNS` in your `.env` file or environment variables.

### Usage

Default (uploads enabled):

```bash
python3 memoria.py /path/to/export -o /mnt/media/processed \
  --immich-url http://immich.local:2283/api --immich-key <KEY>
```

Using `.env` only:

```bash
python3 memoria.py /path/to/export -o /mnt/media/processed
```

Skip uploading:

```bash
python3 memoria.py /path/to/export --skip-upload
```

### Album naming

Albums follow a `Platform/username[/service]` structure:

- Discord: `Discord/{USERNAME}`
- Google Chat: `Google/{USERNAME}/chat`
- Google Photos: `Google/{USERNAME}/photos`
- Google Voice: `Google/{USERNAME}/voice`
- iMessage: `iMessage/{device}` (extracted from directory name, e.g., `iMessage/mac`, `iMessage/iph13p`; includes iMazing-format exports)
- Instagram Messages: `Instagram/{USERNAME}/messages`
- Instagram Public Media: `Instagram/{USERNAME}/{service}` where service is one of: `posts`, `archived_posts`, `profile`, `stories`, `reels`, `other` (Instaloader format goes to `posts`)
- Snapchat Messages: `Snapchat/{USERNAME}/messages`
- Snapchat Memories: `Snapchat/{USERNAME}/memories`

Usernames are extracted from the export directory name using the `platform-username-YYYYMMDD` convention.

---

## Upload-Only Mode

Upload previously processed exports to Immich without re-processing.

### Usage

```bash
./memoria.py --upload-only <processed_output_dir>
```

#### Arguments

- `--upload-only <processed_output_dir>`: Path to processed output directory (required)

#### Requirements

- Valid Immich configuration (URL and API key)
- Processed output directory must exist with Platform/Username/Service structure
- Cannot be used with `--originals` or `--skip-upload`

### How It Works

1. Scans the processed output directory for Platform/Username/Service structure
2. Builds album names directly from the directory structure
3. Uploads each discovered target with the correct album name

### Examples

```bash
# Process with cross-processor dedup
./memoria.py instagram-johndoe-20251027 -o /mnt/media/processed

# Later, upload using directory structure scanning
./memoria.py --upload-only /mnt/media/processed

# The above scans the structure and finds:
#   /mnt/media/processed/Instagram/johndoe/messages -> Instagram/johndoe/messages
#   /mnt/media/processed/Instagram/johndoe/posts    -> Instagram/johndoe/posts
```

### Directory Structure

Upload-only mode expects a Platform/Username/Service directory structure:

```
processed_output/
  Google/
    johndoe/
      photos/     -> Album: Google/johndoe/photos
      chat/       -> Album: Google/johndoe/chat
  Instagram/
    janedoe/
      messages/   -> Album: Instagram/janedoe/messages
      posts/      -> Album: Instagram/janedoe/posts
  Discord/
    user123/      -> Album: Discord/user123
```

### Troubleshooting Upload-Only Mode

**"No Platform/Username/Service structure found"**

- Verify the processed output has the expected structure (Platform/username/service)
- Check that platform names are recognized: Google, Instagram, Snapchat, Discord, iMessage

**"Upload-only mode requires valid Immich configuration"**

- Set `IMMICH_INSTANCE_URL` and `IMMICH_API_KEY` in environment or `.env`
- Or provide `--immich-url` and `--immich-key` arguments

---

## Upload Queuing in Parallel Mode

When using `--parallel` with Immich uploads enabled, upload queuing automatically ensures:

- **Processing happens in parallel** - Multiple exports processed simultaneously
- **Uploads happen sequentially** - One export uploads at a time
- **Concurrent operation** - Uploads begin as soon as first export completes

This maximizes efficiency while preventing network saturation and maintaining upload reliability.

### Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Export 1   │     │  Export 2   │     │  Export 3   │
│  Processing │────▶│  Processing │────▶│  Processing │
└─────────────┘     └─────────────┘     └─────────────┘
      │                    │                    │
      ▼                    ▼                    ▼
┌───────────────────────────────────────────────────┐
│           Upload Queue (shared)                   │
└───────────────────────────────────────────────────┘
                      │
                      ▼
            ┌──────────────────┐
            │  Upload Worker   │
            │  (one at a time) │
            └──────────────────┘
```

### How to Use

Upload queuing is **automatically enabled** with `--parallel`:

```bash
./memoria.py --originals /path/to/exports \
  -o /output \
  --parallel \
  --immich-url http://localhost:2283 \
  --immich-key YOUR_API_KEY
```

### Benefits

1. **Prevents Network Saturation** - Consistent bandwidth usage
2. **Maintains Upload Reliability** - Fewer timeouts and connection errors
3. **Maximizes Efficiency** - Uploads overlap with processing
4. **Better Resource Management** - Minimal file handles

### Performance Impact

**Example:** 4 exports, each 10 min processing + 5 min upload

| Approach | Processing | Uploads | Total | Speedup |
|----------|-----------|---------|-------|---------|
| Sequential | 40 min | 20 min | 60 min | 1.0x |
| Parallel + queued | 10 min | 20 min | ~25 min | 2.4x |

### Adjusting Upload Concurrency

Control files uploaded simultaneously within each export:

```bash
./memoria.py --originals /exports -o /output \
  --parallel \
  --immich-concurrency 8  # More concurrent uploads per export
```

**Recommendation**: Keep between 4-8 for best balance.

### Balancing Processing and Uploads

If processing is faster than uploads:

```bash
# More parallel exports to keep queue fed
MEMORIA_PROCESSES=6 ./memoria.py --originals /exports -o /output \
  --parallel --threads 2
```

If uploads are faster than processing:

```bash
# Fewer parallel exports with more threads each
MEMORIA_PROCESSES=2 ./memoria.py --originals /exports -o /output \
  --parallel --threads 7
```

### Disabling Upload Queuing

Use sequential mode (without `--parallel`), or skip uploads entirely:

```bash
./memoria.py --originals /exports -o /output --skip-upload
```

---

## Platform Tagging

After uploading media to Immich, Memoria automatically applies platform tags to help organize and filter your media by source.

### Supported Platform Tags

| Platform | Color |
|----------|-------|
| Google | `#34A853` (green) |
| Instagram | `#C13584` (magenta) |
| Snapchat | `#FFFC00` (yellow) |
| Discord | `#5865F2` (blurple) |
| iMessage | `#0A84FF` (blue) |

### How It Works

1. After each successful upload, Memoria queries Immich for assets in the album
2. Local files are matched to Immich assets by filename and size
3. Platform tags are applied based on the source processor

### Cross-Processor Deduplication

When using cross-processor deduplication, a single output file may have originated from multiple platforms (primary + secondary sources). In this case, the uploaded asset receives tags for **all contributing platforms**.

For example, if a photo appears in both Google Photos and Instagram Messages:
- The deduplicated output file is uploaded once
- It receives both `Google` and `Instagram` tags

### Configuration

Platform tagging is enabled by default when Immich uploads are enabled.

**Disable tagging:**
```bash
./memoria.py /path/to/export -o /output --no-immich-tag-platforms
```

**Via environment variable:**
```bash
IMMICH_TAG_PLATFORMS=false ./memoria.py /path/to/export -o /output
```

### Creating Platform Tags

Tags are automatically created if they don't exist. To pre-create platform tags with colors before processing, use the standalone test tool:

```bash
python standalone/test_immich_api.py --test setup-platform-tags
```

---

## Related Documentation

- [Usage](Usage) - Command-line options
- [Parallel Processing](Parallel-Processing) - Parallel export processing
