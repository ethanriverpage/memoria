# Getting Started with Memoria

This guide walks you through setting up Memoria and preparing your social media exports for processing.

## What Happens During Processing

Before diving into installation, here's what Memoria does with your data:

1. **Detection**: Memoria identifies which processor(s) can handle your export structure
2. **Copying**: Files are copied (never moved) from the export to the output directory
3. **Metadata Extraction**: Platform-specific JSON/HTML files are parsed for timestamps, locations, and context
4. **EXIF Embedding**: Metadata is written directly into each media file using ExifTool
5. **Deduplication** (Google Photos, iMessage): Duplicate files across albums/exports are identified and skipped
6. **File Organization**: Files are organized by platform and username with descriptive names
7. **Upload** (optional): Files are uploaded to Immich in platform-specific albums

**Your original export remains completely untouched.** All operations work on copies.

For the reasoning behind these choices, see [Design Decisions](Design-Decisions.md).

## Table of Contents

1. [What Happens During Processing](#what-happens-during-processing)
2. [System Requirements](#system-requirements)
3. [Installation](#installation)
4. [Preparing Your Exports](#preparing-your-exports)
5. [Directory Naming Conventions](#directory-naming-conventions)
6. [Quick Start](#quick-start)
7. [Platform-Specific Setup](#platform-specific-setup)
8. [System Dependencies Installation](#system-dependencies-installation)

## System Requirements

Memoria requires the following to be installed on your system:

- **Python**: 3.7 or higher
- **exiftool**: For embedding metadata in media files
- **ffmpeg**: For video processing and re-encoding
- **libmagic**: For file type detection

See [System Dependencies Installation](#system-dependencies-installation) at the end of this guide for platform-specific installation commands.

## Installation

1. Clone or download this repository:

```bash
cd /path/to/memoria
```

2. Install Python dependencies:

```bash
pip install -r requirements.txt
```

3. (Optional) Install in development mode for better module imports:

```bash
pip install -e .
```

4. Verify system dependencies are installed:

```bash
exiftool -ver    # Should display version number
ffmpeg -version  # Should display version info
```

## Preparing Your Exports

### Downloading Platform Exports

Each platform has its own export process:

- **Apple iMessage**: Copy from Mac (`~/Library/Messages/`) or extract from iPhone backup
- **Discord**: Use User Settings > Privacy & Safety > Request My Data
- **Google**: Use [Google Takeout](https://takeout.google.com)
- **Instagram**: Download from Settings > Account Center > Your Information and Permissions > Download Your Information
- **Snapchat**: Use Settings > My Data > Submit Request

See the platform-specific guides for detailed export instructions:

- [Google Export Setup](Google-Export)
- [iMessage Export Setup](iMessage-Export)
- [Discord Export Setup](Discord-Export)
- [Instagram Export Setup](Instagram-Export)
- [Snapchat Export Setup](Snapchat-Export)

### Extract Your Downloads

After downloading, extract the export archives to a working directory. Most platforms provide ZIP files that need to be extracted.

## Directory Naming Conventions

**Important**: Platform exports don't automatically include usernames in their directory structure. To help Memoria identify which account an export belongs to, you should rename the export folder after extracting it.

### Naming Format

Use this format for your export directories:

```
platform-username-YYYYMMDD
```

Where:

- `platform`: One of `google`, `mac`/`iphone*`, `discord`, `instagram`, or `snapchat`
- `username`: The account username or device identifier (can include hyphens for multi-part names)
- `YYYYMMDD`: Date of export (optional but recommended)

### Examples

```
google-john.doe-20250526/
├── Google Photos/        # Actual Google export structure
│   └── Albums...
├── Google Chat/
│   └── ...
└── Voice/
    └── ...

mac-messages-20251202/
├── chat.db               # iMessage database
└── Attachments/          # iMessage attachments
    └── ...

discord-username-20251215/
├── Messages/             # Actual Discord export structure
│   ├── index.json
│   └── c{channel_id}/
│       └── messages.json
└── Servers/
    └── ...

instagram-jane_doe-20251007/
└── your_instagram_activity/    # Actual Instagram export structure
    └── messages/
        └── ...

snapchat-user123-20251007/
├── memories/             # Actual Snapchat export structure
│   └── ...
└── messages/
    └── ...
```

Multi-part usernames are supported:

```
instagram-john-doe-smith-20250526/  → username: john-doe-smith
```

### Why This Matters

Memoria uses the directory name to:

1. Extract the username for the processed files
2. Create properly named output directories
3. Generate appropriate Immich album names

Without proper naming, files will be processed with "unknown" as the username.

## Quick Start

Once you've installed dependencies and prepared your export directory:

1. **Process a single export:**

```bash
./memoria.py /path/to/google-username-20250526 -o /path/to/output
```

2. **Process multiple exports:**

```bash
# Place all your renamed exports in one folder
./memoria.py --originals /path/to/all-exports -o /path/to/output
```

3. **Check available processors:**

```bash
./memoria.py --list-processors
```

For detailed usage options, see [Usage](Usage).

## Platform-Specific Setup

Each platform has unique export structures and requirements. Consult the appropriate guide for your platform:

### Apple iMessage

See [iMessage Export Guide](iMessage-Export) for:

- Mac exports (chat.db with Attachments)
- iPhone exports (SMS/sms.db with Attachments)
- Cross-export deduplication

### Discord

See [Discord Export Guide](Discord-Export) for:

- Requesting your Discord data export
- Downloading attachments from CDN URLs
- Channel types (DMs, group DMs, server channels)

### Google Services

See [Google Export Guide](Google-Export) for:

- Google Photos (albums, metadata, shared libraries)
- Google Chat (groups, direct messages)
- Google Voice (call recordings, voicemail)

### Instagram

See [Instagram Export Guide](Instagram-Export) for:

- Instagram Messages (DMs, group chats)
- Instagram Public Media (new format with date folders)
- Instagram Old Format (legacy timestamped files)

### Snapchat

See [Snapchat Export Guide](Snapchat-Export) for:

- Snapchat Memories (with overlay support)
- Snapchat Messages (chat media)

## Next Steps

After setting up:

1. Review [Usage](Usage) for detailed command options
2. Check platform-specific guides for export structure requirements
3. Configure Immich integration (optional) - see [Immich Upload](Immich-Upload)
4. Learn about parallel processing - see [Parallel Processing](Parallel-Processing)

## Troubleshooting

### "exiftool is not installed"

Install exiftool using the system-specific commands in the System Requirements section.

### "ffmpeg not found"

Install ffmpeg using the system-specific commands in the System Requirements section.

### "No processors matched input directory"

- Verify your export directory structure matches one of the supported formats
- Run `./memoria.py --list-processors` to see available processors
- Check directory naming follows the conventions (platform-username-YYYYMMDD)

### Import errors

If you see import errors, try installing in development mode: `pip install -e .`

### Performance issues

- Reduce `--workers` count if system is overloaded
- Process smaller batches of exports at a time
- Ensure sufficient disk space for temporary files and output

## System Dependencies Installation

### exiftool

Required for embedding metadata in media files.

- **macOS**: `brew install exiftool`
- **Linux**: `sudo apt-get install libimage-exiftool-perl`
- **Windows**: Download from <https://exiftool.org/>

Verify installation:

```bash
exiftool -ver
```

### ffmpeg

Required for video processing and re-encoding.

- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt-get install ffmpeg`
- **Windows**: Download from <https://ffmpeg.org/>

Verify installation:

```bash
ffmpeg -version
```

### libmagic

Required for file type detection.

- **macOS**: `brew install libmagic`
- **Linux**: `sudo apt-get install libmagic1`
- **Windows**: `pip install python-magic-bin` (alternative)

### Python

Python 3.7 or higher is required. Check your version:

```bash
python3 --version
```

If you need to install or upgrade Python:

- **macOS**: `brew install python3`
- **Linux**: `sudo apt-get install python3 python3-pip`
- **Windows**: Download from <https://www.python.org/>
