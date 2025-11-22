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

Example `.env`:

```
IMMICH_INSTANCE_URL=http://192.168.1.216:2283/api
IMMICH_API_KEY=YOUR_API_KEY
IMMICH_UPLOAD_CONCURRENCY=4
IMMICH_SKIP_HASH=true
IMMICH_IGNORE_PATTERNS=**/issues/**,**/needs matching/**
```

### Default Ignore Patterns

By default, the following folders are automatically excluded from uploads:

- `**/issues/**` - Folders named "issues"
- `**/needs matching/**` - Folders named "needs matching"

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

- Instagram Messages: `Instagram/{USERNAME}/messages`
- Instagram Public/Old Media: one per subfolder → `Instagram/{USERNAME}/{posts|archived_posts|profile|stories|reels|other}`
- Google Chat: `Google Chat/{USERNAME}`
- Google Photos: `Google Photos/{USERNAME}`
- Google Voice: `Google Voice/{USERNAME}`
- Snapchat Messages: `Snapchat/{USERNAME}/messages`
- Snapchat Memories: `Snapchat/{USERNAME}/memories`

Usernames are parsed from the export directory name formats handled by the processors.

## Related Documentation

- [Usage](Usage) - Command-line options
- [Upload Only Mode](Upload-Only-Mode) - Upload previously processed exports
- [Upload Queuing](Upload-Queuing) - Upload queuing for parallel processing

