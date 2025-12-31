# Bug Report: iMessage Live Photo Pairing Breaks After Preprocessing (IDs Dropped + Filename Collisions)

## Status: ROOT CAUSE IDENTIFIED - Fix pending

## Summary

During iMessage processing, Memoria logs warnings like:

```text
Live photo video lp_image_14.MOV has no paired image (live_photo_image_id=None), generating independent filename
```

This looks like the original export is missing the Live Photo still image, but that is **not** what’s happening.

**Actual root cause:** iMessage preprocessing creates the Live Photo MOV sidecar entry with a `live_photo_image_id`, but later drops that identifier (and also drops the still image’s `attachment_id`) when building the `metadata.json` conversations/messages structure. The runtime processor expects those IDs to exist to pair the MOV with the still image, so pairing fails and the MOV gets an independent filename.

Additionally, because iMessage Live Photo sidecars are commonly named `lp_image.MOV` in many attachment directories, `hash_with_dedup()` must resolve filename collisions and ends up producing keys like `lp_image_14.MOV` in `metadata.json`. Those names do **not** exist in `/mnt/media/originals`, which makes manual “does this MOV have a paired still?” investigation misleading if you search originals by that name.

## Symptoms

1. **Warnings in processor logs** (example from `logs/media_processor_20251229_151236.log`):

   ```text
   WARNING - Live photo video lp_image_3.MOV has no paired image (live_photo_image_id=None), generating independent filename
   WARNING - Live photo video lp_image_14.MOV has no paired image (live_photo_image_id=None), generating independent filename
   ...
   ```

2. **The warned filenames (e.g., `lp_image_14.MOV`) are not present in originals**, because the real on-disk name is typically `lp_image.MOV` in many different attachment folders.

3. **Output impact**: the Live Photo video component does not reuse the still image’s generated base name, so the still + MOV no longer form a consistent paired naming scheme in processed output.

## What We Verified in Originals (Not Missing Data)

For the export directory:

- `/mnt/media/originals/imessage-iph17pm-20251121`

We confirmed:

- There are many `lp_image.MOV` and corresponding `lp_image.HEIC`/`lp_image.jpeg` files in the export.
- For **every directory that contains `lp_image.MOV`**, a corresponding `lp_image.(HEIC|JPEG|...)` exists **in the same directory**.

So the warnings are **not** evidence of missing base images in the original dataset.

This matches the documented export format in `docs/iMessage-Export.md` (“Live Photos are stored as paired files in the same attachment directory”, and the MOV is not tracked in the DB).

## Affected Code

### 1) Where the Live Photo sidecar gets the pairing ID (preprocess)

`processors/imessage/preprocess.py` creates the MOV sidecar attachment entry and sets:

- `is_live_photo_video = True`
- `live_photo_image_id = row["attachment_id"]`

This is correct at this stage.

### 2) Where the IDs are dropped (preprocess → metadata.json build)

In `processors/imessage/preprocess.py`, `_deduplicate_and_organize()` creates `CopyTask(context=...)` for each attachment, but **does not include**:

- `attachment_id` (needed on the still-image message)
- `live_photo_image_id` (needed on the MOV message)

Only `is_live_photo_video` is kept.

As a result, the `metadata.json` message entries that later drive processing contain `is_live_photo_video` but lack the pairing IDs.

### 3) Why you see `lp_image_14.MOV` (collision resolution)

`processors/preprocess_base.py` (`PreprocessorBase.hash_with_dedup()`) assigns `dest_filename` for each unique content-hash group, and resolves filename collisions via `_get_unique_filename()` by appending `_{counter}`.

Because many Live Photo sidecars share the same original dest name `lp_image.MOV`, preprocessing will produce:

- `lp_image.MOV`, `lp_image_1.MOV`, `lp_image_2.MOV`, … `lp_image_14.MOV`, etc.

These are internal “preprocessed metadata keys” and do not reflect original filesystem names.

### 4) Where the runtime pairing fails and logs warnings (processor)

`processors/imessage/processor.py` does two-pass filename generation:

- Pass A generates filenames for non-live-video items; if an image has a paired live-photo video it records a base in `live_photo_bases[attachment_id]`.
- Pass B generates filenames for live-photo video items; it tries to reuse `live_photo_bases[live_photo_image_id]`, otherwise logs:

```text
Live photo video <media_filename> has no paired image (live_photo_image_id=None), generating independent filename
```

Since `attachment_id` and `live_photo_image_id` never make it into `metadata.json`, pairing cannot succeed.

## Root Cause

**iMessage preprocessing drops Live Photo pairing identifiers when constructing `metadata.json`.**

Concretely:

- The preprocessor knows the still image’s `attachment_id` and assigns the MOV sidecar `live_photo_image_id`.
- But `_deduplicate_and_organize()` only preserves `is_live_photo_video` in per-file contexts and message entries.
- The runtime processor’s pairing logic relies on IDs (`attachment_id` and `live_photo_image_id`) that are no longer present, so it always takes the fallback path for some/all live photo videos.

The collision-generated filenames (`lp_image_14.MOV`) are a secondary contributor to confusion: they make it look like the originals are missing a specific file, when the “numbered” names are preprocessing artifacts.

## Reproduction

1. Run Memoria iMessage preprocessing + processing on an export containing multiple Live Photos (multiple directories containing `lp_image.*`).
2. Ensure multiple Live Photo videos exist (many `lp_image.MOV` paths).
3. Observe in processing logs:
   - Warnings: `Live photo video lp_image_<n>.MOV has no paired image (live_photo_image_id=None) ...`

## Expected vs Actual

- **Expected**:
  - Each live-photo MOV reuses the still image’s generated base filename.
  - No warnings for “no paired image” unless the still genuinely cannot be found / was skipped.

- **Actual**:
  - Many MOVs fall back to independent filenames due to missing pairing IDs in metadata.
  - Warnings are emitted even though originals contain properly paired stills.

## Recommended Fix

Pick one of the following (A is the most direct and preserves current processor design):

- **A) Preserve pairing IDs in `metadata.json` (recommended)**
  - In `processors/imessage/preprocess.py` `_deduplicate_and_organize()`:
    - Include `attachment_id` in contexts for still-image attachments.
    - Include `live_photo_image_id` in contexts for live-photo MOV sidecars.
  - Ensure those fields survive context merging for duplicates.
  - Emit them into the final message dict(s) in `conversations[*].messages[*]` (and merged message variants).

- **B) Make runtime pairing independent of IDs**
  - In `processors/imessage/processor.py`, for `is_live_photo_video` items:
    - Use `source_paths[media_filename]` to locate the MOV’s directory in originals.
    - Look for `lp_image.(heic|jpeg|jpg|png)` in the same directory and pair based on filesystem, not attachment IDs.
  - This avoids dependence on `attachment_id` fields, but requires careful handling for dedup/merged contexts and is less deterministic than ID-based pairing.

## Notes / Context

This behavior aligns with iMessage export reality:

- Live Photo MOV sidecars are not tracked in the DB (so the preprocessor must synthesize entries).
- Multiple attachment folders reuse `lp_image.*` names, so collision resolution is expected during preprocessing.

However, once collision resolution happens, it becomes even more important to preserve a stable pairing signal (`live_photo_image_id` or equivalent) through to processing.


