# Processor Architecture Refactor Proposal

## Overview

This document proposes architectural changes to clarify the separation of concerns between processors and common utilities. The goal is to ensure processors handle **only format-specific logic**, while all common operations live in shared utilities.

## Core Philosophy

Processors have exactly three responsibilities:

1. **Take in a raw data export** - Detection and validation
2. **Rebuild the export in a standardized format** - Preprocessing to `metadata.json` + `media/`
3. **Build fields to pass to common utils** - Format-specific data extraction

Everything else should be common code.

---

## Proposed Changes

### 1. Timestamp Normalization

#### Problem

Currently, each preprocessor outputs timestamps in different formats:

| Preprocessor | Current Output |
|--------------|----------------|
| Google Photos | `2018-04-08T16:25:44Z` |
| Google Voice | `2016-05-18 11:00:23` |
| Google Chat | `2016-05-04 04:20:19` |
| Instagram Messages | `2017-09-22 06:33:00` |
| Discord | `2024-01-15 10:30:00 UTC` |
| Snapchat | `2024-01-15 10:30:00 UTC` |

This forces processing code to accept a `timestamp_format` parameter from each processor.

#### Solution

Standardize all timestamps to **ISO 8601 UTC** at the preprocessing boundary.

**New file: `common/timestamps.py`**

```python
from datetime import datetime, timezone

# The one true format
STANDARD_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

def normalize_timestamp(dt: datetime) -> str:
    """Convert any datetime to standardized ISO 8601 UTC string.
    
    Args:
        dt: Datetime object (naive assumed UTC, aware converted to UTC)
        
    Returns:
        ISO 8601 string: "2024-01-15T10:30:00Z"
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(STANDARD_TIMESTAMP_FORMAT)


def parse_standard_timestamp(ts_str: str) -> datetime:
    """Parse standardized ISO 8601 timestamp string.
    
    Args:
        ts_str: ISO 8601 string from metadata.json
        
    Returns:
        Timezone-aware datetime in UTC
    """
    return datetime.strptime(ts_str, STANDARD_TIMESTAMP_FORMAT).replace(
        tzinfo=timezone.utc
    )


def to_exif_format(ts_str: str) -> str:
    """Convert standard timestamp to EXIF format.
    
    Args:
        ts_str: ISO 8601 string: "2024-01-15T10:30:00Z"
        
    Returns:
        EXIF format: "2024:01:15 10:30:00"
    """
    dt = parse_standard_timestamp(ts_str)
    return dt.strftime("%Y:%m:%d %H:%M:%S")
```

**Preprocessor changes:**

Each preprocessor's `parse_timestamp()` method should:
1. Parse the export's native format into a `datetime` object
2. Call `normalize_timestamp(dt)` to get the standard string

```python
# Before (google_chat/preprocess.py)
def parse_timestamp(self, timestamp_str: str) -> Optional[str]:
    dt = datetime.strptime(timestamp_str, "%A, %B %d, %Y at %I:%M:%S %p %Z")
    return dt.strftime("%Y-%m-%d %H:%M:%S")  # Non-standard!

# After
from common.timestamps import normalize_timestamp

def parse_timestamp(self, timestamp_str: str) -> Optional[str]:
    dt = datetime.strptime(timestamp_str, "%A, %B %d, %Y at %I:%M:%S %p %Z")
    return normalize_timestamp(dt)  # "2016-05-04T04:20:19Z"
```

**Processing changes:**

Common utilities no longer need format parameters:

```python
# Before
update_file_timestamps(output_path, timestamp_str, "%Y-%m-%d %H:%M:%S")

# After
update_file_timestamps(output_path, timestamp_str)  # Assumes ISO 8601
```

---

### 2. Centralized Processing Pipeline

#### Problem

Every processor duplicates ~300-400 lines of nearly identical `_process_working_directory()` logic:

- Load `metadata.json`
- Scan media directory
- Filter by dedup manifest
- Match files to metadata
- Generate filenames
- Copy files in parallel
- Correct extensions
- Batch EXIF operations
- Handle unmatched files
- Print summary

#### Solution

Create a generic processing pipeline in `common/processing.py` that processors configure with callbacks.

**New function: `process_standardized_export()`**

