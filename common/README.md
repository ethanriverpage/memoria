# Common Modules

This directory contains shared modules used across all media processors.

## Purpose

The `common` module provides reusable functionality that is shared across multiple processor scripts, avoiding code duplication and ensuring consistency.

## Modules

### `filter_banned_files.py`

Provides the `BannedFilesFilter` class for filtering out system files and directories that should be skipped during processing.

**Usage:**

```python
from common.filter_banned_files import BannedFilesFilter

# Initialize the filter
banned_filter = BannedFilesFilter()

# Check if a file should be skipped
from pathlib import Path
if banned_filter.is_banned(Path(".DS_Store")):
    print("File should be skipped")

# Add custom patterns
banned_filter.add_pattern("custom_pattern")
```

**Default banned patterns:**

- `@eaDir` - QNAP NAS system directory
- `@__thumb` - QNAP thumbnail directory
- `SYNOFILE_THUMB_` - Synology NAS thumbnail files
- `Lightroom Catalog` - Adobe Lightroom catalog
- `thumbnails` - Android photo thumbnails
- `.DS_Store` - macOS custom attributes
- `._` - macOS resource fork files
- `.photostructure` - PhotoStructure application directory

## Setup

### Development Installation (Recommended)

From the project root:

```bash
pip install -e .
```

This makes the `common` module available to all processors.

## Adding New Shared Modules

1. Add your module to `/path/to/memoria/common/`
2. Update `common/__init__.py` to export the module:

   ```python
   from .your_module import YourClass
   __all__ = ["BannedFilesFilter", "YourClass"]
   ```

3. Import in processor scripts:

   ```python
   from common.your_module import YourClass
   ```

## Testing

Verify the setup works:

```bash
cd /path/to/memoria
python3 -c "from common.filter_banned_files import BannedFilesFilter; print('Success!')"
```

## Migration Notes

This common module structure was created to consolidate functionality that was previously duplicated across all processors. Any updates to shared functionality should be made here in the common module rather than in individual processor modules.
