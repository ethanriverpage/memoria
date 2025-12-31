# Bug Report: Snapchat Messages overlay failures are misreported as “missing MKV outputs”

## Summary

When Snapchat Messages video overlay embedding fails (FFmpeg/overlay pipeline), the worker **correctly falls back to copying the original video** (e.g., `.mp4`), but then **returns the wrong output path** (`.mkv`) to the main process. This causes the EXIF batch stage to treat the `.mkv` as a successful output, later discover it does not exist, and incorrectly record **processing failures** (`exif_target_missing`) even though the fallback `.mp4` outputs are present.

This is a **reporting / control-flow bug** in the Snapchat Messages processor, not an EXIF bug. It also hides the real underlying overlay error details (FFmpeg stderr).

## Impact

- **False processing failures**: `issues/failure-report.json` reports `exif_target_missing` for MKVs that were never supposed to exist after a fallback.
- **Confusing diagnostics**: Logs show “missing at EXIF write time” instead of the actual overlay/FFmpeg failure.
- **Incorrect failure handling**: FailureTracker tries to move the missing `.mkv` into `issues/failed-processing/` and records `move_error: Source file not found`.
- **User-visible output mismatch**: Instead of the expected dual-track `.mkv`, users still get the fallback `.mp4` (base video without overlay), but the run is marked as failed.

## Evidence (anonymized)

- **Run log** (`logs/run_…memoria.log`) contains:
  - `common.media.exiftool - WARNING - Skipping 7 non-existent files in metadata batch`
  - Missing paths are all `.../messages/snap-<USER>-msgs-<DATE>.mkv`

- **Failure report** (`/mnt/media/processed/Snapchat/<USER>/messages/issues/failure-report.json`) shows 7 entries with:
  - `reason: exif_target_missing`
  - `error_details: Output file missing at EXIF write time: ...snap-<USER>-msgs-<DATE>.mkv`
  - `context.move_error: Source file not found`

- **Filesystem check** confirms the fallback files exist:
  - For each missing `snap-<USER>-msgs-<DATE>.mkv`, a corresponding `snap-<USER>-msgs-<DATE>.mp4` exists in the output directory.

## Root cause

In `processors/snapchat_messages/processor.py`, `_create_file_worker()` has this control flow for **video + overlay**:

1. Calls `create_video_with_overlay(...)` to produce the final `.mkv`.
2. If overlay processing raises `OverlayProcessingError`, the code:
   - Copies a fallback base video to `fallback_path` (same basename, but `.mp4`),
   - Updates timestamps,
   - Then **re-raises** the exception.
3. The outer `except OverlayProcessingError:` handler returns **success** but uses `output_path` (the `.mkv`) instead of the `fallback_path` (the `.mp4`).

Net result: the pipeline reports a successful output at a path that does not exist. Downstream, `ProcessorBase.finalize_files()` → `batch_read_existing_metadata()` correctly flags the non-existent `.mkv` as missing, which becomes `exif_target_missing` processing failures.

### Why it only shows up for some Snapchat videos (e.g., older 2017–2018 era)

Those files are the ones whose overlay embedding fails (FFmpeg/encoder/filters). The processor *should* degrade gracefully to base video output; it already tries to, but the wrong output path is returned.

## Recommended fix

### Fix the incorrect returned path on `OverlayProcessingError` for videos

In `_create_file_worker()`:

- After copying the fallback base video, **do not re-raise** `OverlayProcessingError`; return `(True, str(fallback_path), False, ...)` directly.
  - or, if you want the error to be tracked elsewhere, re-raise a richer exception type and ensure the outer handler returns `fallback_path`, not `output_path`.

Also ensure the `is_mkv` flag matches the returned path (`False` for fallback).

### Optional (cleanup / performance): Don’t push MKVs into EXIF batch processing

Currently, `ProcessorBase.collect_processing_results()` always appends `output_path` to `file_paths` even if the caller’s `info_extractor()` returns `None` (used to “skip MKVs”).

Options:
- Update `collect_processing_results()` to only add to `file_paths` when the result is included in `file_info`, or
- Filter `collected.file_paths` in the Snapchat Messages processor to exclude MKV results.

This won’t fix the misreported failures by itself, but it prevents unnecessary EXIF probing of MKVs.

## Reproduction

1. Run processing on a Snapchat Messages export containing at least one video + overlay where overlay embedding fails.
2. Observe that the output directory contains `snap-<USER>-msgs-<DATE>.mp4` but not `...mkv`.
3. Observe failure report entries for missing `.mkv` and EXIF batch warnings about missing files.

## Expected behavior

- If overlay embedding succeeds: produce the dual-track `.mkv` and do not include it in EXIF batch processing.
- If overlay embedding fails: produce the fallback `.mp4` and:
  - do **not** report `exif_target_missing`,
  - optionally record a **non-fatal warning** like `overlay_embed_failed` with the underlying FFmpeg stderr excerpt.


