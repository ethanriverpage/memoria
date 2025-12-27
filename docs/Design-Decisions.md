# Design Decisions

This document explains key architectural and UX decisions in Memoria and the rationale behind them.

## Metadata Embedding Over Sidecar Files

**Decision:** All metadata is embedded directly into media files using EXIF/XMP tags.

**Rationale:**

- Files remain self-contained and portable - move a file anywhere and it retains its context
- No dependency on external databases or sidecar files that can get separated
- Works with any photo management software that reads EXIF (nearly all of them)
- Future-proof: EXIF is an industry standard dating back to 1995
- Simplifies backup and migration - just copy the files

**Trade-offs:**

- Cannot add metadata to formats that don't support EXIF without re-encoding
- Modifying metadata later requires specialized tools (exiftool)
- Slightly increases file size (typically <1% overhead)

## Original Filename Preservation

**Decision:** Original source filenames are embedded in file metadata using `XMP:PreservedFileName`.

**Rationale:**

- Files are renamed during processing (to timestamp-based names)
- Original filenames may contain context (e.g., `IMG_1234.jpg`, `Screenshot_2023-01-15`)
- Preserves traceability back to source export
- Uses dedicated XMP field rather than overloading description/caption fields
- Standard field used by Adobe Lightroom and other professional tools

**Implementation:**

- **XMP-supported formats** (JPEG, PNG, HEIC, MP4, MOV, etc.): `XMP:PreservedFileName` via exiftool
- **MKV files** (Snapchat overlays): `ORIGINAL_FILENAME` Matroska tag via FFmpeg
- **Unsupported formats** (BMP, WebM): Skipped gracefully

**Trade-offs:**

