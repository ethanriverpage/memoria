# Logging

## Basic Logging (Default)

Without the `--verbose` flag, memoria outputs:

- Console output at INFO level
- Processing progress and summary
- No log files created

## Verbose Logging

Enable detailed logging with `--verbose`:

```bash
./memoria.py /path/to/export --verbose
```

### What Gets Logged

- Console output at DEBUG level with timestamps
- Main log file: `logs/media_processor_YYYYMMDD_HHMMSS.log`
- Per-export logs: `logs/log-{export-name}_YYYYMMDD_HHMMSS.log` (when using `--originals`)
- Detailed processing information for debugging

### Log Files Location

All logs are stored in the `logs/` directory, created automatically in the project root.

## Examples

```bash
# Basic verbose logging
./memoria.py /path/to/export --verbose

# Verbose with parallel processing (creates per-export logs)
./memoria.py --originals /exports -o /output --parallel-exports 2 --verbose
```

## Related Documentation

- [Usage](Usage) - Command-line options
- [Parallel Processing](Parallel-Processing) - Parallel export processing

