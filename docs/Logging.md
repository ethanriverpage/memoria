# Logging

Memoria uses [Rich](https://rich.readthedocs.io/) for enhanced terminal output, providing colorized logs, styled progress bars, tables, and panels.

## Console Output

### Default Mode

Without any verbosity flags, memoria outputs:

- Minimal console output (errors only)
- Styled progress bars showing phase, progress, and timing
- Summary tables after catalog phase
- Styled panels for processing completion
- No log files created

### Verbosity Levels

Memoria supports tiered verbosity with `-v` flags:

| Level | Flag | Console Output |
|-------|------|----------------|
| Default | (none) | Errors, progress bars, and summaries only |
| Verbose | `-v` | INFO level: phase summaries, per-export totals |
| Debug | `-vv` | DEBUG level: file-level operations, matching details |

```bash
# Default mode - clean output with progress bars
./memoria.py /path/to/export

# Verbose mode - phase summaries and totals
./memoria.py /path/to/export -v

# Debug mode - file-level details
./memoria.py /path/to/export -vv
```

### When to Use Each Level

- **Default**: Normal operation, watching progress
- **Verbose (`-v`)**: Troubleshooting, understanding what processors are running
- **Debug (`-vv`)**: Investigating specific file matching or metadata issues

## Standardized Logging with ProcessorLogger

Processors use the `ProcessorLogger` class for consistent, single-line events (starts, counts, copies, EXIF). Preprocessors may emit their own report logs, but they follow the same one-event/one-line rules.

## Noise-Reduction Rules (applied across all processors)

- One file event → one line (copy/transform/skip).
- Use `short_path()` for any path in per-file logs; avoid absolute paths.
- Never log temp filenames on success.
- ffmpeg/ffprobe commands are logged **only on failure** (with return code + trimmed stderr).
- Aggregate high-cardinality loops into counters (optional tiny DEBUG sample).
- Human-readable one-liners (no strict key=value), stable identifiers (export + filename).

### Examples (DEBUG)

- Instagram Public Media copy (Instaloader format):
  - `proc=instagram_public_media src=2018-08-01_18-08-04_UTC.mp4 dest=insta-posts-ethantastic_-20180801.mp4 type=posts`
- Overlay decision:
  - `proc=overlay video=2021-03-11_19-50-55.mp4 rotate=90 dims=1232x656->656x1232 bitrate=6.6Mbps encoder=hevc_vaapi overlay=memories_overlay.png out=snap-memories-ethriverpage-20110311.mkv`
- Dedup validation summary:
  - `Dedup validation rejects: gps=1234 aspect=456`

### Processor audit (all 9)

- High-cardinality per-item logs collapsed to one-line summaries:
  - Instagram Messages, Instagram Public Media preprocessors.
- Shared noise hotspots updated:
  - `common/media/probe.py` (quiet on success), `common/media/overlay.py` (one decision line; commands on failure), `common/dedup/service.py` (aggregated rejects).
- Path shortening and banner cleanup applied to Discord, Google Chat, Google Voice, iMessage (including iMazing-format exports), Snapchat Memories, Snapchat Messages.

### Using ProcessorLogger

```python
from common.output.logging import get_processor_logger

# Get a processor logger (at module level)
log = get_processor_logger(__name__)

# Use standardized methods
log.processor_start("Google Photos Processor")
log.loading_metadata("/path/to/metadata.json")
log.found_files(150, "media")
log.processing_start("/output/photos")
log.copied_files(145, 5)
log.exif_batch_start()
log.exif_written(145)
```

**Module Location:** The logging utilities are in `common/output/logging.py`. The `get_processor_logger()` function returns a `ProcessorLogger` instance that wraps standard Python logging with semantic methods for common operations.

### Available Methods

#### Header/Phase Markers

- `processor_start(name)` - Log processor start with separator
- `preprocessor_start(name, export_path, output_path)` - Log preprocessor start
- `phase_separator()` - Log a separator line

#### Metadata and Scanning

- `loading_metadata(path)` - Log metadata loading
- `export_username(username)` - Log detected username
- `scanning_directory(path)` - Log directory scan
- `found_files(count, file_type)` - Log files found
- `found_items(count, item_type)` - Log items found (generic)

#### Matching and Processing

- `building_index(index_type)` - Log index building
- `matched_files(matched, unmatched)` - Log matching results
- `skipped_secondary(count)` - Log dedup skipped files
- `processing_start(output_dir)` - Log processing phase start
- `using_workers(count)` - Log worker count (DEBUG level)
- `copied_files(success, failed)` - Log copy results
- `created_files(success, needs_exif)` - Log file creation results

#### EXIF Operations

- `exif_batch_start()` - Log EXIF batch start
- `exif_rebuilding(count)` - Log EXIF rebuild
- `exif_written(count)` - Log EXIF write completion

#### Preprocessor Operations

- `downloading(count, item_type)` - Log download start
- `parsing(item_type)` - Log parsing phase
- `loaded_mappings(count, mapping_type)` - Log loaded mappings
- `saved_metadata(path)` - Log metadata save
- `preprocessing_complete()` - Log preprocessing done

#### Directory Detection

- `using_preprocessed(path)` - Log using preprocessed export
- `using_cache(path)` - Log using cache
- `running_preprocessing()` - Log preprocessing start
- `preprocessing_to(path)` - Log preprocessing output path
- `preprocessing_done(path)` - Log preprocessing completion
- `raw_export_detected(path)` - Log raw export detection
- `already_preprocessed(path)` - Log already preprocessed

#### Errors and Warnings

- `file_not_found(path, file_type)` - Log file not found
- `processing_error(filename, error)` - Log processing error
- `copy_error(filename, error)` - Log copy error
- `parse_error(filename, error)` - Log parse error
- `missing_file(filename, context)` - Log missing file warning
- `no_metadata_for_file(filename)` - Log no metadata warning

#### Debug Level

- `detection_failed(processor_name, error)` - Log detection failure
- `generating_filenames()` - Log filename pre-generation
- `file_operation(operation, source, dest)` - Log file operations

### Accessing the Underlying Logger

For custom logging needs, access the standard logger via the `logger` property:

```python
log.logger.info("Custom message: %s", value)
log.logger.debug("Detailed info: %s", details)
```

### Log Level Guidelines

| Level | Use Case |
|-------|----------|
| DEBUG | File-level operations, worker counts, internal state |
| INFO | Phase transitions, summary counts, high-level progress |
| WARNING | Recoverable issues, skipped files, missing optional data |
| ERROR | Failures that stop processing or require user attention |

## Visual Elements

### Progress Bars

Progress bars display with a consistent format across all processing phases:

```
[Phase] Action  [=====>    ]  50/100  |  00:05  <  00:05
```

Components shown:

- **Phase**: Current processing phase (Preprocess, Process, EXIF, Catalog, Dedup)
- **Action**: Description of the current operation
- **Progress bar**: Visual indicator of completion
- **Count**: Items completed / total items
- **Elapsed time**: Time spent so far
- **Remaining time**: Estimated time to completion

### Summary Tables

After the catalog phase completes, a summary table displays results:

```
              Catalog Results
+------------------------------+-------+--------+
| Export                       | Files | Status |
+------------------------------+-------+--------+
| google-user-20251007         |    30 | OK     |
| instagram-user-20251007      |    15 | OK     |
| snapchat-user-20251017       |    25 | OK     |
+------------------------------+-------+--------+
| Total                        |    70 |        |
+------------------------------+-------+--------+
```

### Processing Panels

Processing completion summaries display in styled panels:

```
+-------------- Processing Complete --------------+
| Successfully processed: 25                      |
| Failed: 0                                       |
| EXIF structures rebuilt: 3                      |
| Total: 25                                       |
+-------------------------------------------------+
Output: /path/to/output/directory
```

The panel border is green for success, red if any failures occurred.

## File Logging

Log files use a plain text format (no Rich formatting) for compatibility with log analysis tools:

```
2025-12-17 14:30:45 - common.processing - INFO - Processing started
```

All logs are stored in the `logs/` directory, created automatically in the project root.

File logging is enabled when using `-v` or `-vv` flags:

- Main log file: `logs/{run_id}_YYYYMMDD_HHMMSS.memoria.log`
- Per-export logs: `logs/{export-name}_YYYYMMDD_HHMMSS.memoria.log` (when using `--originals`)

### File Log Level

By default, file logging captures DEBUG level (all details). For smaller log files, use the `--file-log-level` option:

```bash
# Default: full DEBUG logging (can produce large files)
./memoria.py /path/to/export -v

# Smaller logs: INFO level only (phase summaries, no file-level operations)
./memoria.py /path/to/export -v --file-log-level info
```

| Level | Contents | Typical Size |
|-------|----------|--------------|
| `debug` (default) | All details including file operations | Large (100K+ lines for big exports) |
| `info` | Phase summaries, counts, and warnings only | Much smaller (100-1000 lines) |

## Parallel Processing

When processing multiple exports in parallel, progress bars from subprocess workers are automatically suppressed to prevent interleaved output. The main process displays a summary table after all catalogs complete.

## Non-TTY Environments

Rich automatically detects non-TTY environments (CI pipelines, redirected output) and falls back to plain text output without colors or progress animations.

## Examples

```bash
# Default mode - minimal output with progress bars
./memoria.py /path/to/export

# Verbose mode - colorized logs with file output
./memoria.py /path/to/export -v

# Debug mode - all file-level details
./memoria.py /path/to/export -vv

# Verbose with parallel processing (creates per-export logs)
./memoria.py --originals /exports -o /output --parallel-exports 2 -v
```

## Related Documentation

- [Usage](Usage) - Command-line options
- [Parallel Processing](Parallel-Processing) - Parallel export processing
