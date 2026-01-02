# Code Cleanup

## 1. Executive Summary

Overall the repo is **well-structured and test-backed** (clear processor boundaries in `processors/`, shared utilities in `common/`, and broad test coverage under `tests/`).

---

## 2. High Impact Findings

These findings represent the largest opportunities for improvement, either through significant code reduction, performance gains, or risk mitigation.

---

## Finding P2 — Duplicated `_process_working_directory` boilerplate

- **Category**: DRY violation
- **Location**: All 11 processors implement variants of this pattern
- **Description & evidence**
  - Every processor implements 200-300 lines of identical workflow: load metadata → init FailureTracker → generate filenames → iterate entries with dedup check → parallel processing → collect results → batch EXIF → track failures → save dedup metadata → print summary.
  - Minor variations in metadata structure extraction and task tuple construction.
- **Impact**
  - **High**: ~2500 lines of near-identical boilerplate across processors.
- **Confidence**: **High**

## Finding 56 — Near-identical dedup filtering in 10 processors

- **Category**: DRY violation
- **Location**: `processors/base.py` or `common/dedup/service.py`, 10 processors
- **Description & evidence**
  - `should_skip_for_dedup()` is consolidated in `ProcessorBase` (line 680).
  - Full `filter_files_for_dedup()` helper not implemented.
  - Near-identical filtering pattern repeated:

    ```python
    file_hashes = metadata.get("file_hashes", {})
    skipped_dedup = 0
    content_hash = file_hashes.get(media_file)
    if dedup_manifest:
        if content_hash:
            if not is_file_primary_by_hash(...):
                skipped_dedup += 1
                continue
        else:
            if Processor.should_skip_for_dedup(...):
                skipped_dedup += 1
                continue
    ```

- **Impact**
  - **High**: ~300 lines of duplicated filtering logic.
- **Confidence**: **High**

## Finding 9 — Multi-pass video processing causes multiple re-encodes

- **Category**: Performance
- **Location**: `common/media/overlay.py` (lines 130-680, 890-1003)
- **Description & evidence**
  - 3-pass approach: rotate (Pass 1) -> overlay (Pass 2) -> combine (Pass 3).
  - Each video pass re-encodes, multiplying processing time.
  - Could combine rotation and overlay into single FFmpeg filter graph: `[0:v]transpose=1[rotated];[rotated][1:v]overlay=0:0[out]`
- **Impact**
  - **High**: Potential 7-12min savings at scale.
- **Confidence**: **High**

## Finding 2 — `is_file_primary_by_hash` iterates all duplicates per call

- **Category**: Performance
- **Location**: `common/dedup/service.py` (lines 1080-1173)
- **Description & evidence**
  - Direct lookup by content_hash is O(1) for exact hash groups.
  - Falls back to O(n) search over perceptual groups when not found directly.
  - With large manifests, repeated perceptual group searches add up.
  - Should build an index `content_hash -> group_key` at manifest load time for O(1) access.
- **Impact**
  - **High**: Potential 5-10min savings at scale.
- **Confidence**: **High**

## Finding P17 — Duplicated logic between Instagram processors

- **Category**: DRY Violation
- **Location**: `processors/instagram_messages/processor.py`, `processors/instagram_public_media/processor.py`
- **Description & evidence**
  - Both share similar `process_logic`, `_process_working_directory`, and metadata request building logic (`_build_metadata_requests`).
  - Validation and task construction logic is repeated.
- **Impact**
  - **Medium-High**: Maintenance burden; logic updates must be mirrored.
- **Confidence**: **High**

## Finding P18 — Duplicated logic between Snapchat processors

- **Category**: DRY Violation
- **Location**: `processors/snapchat_memories/processor.py`, `processors/snapchat_messages/processor.py`
- **Description & evidence**
  - Significant overlap in overlay handling (`create_video_with_overlay` calls), filename generation, and metadata embedding.
  - `_create_file_worker` / `_create_memory_file` share structure and error handling for overlays.
- **Impact**
  - **Medium-High**: Complex overlay logic is duplicated, increasing risk of bugs.
- **Confidence**: **High**

---

## 3. Medium Impact Findings

These findings have moderate impact on maintainability, performance, or code quality.

---

## Finding 81 — Inconsistent error handling patterns across modules