- Adds small amount of metadata overhead
- Not all viewers display this field (but it's always readable via exiftool)
- BMP and WebM files cannot store this information

**Alternatives considered:**

- **Sidecar files** (`.json`, `.xmp`): Easy to lose or separate from media
- **Database**: Creates vendor lock-in and portability issues
- **Folder structure**: Loses context when files are moved

## Flat Directory Structure

**Decision:** Output is organized by platform/username, not by album or conversation.

**Rationale:**

- Simplifies deduplication (Google Photos exports have 30-60% duplicates across albums)
- Avoids complex nested structures that are difficult to navigate and maintain
- Album/conversation context is preserved in EXIF metadata fields
- Immich and other photo management systems can recreate albums from metadata during import
- Prevents questions about which copy in which folder is the "canonical" version
- Reduces total file count and storage requirements significantly

**Trade-offs:**

- Users expecting folder-based organization may find this surprising
- Cannot browse by album in a file manager (must use photo management software)
- All context is in metadata, not visually obvious from folder structure

**Alternatives considered:**

- **Preserve album folders**: Would create massive duplication (30-60% wasted space)
- **Symlinks for albums**: Complex to manage, doesn't work across filesystems
- **Hardlinks for albums**: Confusing to users, risky for modification operations

**Related:** See [Deduplication](Deduplication) for implementation details.

## Directory Naming Convention Required

**Decision:** Export directories must follow `platform-username-YYYYMMDD` format.

**Rationale:**

- Platform exports don't include usernames in their internal structure
- Enables batch processing of multiple accounts without manual configuration
- Provides clear context in output filenames and logs
- Allows automatic Immich album naming without prompting user for input
- Makes output self-documenting (you can tell what's what from filenames alone)
- Supports processing of multiple accounts from the same platform

**Trade-offs:**

- Requires manual renaming of extracted exports
- Not immediately obvious to new users
- Documentation must explain the requirement clearly

**Alternatives considered:**

- **Auto-detect username**: Not possible - platforms don't include this in export structure
- **Prompt for username**: Breaks non-interactive batch processing
- **Configuration file**: Adds complexity and another file to manage

**Why not auto-detect:**
Instagram export: `your_instagram_activity/` doesn't contain the actual username
Google export: `Takeout/Google Photos/` doesn't specify which account
Snapchat export: `memories/` and `messages/` have no account identifier

## Google Photos Deduplication is Mandatory

**Decision:** Deduplication cannot be disabled for Google Photos processing.

**Rationale:**

- Google Photos exports contain massive duplication (same photo in multiple albums)
- Processing duplicates wastes time (2-3x longer), disk space (30-60% more), and upload bandwidth
- Album membership is preserved in metadata - no information is lost
- No practical use case exists for keeping duplicate copies
- Simplifies code and prevents user confusion about options
- Deduplication is fast (xxhash) and reliable (negligible collision probability)

**Trade-offs:**

- Users cannot preserve album folder structure with duplicate files
- If someone genuinely needs duplicates, Memoria won't work for them
- No flexibility for edge cases

**Why it can't be disabled:**
The entire Google Photos processor architecture assumes deduplication. Making it optional would require:

- Duplicate processing logic paths
- Complex configuration management
- Significantly more complex output structure
- Testing and maintenance of both paths

The benefits don't justify the complexity.

**Related:** See [Deduplication](Deduplication) for technical implementation.

## Sequential Uploads in Parallel Mode

**Decision:** When processing multiple exports in parallel, uploads happen sequentially.

**Rationale:**

- Prevents network saturation and bandwidth exhaustion
- Reduces server load and upload failures
- Improves upload reliability (fewer timeouts and connection errors)
- Still allows uploads to begin while other exports are processing
- Easier to monitor and debug (clear progress indication)
- Reduces concurrent file handle usage

**Trade-offs:**

- Upload phase is not parallelized
- Total completion time is longer than if all uploads ran in parallel
- Advanced users may want fully parallel uploads

**Why sequential:**
Testing showed that parallel uploads to Immich:

- Saturate network bandwidth, causing timeouts
- Overload Immich server, causing import failures
- Use excessive file handles, hitting system limits
- Provide minimal time savings (network is the bottleneck)

Sequential uploads with concurrent processing provides the best balance.

**Related:** See [Immich Upload](Immich-Upload) for implementation details.

## Non-Destructive Processing

**Decision:** Original exports are never modified; all operations work on copies.

**Rationale:**

- Safety: Users can always return to original state
- Confidence: Users can experiment without fear
- Debugging: Original data available for comparison if issues arise
- Re-processing: Can run multiple times with different settings
- Standard practice for data processing tools

**Trade-offs:**

- Requires double the disk space during processing
- Slower than in-place modification
- Users must manage both original and processed copies

**Why this is non-negotiable:**
Working with irreplaceable personal memories requires maximum safety. The disk space cost is worth the peace of mind.

## Auto-Discovery Processor System

**Decision:** Processors are automatically discovered and loaded from the `processors/` directory.

**Rationale:**

- Extensibility: Easy to add new processors without modifying core code
- Modularity: Each processor is self-contained
- Maintainability: Changes to one processor don't affect others
- Testing: Processors can be tested in isolation
- Community contributions: Clear pattern for adding support for new platforms

**Trade-offs:**

- Slightly more complex than hardcoded processor list
- Requires consistent naming and structure conventions
- Potential for processor conflicts if detection logic overlaps

**Implementation:**
Each processor:

- Lives in its own directory under `processors/`
- Implements `ProcessorBase` abstract class
- Provides `detect()`, `get_name()`, `get_priority()`, and `process()` methods
- Exports `get_processor()` function for auto-discovery

Many processors also have a preprocessor that extends `PreprocessorBase`, providing:

- Built-in deduplication via `copy_with_dedup()`
- Context merging for duplicate metadata
- Hash caching for performance
- Thread-safe logging and failure tracking

**Related:** See [Adding Processors](Adding-Processors) for development guide.

## Common Module Organization

**Decision:** The `common` package is organized into focused subpackages rather than a flat structure.

**Rationale:**

- Clear separation of concerns (dedup, media, files, output, upload)
- Easier to navigate and understand codebase
- Reduces import conflicts and circular dependencies
- Makes it clear which utilities belong together
- Easier to test in isolation

**Structure:**

```
common/
├── dedup/      # Deduplication (hashing, database, service, catalog)
├── media/      # Media processing (exiftool, overlay, encoder)
├── files/      # File operations (scanner, filter, utils)
├── config/     # Configuration (processor, env)
├── output/     # Output utilities (console, logging, progress, paths)
├── upload/     # Upload utilities (immich, targets)
└── preprocess/ # Preprocessing utilities (context_mergers)
```

**Trade-offs:**

- Longer import paths (e.g., `from common.dedup.hashing import compute_content_hash`)
- Need to update imports when reorganizing

**Why subpackages:**

The flat structure became unwieldy with 20+ modules. Grouping by functionality:

- Makes it obvious where to find things
- Enables `from common.dedup import ...` style imports
- Supports re-exports from subpackage `__init__.py` for convenience

**Related:** See [Adding Processors](Adding-Processors) for import examples.

## Priority-Based Processor Ordering

**Decision:** Processors have priority values that determine execution order when multiple match.

**Rationale:**

- Ensures specific processors run before generic ones
- Allows graceful handling of ambiguous exports
- Provides predictable, controllable behavior
- Enables processors to make assumptions based on order

**Why it matters:**
A Google Takeout export may contain:

- Google Photos (priority 50)
- Google Chat (priority 50)
- Google Voice (priority 50)

All three processors match and should run. Priority ensures they run in a sensible order (processors with higher priority run first).

**Trade-offs:**

- Requires developers to choose appropriate priority values
- Potential for priority conflicts (mitigated by documentation)



## Default to Immich Upload

**Decision:** Immich upload is enabled by default when configured; must use `--skip-upload` to disable.

**Rationale:**

- Most users who configure Immich want to upload by default
- Forgetting to upload is more common than accidental upload
- Explicit `--skip-upload` flag makes intent clear
- Testing/dry-run users can easily skip upload

**Trade-offs:**

- Users may accidentally upload test runs
- Requires `--skip-upload` for every test invocation

**Mitigation:**
Documentation emphasizes:

- Don't configure Immich credentials until ready
- Use `--skip-upload` for testing
- `--upload-only` mode for re-uploading later

## Filesystem Timestamp Handling

**Decision:** Processors must read filesystem modification times BEFORE any file operations (copying, moving, or modifying).

**Rationale:**

- Some platforms don't provide metadata files for all content
- Filesystem modification times are the only timestamp source for some files
- File operations (copy, touch, modify) update the modification time to "now"
- Once updated, the original timestamp is permanently lost
- This would cause files to be dated incorrectly (to processing date instead of capture date)

**Implementation:**

- Preprocessing step extracts ALL metadata before file operations
- Filesystem timestamps are read and stored in metadata structures
- File copying happens AFTER all metadata extraction is complete
- EXIF embedding uses the stored timestamps, not current filesystem times

**Why this matters:**
Platforms affected by this:

- **Instaloader**: Relies on file modification times as fallback when filename parsing fails
- **Google Photos**: Some older items lack JSON metadata and use filesystem times
- **Snapchat Messages**: Uses file modification timestamps to match overlay files to media files (critical for correct overlay embedding)
- **Any platform with incomplete metadata**: Filesystem time may be the only available date

**Snapchat Messages - Special Case:**

Snapchat Messages has a unique dependency on filesystem timestamps - they're used not just for dating files, but for **matching overlays to media files**. The export provides separate files for media (photos/videos) and overlays (PNG images) without explicit links between them. The ONLY way to match them is by comparing their modification timestamps.

If timestamps are read after copying:

- Both media and overlay timestamps change to "now"
- They might still match each other (both stamped at the same moment)
- But if files are copied milliseconds apart, timestamps won't align
- Overlay matching fails completely
- Videos end up without their intended text/drawings/stickers

This makes timestamp preservation absolutely critical for Snapchat Messages processing.

**Example of what would go wrong without this:**

```
1. Original file: photo.jpg (modified: 2020-01-15)
2. Processor copies file to output → New file now shows: 2024-11-22 (today)
3. Processor reads file time for EXIF → Embeds 2024-11-22 as capture date ✗ WRONG
```

**Correct approach:**

```
1. Original file: photo.jpg (modified: 2020-01-15)
2. Processor reads and stores file time: 2020-01-15
3. Processor copies file to output
4. Processor embeds stored timestamp (2020-01-15) into EXIF ✓ CORRECT
```

**Trade-offs:**

- Requires careful ordering of operations in processor code
- More complex than just reading timestamps when needed
- Preprocessing step adds a small amount of processing time
- But ensures data accuracy, which is non-negotiable

**Developer guidelines:**
When creating custom processors:

- Always read filesystem times during preprocessing/metadata extraction
- Never read filesystem times after copying or modifying files
- Store timestamps in your metadata structures
- Use stored timestamps for EXIF embedding

**Related:** See [Adding Processors](Adding-Processors) for implementation guidelines and [Common Gotchas](Common-Gotchas#filesystem-timestamps-can-be-fragile) for user-facing explanation.

## Conclusion

These design decisions prioritize:

1. **Data safety** (non-destructive processing)
2. **Portability** (metadata-embedded files)
3. **Efficiency** (deduplication, flat structure)
4. **Reliability** (battle-tested tools like ExifTool and FFmpeg)
5. **Future-proofing** (industry-standard formats)

Some decisions limit flexibility, but they create a tool that "just works" for the common case while remaining safe and predictable.

For specific behaviors and gotchas, see [Common Gotchas](Common-Gotchas).
