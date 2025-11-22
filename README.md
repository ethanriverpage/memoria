<div align="center">

# Memoria

**Transform messy social media exports into well-organized, properly dated media libraries**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

[Features](#key-features) • [Quick Start](#quick-start) • [Documentation](../../wiki) • [Platforms](#supported-platforms) • [Contributing](CONTRIBUTING.md)

</div>

---

## Table of Contents

- [What is Memoria?](#what-is-memoria)
- [Key Features](#key-features)
- [Important Disclaimers](#important-disclaimers)
- [What Do You Get?](#what-do-you-get)
- [Design Philosophy](#design-philosophy)
- [Supported Platforms](#supported-platforms)
- [Quick Start](#quick-start)
- [Documentation](#documentation)
- [Contributing](#contributing)
- [License](#license)

---

## What is Memoria?

Memoria transforms messy social media exports into well-organized, properly dated media libraries.

It takes the JSON metadata files, HTML conversation dumps, and scattered media files from your platform exports and produces a clean collection where every photo and video has its original timestamp, location, and context embedded directly into the file itself - making your memories searchable and sortable in any photo application, now and in the future.

## Key Features

<table>
<tr>
<td width="50%">

**Smart Metadata Processing**

- Parses platform-specific formats (JSON, HTML)
- Extracts dates, locations, and context
- Preserves captions and message content

</td>
<td width="50%">

**Industry-Standard EXIF**

- Embeds all metadata directly into files
- Works with any photo management app
- No proprietary databases or sidecar files

</td>
</tr>
<tr>
<td width="50%">

**Intelligent Organization**

- Groups by platform and account
- Descriptive, sortable filenames
- Automatic deduplication

</td>
<td width="50%">

**Batch Processing & Integration**

- Process multiple platforms at once
- Parallel processing support
- Optional Immich server upload

</td>
</tr>
</table>

---

## IMPORTANT DISCLAIMERS

> [!CAUTION]
> **Backup and Data Loss**
>
> While Memoria processes copies of your export files and does not modify the original export directories, **I am not responsible for any data loss**. Always maintain backups of your original exports before processing.
>
> [!WARNING]
> **AI-Generated Code**
>
> This codebase was created with the assistance of AI. It is **strongly recommended** that you thoroughly review and test the code before using it in any production environment or with irreplaceable data.
>
> [!IMPORTANT]
> **Read the Documentation**
>
> Please read all documentation carefully before use. This tool makes specific design decisions that may not align with everyone's needs or expectations. **Make sure you fully understand the purpose and behavior of this program before processing your data.**

---

## What Do You Get?

After processing, your files are transformed with rich metadata embedded directly into each file.

<details open>
<summary><b>Metadata Embedding</b></summary>

<br>

Every processed file gets comprehensive EXIF metadata written using ExifTool:

**Standard Tags**

| Tag Type | What's Embedded | EXIF Fields |
|----------|----------------|-------------|
| Timestamps | Original capture date/time | `DateTimeOriginal`, `CreateDate`, `ModifyDate` |
| GPS | Location data when available | `GPSLatitude`, `GPSLongitude`, `GPSAltitude` |

**Source Information**

| File Type | Description Fields |
|-----------|-------------------|
| Images | `ImageDescription`, `IPTC:Caption-Abstract` |
| Videos | `Comment`, `Description` |

**Description Format Examples**

```text
Source: Instagram/username/messages - Best Friends - John Doe
Source: Google Photos/john.doe@gmail.com
Source: Snapchat/username/memories - Story from 2023-01-15
```

Platform captions and message text are preserved inline with the metadata.

</details>

<details open>
<summary><b>File Naming</b></summary>

<br>

Files are renamed with descriptive, sortable names that include platform, username, and date:

| Platform | Example Filename |
|----------|------------------|
| Google Chat | `gchat-john.doe-Family Chat-20230115.jpg` |
| Instagram Messages | `instagram-messages-jane_doe-Best Friends-20230220_1.mp4` |
| Snapchat Messages | `snap-messages-user123-john-20230310.jpg` |
| Instagram Posts | `insta-posts-user123-20230405.jpg` |
| Google Photos | `gphotos-john.doe-IMG_1234.jpg` |

</details>

<details open>
<summary><b>Organization Structure</b></summary>

<br>

Processed files are organized by platform and service/media type:

```text
platform-username-YYYYMMDD/
├── Google Photos/
│   └── john.doe/
│       └── gphotos-john.doe-*.jpg
├── Google Chat/
│   └── john.doe/
│       └── gchat-john.doe-*.jpg
├── messages/              # Instagram/Snapchat Messages
│   └── username/
│       └── *-messages-*.jpg
└── memories/              # Snapchat Memories
    └── username/
        └── snap-*.mp4
```

**Additional Features**

- File modification times set to match original capture dates
- Files sort correctly in any file browser
- Easy to navigate and find specific content

</details>

---

### The Result

<div align="center">

**Your media becomes truly portable and future-proof**

Every file carries its complete history in industry-standard formats

| What's Preserved | How It's Stored |
|-----------------|-----------------|
| When it was taken | EXIF timestamps |
| Where you were | GPS coordinates |
| Who sent it | Description metadata |
| What platform it came from | Source tags |

Compatible with any photo management application, cloud service, or future software you might use.

</div>

## Design Philosophy

Memoria makes specific design choices that prioritize data portability and future-proofing.

### Core Principles

- **Metadata-First Approach**: All context is embedded directly in files using industry-standard EXIF tags, not stored in sidecar files or databases

- **Flat Organization**: Files are organized by platform/username, not by albums or conversations, to simplify deduplication and avoid complex folder structures

- **Deduplication by Default**: Google Photos automatically deduplicates across albums to save space and reduce processing time

- **Non-Destructive Processing**: Original exports are never modified; all operations work on copies

> [!NOTE]
> For detailed rationale behind these decisions, see the [Design Decisions](../../wiki/Design-Decisions) document.

## Supported Platforms

Memoria can process exports from the following platforms:

| Platform | Service | What's Supported |
|----------|---------|------------------|
| **Google** | Photos | Albums, shared libraries, and photo metadata |
| | Chat | Group and direct message media |
| | Voice | SMS messages and media |
| **Instagram** | Messages | DM and group chat media |
| | Public Media | Posts, archived posts, stories, etc. |
| | Old Format | Legacy timestamped exports |
| **Snapchat** | Memories | Saved snaps and stories with overlay embedding |
| | Messages | Chat media from conversations |

> [!TIP]
> Each platform has specific export requirements. See our [export guides](#export-guides) for detailed instructions.

## Quick Start

### Installation

First, install system dependencies:

```bash
# Ubuntu/Debian
sudo apt-get install exiftool ffmpeg

# macOS
brew install exiftool ffmpeg
```

Then install Python dependencies:

```bash
pip install -r requirements.txt
# or for development
pip install -e .
```

### Basic Usage

```bash
# Process a single export
./memoria.py /path/to/export -o /path/to/output

# Process multiple exports in parallel
./memoria.py --originals /path/to/all-exports -o /path/to/output

# List available processors
./memoria.py --list-processors

# Enable verbose logging
./memoria.py /path/to/export -o /path/to/output --verbose
```

> [!TIP]
> See the [Getting Started Guide](../../wiki/Getting-Started) for detailed installation instructions and the [Usage Guide](../../wiki/Usage) for all command-line options.

### Export Guides

Before processing, export your data from the platforms:

| Platform | Guide | What to Export |
|----------|-------|----------------|
| Google | [Google Export Guide](../../wiki/Google-Export) | Photos, Chat, Voice |
| Instagram | [Instagram Export Guide](../../wiki/Instagram-Export) | Messages, Posts, Stories |
| Snapchat | [Snapchat Export Guide](../../wiki/Snapchat-Export) | Memories, Messages |

## Documentation

<details>
<summary><b>Getting Started</b></summary>

<br>

- **[Getting Started](../../wiki/Getting-Started)** - Installation, system requirements, and initial setup
- **[Usage Guide](../../wiki/Usage)** - Complete command-line reference and workflows

</details>

<details>
<summary><b>Platform-Specific Guides</b></summary>

<br>

- **[Google Export](../../wiki/Google-Export)** - Google Photos, Chat, and Voice export setup
- **[Instagram Export](../../wiki/Instagram-Export)** - Instagram Messages, posts, and legacy formats
- **[Snapchat Export](../../wiki/Snapchat-Export)** - Snapchat Memories and Messages setup

</details>

<details>
<summary><b>Advanced Topics</b></summary>

<br>

- **[Immich Upload](../../wiki/Immich-Upload)** - Immich upload configuration and ignore patterns
- **[Parallel Processing](../../wiki/Parallel-Processing)** - Process multiple exports in parallel
- **[Upload Only Mode](../../wiki/Upload-Only-Mode)** - Upload previously processed exports
- **[Upload Queuing](../../wiki/Upload-Queuing)** - Parallel processing upload queuing
- **[Logging](../../wiki/Logging)** - Logging configuration and verbose mode
- **[Deduplication](../../wiki/Deduplication)** - Google Photos deduplication system
- **[Standalone Tools](../../wiki/Standalone-Tools)** - Standalone utility scripts for analysis and comparison

</details>

<details>
<summary><b>Reference</b></summary>

<br>

- **[FAQ](../../wiki/FAQ)** - Frequently asked questions
- **[Common Gotchas](../../wiki/Common-Gotchas)** - Important behaviors and surprises to know
- **[Design Decisions](../../wiki/Design-Decisions)** - Rationale for architectural choices

</details>

<details>
<summary><b>Development</b></summary>

<br>

- **[Adding Processors](../../wiki/Adding-Processors)** - Create custom processors for new platforms

</details>

## Pro Tips

| Scenario | Recommendation |
|----------|----------------|
| **First Time?** | Start with the [Getting Started Guide](../../wiki/Getting-Started) for setup instructions |
| **Important Behaviors** | Read [Common Gotchas](../../wiki/Common-Gotchas) to avoid surprises |
| **Export Setup** | See platform-specific guides for export preparation |
| **Performance** | Use `--workers N` to control parallelism |
| **Multiple Exports** | Use `--originals` to batch process everything at once |
| **Debugging** | Enable `--verbose` for detailed logs |
| **Immich Upload** | Configure automatic upload to your Immich server |
| **Questions?** | Check the [FAQ](../../wiki/FAQ) for common questions |

## Contributing

Contributions are welcome! We'd love your help making Memoria better.

**Quick Start for Contributors**

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Run tests and linting
5. Submit a pull request

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines on development setup, code standards, and adding new processors.

---

## Acknowledgments

Special thanks to the [immich-go](https://github.com/simulot/immich-go) project, which provided valuable insights into understanding the structure and handling of Google Photos takeout formats.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

**Disclaimer**: This project is provided as-is for personal use in organizing and preserving your social media exports.

## Troubleshooting

<details>
<summary><b>Common Issues & Solutions</b></summary>

<br>

| Issue | Solution |
|-------|----------|
| `exiftool is not installed` or `ffmpeg not found` | Install required system dependencies (see [Quick Start](#quick-start)) |
| `No processors matched input directory` | Check export structure against platform guides |
| Import errors | Try installing in development mode: `pip install -e .` |
| Performance issues | See [Usage Guide](../../wiki/Usage#performance-tips) for optimization |

For more help, see the **[Getting Started Guide](../../wiki/Getting-Started#troubleshooting)**.

</details>
