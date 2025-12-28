# Log Noise Reduction Guide

This document proposes concrete changes to keep Memoria logs **debug-useful** while drastically reducing line count and repetition (e.g., `logs/media_processor_20251227_184304.log` at 135k+ lines).

It is written against the current codebase (paths and call sites referenced below) and focuses on “high-signal” logging: **one event = one line**, **stable identifiers**, and **details only on failure**.

## Goals

- **Make logs skimmable**: hundreds to a few thousand lines for a full run, not 100k+.
- **Keep debugging power**: preserve the ability to trace a specific file through a pipeline.
- **Prefer stable identifiers**: source export + original filename, not temp file names or long absolute paths.
- **Avoid duplicated statements**: don’t log the same event twice under different wording.

## Core Principles (Rules of Thumb)

- **One “file event” → one line**: A copy/rename/overlay action should be a single line containing the key context.
- **Prefer `short_path()` for paths**: Use `common.files.utils.short_path()` to avoid absolute paths in logs.
- **Do not log temp filenames**: Temp names are not stable and make tracing harder. Log the *real input filename* and the *final output filename*.
- **Do not log full ffmpeg commands on success**: log encoder/filters/decision summary; include full command only on failure.
- **Only log expensive metadata (probe) when needed**:
  - On success: either don’t log at all, or log a single condensed line *only at DEBUG and only when it explains a decision*.
  - On failure: include relevant stderr/return code (and optionally the command).
- **High-cardinality loops must aggregate**:
  - If a log message can happen thousands of times, it should be replaced with a counter summary and (optionally) a tiny sample.

## Logging Format Recommendations

### Use stable, searchable tokens

- **Processor / phase**: `proc=instaloader phase=copy`
- **Export**: `export=snapchat-ethanriverpage-20251017`
- **File identity**: `src=2018-08-01_18-08-04_UTC.mp4 out=insta-posts-...mp4`
- **Optional correlation**: `hash=abcd1234` (first 8 chars)

### Path policy

- **Never** print absolute paths in DEBUG per-file logs.
- **Prefer** `short_path(path)` (example: `snapchat-ethanriverpage-20251017/2021-03-11_19-50-55.mp4`).
- If `short_path()` isn’t sufficient, include:
  - export name
  - relative path inside export (e.g., `memories/media/...`)
  - filename

## Mapping to Your Examples (What to Change)

### 1) Instaloader per-file “Processing” + “Copying” duplication

**Example:** `logs/media_processor_20251227_184304.log:110274-110341`

**Current emitter:** `processors/instaloader/processor.py`

- `logger.debug(f"Processing: {media_file} -> {output_filename}")`
- `logger.debug(f"Copying {media_file} ({file_size} bytes) to {output_path}")`

**Problem:**

- Two lines for one event (copy/rename).
- Output uses a long processed absolute path (not helpful for debugging source export).

**Recommended change:**

- Replace with **one** DEBUG line per file copy:
  - Include: `src filename`, `dest filename`, `size`, and optionally `media_type`.
  - Do **not** include full destination path; at most include `short_path(output_path)`.

**Suggested new log shape (DEBUG):**

```
proc=instaloader phase=copy src=2018-08-01_18-08-04_UTC.mp4 dest=insta-posts-ethantastic_-20180801.mp4 size=2.56MB type=posts
```

**Timestamps logging:** `processors/instaloader/processor.py` also logs:

- `Updating timestamps for {output_path} to {post_data['timestamp']}`
- `Successfully processed {output_path}`

Recommendation:

- Don’t log timestamp updates per file unless something fails (timestamp parse error, permission error).
- If you need a success line, fold it into the single per-file line (above), or rely on a batch summary.

### 2) Probe + overlay repetition and ffmpeg command spam

**Example:** `logs/media_processor_20251227_184304.log:110540-110569`

**Current emitters:**

- `common/media/probe.py` logs success metadata:
  - `Video metadata for ...: 1232x656, rotation=90, bitrate=...`
- `common/media/overlay.py` logs:
  - per-pass narrative (`PASS 1`, `Using pre-fetched bitrate`, `Original dims`, `Target dims`, `Applying rotation`)
  - full ffmpeg command strings for Pass 1/2/3

**Problems:**

- The same facts (dims/rotation/bitrate) are repeated across modules.
- ffmpeg command strings dominate logs and are rarely used unless a run fails.

**Recommended change:**

1) **Make `probe` quiet on success**:
   - `common/media/probe.py`: remove (or heavily gate) the “Video metadata for …” DEBUG line.
   - Only log probe details when probe fails or returns incomplete data.

2) **Make `overlay` emit a single decision line per video**:
   - Emit one DEBUG line summarizing the decisions derived from probe:
     - rotate? by how much?
     - dims in → out
     - bitrate chosen
     - encoder used (hardware vs software)
     - final output filename

3) **Only log full ffmpeg command on failure**:
   - When ffmpeg fails, log:
     - `returncode`
     - trimmed stderr (or first N lines)
     - the command (optional but useful here)

**Suggested new log shape (DEBUG):**

```
proc=overlay video=2021-03-11_19-50-55.mp4 rotate=90 dims=1232x656->656x1232 bitrate=6.66Mbps->7.66Mbps encoder=hevc_vaapi out=snap-memories-ethriverpage-20110311.mkv
```

**Suggested error log shape (ERROR):**

```
proc=overlay pass=1 video=2021-03-11_19-50-55.mp4 error=ffmpeg_failed rc=1 stderr="...moov atom not found..." cmd="ffmpeg ..."
```

