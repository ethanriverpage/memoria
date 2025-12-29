# Bug Report: "No such file or directory" Errors During Timestamp Updates

## Status: Root Cause Confirmed

## Summary

During processing, `update_file_timestamps()` fails with `[Errno 2] No such file or directory` for files that were successfully copied. The timestamp update attempts to access a file path that no longer exists because the file was renamed during extension correction.

## Symptoms

Log messages like:

```text
23:37:23 - common.files.utils - INFO - Corrected 24 file extension(s)
23:37:23 - common.utils - WARNING - Failed to update timestamps for /mnt/media/processed/Google/ethanriverpage/photos/google-ethanriverpage-photos-20220602_26.mp4: [Errno 2] No such file or directory
23:37:23 - common.utils - WARNING - Failed to update timestamps for /mnt/media/processed/Google/ethanriverpage/photos/google-ethanriverpage-photos-20220602_17.mp4: [Errno 2] No such file or directory
```

Notable patterns:

1. Extension corrections and timestamp failures occur at the same second
2. Some file paths appear in warnings multiple times (e.g., `20220522_3.mp4` logged twice, `20220522_6.mp4` logged three times)
3. Affected files all have `.mp4` extension in the warning (suggesting they were renamed from `.mov` or similar)

## Affected Code

### Primary Location

`processors/base.py` - `process_media_copy_batch()` (lines 1232-1286)

```python
# Phase 1: Copy all files
for task in batch_tasks:
    ...
    copy_file(src, dst)
    file_paths.append(dst)
    file_info.append((dst, *task.info_rest))
    timestamp_meta.append((task.timestamp_str, task.timestamp_format))

# Phase 2: Correct file extensions based on actual content
corrections = batch_correct_extensions([Path(p) for p in file_paths])
if corrections:
    file_paths = [corrections.get(p, p) for p in file_paths]
    file_info = [(corrections.get(t[0], t[0]), *t[1:]) for t in file_info]

# Phase 3: Update timestamps and compile results
for idx, info in enumerate(file_info):
    output_path = info[0]
    timestamp_str, timestamp_format = timestamp_meta[idx]
    if timestamp_str:
        update_file_timestamps(output_path, timestamp_str, timestamp_format)
```

### Extension Correction Logic

`common/files/utils.py` - `batch_correct_extensions()` (lines 282-345)

```python
if new_path.exists() and new_path != file_path:
    counter = 1
    while new_path.exists():
        new_path = file_path.with_stem(f"{file_path.stem}_{counter}").with_suffix(detected_ext)
        counter += 1

os.rename(file_path, new_path)
corrections[str(file_path)] = str(new_path)
```

### Live Photo Filename Generation

`processors/google_photos/processor.py` - `_process_working_directory()` (lines 450-469)

```python
if is_live_photo_pair and live_photo_key in live_photo_bases:
    # Second file in live photo pair - reuse base from first file
    output_filename = f"{live_photo_bases[live_photo_key]}{file_ext}"
else:
    # Generate filename using FilenameGenerator (mechanism handles uniqueness)
    ...
    output_filename = filename_gen.generate(canonical_ts, file_ext)

    # If this is first file in a live photo pair, record base for second file
    if is_live_photo_pair:
        base = output_filename[: -len(file_ext)] if file_ext else output_filename
        live_photo_bases[live_photo_key] = base
```

## Root Cause (Confirmed)

### Primary Cause: Live photo filename handling bypasses FilenameGenerator's uniqueness tracking

When the second file of a live photo pair is processed (line 452), the filename is constructed by directly concatenating the stored base with the file extension:

```python
output_filename = f"{live_photo_bases[live_photo_key]}{file_ext}"
```

This filename is **never registered** with `FilenameGenerator.used_filenames`. Subsequently, when a non-live-photo file with the same timestamp is processed, `FilenameGenerator.generate()` may produce the **same filename** because it doesn't know that filename was already used.

**Collision scenario:**

1. Live photo image: Generates `google-...-20220522_3.heic`, stores base `"google-...-20220522_3"`
2. Live photo video: Reuses base -> `google-...-20220522_3.mov` (**NOT** added to `used_filenames`)
3. Regular video with same timestamp: `FilenameGenerator` checks `"google-...-20220522_3.mov"` - not in `used_filenames`, returns it as "unique"

Now tasks 2 and 3 both have `dest_path = "google-...-20220522_3.mov"`.

### Secondary Cause: Parallel batch race conditions

When duplicate destination paths end up in different parallel batches, race conditions occur:

1. **Batch A** copies file1 -> `X.mov`
2. **Batch B** copies file2 -> `X.mov` (overwrites, or may occur before Batch A)
3. **Batch A** renames `X.mov` -> `X.mp4` (success)
4. **Batch B** tries to rename `X.mov` -> **file no longer exists** (already renamed by Batch A)
5. **Batch B's** `corrections` dict is empty for that path (rename failed/skipped)
6. **Batch B's** timestamp update fails with `[Errno 2] No such file or directory`