- **Category**: Inconsistency
- **Location**: Multiple files in `common/`
- **Description & evidence**
  - Some functions silently catch and log:

    ```python
    except Exception as e:
        logger.warning(f"Failed to batch write metadata: {e}")
    ```

  - Some re-raise with context:

    ```python
    except Exception as e:
        raise RuntimeError(f"Failed to process: {e}") from e
    ```

  - Some return error indicators:

    ```python
    except Exception as e:
        logger.error(f"Failed: {e}")
        return None
    ```

  - No established error handling policy or custom exception hierarchy.
- **Impact**
  - **Medium**: Unpredictable error handling makes debugging harder.
- **Confidence**: **High**

## Finding 4 — Database connection overhead in hot paths

- **Category**: Performance
- **Location**: `common/dedup/database.py` (lines 77-89)
- **Description & evidence**
  - Each method creates/closes connection via `_get_connection()` context manager.
  - Uses WAL mode and has optimizations (cache_size, mmap_size) but still opens/closes per call.
  - Adds overhead in hot paths with many database operations.
  - Context manager protocol exists (`__enter__`/`__exit__`/`close()`) but currently no-ops.
  - Should implement persistent connection reuse.
- **Impact**
  - **Medium**: Potential 2-4min savings at scale.
- **Confidence**: **High**

## Finding P15 — Duplicated Instagram username extraction and timestamp parsing across two preprocessors

- **Category**: DRY violation
- **Location**: `processors/instagram_messages/preprocess.py:108-171`, `processors/instagram_public_media/preprocess.py:112-174`
- **Description & evidence**
  - Both preprocessors duplicate:
    - `personal_information.html` username extraction (new vs legacy path variants)
    - `parse_timestamp()` with "new format vs legacy comma after year" parsing formats
- **Impact**
  - **Medium**: Fixes for future Instagram export HTML changes must be applied twice; easy to drift.
- **Confidence**: **High**

## Finding 67 — Nine processors have identical `preprocess()` structure

- **Category**: DRY violation
- **Location**: `processors/base.py`, 9 processors
- **Description & evidence**
  - Nine processors have identical `preprocess()` structure:

    ```python
    @staticmethod
    def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
        input_path = Path(input_dir)
        cache_path = Path(cache_dir)
        workers = kwargs.get("workers")
        logger.info("Preprocessing X export to: %s", cache_path)
        cache_path.mkdir(parents=True, exist_ok=True)
        preprocessor = XPreprocessor(export_path=input_path, output_dir=cache_path, workers=workers)
        if preprocessor.run():
            return str(cache_path)
        return None
    ```

  - Could use template method with abstract `_create_preprocessor()` factory.
- **Impact**
  - **Medium**: ~135 lines of duplicated boilerplate.
- **Confidence**: **High**

## Finding 70 — Similar `_build_metadata_requests()` structure in all 11 processors

- **Category**: DRY violation
- **Location**: `common/media/exiftool.py` or `processors/base.py`, all 11 processors
- **Description & evidence**
  - All processors have similar structure:

    ```python
    def _build_metadata_requests(file_info: List[Tuple]) -> List[MetadataWriteRequest]:
        requests = []
        for item in file_info:
            # Extract fields (processor-specific unpacking)
            datetime_tags = build_datetime_tags(...)
            gps = ...  # Optional
            description = ...  # Build via SourceEntry
            requests.append(MetadataWriteRequest(...))
        return requests
    ```

  - Could create generic builder with callbacks for item unpacking.
- **Impact**
  - **Medium**: ~200 lines of duplicated metadata request building.
- **Confidence**: **High**

## Finding S — Hardcoded platform/service taxonomies in upload-only scanning will drift

- **Category**: DRY violation / architectural drift
- **Location**: `memoria.py` (lines 1178-1262), `processors/preprocess_base.py` (lines 481-494)
- **Description & evidence**
  - Upload-only scanning hardcodes `known_platforms` (line 1180) and `known_services` (line 1187) in `memoria.py`.
  - Additional hardcoded `known_services` in `preprocess_base.py` (line 483) for filename parsing.
  - Similar "known platforms" constants also exist in `common/dedup/description.py` and `standalone/clear_immich_albums.py`.
  - New processors/services or naming changes require edits in multiple places.
- **Impact**
  - **Medium**: Drift-prone behavior and silent functional gaps (missing uploads) when processors evolve.
- **Confidence**: **High**

## Finding S4 — Long functions in `memoria.py` orchestration layer