### 3) Video metadata log spam (unless error)

**Example:** `logs/media_processor_20251227_184304.log:42126-42144`

**Current emitter:** `common/media/probe.py` logs per-file metadata at DEBUG even when successful.

**Problem:**

- You only care about probe metadata if it explains an error or a non-obvious decision.

**Recommended change:**

- Success-path metadata should not be logged by `probe` by default.
- If needed, log metadata only when:
  - probe returns `None`
  - no video stream
  - missing dimensions
  - invalid rotate data
  - downstream step fails and you’re attaching context

### 4) Temp filenames make tracing harder than real filenames

**Example:** `logs/media_processor_20251227_184304.log:135521-135535`

**Current emitter:** `common/media/overlay.py`

- Cleanup logs show `tmp*.mp4` names.
- Pass 2 and Pass 3 often reference temp names (`with_overlay_path.name`) rather than the original input name.

**Problems:**

- The log line can’t be traced back to a source export file without context.
- Temp filenames are not stable across runs.

**Recommended change:**

- Always prefix overlay logs with the *original source filename* (`video_path.name`) and the *final output filename* (`output_path.name`).
- For cleanup:
  - Either don’t log successful temp deletion at all, or log a single summary line per input video:
    - `cleaned_temp_files=3`

**Suggested new log shape (DEBUG):**

```
proc=overlay video=2016-11-09_01-47-21.mp4 out=snap-memories-ethriverpage-20161109.mkv cleanup_temp_files=2
```

### 5) “Absorbed exact group …” per-hash line is not actionable

**Example:** `logs/media_processor_20251227_184304.log:57235-57241`

**Current emitter:** `common/dedup/service.py` in `_expand_perceptual_groups()`:

- Per-hash DEBUG: `Absorbed exact group {content_hash} into perceptual group`
- Already also logs a single INFO summary: `Absorbed N exact groups into perceptual groups`

**Problem:**

- High-cardinality debug line with no diagnostic value in normal debugging.

**Recommended change:**

- Remove the per-hash DEBUG line.
- Keep the INFO summary line (already present).
- If deep investigation is needed, add a limited sample (first 5 hashes) behind DEBUG.

### 6) “GPS distance too large” lacks the identity of the compared files

**Example:** `logs/media_processor_20251227_184304.log:51481-51500`

**Current emitter:** `common/dedup/service.py` in `_validate_duplicate_pair()`:

- `GPS distance too large: 27570m > 500.0m`

**Problems:**

- It’s meaningless without knowing which pair was rejected (which hashes? which sources?).
- This can happen thousands of times and flood logs.

**Recommended change:**

Pick one of these (best → acceptable):

1) **Aggregate + sample**
   - Maintain counters:
     - `gps_rejects`, `aspect_rejects`, `missing_gps_pairs`
   - Log a single INFO summary at the end of validation:
     - `dedup validation: gps_rejects=1234 aspect_rejects=456 ...`
   - Optionally log a tiny sample at DEBUG (first N rejects) including identities.

2) **Only log when debugging a specific match**
   - Add an explicit “debug focus” filter (e.g., env var `DEDUP_DEBUG_HASH=<hashprefix>`) and only log pairwise rejects when one side matches the focus hash.

If logging a reject line, it must include identity and provenance, e.g.:

```
proc=dedup reject=gps distance_m=27570 threshold_m=500 file1=google-.../IMG_0001.JPG hash1=abcd1234 file2=snapchat-.../IMG_0001.JPG hash2=ef901234
```

### 7) `preprocess_base` logs don’t identify the running processor module

**Examples:** `logs/media_processor_20251227_184304.log:42-43` and `:46-48`

**Current emitter:** `processors/preprocess_base.py` logs with the base module logger:

- `processors.preprocess_base - INFO - Starting Google Chat Preprocessing`

**Problem:**

- The logger name is always `processors.preprocess_base`, which hides the actual preprocessor module that’s executing.

**Recommended change:**

- When logging from `PreprocessorBase.run()`, log through a logger named after the subclass module:
  - `logging.getLogger(self.__class__.__module__)`, or
  - `get_processor_logger(self.__class__.__module__)` and call `preprocessor_start(...)`.

**Recommended output:**

- The log record should show `processors.google_chat.preprocess` (or similar) as the logger name.
- The message should include export identity, not full absolute paths:
  - Use `short_path(self.export_path)` or `get_export_name(self.export_path)`.

## Practical “Noise Budget” Targets

These targets assume a typical run over large exports.

- **INFO file log** (`--file-log-level info`):
  - 100–2,000 lines
  - Phase boundaries, counts, warnings/errors, final summaries
- **DEBUG file log** (`--file-log-level debug` or `-vv`):
  - 2,000–20,000 lines
  - One line per file *event* (copy / transform / skip), plus failure diagnostics
  - No ffmpeg commands unless failing
  - No per-comparison dedup logs unless filtered

## Checklist for Implementers

- [ ] Replace per-file multi-line narratives with a single structured summary line.
- [ ] Ensure every per-file line includes a stable identifier (export + filename).
- [ ] Never log temp filenames on success.
- [ ] Move expensive success-path logs (probe metadata, ffmpeg command) behind failure paths.
- [ ] Aggregate high-cardinality loops (dedup validation, absorbed groups, cleanup) into counters + optional samples.
- [ ] Ensure base classes log under the subclass’ logger name (or otherwise embed processor identity).

## Related Documentation

- `docs/Logging.md` (verbosity levels, `ProcessorLogger`, `--file-log-level`)

