# Memoria

## What is Memoria?

Memoria transforms messy social media exports into well-organized, properly dated media libraries. It takes the JSON metadata files, HTML conversation dumps, and scattered media files from your platform exports and produces a clean collection where every photo and video has its original timestamp, location, and context embedded directly into the file itself - making your memories searchable and sortable in any photo application, now and in the future.

The program handles all the tedious work: parsing platform-specific metadata formats, matching media files to their data, embedding everything as standard EXIF tags, and organizing the output by platform and account. Process multiple accounts and platforms in one go, and optionally upload everything to your Immich server with automatically created albums.

---

## IMPORTANT DISCLAIMERS

> **Backup and Data Loss**
>
> While Memoria processes copies of your export files and does not modify the original export directories, **I am not responsible for any data loss**. Always maintain backups of your original exports before processing.

> **AI-Generated Code**
>
> This codebase was created with the assistance of AI. It is **strongly recommended** that you thoroughly review and test the code before using it in any production environment or with irreplaceable data.

> **Read the Documentation**
>
> Please read all documentation carefully before use. This tool makes specific design decisions that may not align with everyone's needs or expectations. **Make sure you fully understand the purpose and behavior of this program before processing your data.**

---

## What Do You Get?

After processing, your files are transformed with rich metadata embedded directly into each file:

### Metadata Embedding

Every processed file gets comprehensive EXIF metadata written using ExifTool:

- **Timestamps**: Original capture date/time embedded in `DateTimeOriginal`, `CreateDate`, and `ModifyDate` tags
- **GPS Coordinates**: Location data (when available) embedded in standard GPS tags (`GPSLatitude`, `GPSLongitude`, `GPSAltitude`)
- **Source Information**: Platform, account, and context embedded in description fields:
  - **Images**: `ImageDescription` and `IPTC:Caption-Abstract` tags
  - **Videos**: `Comment` and `Description` tags

The description field contains structured source information, for example:

- `Source: Instagram/username/messages` + conversation name + sender name
- `Source: Google Photos/username` for photos
- `Source: Snapchat/username/memories` with conversation details and message content for chats
- Platform captions and message text preserved inline

### File Naming

Files are renamed with descriptive, sortable names that include platform, username, and date:

**Examples:**

- `gchat-john.doe-Family Chat-20230115.jpg` (Google Chat)
- `instagram-messages-jane_doe-Best Friends-20230220_1.mp4` (Instagram Messages)
- `snap-messages-user123-john-20230310.jpg` (Snapchat Messages)
- `insta-posts-user123-20230405.jpg` (Instagram Posts)
- `gphotos-john.doe-IMG_1234.jpg` (Google Photos)

### Organization

Processed files are organized by platform and service/media type:

```
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

### File System Timestamps

In addition to EXIF metadata, file modification times are set to match the original capture date, making files sort correctly in file browsers.

### Result

Your media becomes truly portable and future-proof. Every file carries its complete history - when it was taken, where you were, who sent it, what platform it came from - all in industry-standard formats that work with any photo management application, cloud service, or future software you might use.

## Design Philosophy

Memoria makes specific design choices that prioritize data portability and future-proofing:

- **Metadata-First Approach**: All context is embedded directly in files using industry-standard EXIF tags, not stored in sidecar files or databases
- **Flat Organization**: Files are organized by platform/username, not by albums or conversations, to simplify deduplication and avoid complex folder structures
- **Deduplication by Default**: Google Photos automatically deduplicates across albums to save space and reduce processing time
- **Non-Destructive Processing**: Original exports are never modified; all operations work on copies

For detailed rationale behind these decisions, see the [Design Decisions](docs/Design-Decisions.md) document.

## Supported Platforms

Memoria can process exports from the following platforms:

### Google Services

- **Google Photos** - Albums, shared libraries, and photo metadata
- **Google Chat** - Group and direct message media
- **Google Voice** - SMS messages and media

### Instagram

- **Instagram Messages** - DM and group chat media
- **Instagram Public Media** - Posts, archived posts, stories, etc.
- **Instagram Old Format** - Legacy timestamped exports

### Snapchat

- **Snapchat Memories** - Saved snaps and stories with overlay embedding
- **Snapchat Messages** - Chat media from conversations

## Getting Started

New to Memoria? Start here:

1. **[Getting Started Guide](docs/Getting-Started.md)** - Installation, system requirements, and initial setup
2. **[Usage Guide](docs/Usage.md)** - Detailed command-line options and workflows

### Platform-Specific Guides

Detailed export setup and structure requirements for each platform:

- **[Google Export Guide](docs/Google-Export.md)** - Google Photos, Chat, and Voice
- **[Instagram Export Guide](docs/Instagram-Export.md)** - Messages, posts, and legacy formats
- **[Snapchat Export Guide](docs/Snapchat-Export.md)** - Memories and messages

## Quick Start

After installing dependencies (see [Getting Started Guide](docs/Getting-Started.md)):

```bash
# Process a single export
./memoria.py /path/to/export -o /path/to/output