- **Category**: Code smell
- **Location**: `memoria.py`
- **Description & evidence**
  - `run_phased_dedup_processing()` (lines 428-798): ~370 lines mixing:
    - Phase 0 preprocessing loop
    - Phase 1 catalog building with inline parallel/sequential branching
    - Phase 2 dedup analysis invocation
    - Phase 3 processing loop with upload target collection
  - `process_single_export()` (lines 951-1200): ~250 lines mixing:
    - Processor detection with caching
    - Output directory resolution
    - Processing loop
    - Immich upload handling (both queued and immediate modes)
    - Summary logging
  - `main()` (lines 1566-2223): ~660 lines handling all CLI modes.
- **Impact**
  - **Medium**: Difficult to understand control flow; phases are not cleanly separated.
- **Confidence**: **High**

## Finding S9 — Output path structure inconsistency between parallel and sequential modes

- **Category**: Inconsistency / potential bug
- **Location**: `memoria.py`
- **Description & evidence**
  - Parallel mode adjusts output path to include export directory name (lines 955-958):

    ```python
    # Parallel mode: output_base/export_dir.name/Platform/username
    if output_base and total_exports > 1:
        export_output_dir = str(Path(output_base) / export_dir.name)
        args.output = export_output_dir
    ```

  - Sequential mode does not adjust output path:

    ```python
    # Sequential mode: output_base/Platform/username (no export_dir.name)
    # args.output is passed as-is to process_single_export()
    ```

  - This creates different output directory structures depending on which processing mode is used.
- **Impact**
  - **Medium**: Could cause unexpected output locations when switching between modes; may be intentional for isolation but undocumented.
- **Confidence**: **Medium** (verify if intentional before changing)

## Finding 3 — Sequential hash computation in `filter_primary_files`

- **Category**: Performance
- **Location**: `common/dedup/service.py` (lines 1282-1334)
- **Description & evidence**
  - `filter_primary_files()` iterates sequentially calling `compute_content_hash(f)` per file.
  - I/O bound, would benefit from parallel hash computation.
  - Should use `ThreadPoolExecutor` with manifest index for O(1) lookups in workers.
- **Impact**
  - **Medium**: Potential 3-7min savings at scale.
- **Confidence**: **High**

## Finding 10 — PIL pre-scales overlays before FFmpeg

- **Category**: Performance
- **Location**: `common/media/overlay.py` (lines 427-443)
- **Description & evidence**
  - PIL pre-scales overlays before FFmpeg using `Image.resize()` with LANCZOS.
  - Creates temp PNG file for scaled overlay, adding I/O overhead.
  - Could use FFmpeg `scale` filter on the overlay input instead.
- **Impact**
  - **Medium**: Potential 3-5min savings at scale.
- **Confidence**: **High**

## Finding 25 — Sequential ffmpeg remux fallback for large videos

- **Category**: Performance
- **Location**: `common/media/exiftool.py` (lines 676-707)
- **Description & evidence**
  - When ExifTool fails on large videos, the ffmpeg remux fallback is called sequentially within the chunk processing loop.
  - Each `_remux_video_with_metadata()` call is blocking with a 10-minute timeout.
  - Failed videos are processed one at a time rather than in parallel:

    ```python
    for request in filtered_chunk:
        if request.file_path in failed_files:
            if _is_large_video_error(error_msg) and _can_attempt_ffmpeg_remux(...):
                remux_result = _remux_video_with_metadata(...)  # Blocking call
    ```

  - Could parallelize with `ThreadPoolExecutor` since remux operations are independent and I/O-bound.
- **Impact**
  - **Medium**: Potential 5-15min savings when multiple large videos fail ExifTool in the same batch.
- **Confidence**: **High**

## Finding P19 — `_create_memory_file` worker function mixes multiple responsibilities

- **Category**: SRP violation / code smell
- **Location**: `processors/snapchat_memories/processor.py` (lines 423-617)
- **Description & evidence**
  - 195-line worker function that handles:
    1. File existence validation
    2. Media type detection (`get_media_type()`)
    3. Overlay existence checks
    4. Video overlay creation (FFmpeg subprocess)
    5. Image overlay compositing (PIL)
    6. File copying for non-overlay cases
    7. Timestamp updates
    8. Error recovery with fallback copying
  - Each concern returns different tuple variations, making result handling complex.
  - Similar complexity in `snapchat_messages/processor.py:_create_file_worker()`.
- **Impact**
  - **Medium**: Difficult to test individual concerns; changes risk unintended side effects.
- **Confidence**: **High**

## Finding Q10 — Cataloging forces list materialization of all media entries (memory spike risk)