```python
# common/processing.py

from dataclasses import dataclass
from typing import Callable, Dict, List, Any, Optional
from pathlib import Path


@dataclass
class ProcessorConfig:
    """Configuration for generic export processing."""
    
    processor_name: str
    """Name of processor (e.g., "Discord", "Google Photos")"""
    
    build_metadata_request: Callable[[str, dict, str], "MetadataWriteRequest"]
    """Function to build MetadataWriteRequest from (file_path, metadata, username)"""
    
    build_identifier: Callable[[dict, str], str]
    """Function to build filename identifier from (metadata, username)"""
    
    filename_prefix: str
    """Prefix for generated filenames (e.g., "discord", "gphotos")"""
    
    get_timestamp: Callable[[dict], Optional[str]] = lambda m: m.get("timestamp")
    """Function to extract timestamp from metadata dict"""
    
    get_media_files_key: str = "media_files"
    """Key in metadata for list of media filenames"""


def process_standardized_export(
    working_dir: Path,
    output_dir: Path,
    config: ProcessorConfig,
    workers: Optional[int] = None,
    dedup_manifest: Optional[Dict[str, Any]] = None,
) -> bool:
    """Generic processing pipeline for standardized exports.
    
    Handles all common operations:
    - Loading metadata.json
    - Scanning media directory
    - Dedup filtering
    - Filename generation
    - Parallel file copying
    - Extension correction
    - Batch EXIF processing
    - Unmatched file handling
    - Summary printing
    
    Args:
        working_dir: Path to preprocessed directory (has metadata.json + media/)
        output_dir: Output directory for processed files
        config: Processor-specific configuration and callbacks
        workers: Number of parallel workers
        dedup_manifest: Optional deduplication manifest
        
    Returns:
        True if processing succeeded
    """
    # 1. Load metadata.json
    metadata = load_metadata(working_dir / "metadata.json")
    export_username = metadata.get("export_info", {}).get("export_username", "unknown")
    
    # 2. Scan media directory
    media_dir = working_dir / "media"
    media_files = [f for f in media_dir.iterdir() if f.is_file() and get_media_type(f)]
    
    # 3. Filter by dedup manifest
    if dedup_manifest:
        media_files, skipped = filter_by_dedup_manifest(
            media_files, dedup_manifest, config.processor_name
        )
    
    # 4. Build filename index from metadata
    filename_index = build_filename_index(metadata, config.get_media_files_key)
    
    # 5. Match files and generate output filenames
    filename_gen = FilenameGenerator(config.filename_prefix)
    matched, unmatched = match_and_generate_filenames(
        media_files, filename_index, filename_gen,
        config.build_identifier, config.get_timestamp, export_username
    )
    
    # 6. Copy files in parallel
    results = copy_files_parallel(matched, output_dir, workers)
    
    # 7. Correct extensions
    file_paths = [r.output_path for r in results if r.success]
    corrections = batch_correct_extensions(file_paths)
    
    # 8. Batch EXIF processing
    process_exif_batch(
        file_paths, matched, export_username, config.build_metadata_request
    )
    
    # 9. Handle unmatched files
    handle_unmatched_files(unmatched, output_dir)
    
    # 10. Save dedup references if needed
    if dedup_manifest and skipped:
        save_dedup_metadata(dedup_manifest, skipped, config.processor_name, output_dir)
    
    # 11. Print summary
    print_processing_summary(...)
    
    return True
```

**Processor changes:**

Processors become thin wrappers that define only format-specific logic:

```python
# processors/discord/processor.py

from common.processing import process_standardized_export, ProcessorConfig

class DiscordProcessor(ProcessorBase):
    
    @staticmethod
    def process(input_dir: str, output_dir: Optional[str] = None, **kwargs) -> bool:
        # Preprocessing (if needed)
        working_dir = ensure_preprocessed(input_dir, DiscordPreprocessor)
        
        # Configure processor-specific callbacks
        config = ProcessorConfig(
            processor_name="Discord",
            build_metadata_request=_build_metadata_request,
            build_identifier=_build_discord_identifier,
            filename_prefix="discord",
            get_timestamp=lambda m: m.get("message", {}).get("timestamp"),
        )
        
        # Delegate to generic pipeline
        return process_standardized_export(
            working_dir=working_dir,
            output_dir=Path(output_dir) / "messages",
            config=config,
            workers=kwargs.get("workers"),
            dedup_manifest=kwargs.get("dedup_manifest"),
        )


# These stay in the processor - they are format-specific
def _build_metadata_request(file_path: str, message_info: dict, username: str):
    """Build MetadataWriteRequest from Discord message format."""
    ...

def _build_discord_identifier(message_info: dict, username: str) -> str:
    """Build filename identifier from Discord channel info."""
    ...
```

