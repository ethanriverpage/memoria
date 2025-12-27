# Extending Memoria

Memoria uses a processor system to support different social media platforms. If you want to add support for a new platform, you can create a custom processor.

## Before You Start

1. **Check if it's already supported** - Run `./memoria.py --list-processors` to see available processors
2. **Look for existing feature requests** - Check the project's issue tracker
3. **Understand the export format** - Download a sample export from the platform you want to support

## Adding a New Processor

Processors are automatically discovered from the `processors/` directory. Each processor handles a specific platform or export format.

### How discovery works (important)

`memoria.py` loads processors by scanning `processors/*/` for directories that contain a `processor.py`, importing `processors.<dir>.processor`, and then registering the first public subclass of `ProcessorBase` found in that module.

- **One processor class per `processor.py`**: Keep `processor.py` focused: one concrete `ProcessorBase` subclass that `memoria.py` can find.
- **Module name matters**: the file must be named `processor.py` for auto-discovery.
- **Directory name becomes the import path**: e.g. `processors/my_platform/processor.py` is imported as `processors.my_platform.processor`.

### Requirements

- Python knowledge (implementing abstract classes)
- Understanding of the platform's export format (JSON, HTML, file structure)
- Familiarity with EXIF metadata concepts
- Test export data from the platform

### Basic Steps

1. Create a new directory in `processors/` (e.g., `processors/my_platform/`)
2. Implement a `ProcessorBase` subclass in `processors/my_platform/processor.py`
3. Add detection logic to identify your platform's exports
4. Implement processing logic to copy media to the final output and embed metadata
5. If your platform benefits from it, add a `PreprocessorBase` subclass in `processors/my_platform/preprocess.py`
6. Test thoroughly with real export data

### What You'll Need to Implement

Every processor must provide:

- **Detection method**: How to identify if an export is from this platform
- **Name**: Human-readable platform name
- **Priority**: Execution order when multiple processors match
- **Output info**: Where this processor lives under `Platform/User[/Service]` output paths
- **Processing logic**: Copy files to output, embed EXIF metadata, and update filesystem timestamps when appropriate

#### Required processor methods (current contract)

All processors must implement the `ProcessorBase` interface in `processors/base.py`:

- **`detect(input_path: Path) -> bool`**: Fast, specific detection (avoid false positives)
- **`get_name() -> str`**: Human-friendly name (shown in CLI)
- **`get_priority() -> int`**: Higher runs first when multiple processors match one export
- **`get_output_info() -> OutputInfo`**: Defines `platform`, `service`, and `username_prefix` for output routing
- **`process(input_dir: str, output_dir: Optional[str] = None, dedup_manifest: Optional[dict] = None, **kwargs) -> bool`**

Notes:

- **`output_dir` passed into `process()`**: When `-o/--output` is used, Memoria passes a *Platform/User* directory computed from `get_output_info()` and the *original export directory*. Most processors then write into `Path(output_dir) / get_output_info().service` (when `service` is not `None`).
- **`dedup_manifest`**: In cross-processor dedup mode, Memoria passes a manifest so processors can skip secondary duplicates.

#### Optional: Cross-Processor Deduplication

To participate in cross-processor deduplication, implement:

- **`supports_dedup()`**: Return `True` to enable deduplication support
- **`build_catalog()`**: Yield a per-file catalog for the deduplication service

If you implement `supports_dedup() == True`, `ProcessorBase` enforces at import-time that you also implement `build_catalog()`.

See [Deduplication](Deduplication) for details on cross-processor deduplication.

### Example Structure

Look at existing processors as references:

- `processors/google_photos/` - Complex with preprocessing and cross-processor deduplication
- `processors/discord/` - CDN attachment downloading with PreprocessorBase
- `processors/instagram_messages/` - HTML parsing and conversation handling
- `processors/snapchat_memories/` - Overlay embedding and media matching

### PreprocessorBase for Preprocessing

Many processors use a two-phase architecture:

1. **Preprocessing**: Parses the platform export, builds standardized `metadata.json`, and (optionally) hashes/copies media with within-processor deduplication
2. **Processing**: Copies media into the final output, embeds EXIF metadata, and updates filesystem timestamps

Preprocessing is optional, but strongly recommended when:

- the raw export layout is messy (multiple locations, multiple formats)
- media needs matching (HTML/JSON sidecars → media files)
- within-export duplicates are common
- you want fast cross-processor dedup cataloging from a standardized format

#### Preprocessed directory format (canonical)

Memoria treats a directory as “preprocessed” if it contains `metadata.json` (see `common.utils.is_preprocessed_directory`).