- **Category**: Performance / scalability
- **Location**: `common/dedup/catalog.py` (`catalog_from_preprocessed`)
- **Description & evidence**
  - `catalog_from_preprocessed()` loads all extracted media entries into a list before cataloging:
    - `media_entries = list(_extract_media_entries(metadata))`
  - This defeats the otherwise streaming/iterator-based design of cataloging for large exports with many entries.
- **Impact**
  - **Medium**: Potential memory spikes on very large exports; contrasts with the streaming-to-DB architecture elsewhere (`memoria.py` + `HashDatabase.write_catalog_entries_batch()`).
- **Confidence**: **High**

## Finding Q12 — Hash cache introduces persistent cross-run state (conflicts with "stateless batch" posture)

- **Category**: Architectural drift / hidden state
- **Location**: `common/dedup/config.py`, `processors/preprocess_base.py`, `common/dedup/catalog.py`, `common/dedup/database.py`
- **Description & evidence**
  - Dedup config defaults to a persistent SQLite file in the working directory:

    ```python
    database_path: Path = field(default_factory=lambda: Path.cwd() / "dedup.db")
    ```

  - Preprocessing and cataloging explicitly use a hash cache (and tests exercise disabling it via env):
    - `DISABLE_HASH_CACHE` is checked in `PreprocessorBase.hash_with_dedup()` and `PreprocessorBase.register_downloaded_file()`
    - `common/dedup/catalog.py` also reads/writes cached hashes for perceptual hashes and dimensions
  - `HashDatabase` contains an explicit "Hash Cache Methods (Caching for Repeated Runs)" section with `get_cached_hashes()`, `cache_hashes_batch()`, and `clear_hash_cache()`.
- **Impact**
  - **Medium**: Cross-run persistence makes behavior/perf depend on prior runs and cache invalidation heuristics (mtime/size). This contradicts the stated "batch reprocesses from scratch" posture and increases the surface area for "works on my machine" effects.
- **Confidence**: **High**

## Finding P16 — Duplicated Google username detection logic across preprocessors

- **Category**: DRY violation
- **Location**: `processors/google_photos/preprocess.py:135-196`, `processors/google_chat/preprocess.py:99-160`
- **Description & evidence**
  - Both implement similar detection priority and parsing:
    - `Profile/Profile.json` → email → username
    - `Google Chat/Users/*/user_info.json` → email → username
    - directory name fallback
- **Impact**
  - **Low-Medium**: Maintenance overhead; changes to detection heuristics must be duplicated.
- **Confidence**: **Medium**

## Finding S5 — Catalog building logic inlined in orchestration layer

- **Category**: Boundary violation
- **Location**: `memoria.py` (lines 287-394, 533-656)
- **Description & evidence**
  - `_build_catalog_worker()` reimplements catalog iteration and database writing logic.
  - Sequential catalog building (lines 605-656) duplicates similar logic with direct database calls.
  - Catalog building responsibility should be delegated entirely to `common/dedup/` or `ProcessorBase`.

    ```python
    # Current: inline in memoria.py
    for entry in processor.build_catalog(catalog_dir, workers=workers):
        if "_processor" not in entry:
            entry["_processor"] = processor_name
        batch.append(entry)
        if len(batch) >= 100:
            db.write_catalog_entries_batch(run_id, export_dir.name, batch)
            batch = []
    ```

  - Similar batching logic appears in both parallel and sequential paths.
- **Impact**
  - **Low-Medium**: Tight coupling between orchestration and catalog internals.
- **Confidence**: **High**

## Finding 12 — `ThreadPoolExecutor` for CPU-bound hashing limited by GIL

- **Category**: Performance
- **Location**: `common/dedup/catalog.py` (lines 170, 542)
- **Description & evidence**
  - `ThreadPoolExecutor` used for hashing in `_compute_missing_hashes_parallel()` and `catalog_media_files()`.
  - File I/O benefits from threads, but xxHash/perceptual hash computation is CPU-bound.
  - GIL limits parallel CPU work; `ProcessPoolExecutor` would improve throughput.
- **Impact**
  - **Low-Medium**: Potential 1-3min savings at scale.
- **Confidence**: **Medium**

## Finding Q9 — Version/format handling is often embedded directly in preprocessors (quirk logic leakage)

