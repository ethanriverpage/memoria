# Failure Handling: Severity, Scope, and Policy

This document defines how Memoria classifies failures and what the system must do in response (log, track, move/copy files, and whether to stop processing).

## Goals

- Provide **consistent, predictable** behavior across all processors.
- Make failures **actionable**: every non-trivial failure should either be:
  - **tracked** (so it appears in `issues/`), or
  - **explicitly ignored** as an acceptable warning.
- Prevent **silent data corruption** by treating invariants as hard failures.

## Non-Goals

- Backwards compatibility for internal report formats or directory layouts (this is a batch reprocessor).
- User-facing remediation UI (this is a CLI batch processor).

## Severity Levels

Memoria uses four severity levels:

- **Warning**: A recoverable degradation for a specific media item or metadata field. Output is still produced. Processing continues.
- **Soft-fail**: A specific media item fails and must be **handled explicitly** (tracked and placed under `issues/`). Processing continues.
- **Hard-fail**: Indicates a **bug or violated invariant** within a single processor run. Processing must stop **for that processor** (for that export), but other processors/exports should continue.
- **Error**: A global failure where it is unsafe to continue. Processing must stop for **all** processors.

## Scope Levels (Where a failure applies)

Severity describes “how bad”; scope describes “what should stop”.

- **Media-level**: One output file/item.
- **Export-level**: The current export directory for the current processor.
- **Processor-level**: The processor’s run for the export (equivalent to “export-level processor invocation”).
- **Global**: Entire Memoria run (all exports/processors).

**Required mapping**:

- Warning → media-level (almost always)
- Soft-fail → media-level (sometimes export-level if no media can be processed but other exports can proceed)
- Hard-fail → processor-level (this processor invocation)
- Error → global

## Standard Handling Actions

### Logging

- **Warning**: `WARNING` log line (high-signal, limited volume); include a reason code and any identifying context.
- **Soft-fail**: `WARNING` or `ERROR` log line + FailureTracker entry.
- **Hard-fail**: `ERROR` log line + abort the processor invocation.
- **Error**: `CRITICAL` log line + abort entire run.

### Tracking & Output Layout (`issues/`)

Processors must use `FailureTracker` to record soft-fails and matching failures. Issues are written under the processor output directory:

- `issues/failed-matching/`
  - `media/`: orphaned media (file exists, no metadata match)
  - `metadata/`: orphaned metadata (metadata references missing file)
- `issues/failed-processing/` *(to be implemented consistently across processors)*
  - output files that were created/copied but failed during processing (EXIF/timestamps/validation/etc.)
- `issues/needs_matching/`
  - ambiguous matching cases materialized for human inspection (see `ProcessorBase.materialize_ambiguous_cases()`)

**Policy**:

- For **failed-matching**, we keep originals intact and **copy** evidence into `issues/failed-matching/`.
- For **failed-processing**, we must ensure failed outputs do not remain mixed with successful outputs; the preferred behavior is to **move** failed output files into `issues/failed-processing/`.

## Canonical Failure Conditions (Policy Decisions)

This section captures the required behavior for specific conditions.

### 1) Missing timestamp because the media is orphaned (soft-fail)

- **Condition**: A media file exists but has no matching metadata (so no authoritative timestamp).
- **Severity**: Soft-fail (media-level).
- **Tracking**: `FailureTracker.add_orphaned_media(...)`
- **Placement**: `issues/failed-matching/media/` (copied evidence)
- **Notes**: This is a matching failure, not a processing failure.

### 2) Missing timestamp because EXIF timestamp update failed (soft-fail)

- **Condition**: We attempted to embed timestamps (EXIF/XMP) and it failed.
- **Severity**: Soft-fail (media-level).
- **Tracking**: `FailureTracker.add_processing_failure(...)` with a reason code like `timestamp_update_failed`.
- **Placement**: `issues/failed-processing/` (move failed output)

### 3) Filesystem timestamp update fails but EXIF succeeds (warning)

- **Condition**: EXIF embed succeeded, but updating filesystem mtime/atime failed (e.g., permissions).
- **Severity**: Warning (media-level).
- **Tracking**: No FailureTracker entry required (unless you want a separate “warning report”; currently out of scope).
- **Placement**: None; keep output in normal location.
- **Notes**: The file remains correct from a metadata perspective; the filesystem timestamp is a convenience.
  - If the filesystem timestamp update fails because the **output path does not exist**, that strongly suggests a path mismatch (e.g., extension correction/rename after the path was computed). Treat as a warning for the media item (since EXIF is canonical) but consider it a **bug to fix** in the processor pipeline ordering.

### 4) Export username must never be “unknown” (hard-fail)

- **Condition**: Any processor would produce output paths or embedded metadata containing `unknown` username due to failed detection/override.
- **Severity**: Hard-fail (processor-level).
- **Required behavior**:
  - Abort this processor invocation with a clear error (include detection source and input directory).
  - Other processors/exports continue.
- **Rationale**: Output paths and embedded source attribution must be correct and deterministic.

### 5) Generated filename date must match embedded EXIF date (hard-fail)

