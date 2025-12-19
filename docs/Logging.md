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
- Main log file: `logs/media_processor_YYYYMMDD_HHMMSS.log`
- Per-export logs: `logs/log-{export-name}_YYYYMMDD_HHMMSS.log` (when using `--originals`)

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
