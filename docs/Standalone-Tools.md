# Standalone Tools

The `standalone/` directory contains utility scripts for analyzing and comparing media exports.

## Find Duplicates

Analyzes media exports to identify duplicate files across directories using fast xxhash-based content hashing.

**Features:**

- Fast xxhash (128-bit) for efficient duplicate detection
- Multiprocessing support for large exports
- Filters out overlays, thumbnails, and system files
- Generates detailed JSON reports with file paths and hashes
- Supports cache files to speed up repeated analysis

**Usage:**

```bash
# Analyze single directory
./standalone/find_duplicates.py /path/to/export

# Analyze multiple directories
./standalone/find_duplicates.py /path/to/export1 /path/to/export2 /path/to/export3

# With custom output file
./standalone/find_duplicates.py /path/to/export -o duplicates_report.json

# Use cached hashes (much faster for repeated runs)
./standalone/find_duplicates.py /path/to/export --cache hash_cache.pkl

# Control parallelism
./standalone/find_duplicates.py /path/to/export --workers 8
```

**Output:**

- Console: Summary statistics of duplicates found
- JSON file: Detailed report with file paths, hashes, and duplicate groups

## Compare Exports

Compares two processed media exports and logs all differences including directory structure, file lists, content hashes, EXIF/XMP metadata, and timestamps.

**Features:**

- Directory structure comparison
- File content comparison (via hash)
- EXIF/XMP metadata comparison (requires exiftool)
- File size and timestamp comparison
- Detailed logging to file and console
- Ignore pattern support

**Usage:**

```bash
# Basic comparison
./standalone/compare_exports.py /path/to/export1 /path/to/export2

# With custom log file
./standalone/compare_exports.py /path/to/export1 /path/to/export2 -o comparison.log

# Skip content hashing (faster, structure only)
./standalone/compare_exports.py /path/to/export1 /path/to/export2 --skip-content

# Skip metadata comparison
./standalone/compare_exports.py /path/to/export1 /path/to/export2 --skip-metadata

# Ignore specific patterns
./standalone/compare_exports.py /path/to/export1 /path/to/export2 --ignore .DS_Store --ignore thumbs.db
```

**Output:**

- Console: Summary of differences found
- Log file: Detailed comparison report with all differences

## Related Documentation

- [Usage](Usage) - Main usage guide
- [Deduplication](Deduplication) - Deduplication implementation details