- **Category**: Boundary leakage / maintainability risk
- **Location**: `processors/instagram_messages/preprocess.py`, `processors/instagram_public_media/preprocess.py`
- **Description & evidence**
  - Each preprocessor performs "new vs legacy export format" detection in `__init__` (sets `is_legacy_format` and chooses a root directory), but format-specific branching continues inside multiple methods (e.g., timestamp parsing and HTML structure parsing).
  - This disperses platform/version quirks across the preprocessor's parsing logic rather than isolating them to a single adapter boundary.
- **Impact**
  - **Low-Medium**: Works today, but increases the surface area impacted by future export format changes.
- **Confidence**: **High**

---

## 4. Low Impact Findings

These findings have limited impact but are worth addressing for code hygiene and maintainability.

---

## Finding 78 — Long functions in DeduplicationService exceed reasonable length

- **Category**: Code smell
- **Location**: `common/dedup/service.py`
- **Description & evidence**
  - `_group_by_perceptual_hash_parallel()` (lines 490-680): ~190 lines (parallel execution, chunking, union-find)
  - `_build_manifest_from_groups()` (lines 231-306): ~75 lines (statistics, group processing)
  - `_merge_metadata()` (lines 970-1061): ~90 lines (multiple field merge strategies)
  - Long functions are harder to test, understand, and maintain.
- **Impact**
  - **Low**: Works, but reduces testability and readability.
- **Confidence**: **High**

## Finding 82 — `FailureTracker` handles both tracking and report generation

- **Category**: SRP violation
- **Location**: `common/failures/tracker.py`
- **Description & evidence**
  - Class handles:
    1. Failure tracking (adding entries, counting)
    2. Report generation and file I/O (copying files, saving JSON)
  - Should extract report generation to separate `FailureReporter` class.
- **Impact**
  - **Low**: Works, but class is larger than necessary.
- **Confidence**: **High**

## Finding 83 — No retry logic for external process calls

- **Category**: Missing resilience
- **Location**: `common/media/exiftool.py`, `common/media/overlay.py`
- **Description & evidence**
  - External process calls (exiftool, ffmpeg, ffprobe) have no retry logic.
  - Transient failures (high system load, temporary file locks) cause immediate failure.
  - Current: `result = subprocess.run(cmd, capture_output=True, ...)` with no retry.
- **Impact**
  - **Low**: Only relevant if transient failures are observed in production.
- **Confidence**: **Medium**

## Finding 7 — `get_similar` pulls all hashes into memory

- **Category**: Performance
- **Location**: `common/dedup/database.py`
- **Description & evidence**
  - O(N) memory usage for similarity search.
  - Could store 256-bit hash as four 64-bit INTEGER columns for SQL bitwise ops.
- **Impact**
  - **Low**: Dedup is already fast (11min for 1.2B comparisons); not worth the effort.
- **Confidence**: **Medium**

## Finding M — Unmaintained standalone scripts

- **Category**: Dead code / maintenance debt
- **Location**: `standalone/` directory
- **Description & evidence**
  - Scripts like `sample_metadata_audit.py`, `verify_metadata.py`, and `compare_exports.py` are not invoked by main `memoria.py` or processors.
  - They likely rely on older data structures (e.g., before the source_path-only refactor).
- **Impact**
  - **Low**: Clutter; potential confusion for new contributors.
- **Confidence**: **Medium** (Check utility before deletion).

## Finding M2 — `verify_metadata.py` contains stub implementation

- **Category**: Incomplete implementation
- **Location**: `standalone/verify_metadata.py:56-78`
- **Description & evidence**
  - `find_source_metadata()` function contains placeholder logic that always returns `None`:

    ```python
    def find_source_metadata(processed_path: Path, dedup_manifest: Dict):
        for content_hash, group_data in dedup_manifest.get("duplicates", {}).items():
            primary = group_data.get("primary", {})
            primary.get("source_path", "")  # Value extracted but never used
            merged = group_data.get("merged_metadata", {})
            if merged:
                pass  # Stub: no implementation
        return None, None, "unknown"  # Always returns None
    ```

  - Function signature suggests it should find source metadata for processed files but implementation is incomplete.
- **Impact**
  - **Low**: Standalone script only; does not affect production code.
- **Confidence**: **High**

## Finding M4 — `filter_primary_files()` potentially superseded by `should_skip_for_dedup()`