---

### 3. What Stays in Processors

After refactoring, processors contain only:

| Component | Location | Reason |
|-----------|----------|--------|
| `detect()` | Processor | Format-specific directory/file patterns |
| `get_name()` | Processor | Static metadata |
| `get_priority()` | Processor | Static metadata |
| `Preprocessor` class | Processor | Format-specific parsing logic |
| `_build_metadata_request()` | Processor | Format-specific field mapping |
| `_build_identifier()` | Processor | Format-specific naming policy |
| `_build_description()` | Processor | Format-specific description text |

### 4. What Moves to Common

| Component | New Location | Current Duplication |
|-----------|--------------|---------------------|
| `_process_working_directory()` | `common/processing.py` | 6+ processors |
| Dedup filtering loop | `common/processing.py` | All processors |
| File copy + timestamp update | `common/processing.py` | All processors |
| EXIF batch orchestration | `common/processing.py` | All processors |
| Unmatched file handling | `common/processing.py` | All processors |
| Timestamp normalization | `common/timestamps.py` | All preprocessors |
| Timestamp parsing (standard) | `common/timestamps.py` | All processors |

---

## Migration Plan

### Phase 1: Timestamp Standardization

1. Create `common/timestamps.py` with `normalize_timestamp()` and `parse_standard_timestamp()`
2. Update each preprocessor's `parse_timestamp()` to use `normalize_timestamp()`
3. Update `update_file_timestamps()` to use standard format only
4. Update EXIF writing to use `to_exif_format()`
5. Run tests to verify timestamp handling

### Phase 2: Processing Pipeline

1. Create `ProcessorConfig` dataclass
2. Implement `process_standardized_export()` with all common logic
3. Migrate one processor (Discord) as proof of concept
4. Migrate remaining processors one at a time
5. Remove duplicated code from processors

### Phase 3: Cleanup

1. Remove processor-specific `_process_working_directory()` functions
2. Remove format-specific timestamp handling from common utilities
3. Update documentation
4. Update tests

---

## Code Size Impact

| Processor | Current Lines | After Refactor | Reduction |
|-----------|---------------|----------------|-----------|
| Discord | ~800 | ~250 | 69% |
| Snapchat Messages | ~1200 | ~350 | 71% |
| Google Photos | ~730 | ~250 | 66% |
| Instagram Messages | ~575 | ~200 | 65% |
| Google Chat | ~600 | ~200 | 67% |

**Total reduction: ~2500 lines of duplicated code**

---

## Benefits

1. **Single source of truth** for processing logic
2. **Easier maintenance** - bug fixes apply to all processors
3. **Clearer separation** - processors only contain format-specific code
4. **Simpler testing** - test common pipeline once, test format-specific logic per processor
5. **Easier onboarding** - new processors just define callbacks, not full pipelines
6. **Consistent behavior** - all processors handle edge cases identically

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Processors have subtle differences | Review each processor's current behavior before migrating |
| Breaking existing functionality | Comprehensive test coverage before changes |
| Callback API too restrictive | Design API with escape hatches for special cases |
| Migration takes too long | Phase approach allows incremental progress |

---

## Open Questions

1. **Overlay handling** - Snapchat Messages has overlay processing. Should this be a callback or special case?
2. **Live Photo pairing** - Google Photos pairs images with videos. How does this fit the generic pipeline?
3. **Consolidated exports** - Some processors support multiple exports. Keep as separate method?

---

## Related Documentation

- [Adding Processors](Adding-Processors.md) - Will need update after refactor
- [Design Decisions](Design-Decisions.md) - Document this architectural decision
- [DRY Violations](DRY-Violations.md) - This refactor addresses many items



