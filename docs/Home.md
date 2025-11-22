# Memoria

## What is Memoria?

Memoria transforms messy social media exports into well-organized, properly dated media libraries. It takes the JSON metadata files, HTML conversation dumps, and scattered media files from your platform exports and produces a clean collection where every photo and video has its original timestamp, location, and context embedded directly into the file itself - making your memories searchable and sortable in any photo application, now and in the future.

The program handles all the tedious work: parsing platform-specific metadata formats, matching media files to their data, embedding everything as standard EXIF tags, and organizing the output by platform and account. Process multiple accounts and platforms in one go, and optionally upload everything to your Immich server with automatically created albums.

## Supported Platforms

Memoria can process exports from the following platforms:

### Google Services

- **Google Photos** - Albums, shared libraries, and photo metadata
- **Google Chat** - Group and direct message media
- **Google Voice** - Call recordings and voicemail

### Instagram

- **Instagram Messages** - DM and group chat media
- **Instagram Public Media** - Posts and archived posts (new JSON format)
- **Instagram Old Format** - Legacy timestamped exports

### Snapchat

- **Snapchat Memories** - Saved snaps and stories with overlay embedding
- **Snapchat Messages** - Chat media from conversations

## Getting Started

New to Memoria? Start here:

1. **[Getting Started](Getting-Started)** - Installation, system requirements, and initial setup
2. **[Usage Guide](Usage)** - Detailed command-line options and workflows

### Platform-Specific Guides

Detailed export setup and structure requirements for each platform:

- **[Google Export Guide](Google-Export)** - Google Photos, Chat, and Voice
- **[Instagram Export Guide](Instagram-Export)** - Messages, posts, and legacy formats
- **[Snapchat Export Guide](Snapchat-Export)** - Memories and messages

## Quick Start

After installing dependencies (see [Getting Started](Getting-Started)):

```bash
# Process a single export
./memoria.py /path/to/export -o /path/to/output

# Process multiple exports
./memoria.py --originals /path/to/all-exports -o /path/to/output

# List available processors
./memoria.py --list-processors
```

For all command-line options, see the [Usage Guide](Usage).

## Documentation

### Core Guides

- **[Getting Started](Getting-Started)** - Installation, system requirements, and initial setup
- **[Usage Guide](Usage)** - Complete command-line reference and workflows

### Platform-Specific Guides

- **[Google Export Guide](Google-Export)** - Google Photos, Chat, and Voice export setup
- **[Instagram Export Guide](Instagram-Export)** - Instagram Messages, posts, and legacy formats
- **[Snapchat Export Guide](Snapchat-Export)** - Snapchat Memories and Messages setup

### Advanced Topics

- **[Immich Upload](Immich-Upload)** - Immich upload configuration and ignore patterns
- **[Parallel Processing](Parallel-Processing)** - Process multiple exports in parallel
- **[Upload Only Mode](Upload-Only-Mode)** - Upload previously processed exports
- **[Upload Queuing](Upload-Queuing)** - Parallel processing upload queuing
- **[Logging](Logging)** - Logging configuration and verbose mode
- **[Deduplication](Deduplication)** - Google Photos deduplication system
- **[Standalone Tools](Standalone-Tools)** - Standalone utility scripts for analysis and comparison

### Reference

- **[FAQ](FAQ)** - Frequently asked questions
- **[Common Gotchas](Common-Gotchas)** - Important behaviors and surprises to know
- **[Design Decisions](Design-Decisions)** - Rationale for architectural choices

### Development

- **[Adding Processors](Adding-Processors)** - Create custom processors for new platforms

## Tips

1. **First Time?** Start with the [Getting Started](Getting-Started) guide for setup instructions
2. **Important Behaviors**: Read [Common Gotchas](Common-Gotchas) to avoid surprises
3. **Export Setup**: See platform-specific guides ([Google](Google-Export), [Instagram](Instagram-Export), [Snapchat](Snapchat-Export)) for export preparation
4. **Questions?** Check the [FAQ](FAQ) for common questions and answers
5. **Performance**: Use `--workers` to control parallelism (see [Usage Guide](Usage))
6. **Multiple Exports**: Use `--originals` to batch process (see [Parallel Processing](Parallel-Processing))
7. **Debugging**: Use `--verbose` for detailed logs (see [Logging](Logging))
8. **Immich Upload**: Configure automatic upload to Immich (see [Immich Upload](Immich-Upload))

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

For troubleshooting help, see the **[Getting Started Guide](Getting-Started#troubleshooting)**.

Common issues:

- **"exiftool is not installed"** or **"ffmpeg not found"**: Install required system dependencies
- **"No processors matched input directory"**: Check export structure against platform guides
- **Import errors**: Try installing in development mode: `pip install -e .`
- **Performance issues**: See [Usage Guide](Usage#performance-tips) for optimization