- **Category**: Potential dead code
- **Location**: `common/dedup/service.py:1269-1321`
- **Description & evidence**
  - Function exists and is tested (`tests/test_dedup/test_dedup_service.py:1105-1224`).
  - Processors use inline dedup filtering via `should_skip_for_dedup()` with precomputed hashes from preprocessing.
  - `filter_primary_files()` recomputes hashes at runtime (inefficient pattern):

    ```python
    for f in progress_bar(files, PHASE_DEDUP, "Filtering duplicates"):
        content_hash = compute_content_hash(f)  # Redundant I/O
        if is_file_primary_by_hash(dedup_manifest, content_hash, processor_name, str(f)):
            filtered.append(f)
    ```

  - No grep matches show production usage in processors; appears superseded by hash-at-preprocess pattern.
- **Impact**
  - **Low**: Function works but may be dead code in practice.
- **Confidence**: **Medium** (verify no production callers before removal).

## Finding P5 — Duplicated timestamp extraction functions

- **Category**: DRY violation
- **Location**: `processors/imessage/processor.py:538+`, `processors/snapchat_messages/processor.py:480+`
- **Description & evidence**
  - `_extract_message_timestamp()` / `_get_message_timestamp()` implement identical logic for extracting timestamps from merged vs single messages.
  - All check for `primary_created`, then `messages[0].created`, then `created`.
- **Impact**
  - **Low**: ~50 lines duplicated; straightforward to consolidate.
- **Confidence**: **High**

## Finding 68 — Nine processors have similar `needs_preprocessing()` pattern

- **Category**: DRY violation
- **Location**: `processors/base.py`, 9 processors
- **Description & evidence**
  - Nine processors have similar pattern:

    ```python
    @staticmethod
    def needs_preprocessing(input_dir: str) -> bool:
        input_path = Path(input_dir)
        if is_preprocessed_directory(input_dir):
            return False
        if (specific_raw_structure).exists():
            return True
        return False
    ```

  - Could use template method with abstract `_has_raw_export_structure()`.
- **Impact**
  - **Low**: ~90 lines of duplicated boilerplate.
- **Confidence**: **High**

## Finding 72 — Overlapping imports across all 11 processor modules

- **Category**: DRY violation
- **Location**: All 11 processor modules
- **Description & evidence**
  - Each processor has 25-35 lines of imports with significant overlap:

    ```python
    import json
    import logging
    from pathlib import Path
    from typing import Any, Dict, List, Optional, Tuple
    from common.dedup.catalog import catalog_media_files
    from common.dedup.service import is_file_primary_by_hash
    from common.files.utils import copy_file
    from common.media.exiftool import (
        DEFAULT_PREAMBLE,
        DescriptionWriteSpec,
        MetadataWriteRequest,
        build_datetime_tags,
    )
    # ... 15+ more imports
    ```

  - Could create `processors/common_imports.py` for shared imports.
- **Impact**
  - **Low**: ~200 lines of duplicated imports.
- **Confidence**: **High**

## Finding P20 — Inconsistent field naming between `media_files` and `media_file` in schema

- **Category**: Inconsistency
- **Location**: `common/metadata/schema.py`, processors using `MessageEntry`
- **Description & evidence**
  - `MessageEntry` TypedDict defines both:

    ```python
    media_files: List[str]  # Plural
    media_file: str         # Singular
    ```

  - Processors inconsistently use one or the other:
    - Discord, Snapchat Messages: `media_files` (list)
    - Some iMessage code paths check both
  - `_validate_conversation_source_paths()` handles both with fallback logic (lines 461-465).
- **Impact**
  - **Low**: Handled by validation fallback, but adds complexity.
- **Confidence**: **High**

## Finding S7 — Unused return values from processing functions

- **Category**: Dead code
- **Location**: `memoria.py`
- **Description & evidence**
  - `run_phased_dedup_processing()` returns 4-tuple but caller ignores `success_count` and `results`:

    ```python
    # Line 1920: results and success_count are never used after capture
    success_count, failed_count, results, upload_targets = (
        run_phased_dedup_processing(...)
    )
    # Only failed_count and upload_targets are used
    ```

  - `process_single_export()` returns `results` and `upload_tasks` but all callers ignore them:

    ```python
    # Line 961 and 2150: Both use underscore prefix to indicate intentional non-use
    success_count, failed_count, _results, _upload_tasks = process_single_export(...)
    ```

  - The `results` list inside `run_phased_dedup_processing()` is populated (lines 777-809) but never used for summary display or returned value consumption.
- **Impact**
  - **Low**: Return values are computed but discarded; could simplify return types.
- **Confidence**: **High**

## Finding 34 — HTTP requests without connection pooling for Discord