The `corrections` dictionary is **per-batch**, so Batch B cannot benefit from Batch A's corrections.

### Evidence from Log

The log shows files with the same path failing multiple times in the same second:

```text
4205: .../20220522_3.mp4: [Errno 2] No such file or directory
4206: .../20220522_3.mp4: [Errno 2] No such file or directory
4207: .../20220522_6.mp4: [Errno 2] No such file or directory
4208: .../20220522_6.mp4: [Errno 2] No such file or directory
4208: .../20220522_6.mp4: [Errno 2] No such file or directory
```

The duplicates (same path appearing 2-3 times) confirm that multiple tasks were assigned the same destination path.

### Original Hypotheses - Evaluation

| Hypothesis                                 | Verdict                                                              |
| ------------------------------------------ | -------------------------------------------------------------------- |
| H1: Collision suffix not reflected         | Partially correct - but real issue is duplicate dest_paths upstream  |
| H2: Duplicate entries in processing list   | **Confirmed** - this is the root cause                               |
| H3: Race condition across parallel batches | **Confirmed** - this is the failure mechanism                        |
| H4: Path string representation mismatch    | Not observed - paths are constructed consistently                    |

## Impact

- File timestamps are not set correctly for affected files
- Files are still successfully copied and have correct extensions
- No data loss, but filesystem mtime/atime don't reflect capture timestamps
- Affects downstream tools that rely on file timestamps for sorting/organization

## Recommended Fix

### Constraint: Live Photo Pair Naming

Live photo pairs **must** have identical base filenames with different extensions to maintain their association:

```text
google-ethanriverpage-photos-20230526.jpg   (image)
google-ethanriverpage-photos-20230526.mov   (video)
```

Any fix must preserve this pairing. Appending `_1` to either file would break the association.

### Option A: Reserve all live photo filenames upfront (Recommended)

In `processors/google_photos/processor.py`, when processing the **first** file of a live photo pair, reserve filenames for **all** files in that pair. This prevents other files from claiming those names.

```python
if is_live_photo_pair and live_photo_key in live_photo_bases:
    # Second+ file in live photo pair - reuse base from first file
    output_filename = f"{live_photo_bases[live_photo_key]}{file_ext}"
else:
    # Generate filename using FilenameGenerator (mechanism handles uniqueness)
    timestamp_str = media_data.get("capture_timestamp")
    timestamp_clean = _clean_iso_timestamp(timestamp_str)
    canonical_ts = get_canonical_timestamp(
        dedup_manifest, media_data.get("content_hash"), timestamp_clean
    )
    media_data["canonical_timestamp"] = canonical_ts
    output_filename = filename_gen.generate(canonical_ts, file_ext)

    # If this is first file in a live photo pair, reserve ALL extensions for the pair
    if is_live_photo_pair:
        base = output_filename[: -len(file_ext)] if file_ext else output_filename
        live_photo_bases[live_photo_key] = base

        # Reserve all filenames for this live photo pair to prevent collisions
        for _, ext in live_photo_groups.get(live_photo_key, []):
            reserved_name = f"{base}{ext}"
            filename_gen.used_filenames.add(reserved_name)
```

**How it works:**

1. When the first file of a live photo pair (e.g., `.jpg`) is processed, `FilenameGenerator` creates a unique name
2. We immediately reserve ALL extensions for that base name (both `.jpg` and `.mov`)
3. When a regular file with the same timestamp comes along, `FilenameGenerator` sees those names are taken and generates `_2` variants
4. When the second live photo file (`.mov`) is processed, it reuses the reserved base - no collision

**Example:**

1. Live photo image: generates `20230526_3.jpg`, reserves both `20230526_3.jpg` and `20230526_3.mov`
2. Regular video (same timestamp): tries `20230526_3.mov` - taken! Gets `20230526_3_2.mov`
3. Live photo video: uses reserved `20230526_3.mov` - no collision, pair is intact

## Reproduction

Run the full processing pipeline on a Google Photos export with:

- Multiple files per day (generates sequence numbers)
- Video files with incorrect extensions (e.g., `.mov` files that are actually MP4)
- Live photo pairs where the video component has the same timestamp as other non-live-photo videos

## Related Files

- `processors/base.py` - batch processing and timestamp updates
- `common/files/utils.py` - extension correction logic
- `common/utils.py` - contains `update_file_timestamps` and `FilenameGenerator`
- `processors/google_photos/processor.py` - live photo handling and filename generation

## Log References

- `/home/ethan/media-processing/logs/media_processor_20251228_185550.log` lines 4170-4230