# Process multiple exports
./memoria.py --originals /path/to/all-exports -o /path/to/output

# List available processors
./memoria.py --list-processors
```

For all command-line options, see the [Usage Guide](docs/Usage.md).

## Standalone Tools

The `standalone/` directory contains utility scripts for analyzing and comparing media exports.

See **[Standalone Tools](docs/Standalone-Tools.md)** for detailed usage documentation.

## Documentation

### Getting Started

- **[Getting Started](docs/Getting-Started.md)** - Installation, system requirements, and initial setup
- **[Usage Guide](docs/Usage.md)** - Complete command-line reference and workflows

### Platform-Specific Guides

- **[Google Export](docs/Google-Export.md)** - Google Photos, Chat, and Voice export setup
- **[Instagram Export](docs/Instagram-Export.md)** - Instagram Messages, posts, and legacy formats
- **[Snapchat Export](docs/Snapchat-Export.md)** - Snapchat Memories and Messages setup

### Advanced Topics

- **[Immich Upload](docs/Immich-Upload.md)** - Immich upload configuration and ignore patterns
- **[Parallel Processing](docs/Parallel-Processing.md)** - Process multiple exports in parallel
- **[Upload Only Mode](docs/Upload-Only-Mode.md)** - Upload previously processed exports
- **[Upload Queuing](docs/Upload-Queuing.md)** - Parallel processing upload queuing
- **[Logging](docs/Logging.md)** - Logging configuration and verbose mode
- **[Deduplication](docs/Deduplication.md)** - Google Photos deduplication system
- **[Standalone Tools](docs/Standalone-Tools.md)** - Standalone utility scripts for analysis and comparison

### Reference

- **[FAQ](docs/FAQ.md)** - Frequently asked questions
- **[Common Gotchas](docs/Common-Gotchas.md)** - Important behaviors and surprises to know
- **[Design Decisions](docs/Design-Decisions.md)** - Rationale for architectural choices

### Development

- **[Adding Processors](docs/Adding-Processors.md)** - Create custom processors for new platforms

## Tips

1. **First Time?** Start with the [Getting Started Guide](docs/Getting-Started.md) for setup instructions
2. **Important Behaviors**: Read [Common Gotchas](docs/Common-Gotchas.md) to avoid surprises
3. **Export Setup**: See platform-specific guides ([Google](docs/Google-Export.md), [Instagram](docs/Instagram-Export.md), [Snapchat](docs/Snapchat-Export.md)) for export preparation
4. **Questions?** Check the [FAQ](docs/FAQ.md) for common questions and answers
5. **Performance**: Use `--workers` to control parallelism (see [Usage Guide](docs/Usage.md))
6. **Multiple Exports**: Use `--originals` to batch process (see [Parallel Processing](docs/Parallel-Processing.md))
7. **Debugging**: Use `--verbose` for detailed logs (see [Logging](docs/Logging.md))
8. **Immich Upload**: Configure automatic upload to Immich (see [Immich Upload](docs/Immich-Upload.md))

## Acknowledgments

Special thanks to the [immich-go](https://github.com/simulot/immich-go) project, which provided valuable insights into understanding the structure and handling of Google Photos takeout formats.

## License

This project is provided as-is for personal use in organizing and preserving your social media exports.

## Contributing

When contributing new processors or improvements:

1. Follow the existing code structure and patterns
2. Create processors in `processors/` directory with underscores in names (e.g., `my_platform/`)
3. Inherit from `ProcessorBase` (from `processors.base`) and implement all required methods
4. Add proper detection logic that doesn't overlap with existing processors
5. Include a `get_processor()` function for auto-discovery
6. Include documentation and examples
7. Test with real export data before submitting
8. Use appropriate priority value (see `processors/base.py` for guidelines)

## Troubleshooting

For troubleshooting help, see the **[Getting Started Guide](docs/Getting-Started.md#troubleshooting)**.

Common issues:

- **"exiftool is not installed"** or **"ffmpeg not found"**: Install required system dependencies
- **"No processors matched input directory"**: Check export structure against platform guides
- **Import errors**: Try installing in development mode: `pip install -e .`
- **Performance issues**: See [Usage Guide](docs/Usage.md#performance-tips) for optimization