- **Category**: Performance
- **Location**: `processors/discord/preprocess.py`
- **Description & evidence**
  - `requests.get()` per attachment without pooling. Repeated handshakes.
  - Should use `requests.Session()` with `HTTPAdapter`. Thread-local sessions for multithreaded.
  - Discord preprocessing: 1,223 attachments.
- **Impact**
  - **Low**: Potential <1min savings at scale.
- **Confidence**: **High**

## Finding 35 — Discord downloads to disk then re-reads for content_hash

- **Category**: Performance
- **Location**: `processors/discord/preprocess.py`, `processors/preprocess_base.py`
- **Description & evidence**
  - Downloads to disk then re-reads for content_hash. Redundant I/O.
  - Options:
    - Hash incrementally while streaming (hash-as-you-write)
    - Cache hashes by `(path, size, mtime)`
- **Impact**
  - **Low**: Potential <1min savings at scale.
- **Confidence**: **High**

## Finding 36 — Per-file SQLite overhead in download dedup

- **Category**: Performance
- **Location**: `processors/preprocess_base.py`
- **Description & evidence**
  - `register_downloaded_file()` creates DB objects per file. Per-file SQLite overhead.
  - Should use thread-local `HashDatabase` or enable connection reuse.
- **Impact**
  - **Low**: Potential <1min savings at scale.
- **Confidence**: **High**

## Finding 19 — Perceptual comparisons scale O(n^2)

- **Category**: Performance
- **Location**: `common/dedup/service.py`
- **Description & evidence**
  - Perceptual comparisons scale O(n^2).
  - Could pre-index with LSH, split 256-bit hash into bands, compare only matching bands.
  - Deprioritized since dedup is already fast (11min for 1.2B comparisons).
- **Impact**
  - **Low**: Dedup already fast; limited ROI.
- **Confidence**: **Medium**

## Finding 22 — Pickling overhead in IPC for perceptual comparison

- **Category**: Performance
- **Location**: `common/dedup/service.py` (lines 33-62)
- **Description & evidence**
  - `_compare_hash_chunk` ships hash strings per pair. Pickling overhead at scale.
  - Could convert to integers, share via `multiprocessing.Array`, ship index pairs only.
  - Deprioritized since dedup is already fast.
- **Impact**
  - **Low**: Dedup already fast; limited ROI.
- **Confidence**: **Medium**

## Finding 8 — Large HEIC fully decoded during hashing

- **Category**: Performance
- **Location**: `common/dedup/hashing.py`
- **Description & evidence**
  - Large HEIC fully decoded (50MB+). JPEG uses `img.draft()` but HEIC doesn't.
  - Could check if `pillow-heif` supports `draft()`.
  - Already using early downsampling; acceptable.
- **Impact**
  - **Low**: Current approach acceptable.
- **Confidence**: **Medium**

## Finding 24 — Full metadata through compare phases when only subset needed

- **Category**: Performance
- **Location**: `common/dedup/catalog.py`, `common/dedup/service.py`
- **Description & evidence**
  - Full metadata through compare phases when only subset needed.
  - Could create `CatalogEntryMinimal` for comparison, load full on manifest build.
- **Impact**
  - **Low**: Limited ROI.
- **Confidence**: **Medium**

## Finding Q3 — Bare `pass` statements in exception handlers

- **Category**: Code smell / potential silent failures
- **Location**: Multiple occurrences across codebase
- **Key locations**:
  - `processors/imessage/processor.py`
  - `common/media/exiftool.py`
  - `common/files/utils.py`
  - `common/text/parse.py`
  - `common/dedup/hashing.py`
- **Description & evidence**
  - Many are intentional fallback logic (e.g., trying multiple parse formats).
  - Some may suppress unexpected errors silently.
- **Impact**
  - **Low**: Most are intentional; worth documenting.
- **Confidence**: **Medium**

## Finding Q6 — Live photo grouping uses tuple key with potential collision

- **Category**: Code smell
- **Location**: `processors/google_photos/processor.py` (lines 217-226, 415-431)
- **Description & evidence**
  - Live photo pairing uses `(timestamp, base_name)` tuple as group key:

    ```python
    def get_live_photo_group_key(media_data):
        timestamp = media_data.get("capture_timestamp", "")
        original_filename = media_data.get("original_filename", "")
        base_name = os.path.splitext(original_filename)[0]
        return (timestamp, base_name)
    ```

  - If `original_filename` is empty, all such files share key `(timestamp, "")`.
  - Could cause incorrect live photo pairing for files with missing original filenames.
