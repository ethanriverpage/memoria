# Extending Memoria

Memoria uses a processor system to support different social media platforms. If you want to add support for a new platform, you can create a custom processor.

## Before You Start

1. **Check if it's already supported** - Run `./memoria.py --list-processors` to see available processors
2. **Look for existing feature requests** - Check the project's issue tracker
3. **Understand the export format** - Download a sample export from the platform you want to support

## Adding a New Processor

Processors are automatically discovered from the `processors/` directory. Each processor handles a specific platform or export format.

### Requirements

- Python knowledge (implementing abstract classes)
- Understanding of the platform's export format (JSON, HTML, file structure)
- Familiarity with EXIF metadata concepts
- Test export data from the platform

### Basic Steps

1. Create a new directory in `processors/` (e.g., `processors/my_platform/`)
2. Implement the `ProcessorBase` abstract class from `processors/base.py`
3. Add detection logic to identify your platform's exports
4. Implement the processing logic to extract metadata and copy files
5. Test thoroughly with real export data

### What You'll Need to Implement

Every processor must provide:

- **Detection method**: How to identify if an export is from this platform
- **Name**: Human-readable platform name
- **Priority**: Execution order when multiple processors match
- **Processing logic**: Extract metadata, copy files, embed EXIF data

### Example Structure

Look at existing processors as references:

- `processors/google_photos/` - Complex with preprocessing and deduplication
- `processors/instagram_messages/` - HTML parsing and conversation handling
- `processors/snapchat_memories/` - Overlay embedding and media matching

### Testing Your Processor

```bash
# Test detection
./memoria.py /path/to/test/export --verbose

# Verify it was discovered
./memoria.py --list-processors

# Process a test export
./memoria.py /path/to/test/export -o /output/test --verbose
```

## Development Resources

For detailed implementation guidance:

1. **Read `processors/base.py`** - Contains the abstract class and detailed comments
2. **Review `CONTRIBUTING.md`** - Coding standards and project structure
3. **Study existing processors** - See how they handle detection, metadata extraction, and EXIF embedding
4. **Check inline documentation** - Processors have detailed docstrings

## Key Considerations

- **Detection specificity**: Avoid false positives by checking for unique file/structure patterns
- **Error handling**: Return `False` on failure, don't raise unhandled exceptions
- **Thread safety**: Processing may be multi-threaded
- **EXIF standards**: Use standard EXIF tags that work across photo management systems
- **Documentation**: Document the expected export structure in your processor

### Critical: Filesystem Timestamp Handling

**WARNING**: You must be extremely careful with filesystem timestamps to avoid data corruption.

**The Problem:**
When a platform doesn't provide metadata files (JSON, EXIF, etc.), filesystem modification times may be the only available timestamp source. However, any file operation (copy, move, touch) updates the modification time to "now", permanently destroying the original timestamp.

**What happens if you get this wrong:**

```python
# WRONG - Don't do this!
def process(input_dir, output_dir):
    for file in input_dir.glob("*.jpg"):
        output_file = output_dir / file.name
        shutil.copy2(file, output_file)  # File copied
        timestamp = output_file.stat().st_mtime  # ✗ This is NOW, not original!
        embed_exif(output_file, timestamp)  # ✗ Wrong date embedded!
```

Result: All photos dated to the processing date instead of capture date.

**Correct approach:**

```python
# CORRECT - Read timestamps BEFORE file operations
def preprocess(input_dir):
    metadata = []
    for file in input_dir.glob("*.jpg"):
        # Read filesystem timestamp BEFORE copying
        timestamp = file.stat().st_mtime
        metadata.append({
            'path': file,
            'timestamp': timestamp
        })
    return metadata

def process(input_dir, output_dir):
    metadata = preprocess(input_dir)  # Get timestamps first
    for item in metadata:
        output_file = output_dir / item['path'].name
        shutil.copy2(item['path'], output_file)
        # Use stored timestamp, not current file time
        embed_exif(output_file, item['timestamp'])  # ✓ Correct!
```

**Key Rules:**

1. Read ALL filesystem timestamps during preprocessing/metadata extraction
2. Store timestamps in your metadata structures
3. NEVER read filesystem times after copying or modifying files
4. Use stored timestamps for EXIF embedding

**Platforms where this matters:**

- Instagram Old Format (uses file mtimes as fallback)
- Any export lacking JSON/metadata files
- Exports with incomplete metadata

**See also:**

- Look at `processors/instagram_old_public_media/preprocess.py` for a real example
- See [Design Decisions](Design-Decisions.md#filesystem-timestamp-handling) for detailed rationale

## Contributing

If you create a processor for a new platform:

1. Test it thoroughly with multiple export samples
2. Document the platform's export process (how users get the export)
3. Include examples of the expected directory structure
4. Submit a pull request with your processor and documentation

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.

## Getting Help

- Review existing processors for patterns and examples
- Check processor base class documentation in `processors/base.py`
- Open an issue to discuss your approach before implementing

## Related Documentation

- [Design Decisions](Design-Decisions.md) - Understand Memoria's architecture
- [Usage](Usage.md) - Command-line options for testing
- [Getting Started](Getting-Started.md) - Development environment setup