- **Condition**: The derived date in the filename (e.g., `...-YYYYMMDD...`) does not match the timestamp being embedded.
- **Severity**: Hard-fail (processor-level).
- **Required behavior**:
  - Abort processor invocation.
  - Emit diagnostic context: computed filename date, embedded timestamp, source timestamp, and the media item identity.
- **Rationale**: This indicates a logic bug and risks systemic mis-dating.

### 6) Unable to perform sender attribution for messaging platforms (warning)

- **Condition**: A message attachment cannot be attributed to a sender (missing sender field, unknown mapping, etc.).
- **Severity**: Warning (media-level).
- **Required behavior**:
  - Log a warning including the conversation/message identifier (non-PII) and attachment filename.
  - Continue processing; embed `null`/missing attribution fields rather than incorrect placeholders.

### 7) GPS coordinates must never be embedded as (0.0, 0.0) (hard-fail)

- **Condition**: A pipeline attempts to embed GPS coordinates where either:
  - `(lat, lon) == (0.0, 0.0)`, or
  - values are outside valid ranges (lat not in \([-90, 90]\), lon not in \([-180, 180]\)).
- **Severity**: Hard-fail (processor-level).
- **Required behavior**:
  - Abort processor invocation.
  - Log the offending item and its GPS source.
- **Rationale**: (0,0) is a common “unknown” sentinel and embedding it corrupts location data.

## Additional Failure Conditions Observed in Logs (Needs Policy)

These are real patterns seen in `logs/media_processor_20251227_184304.log` that should be explicitly categorized:

### A) Export contains no media files

- **Observed pattern**: Preprocessing reports “No media files found”.
- **Policy**: **soft-fail (export-level)**.
  - Treat as an acceptable empty export unless we can prove otherwise.
  - Record a failure report under `issues/` (no files moved/copied because there are no media items).
  - Processing continues for other processors/exports.
  - Rationale: while this *may* indicate a detection/validation bug, it is safer (and more batch-friendly) to not assume that.

### B) Processor skips because directory is not preprocessed

- **Observed pattern**: “Directory not preprocessed, skipping …”.
- **Policy**: **warning**.
  - This is expected for some exports (notably Google Takeout), where service folder structure may exist even when there is no data for that service.
  - Continue processing without recording a FailureTracker entry.

### C) Metadata references missing source files

- **Observed pattern**: “Source file not found or not media”.
- **Proposed policy**:
  - If source file is missing: **soft-fail (media-level)** as a matching failure (orphaned metadata) and ensure it’s in `issues/failed-matching/metadata/`.
  - If file exists but is not a supported media type: **warning** or **soft-fail** depending on whether the file should have been filtered earlier.

### D) ExifTool reports “File format error” / probe failures (“moov atom not found”)

- **Observed pattern**: ExifTool stderr contains “File format error”; FFprobe reports “moov atom not found”.
- **Proposed policy**:
  - If EXIF embed is required for correctness (timestamps, attribution): **soft-fail (media-level)** → `issues/failed-processing/`.
  - If EXIF embed is optional for that format: **warning** with explicit “skipping EXIF for unsupported/corrupt file”.

### F) EXIF batch skips non-existent files listed for writing

- **Observed pattern**: EXIF pipeline logs “Skipping N non-existent files in metadata batch”.
- **Policy**: **soft-fail (media-level)** for the impacted items.
  - The pipeline should not silently drop items that were expected to exist at write time.
  - Track these as processing failures (if the output should have existed) or matching failures (if the source is missing), depending on where the absence occurs.

### G) Overlay handling failures (Snapchat overlays)

Two distinct cases appear in logs:

- **Overlay file not found**
  - **Policy**: **warning (media-level)**.
  - Continue by producing the base media output (without overlay). Do not embed “fake” overlay data.

- **Overlay file exists but is unreadable/corrupt** (e.g., image decode failure)
  - **Policy**: **soft-fail (media-level)** by default.
  - Track the affected output as failed-processing (overlay could not be applied) and ensure the failed output is segregated under `issues/failed-processing/`.

### H) Hardware encoding fails and we fall back to software encoding

- **Observed pattern**: “Hardware encoding failed, retrying with software”.
- **Policy**: **warning (media-level)**.
  - Continue processing. This is a performance degradation, not a correctness failure.

### I) Upload failures (post-processing)

- **Observed pattern**: Upload fails (e.g., “Upload path does not exist”).
- **Policy**: **warning (export-level)** by default.
  - Processing output is still correct; upload is a downstream action.
  - Continue processing other exports/processors; include a clear summary that upload failed.

## Implementation Notes (How to enforce this)

This section describes recommended mechanisms; exact code changes live elsewhere.

- **Centralize invariants**: add shared validators (e.g., `common/validation/`) for:
  - export username not unknown
  - filename date matches embedded timestamp
  - GPS validity (including 0.0,0.0)
- **Propagate outcome**: helpers like timestamp/EXIF writers must return success/failure so processors can classify correctly.
- **Use FailureTracker consistently**: every processor should:
  - track failures during preprocessing (failed-matching)
  - track failures during processing (failed-processing)
  - call `failure_tracker.handle_failures(output_dir)` at end of run

## Related Documentation

- [Logging](Logging) (log level guidelines)
- [Design Decisions](Design-Decisions) (timestamp correctness and ordering)
- Platform docs in `docs/` for export format expectations