- **Impact**
  - **Low**: Edge case; would only occur with malformed metadata.
- **Confidence**: **Medium**

## Finding Q7 — Result tuple structures vary across processor workers

- **Category**: Inconsistency
- **Location**: All processor worker functions
- **Description & evidence**
  - Worker functions return different tuple structures:
    - `google_photos`: 3-element `(success, output_path, info_tuple)`
    - `snapchat_memories`: 8-element `(success, output_path, is_mkv, media_data, username, hash, failure_info, original_filename)`
    - `discord`: 6-element `(success, output_path, message_info, username, hash, original_filename)`
  - `collect_processing_results()` uses optional `info_extractor` callback to handle variations.
  - No common `WorkerResult` dataclass enforcing structure.
- **Impact**
  - **Low**: Works via callbacks, but adds cognitive overhead when reading processor code.
- **Confidence**: **High**

---

## 5. Very Low Impact Findings

These findings have minimal impact but can be addressed during regular maintenance.

---

## Finding M3 — Unused variables in `verify_metadata.py`

- **Category**: Dead code
- **Location**: `standalone/verify_metadata.py`
- **Description & evidence**
  - Multiple computed values are never used:

    ```python
    # Lines 330-332: Date parts extracted but discarded
    date_str[4:6]  # month - unused
    date_str[6:8]  # day - unused

    # Lines 506-507: Check counts computed but never used
    sum(1 for c in result["checks"] if c.get("passed", False))
    len(result["checks"])
    ```

  - Indicates incomplete refactoring or abandoned feature development.
- **Impact**
  - **Very Low**: No runtime effect; cosmetic dead code.
- **Confidence**: **High**

## Finding S6 — Unused parameters and variables in memoria.py

- **Category**: Dead code
- **Location**: `memoria.py`
- **Description & evidence**
  - `_export_idx` parameter in `_process_export_worker()` (line 893) is passed but never used inside the function:

    ```python
    def _process_export_worker(
        ...
        _export_idx: int,  # Passed at line 2088 but never referenced
        total_exports: int,
        ...
    ```

  - `export_idx` variable in sequential processing loop (line 2141) is defined but never used:

    ```python
    for export_idx, export_dir in enumerate(dirs_to_process, 1):
        # export_idx is never used in the loop body
    ```

- **Impact**
  - **Very Low**: No runtime effect; cosmetic dead code.
- **Confidence**: **High**

## Finding S8 — Dead code: output path restoration in KeyboardInterrupt handler

- **Category**: Dead code
- **Location**: `memoria.py` (lines 2174-2175)
- **Description & evidence**
  - Sequential mode's KeyboardInterrupt handler contains code to restore `args.output`:

    ```python
    except KeyboardInterrupt:
        # Restore original output path if we modified it
        if len(dirs_to_process) > 1 and output_base:
            args.output = str(output_base)
    ```

  - In sequential mode, `args.output` is never modified (unlike parallel mode at lines 955-958), so this restoration code never does anything useful.
- **Impact**
  - **Very Low**: Unreachable logic that appears to be leftover from refactoring.
- **Confidence**: **High**

## Finding Q4 — Duplicated `MEDIA_EXTENSIONS` constant in standalone script

- **Category**: DRY violation
- **Location**: `standalone/sample_metadata_audit.py:26-29`
- **Description & evidence**
  - Defines local `MEDIA_EXTENSIONS` instead of importing from `common.files.types` (e.g., `ALL_MEDIA_EXTENSIONS`).
- **Impact**
  - **Very Low**: Standalone script isolation, but risks drift.
- **Confidence**: **High**

## Finding Q13 — Test-only "backward compatibility alias" is unused and implies legacy surface

- **Category**: Dead code / test hygiene
- **Location**: `tests/fixtures/generators.py:792-793`, `tests/fixtures/__init__.py`
- **Description & evidence**
  - `tests/fixtures/generators.py` defines:

    ```python
    # Backward compatibility alias
    create_instagram_old_export = create_instaloader_export
    ```

  - Repo-wide search shows no usage outside fixture re-export (`tests/fixtures/__init__.py`); tests can import `create_instaloader_export` directly.
- **Impact**
  - **Very Low**: No runtime impact, but increases cognitive load and suggests there is a supported legacy API surface in fixtures when there isn't.
- **Confidence**: **High**

---
