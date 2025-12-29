# Bug Report: iMessage/iMazing Live Photo Pairs Not Matched by Filename

## Status: Confirmed

## Summary

iMessage and iMazing processors do not generate matching base filenames for live photo pairs. The image and video components of a live photo receive independent filenames with sequence numbers, breaking the association that photo libraries expect.

## Verification (Code + Docs)

- **Export format expectation (docs)**: iMessage Live Photos are a paired still image + sidecar video in the same attachment directory, where the MOV sidecar is **not present in the database** and must be found by scanning the filesystem (`lp_image.MOV` next to `lp_image.HEIC/.JPG`). See: `docs/iMessage-Export.md` (Live Photos section).
- **Preprocessing already links the pair**: `processors/imessage/preprocess.py` detects the `lp_image.MOV` sidecar and emits a second attachment entry with:
  - `is_live_photo_video = True`
  - `live_photo_image_id = row["attachment_id"]` (links the MOV to the image DB attachment)
- **Processing ignores the link during renaming**:
  - `processors/imessage/processor.py` and `processors/imessage_imazing/processor.py` both call `FilenameGenerator.generate()` independently for each file.
  - No logic exists to reuse the image’s base filename for the linked MOV sidecar.
  - Result: the HEIC/JPG and MOV can (and often will) end up with different sequence suffixes, producing mismatched bases (e.g. `...-YYYYMMDD.heic` vs `...-YYYYMMDD_2.mov`).

## Symptoms

Live photo pairs from iMessage exports have mismatched filenames:

```text
imessage-user-20230526.heic      (image - first file processed)
imessage-user-20230526_2.mov     (video - gets sequence number)
```

Expected behavior (matching Google Photos):

```text
imessage-user-20230526.heic      (image)
imessage-user-20230526.mov       (video - same base, different extension)
```

Photo libraries (Apple Photos, Google Photos, etc.) rely on matching base filenames to associate live photo components.

## Affected Code

### iMessage Processor

`processors/imessage/processor.py` - `_process_working_directory()` (lines 743-774)

```python
filename_gen = FilenameGenerator(
    "imessage",
    export_username,
    timestamp_format="%Y-%m-%d %H:%M:%S UTC",
)

# Each file processed independently - no pairing logic
for media_filename, message in filename_index.items():
    ...
    file_ext = source_path.suffix.lower()
    timestamp = _extract_message_timestamp(message)
    canonical_ts = get_canonical_timestamp(dedup_manifest, content_hash, timestamp)
    message["canonical_timestamp"] = canonical_ts
    output_filename = filename_gen.generate(canonical_ts, file_ext)  # No pairing
    matched_files.append((source_path, message, output_filename, content_hash))
```

### iMazing Processor

`processors/imessage_imazing/processor.py` - `_process_working_directory()` (lines 608-651)

Same pattern - each file goes through `FilenameGenerator.generate()` independently.

### Preprocessing (iMessage)

`processors/imessage/preprocess.py` already detects live photo pairs and tags them:

```python
mov_attachment["is_live_photo_video"] = True
mov_attachment["live_photo_image_id"] = row["attachment_id"]
```

The `live_photo_image_id` field links the video to its corresponding image, but this relationship is not used during filename generation.

## Root Cause

Unlike Google Photos processor, iMessage/iMazing processors:

1. Do not group live photo pairs before filename generation
2. Do not track base filenames for reuse across pair components
3. Process each file independently through `FilenameGenerator`

The live photo detection exists in preprocessing, but the pairing information is only used for metadata tagging, not filename coordination.

## Comparison with Google Photos

Google Photos processor (`processors/google_photos/processor.py`) handles this correctly:

```python
# First pass: Group live photos together
live_photo_groups = {}
for media_data in media_files:
    live_photo_key = get_live_photo_group_key(media_data)
    live_photo_groups[live_photo_key].append((media_data, file_ext))

# Second pass: Generate filenames with pairing
live_photo_bases = {}
for media_data in media_files:
    if is_live_photo_pair and live_photo_key in live_photo_bases:
        # Reuse base from first file in pair
        output_filename = f"{live_photo_bases[live_photo_key]}{file_ext}"
    else:
        output_filename = filename_gen.generate(canonical_ts, file_ext)
        if is_live_photo_pair:
            live_photo_bases[live_photo_key] = base
```

### Note: Collision prevention (Google Photos fix pattern)

Google Photos previously had a related race/collision bug when pairing logic bypassed `FilenameGenerator.used_filenames`. The fix was to **reserve all filenames for the pair** up front (see `.cursor/plans/live_photo_collision_fix_8b659c96.plan.md` and `docs/bugs/timestamp-update-file-not-found.md`).

When implementing iMessage pairing (base reuse), apply the same reservation pattern to ensure no non-live-photo file can claim `{base}.mov` (or other reserved extensions) for the same timestamp.

## Impact

- Live photo pairs are not recognized by photo management software
- Video component appears as separate file rather than motion component
- User must manually reassociate pairs after import
- Affects all iMessage exports containing live photos

## Recommended Fix

Add live photo pairing logic to both processors, similar to Google Photos but using the existing `live_photo_image_id` relationship from preprocessing.

### Step 1: Build live photo groups from metadata

```python
# Build live photo groups using live_photo_image_id from preprocessing
live_photo_groups = {}  # attachment_id -> list of (filename, message, ext)
live_photo_video_to_image = {}  # video_filename -> image_attachment_id

for media_filename, message in filename_index.items():
    if message.get("is_live_photo_video"):
        image_id = message.get("live_photo_image_id")
        if image_id:
            live_photo_video_to_image[media_filename] = image_id
            if image_id not in live_photo_groups:
                live_photo_groups[image_id] = []
            live_photo_groups[image_id].append((media_filename, message, "video"))
```

### Step 2: Generate paired filenames

```python
live_photo_bases = {}  # image_attachment_id -> base filename

for media_filename, message in filename_index.items():
    file_ext = source_path.suffix.lower()
    
    # Check if this is a live photo video with a known image pair
    if message.get("is_live_photo_video"):
        image_id = message.get("live_photo_image_id")
        if image_id and image_id in live_photo_bases:
            # Reuse base from image component
            output_filename = f"{live_photo_bases[image_id]}{file_ext}"
            filename_gen.used_filenames.add(output_filename)  # Reserve it
        else:
            output_filename = filename_gen.generate(canonical_ts, file_ext)
    else:
        output_filename = filename_gen.generate(canonical_ts, file_ext)
        
        # If this image has a video pair, store base for later
        attachment_id = message.get("attachment_id")
        if attachment_id and attachment_id in live_photo_groups:
            base = output_filename[: -len(file_ext)] if file_ext else output_filename
            live_photo_bases[attachment_id] = base
            
            # Reserve all extensions for this pair (prevent collisions)
            for _, _, _ in live_photo_groups[attachment_id]:
                # Reserve .mov extension
                filename_gen.used_filenames.add(f"{base}.mov")
```

### Important: Apply collision fix from related bug

When implementing this fix, ensure the reserved filenames are registered with `FilenameGenerator.used_filenames` to prevent collisions with non-live-photo files.

See: `docs/bugs/timestamp-update-file-not-found.md` for the collision bug and fix pattern.

## Related Files

- `processors/imessage/processor.py` - main processing logic
- `processors/imessage/preprocess.py` - live photo detection
- `processors/imessage_imazing/processor.py` - iMazing processing logic
- `processors/google_photos/processor.py` - reference implementation
- `common/utils.py` - `FilenameGenerator` class
- `docs/bugs/timestamp-update-file-not-found.md` - related collision bug

## Related Issues

- `timestamp-update-file-not-found.md` - Documents collision bug in Google Photos live photo handling; fix pattern applies here

