# BUG-001: Preprocessed Directory Path Not Passed to Process Phase

**Date Discovered:** 2024-12-26  
**Date Fixed:** 2024-12-26  
**Severity:** Critical  
**Status:** Fixed  
**Affected Components:** `memoria.py`, all processors except Snapchat Memories

---

## Summary

The orchestrator (`memoria.py`) preprocesses exports to a separate output directory (`/mnt/media/pre/`) but then passes the original input directory (`/mnt/media/test/`) to the process phase. This causes all processors to fail with "Input directory is not preprocessed" errors.

---

## Symptoms

During Phase 3 (Process), processors fail with:

```
ERROR - Input directory is not preprocessed: /mnt/media/test/discord-minmatic-20251204. Call preprocess() first or run via memoria.py.
```

This occurs despite preprocessing completing successfully in Phase 0.

---

## Reproduction Steps

1. Run memoria.py with cross-processor deduplication enabled:
   ```bash
   python memoria.py --originals /mnt/media/test -o /mnt/media/test_proc -v
   ```

2. Observe that:
   - Phase 0 (Preprocess) succeeds and writes to `/mnt/media/pre/`
   - Phase 3 (Process) fails because it looks for preprocessed data in `/mnt/media/test/`

---

## Expected Behavior

The process phase should receive the preprocessed directory paths (e.g., `/mnt/media/pre/discord-minmatic-20251204_discord_a21ef93d`) instead of the original input paths.

---

## Actual Behavior

| Phase | Directory Used | Result |
|-------|----------------|--------|
| Preprocess | Writes to `/mnt/media/pre/{export}_{processor}_{hash}` | Success |
| Catalog | Reads from preprocessed directories | Partial success |
| Process | Reads from `/mnt/media/test/{export}` (wrong!) | Failure |

---

## Affected Processors

All processors fail except Snapchat Memories:

- Discord
- Google Chat
- Google Photos
- iMessage
- Instagram Messages
- Instagram Public Media
- Instaloader
- Snapchat Messages

**Why Snapchat Memories works:** It has a different flow that looks for `metadata.json` directly in the `memories/` subfolder of the original export, which exists from the download step (not from preprocessing).

---

## Evidence from Logs

**Preprocessing output location:**
```
17:48:31 - processors.discord.processor - INFO - Preprocessing Discord export to: /mnt/media/pre/discord-minmatic-20251204_discord_a21ef93d
```

**Process phase looking in wrong location:**
```
17:53:51 - processors.discord.processor - ERROR - Input directory is not preprocessed: /mnt/media/test/discord-minmatic-20251204. Call preprocess() first or run via memoria.py.
```

---

## Run Statistics

From the affected run (`run_50c063129552`):

| Metric | Value |
|--------|-------|
| Exports found | 14 |
| Preprocessing succeeded | 13 |
| Preprocessing failed | 1 (Instagram Public Media - missing HTML metadata) |
| Process succeeded | 2 (Snapchat Memories only) |
| Process failed | 16 (all other processor invocations) |
| Files cataloged | 106 |
| Files actually processed | 40 (Snapchat Memories only) |

---

## Root Cause Analysis

**Root cause confirmed:** The bug is in `memoria.py` in the `run_phased_dedup_processing()` function.

### Phase 0 (Preprocess) - Correct

The `preprocessed_dirs` mapping is correctly built during Phase 0 (lines 393-444):

```python
# Map of (export_dir, processor_name) -> working_dir for preprocessed exports
preprocessed_dirs: dict[tuple[Path, str], Path] = {}

# ... after successful preprocessing:
preprocessed_dirs[(export_dir, processor_name)] = Path(working_dir)
```

This correctly stores mappings like:
- `(Path('/mnt/media/test/discord-minmatic-20251204'), 'Discord')` -> `Path('/mnt/media/pre/discord-minmatic-20251204_discord_a21ef93d')`

### Phase 1 (Catalog) - Correct

The catalog phase correctly uses `preprocessed_dirs` to read from the preprocessed location (lines 540-542):

```python
# Use preprocessed directory if available
catalog_dir = preprocessed_dirs.get(
    (export_dir, processor_name), export_dir
)
```

### Phase 3 (Process) - BUG

The process phase (lines 614-639) **never consults `preprocessed_dirs`**. It passes the original `export_dir` directly to `processor.process()`:

```python
for export_dir in dirs_to_process:                    # <- original path
    matching_processors = registry.detect_all(export_dir)
    
    for processor in matching_processors:
        # ...
        success = processor.process(
            str(export_dir),  # <- BUG: should use preprocessed_dirs lookup
            output_dir,
            dedup_manifest=manifest,
            **kwargs
        )
```

The `preprocessed_dirs` mapping is available in scope but is simply never used in Phase 3.

---

## Suggested Fix

Replace lines 638-639 in `memoria.py`:

```python
# Current (buggy):
success = processor.process(
    str(export_dir), output_dir, dedup_manifest=manifest, **kwargs
)
```

With:

```python
# Fixed: Look up preprocessed directory, fall back to original
processor_name = processor.get_name()
working_dir = preprocessed_dirs.get(
    (export_dir, processor_name),
    export_dir  # Fall back to original if not preprocessed
)

success = processor.process(
    str(working_dir), output_dir, dedup_manifest=manifest, **kwargs
)
```

---

## Workaround

None available. The preprocessing and processing phases are tightly coupled in the current flow.

---

## Related Files

- `memoria.py` - Main orchestrator (lines 606-687 contain the bug)
- `processors/base.py` - Base processor class with `is_preprocessed()` check
- `processors/*/processor.py` - Individual processor implementations

---

## Log File

Full log available at: `logs/media_processor_20251226_174830.log`

---

## Fix Applied

**Commit:** (pending)  
**Date:** 2024-12-26

### Change Made

In `memoria.py` line 619-627, added lookup of preprocessed directory before calling `processor.process()`:

```python
for processor in matching_processors:
    processor_name = processor.get_name()
    logger.info("  Running: %s", processor_name)
    try:
        # Look up preprocessed directory, fall back to original
        working_dir = preprocessed_dirs.get(
            (export_dir, processor_name),
            export_dir,
        )
        # ... rest of processing uses working_dir instead of export_dir
```

### Verification Results

After the fix:
- **Before:** 2 succeeded, 16 failed
- **After:** 19 succeeded, 0 failed

The preprocessed paths are now correctly passed to processors. For example:
```
Input directory is already preprocessed: /mnt/media/pre/discord-minmatic-20251204_discord_a21ef93d
```

---

## Related Issue Discovered

During verification, a separate issue was discovered affecting processors that use hash-only preprocessing (Google Chat, Instagram Messages, Instaloader, Instagram Public Media, Snapchat Messages):

These processors use `hash_with_dedup()` which does NOT create a `media/` subdirectory. However, `is_preprocessed_directory()` requires both `metadata.json` AND `media/` to exist.

This causes these processors to report "not preprocessed" even when receiving the correct preprocessed path. This is a **separate bug** tracked separately.