- **Copy-based preprocessing**: writes `metadata.json` and may create `media/` (but processors should prefer `source_path` from metadata)
- **Hash-only preprocessing (preferred for large exports)**: writes `metadata.json` only; processors later copy directly from `source_path` to final output

The standardized schema is defined in `common/metadata/schema.py` and validated by `common.metadata.schema.validate_metadata()`:

- `export_info`: required metadata about the export (including detected username)
- `file_hashes`: mapping of output filename → content hash (supports cross-processor dedup)
- exactly one of: `conversations`, `media`, or `media_files`
- all media references must include `source_path` (direct file path) so processors and dedup can operate without relying on temp copies

#### Implementing a preprocessor (current template)

For preprocessing, extend the `PreprocessorBase` class from `processors/preprocess_base.py`:

```python
from pathlib import Path

from processors.preprocess_base import PreprocessorBase, CopyTask
from common.detection import DetectionResult, resolve_username, detect_from_dirname

class MyPlatformPreprocessor(PreprocessorBase):
    """Preprocessor for MyPlatform exports."""
    
    def __init__(self, export_path: Path, output_dir: Path, **kwargs):
        super().__init__(
            processor_name="MyPlatform",
            export_path=export_path,
            output_dir=output_dir,
            **kwargs
        )
    
    def validate_export(self) -> bool:
        """Validate export directory structure."""
        # Check for required files/directories
        return (self.export_path / "required_file.json").exists()
    
    def detect_username(self) -> DetectionResult:
        """Detect the export owner's username."""
        # Try various detection methods (in priority order)
        return resolve_username(
            self.export_path,
            [
                ("dirname", lambda p: detect_from_dirname(p, "myplatform")),
                # Add higher-signal detectors first (e.g. profile JSON) when available
            ],
        )
    
    def get_content_key(self) -> str:
        """Return the content key for metadata.json."""
        return "media_files"
    
    def process_export(self) -> bool:
        """Platform-specific preprocessing logic (called by run())."""
        # 1. Parse metadata
        # 2. Build CopyTask list for media files
        # 3. Choose ONE:
        #    - self.hash_with_dedup(tasks)  # preferred: no intermediate copy
        #    - self.copy_with_dedup(tasks)  # if you truly need media/ created
        # 4. Build content_data with 'filename' + 'source_path' (+ timestamps, etc.)
        # 5. self.save_metadata(content_data, extra_export_info={...})
        return True
    
    def print_statistics(self) -> None:
        """Print preprocessing statistics."""
        # Display summary of processed files
        pass
```

**Key PreprocessorBase Features:**

- **Template method pipeline**: Call `preprocessor.run()` (not `process_export()` directly)
- **Within-processor deduplication**:
  - `hash_with_dedup()` for hash-only preprocessing (preferred)
  - `copy_with_dedup()` for copy-based preprocessing (creates `media/`)
- **Context merging**: Set `set_context_merger()` to merge metadata when duplicates are found
- **Hash caching**: File hashes are cached in SQLite for faster repeated processing
- **Thread-safe logging**: Use `log_message()` for thread-safe log entries
- **Failure tracking**: Built-in `FailureTracker` for reporting missing/bad files without crashing
- **Standardized metadata**: `save_metadata()` builds/validates the schema and writes `metadata.json`

See `processors/discord/preprocess.py` or `processors/instagram_messages/preprocess.py` for complete examples.

#### Wiring preprocessing into your processor

If you want Memoria to run preprocessing automatically, implement these optional hooks on your `ProcessorBase` subclass:

- **`needs_preprocessing(input_dir: str) -> bool`**:
  - Return `False` when `metadata.json` exists (`is_preprocessed_directory(input_dir)`).
  - Return `True` when raw export structures are detected.
- **`preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]`**:
  - Create your preprocessor and call `run()`.
  - Return the working directory path (usually `cache_dir`) on success, `None` on failure.

Look at `processors/google_photos/processor.py` or `processors/instagram_messages/processor.py` for the pattern.

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

## Cross-Processor Deduplication Support

Processors that handle media files (photos, videos) should implement cross-processor deduplication support. This allows Memoria to identify duplicate files across different platform exports.

### Implementation

Add these methods to your processor class:

```python
@staticmethod
def supports_dedup() -> bool:
    """Return True to enable cross-processor deduplication."""
    return True

@staticmethod
def build_catalog(input_dir: str, **kwargs) -> Iterator[Dict[str, Any]]:
    """Build catalog by yielding entries one at a time (memory-efficient).
    
    This method is called during Phase 1 (CATALOG) of deduplication.
    It should scan all media files, compute hashes, and YIELD entries
    one at a time WITHOUT copying any files.
    
    Using a generator (yield) instead of returning a list allows
    streaming entries to the database without holding all entries
    in memory - critical for large exports with 100,000+ files.
    
    Args:
        input_dir: Path to input directory
        **kwargs: Additional arguments (verbose, workers)
    
    Yields:
        Catalog entry dictionaries with structure:
        {
            "source_path": str,           # Full path to source
            "relative_path": str,         # Path relative to input_dir
            "content_hash": str,          # xxHash64 hex digest
            "perceptual_hash": str|None,  # dhash hex or None
            "file_size": int,             # Size in bytes
            "dimensions": tuple|None,     # (width, height) or None
            "timestamp": str|None,        # ISO timestamp or None
            "metadata": dict,             # Processor-specific metadata
            "_processor": str,            # Processor name (added automatically)
        }
    """
    for file_path in media_files:
        yield {
            "source_path": str(file_path),
            "content_hash": compute_hash(file_path),
            # ... other fields
        }
```

**Recommended pattern:** In `build_catalog()`, first check whether `input_dir` is already preprocessed in the standardized “source_path format”, and if so, yield from `ProcessorBase.catalog_from_preprocessed_if_available(...)`. Otherwise, either:

- yield a catalog directly from the raw export format, or
- return nothing (but then this processor will not participate fully in cross-processor dedup unless preprocessing is always run first).

### Processing with Dedup Manifest

Update your `process()` method to accept and use the dedup manifest:

```python
@classmethod
def process(
    cls,
    input_dir: str,
    output_dir: Optional[str] = None,
    dedup_manifest: Optional[Dict[str, Any]] = None,  # New parameter
    **kwargs
) -> bool:
    """Process the export, respecting dedup manifest if provided.
    
    When dedup_manifest is provided, skip files marked as secondary
    (they will be handled by their primary processor).
    """
    # Recommended: use ProcessorBase.should_skip_for_dedup()
    for file_path in media_files:
        if ProcessorBase.should_skip_for_dedup(
            str(file_path), dedup_manifest, "YourProcessorName"
        ):
            continue
        # Process file normally...
```

### Helper Functions

Use these utilities from the `common` subpackages:

```python
# Deduplication utilities
from common.dedup.config import get_dedup_config
from common.dedup.service import is_file_primary_by_hash
from common.dedup.hashing import (
    compute_content_hash,
    compute_perceptual_hash_with_dims,
)

# Media processing
from common.media.exiftool import batch_write_metadata_requests, MetadataWriteRequest

# Logging
from common.output.logging import get_processor_logger

# Progress bars
from common.output.progress import PHASE_PROCESS, progress_bar

# File utilities
from common.files.utils import short_path, batch_correct_extensions

# Get dedup configuration
config = get_dedup_config()

# Check if perceptual hashing is enabled
# compute_perceptual_hash_with_dims returns both hash and dimensions in one file open
if config.use_perceptual:
    perceptual_hash, dimensions = compute_perceptual_hash_with_dims(file_path)

# Check if file is primary using hash-based lookup (works with preprocessed files)
content_hash = compute_content_hash(file_path)
if is_file_primary_by_hash(dedup_manifest, content_hash, "YourProcessorName"):
    # Copy and process file
```

**Module Structure:**

The `common` package is organized into subpackages:

- `common.dedup` - Deduplication (hashing, database, service, catalog)
- `common.media` - Media processing (exiftool, overlay, encoder)
- `common.files` - File operations (scanner, filter, utils)
- `common.config` - Configuration (processor, env)
- `common.output` - Output utilities (console, logging, progress, paths)
- `common.upload` - Upload utilities (immich, targets)

**Note:** The hash-based lookup (`is_file_primary_by_hash`) is preferred over path-based lookup (`is_file_primary`) because it works correctly even when files are preprocessed to temporary directories before processing.

### Example Implementation

See `processors/snapchat_memories/processor.py` for a complete example of deduplication support.

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

- Instaloader (uses file mtimes as fallback)
- Any export lacking JSON/metadata files
- Exports with incomplete metadata

**See also:**

- Look at `processors/instaloader/preprocess.py` for a real example
- See [Design Decisions](Design-Decisions#filesystem-timestamp-handling) for detailed rationale

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

- [Design Decisions](Design-Decisions) - Understand Memoria's architecture
- [Usage](Usage) - Command-line options for testing
- [Getting Started](Getting-Started) - Development environment setup
