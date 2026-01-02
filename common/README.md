# Common Modules

This directory contains shared modules used across all media processors.

## Purpose

The `common` module provides reusable functionality that is shared across multiple processor scripts, avoiding code duplication and ensuring consistency.

## Package Structure

```
common/
├── __init__.py              # Facade: re-exports common utilities for convenience
├── config/                  # Configuration utilities
│   ├── env.py               # Environment variable loading from .env files
│   ├── runtime.py           # Runtime config (worker count, bool parsing, cleanup flags)
│   └── dependencies.py      # System dependency checking (exiftool, ffmpeg, etc.)
├── dedup/                   # Deduplication subsystem
│   ├── catalog.py           # Catalog media files for dedup
│   ├── config.py            # Dedup configuration
│   ├── database.py          # Hash database persistence
│   ├── description.py       # Description building for deduped files
│   ├── hashing.py           # Content and perceptual hashing
│   └── service.py           # Deduplication service
├── failures/                # Failure tracking
│   └── tracker.py           # FailureTracker class
├── files/                   # File operations
│   ├── filter.py            # BannedFilesFilter for system file filtering
│   ├── naming.py            # Filename sanitization and generation
│   ├── scanner.py           # Directory scanning utilities
│   ├── timestamps.py        # File timestamp utilities
│   ├── types.py             # Media extension constants and type detection
│   └── utils.py             # File copy, MIME detection, path utilities
├── media/                   # Media processing
│   ├── encoder.py           # Video encoding with HW acceleration
│   ├── exiftool.py          # Batch EXIF operations
│   ├── overlay.py           # Image/video overlay embedding
│   └── probe.py             # Video metadata probing
├── metadata/                # Metadata schemas
│   ├── extraction.py        # Extract media entries from metadata
│   └── schema.py            # Preprocessed metadata schema definitions
├── output/                  # Console/logging/progress
│   ├── console.py           # Shared Rich console instance
│   ├── logging.py           # Centralized logging configuration
│   ├── paths.py             # Output path configuration
│   ├── progress.py          # Progress bar utilities
│   └── summary.py           # Processing summary display
├── preprocess/              # Preprocessing utilities
│   └── context_mergers.py   # Context merging for albums, Snapchat, iMessage
├── processing/              # Parallel processing and caching
│   ├── batch.py             # Batch parallel processing
│   ├── cache.py             # Preprocessing cache management
│   └── temp.py              # Temporary directory management
├── text/                    # Text processing
│   ├── normalize.py         # Unicode text normalization
│   └── parse.py             # Timestamp parsing utilities
├── upload/                  # Upload utilities
│   ├── immich.py            # Immich CLI wrapper
│   └── targets.py           # Upload target building
├── validation/              # Validation utilities
│   └── invariants.py        # Invariant validators
└── detection.py             # Username detection from directory names
```

## Usage

### Importing from the Facade

For convenience, commonly used utilities are re-exported from `common/__init__.py`:

```python
from common import (
    # File operations
    BannedFilesFilter,
    collect_media_files,
    iter_media_files,
    short_path,
    get_export_name,
    sanitize_filename,
    FilenameGenerator,
    update_file_timestamps,
    
    # Extension constants
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    ALL_MEDIA_EXTENSIONS,
    get_media_type,
    
    # Configuration
    default_worker_count,
    check_exiftool,
    check_ffmpeg,
    
    # Logging
    setup_logging,
    get_processor_logger,
    
    # Progress
    progress_bar,
    futures_progress,
    PHASE_PREPROCESS,
    PHASE_PROCESS,
    
    # Processing
    temp_processing_directory,
    process_batches_parallel,
    print_processing_summary,
    print_preprocessing_stats,
    
    # Failures
    FailureTracker,
)
```

### Importing from Subpackages

For more specific imports, use the subpackage directly:

```python
# File types and extensions
from common.files.types import IMAGE_EXTENSIONS, get_media_type

# Filename generation
from common.files.naming import FilenameGenerator, sanitize_filename

# Runtime configuration
from common.config.runtime import default_worker_count, parse_bool_env

# Text normalization
from common.text.normalize import normalize_text

# Failure tracking
from common.failures import FailureTracker

# Processing utilities
from common.processing import process_batches_parallel, temp_processing_directory

# Summary display
from common.output.summary import print_processing_summary
```

## Setup

### Development Installation (Recommended)

From the project root:

```bash
pip install -e .
```

This makes the `common` module available to all processors.

## Testing

Verify the setup works:

```bash
cd /path/to/memoria
python3 -c "from common import FailureTracker, FilenameGenerator; print('Success!')"
```

## Adding New Shared Modules

1. Add your module to the appropriate subpackage under `common/`
2. Update the subpackage `__init__.py` to export the new symbols
3. If it should be part of the facade API, update `common/__init__.py`
4. Import in processor scripts from the facade or directly from the subpackage
