
### 1. Executive Summary

Overall the repo is **well-structured and test-backed** (clear processor boundaries in `processors/`, shared utilities in `common/`, and broad test coverage under `tests/`).

---

### 2. Findings

#### 2.1 `common/` Directory Audit

##### Finding N — `common/preprocess/__init__.py` exports are incomplete vs actual usage

- **Category**: API inconsistency
- **Location**: `common/preprocess/__init__.py`
- **Description & evidence**
  - `merge_imessage_context()` is used by preprocessors, but `common/preprocess/__init__.py` only exports `merge_albums` and `merge_snapchat_context`.
- **Impact**
  - **Low**: Works (callers import from `common.preprocess.context_mergers`), but increases drift and inconsistent "public API" conventions.
- **Confidence**: **High**

##### Finding 74 — `HashDatabase` is a concrete class with no abstract interface

- **Category**: DIP violation
- **Location**: `common/dedup/database.py`
- **Description & evidence**
  - No Protocol or ABC defined for database operations.
  - Makes testing harder (can't easily mock database operations).
  - Future alternative implementations (e.g., PostgreSQL) harder to integrate.
  - High-level modules depend on concrete implementation.
- **Impact**
  - **Low**: Works, but limits testability and extensibility.
- **Confidence**: **High**

##### Finding 75 — Environment variable parsing is scattered across multiple modules

- **Category**: DRY violation
- **Location**: `common/config/runtime.py`, `common/processing/cache.py`, `common/dedup/service.py`, `common/dedup/config.py`
- **Description & evidence**
  - `common/config/runtime.py`: `parse_bool_env()`, `should_cleanup_preprocessing()`
  - `common/processing/cache.py`: preprocessing cache cleanup behavior
  - `common/dedup/service.py`: `PARALLEL_DEDUP` (line 408)
  - `common/dedup/config.py`: Multiple dedup-related env vars (lines 97-155)
  - Hard to find all configuration points; violates DRY.
- **Impact**
  - **Medium**: Configuration drift; hard to audit all env vars.
- **Confidence**: **High**

##### Finding 76 — `exiftool.py` mixes command building with process execution

- **Category**: SRP violation
- **Location**: `common/media/exiftool.py`
- **Description & evidence**
  - Module is ~1260 lines mixing multiple concerns:
    1. Building exiftool commands (metadata request construction, tag formatting)
    2. Process execution (subprocess calls, argfile management, error handling)
    3. FFmpeg remux fallback for large videos
  - Subprocess execution logic is duplicated between `batch_write_metadata_requests()`, `batch_validate_exif()`, and `batch_rebuild_exif()`.
  - Extracting process management would also prepare for `-stay_open` mode implementation.
- **Impact**
  - **Medium**: Large module, harder to test, duplicated execution logic.
- **Confidence**: **High**

##### Finding 78 — Long functions in DeduplicationService exceed reasonable length

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

##### Finding 79 — Circular import workarounds via lazy imports

- **Category**: Architectural smell
- **Location**: `common/processing/cache.py`, `common/media/exiftool.py`
- **Description & evidence**
  - `common/processing/cache.py` (lines 13, 48): `from common.dedup.config import get_dedup_config`
  - `common/media/exiftool.py` (line 767): `from common.dedup.service import get_merged_metadata_for_hash`
  - `common/media/exiftool.py` (line 898): `from common.dedup.service import get_secondary_sources_for_hash`
  - Lazy imports add overhead on each call, hide dependencies from static analysis, make dependency graph unclear.
- **Impact**
  - **Low**: Works, but architectural smell indicating need for restructuring.
- **Confidence**: **Medium**

##### Finding 80 — Inconsistent type hints in processing functions

- **Category**: Type safety gap
- **Location**: `common/processing/batch.py`
- **Description & evidence**
  - TypeVars T, R are defined (lines 31-32) and used in `process_items_parallel()` (line 164).
  - But `process_batches_parallel()` (line 117) still uses `List[Any]`:

    ```python
    def process_batches_parallel(
        tasks: List[Any],                    # Should use TypeVar T
        worker_fn: Callable[[List[Any]], List[Any]],
        ...
    ) -> List[Any]:
    ```

  - Inconsistent type safety across similar functions.
- **Impact**
  - **Low**: Works, but misses static analysis benefits.
- **Confidence**: **High**

##### Finding 81 — Inconsistent error handling patterns across modules

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

##### Finding 82 — `FailureTracker` handles both tracking and report generation

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

##### Finding 83 — No retry logic for external process calls

- **Category**: Missing resilience
- **Location**: `common/media/exiftool.py`, `common/media/overlay.py`
- **Description & evidence**
  - External process calls (exiftool, ffmpeg, ffprobe) have no retry logic.
  - Transient failures (high system load, temporary file locks) cause immediate failure.
  - Current: `result = subprocess.run(cmd, capture_output=True, ...)` with no retry.
- **Impact**
  - **Low**: Only relevant if transient failures are observed in production.
- **Confidence**: **Medium**

##### Finding 84 — `get_media_type()` returns magic strings instead of Enum

- **Category**: Type safety gap
- **Location**: `common/files/types.py`
- **Description & evidence**
  - Returns `"image"`, `"video"`, or `None` as strings.
  - String comparisons are error-prone and not caught by static analysis.
  - Should use `MediaType` Enum.
- **Impact**
  - **Low**: Works, but misses static analysis benefits.
- **Confidence**: **High**

##### Finding 4 — Database connection overhead in hot paths

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

##### Finding 7 — `get_similar` pulls all hashes into memory

- **Category**: Performance
- **Location**: `common/dedup/database.py`
- **Description & evidence**
  - O(N) memory usage for similarity search.
  - Could store 256-bit hash as four 64-bit INTEGER columns for SQL bitwise ops.
- **Impact**
  - **Low**: Dedup is already fast (11min for 1.2B comparisons); not worth the effort.
- **Confidence**: **Medium**

---

#### 2.2 Processor-Level Findings

##### Finding M — Unmaintained standalone scripts

- **Category**: Dead code / maintenance debt
- **Location**: `standalone/` directory
- **Description & evidence**
  - Scripts like `sample_metadata_audit.py`, `verify_metadata.py`, and `compare_exports.py` are not invoked by main `memoria.py` or processors.
  - They likely rely on older data structures (e.g., before the source_path-only refactor).
- **Impact**
  - **Low**: Clutter; potential confusion for new contributors.
- **Confidence**: **Medium** (Check utility before deletion).

##### Finding P2 — Duplicated `_process_working_directory` boilerplate

- **Category**: DRY violation
- **Location**: All 11 processors implement variants of this pattern
- **Description & evidence**
  - Every processor implements 200-300 lines of identical workflow: load metadata → init FailureTracker → generate filenames → iterate entries with dedup check → parallel processing → collect results → batch EXIF → track failures → save dedup metadata → print summary.
  - Minor variations in metadata structure extraction and task tuple construction.
- **Impact**
  - **High**: ~2500 lines of near-identical boilerplate across processors.
- **Confidence**: **High**

##### Finding P4 — Duplicated `_build_description` and helper functions between iMessage processors

- **Category**: DRY violation
- **Location**: `processors/imessage/processor.py:338+`, `processors/imessage_imazing/processor.py:276+`
- **Description & evidence**
  - `_build_description()` is 95% identical between `imessage` and `imessage_imazing`.
  - `_extract_date_from_source_export()` and `_extract_device_from_source_export()` are copy-pasted verbatim.
  - `snapchat_messages/processor.py:289+` and `discord/processor.py:218+` have similar but smaller duplication.
- **Impact**
  - **Medium**: 200+ lines of duplicated code across 2-3 processors.
- **Confidence**: **High**

##### Finding P5 — Duplicated timestamp extraction functions

- **Category**: DRY violation
- **Location**: `processors/imessage/processor.py:538+`, `processors/imessage_imazing/processor.py:449+`, `processors/snapchat_messages/processor.py:480+`
- **Description & evidence**
  - `_extract_message_timestamp()` / `_get_message_timestamp()` implement identical logic for extracting timestamps from merged vs single messages.
  - All check for `primary_created`, then `messages[0].created`, then `created`.
- **Impact**
  - **Low**: ~50 lines duplicated; straightforward to consolidate.
- **Confidence**: **High**

##### Finding P11 — Internal backwards-compat timestamp fallback in Snapchat Messages processing

- **Category**: Legacy internal compatibility path
- **Location**: `processors/snapchat_messages/processor.py:390+`, `processors/snapchat_messages/processor.py:505+`
- **Description & evidence**
  - EXIF datetime tags are built from `canonical_timestamp` / `primary_created` / `created` with special handling for "legacy date-only" strings.
  - `_update_snapchat_timestamps()` explicitly falls back to parsing `YYYY-MM-DD` "for backwards compatibility with old metadata".
- **Impact**
  - **Medium**: Allows non-canonical internal metadata formats to persist; increases timestamp handling drift and makes downstream behavior harder to reason about.
- **Confidence**: **High**

##### Finding P15 — Duplicated Instagram username extraction and timestamp parsing across two preprocessors

- **Category**: DRY violation
- **Location**: `processors/instagram_messages/preprocess.py:108-171`, `processors/instagram_public_media/preprocess.py:112-174`
- **Description & evidence**
  - Both preprocessors duplicate:
    - `personal_information.html` username extraction (new vs legacy path variants)
    - `parse_timestamp()` with "new format vs legacy comma after year" parsing formats
- **Impact**
  - **Medium**: Fixes for future Instagram export HTML changes must be applied twice; easy to drift.
- **Confidence**: **High**

##### Finding P16 — Duplicated Google username detection logic across preprocessors

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

##### Finding 52 — Inconsistent dedup check patterns across processors

- **Category**: Inconsistency
- **Location**: All 11 processors
- **Description & evidence**
  - Pattern A (most processors):

    ```python
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

  - Pattern B (Snapchat Memories): Uses `should_skip_for_dedup()` without cached `file_hashes`.
  - Pattern B always re-hashes (no cached hashes), wasting cycles.
- **Impact**
  - **Low**: Works, but inconsistent and less efficient for Pattern B.
- **Confidence**: **High**

##### Finding 55 — All processors have identical `process()` structure

- **Category**: DRY violation
- **Location**: `processors/base.py`, all 11 processors
- **Description & evidence**
  - All 11 processors implement identical `process()` structure:

    ```python
    @staticmethod
    def process(input_dir, output_dir=None, dedup_manifest=None, **kwargs) -> bool:
        if not check_exiftool():
            print_exiftool_error()
            return False
        try:
            if output_dir:
                processor_output = str(Path(output_dir) / "subdirectory")
            else:
                processor_output = kwargs.get("output", "default_path")
            process_logic(input_dir=input_dir, output_dir=processor_output, ...)
            return True
        except Exception as e:
            logger.error(f"Error in Processor: {e}")
            return False
    ```

  - Could use template method pattern with abstract `_process_impl()`.
- **Impact**
  - **Medium**: ~220 lines of duplicated boilerplate.
- **Confidence**: **High**

##### Finding 56 — Near-identical dedup filtering in 10 processors

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

##### Finding 57 — Near-identical batch workers in six processors

- **Category**: DRY violation
- **Location**: `google_chat`, `google_photos`, `google_voice`, `instagram_messages`, `instagram_public_media`, `instaloader`
- **Description & evidence**
  - Six processors have near-identical batch workers:

    ```python
    def process_media_batch(batch_args):
        results = []
        file_paths = []
        file_info = []
        # Phase 1: Copy
        # Phase 2: Correct extensions
        # Phase 3: Update timestamps
        return results
    ```

  - Could create `create_batch_copy_worker()` factory function.
- **Impact**
  - **Medium**: ~240 lines of duplicated batch worker code.
- **Confidence**: **High**

##### Finding 63 — No automated enforcement of processor patterns

- **Category**: Missing test infrastructure
- **Location**: `tests/` (missing `test_processor_compliance.py`)
- **Description & evidence**
  - No automated enforcement of processor patterns.
  - Should have compliance test suite checking:
    - Inherits from base
    - Has required static methods
    - `get_name()` returns non-empty string
    - `get_priority()` in valid range
    - `get_output_info()` valid
    - `supports_dedup()` implies `build_catalog` override
    - `supports_consolidation()` implies `process_consolidated` override
- **Impact**
  - **Medium**: Pattern violations not caught until runtime.
- **Confidence**: **High**

##### Finding 67 — Nine processors have identical `preprocess()` structure

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

##### Finding 68 — Nine processors have similar `needs_preprocessing()` pattern

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

##### Finding 70 — Similar `_build_metadata_requests()` structure in all 11 processors

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

##### Finding 72 — Overlapping imports across all 11 processor modules

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

---

#### 2.3 CLI / Orchestration Findings (`memoria.py`)

##### Finding S — Hardcoded platform/service taxonomies in upload-only scanning will drift

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

##### Finding S2 — Upload-only "legacy detection" mode is a superseded internal compatibility branch

- **Category**: Legacy internal compatibility path (internal behavior)
- **Location**: `memoria.py` (lines 1283-1349)
- **Description & evidence**
  - Upload-only has two modes:
    - New mode: scan processed output structure via `_scan_platform_username_service_structure()`
    - Legacy mode: if `original_export` is provided, detect processors from the original export via `_build_upload_only_targets()`
  - Log strings reveal the split:
    - `Upload-only legacy mode: missing output directory for ...` (line 1326)
    - Log prefix differs: `upload-legacy` vs `upload` (line 1362)
- **Impact**
  - **Medium**: Two target-derivation semantics to maintain/test; legacy branch can drift from "scan processed outputs" behavior.
- **Confidence**: **High**

---

#### 2.4 Performance Findings

##### Finding 39 — Repeated exiftool invocations incur startup overhead at scale

- **Category**: Performance
- **Location**: `common/media/exiftool.py`
- **Description & evidence**
  - Each batch write creates a new exiftool subprocess via `subprocess.run()`.
  - Uses argfile approach for batching, but still starts/stops process per chunk.
  - EXIF operations remain primary bottleneck for iMessage + Google Photos.
  - Should use `-stay_open True` mode with long-lived subprocess.
  - Requires careful error handling for persistent process.
- **Impact**
  - **High**: Potential 15-30min savings at scale.
- **Confidence**: **High**

##### Finding 9 — Multi-pass video processing causes multiple re-encodes

- **Category**: Performance
- **Location**: `common/media/overlay.py` (lines 130-680, 890-1003)
- **Description & evidence**
  - 3-pass approach: rotate (Pass 1) -> overlay (Pass 2) -> combine (Pass 3).
  - Each video pass re-encodes, multiplying processing time.
  - Could combine rotation and overlay into single FFmpeg filter graph: `[0:v]transpose=1[rotated];[rotated][1:v]overlay=0:0[out]`
- **Impact**
  - **High**: Potential 7-12min savings at scale.
- **Confidence**: **High**

##### Finding 2 — `is_file_primary_by_hash` iterates all duplicates per call

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

##### Finding 3 — Sequential hash computation in `filter_primary_files`

- **Category**: Performance
- **Location**: `common/dedup/service.py` (lines 1282-1334)
- **Description & evidence**
  - `filter_primary_files()` iterates sequentially calling `compute_content_hash(f)` per file.
  - I/O bound, would benefit from parallel hash computation.
  - Should use `ThreadPoolExecutor` with manifest index for O(1) lookups in workers.
- **Impact**
  - **Medium**: Potential 3-7min savings at scale.
- **Confidence**: **High**

##### Finding 10 — PIL pre-scales overlays before FFmpeg

- **Category**: Performance
- **Location**: `common/media/overlay.py` (lines 427-443)
- **Description & evidence**
  - PIL pre-scales overlays before FFmpeg using `Image.resize()` with LANCZOS.
  - Creates temp PNG file for scaled overlay, adding I/O overhead.
  - Could use FFmpeg `scale` filter on the overlay input instead.
- **Impact**
  - **Medium**: Potential 3-5min savings at scale.
- **Confidence**: **High**

##### Finding 12 — `ThreadPoolExecutor` for CPU-bound hashing limited by GIL

- **Category**: Performance
- **Location**: `common/dedup/catalog.py` (lines 170, 542)
- **Description & evidence**
  - `ThreadPoolExecutor` used for hashing in `_compute_missing_hashes_parallel()` and `catalog_media_files()`.
  - File I/O benefits from threads, but xxHash/perceptual hash computation is CPU-bound.
  - GIL limits parallel CPU work; `ProcessPoolExecutor` would improve throughput.
- **Impact**
  - **Low-Medium**: Potential 1-3min savings at scale.
- **Confidence**: **Medium**

##### Finding 5 — `iterdir()` + `is_file()` slower than `os.scandir()`

- **Category**: Performance
- **Location**: `common/files/scanner.py` (lines 64, 127, 232)
- **Description & evidence**
  - Uses `directory.iterdir()` + `entry.is_file()` pattern in `iter_subdirs()`, `iter_files()`, and non-recursive `iter_media_files()`.
  - `os.walk()` is already used for recursive scanning (efficient).
  - Non-recursive paths could use `os.scandir()` which gets file type in the same syscall.
- **Impact**
  - **Low**: Potential <1min savings at scale.
- **Confidence**: **High**

##### Finding 34 — HTTP requests without connection pooling for Discord

- **Category**: Performance
- **Location**: `processors/discord/preprocess.py`
- **Description & evidence**
  - `requests.get()` per attachment without pooling. Repeated handshakes.
  - Should use `requests.Session()` with `HTTPAdapter`. Thread-local sessions for multithreaded.
  - Discord preprocessing: 1,223 attachments.
- **Impact**
  - **Low**: Potential <1min savings at scale.
- **Confidence**: **High**

##### Finding 35 — Discord downloads to disk then re-reads for content_hash

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

##### Finding 36 — Per-file SQLite overhead in download dedup

- **Category**: Performance
- **Location**: `processors/preprocess_base.py`
- **Description & evidence**
  - `register_downloaded_file()` creates DB objects per file. Per-file SQLite overhead.
  - Should use thread-local `HashDatabase` or enable connection reuse.
- **Impact**
  - **Low**: Potential <1min savings at scale.
- **Confidence**: **High**

##### Finding 19 — Perceptual comparisons scale O(n^2)

- **Category**: Performance
- **Location**: `common/dedup/service.py`
- **Description & evidence**
  - Perceptual comparisons scale O(n^2).
  - Could pre-index with LSH, split 256-bit hash into bands, compare only matching bands.
  - Deprioritized since dedup is already fast (11min for 1.2B comparisons).
- **Impact**
  - **Low**: Dedup already fast; limited ROI.
- **Confidence**: **Medium**

##### Finding 22 — Pickling overhead in IPC for perceptual comparison

- **Category**: Performance
- **Location**: `common/dedup/service.py` (lines 33-62)
- **Description & evidence**
  - `_compare_hash_chunk` ships hash strings per pair. Pickling overhead at scale.
  - Could convert to integers, share via `multiprocessing.Array`, ship index pairs only.
  - Deprioritized since dedup is already fast.
- **Impact**
  - **Low**: Dedup already fast; limited ROI.
- **Confidence**: **Medium**

##### Finding 8 — Large HEIC fully decoded during hashing

- **Category**: Performance
- **Location**: `common/dedup/hashing.py`
- **Description & evidence**
  - Large HEIC fully decoded (50MB+). JPEG uses `img.draft()` but HEIC doesn't.
  - Could check if `pillow-heif` supports `draft()`.
  - Already using early downsampling; acceptable.
- **Impact**
  - **Low**: Current approach acceptable.
- **Confidence**: **Medium**

##### Finding 24 — Full metadata through compare phases when only subset needed

- **Category**: Performance
- **Location**: `common/dedup/catalog.py`, `common/dedup/service.py`
- **Description & evidence**
  - Full metadata through compare phases when only subset needed.
  - Could create `CatalogEntryMinimal` for comparison, load full on manifest build.
- **Impact**
  - **Low**: Limited ROI.
- **Confidence**: **Medium**

---

#### 2.5 Code Quality Findings

##### Finding Q2 — TODO in test code referencing untracked bug

- **Category**: Incomplete implementation
- **Location**: `tests/test_dedup/test_cross_processor_description.py:555`
- **Description & evidence**
  - Comment: `# TODO: When bug is fixed, add actual file metadata verification:`
  - Bug status/tracking unclear.
- **Impact**
  - **Low**: Test incompleteness; may indicate unfixed bug.
- **Confidence**: **High**

##### Finding Q3 — Bare `pass` statements in exception handlers

- **Category**: Code smell / potential silent failures
- **Location**: 17 occurrences across codebase
- **Key locations**:
  - `processors/imessage/processor.py:97, 372`
  - `processors/imessage_imazing/processor.py:62, 72, 102, 300`
  - `common/media/exiftool.py:430, 507, 755`
  - `common/files/utils.py:156, 172`
  - `common/text/parse.py:39, 45`
  - `common/dedup/hashing.py:48, 352, 403`
- **Description & evidence**
  - Many are intentional fallback logic (e.g., trying multiple parse formats).
  - Some may suppress unexpected errors silently.
- **Impact**
  - **Low**: Most are intentional; worth documenting.
- **Confidence**: **Medium**

##### Finding Q4 — Duplicated `MEDIA_EXTENSIONS` constant in standalone script

- **Category**: DRY violation
- **Location**: `standalone/sample_metadata_audit.py:26-29`
- **Description & evidence**
  - Defines local `MEDIA_EXTENSIONS` instead of importing from `common.files.types` (e.g., `ALL_MEDIA_EXTENSIONS`).
- **Impact**
  - **Very Low**: Standalone script isolation, but risks drift.
- **Confidence**: **High**

##### Finding Q5 — Misleading "legacy mode" comment in catalog worker docstring

- **Category**: Dead code / misleading comment
- **Location**: `memoria.py` (lines 200-300)
- **Description & evidence**
  - Docstring (line 216) claims: "legacy mode returns empty count for compatibility".
  - Implementation (lines 297-299) actually counts files via `for _ in processor.build_catalog(...): file_count += 1`.
  - Function always returns `(export_dir.name, file_count)`, so count is never empty.
- **Impact**
  - **Low-Medium**: Misleading comment about legacy behavior.
- **Confidence**: **High**

##### Finding Q6 — Orphaned internal backwards-compat type alias in dedup hashing

- **Category**: Legacy internal compatibility artifact
- **Location**: `common/dedup/hashing.py` (line 72)
- **Description & evidence**
  - Defines `VideoInfo = VideoMetadata` with comment "Backwards-compatible alias for internal usage".
  - Used only internally in `_get_video_info()` return type annotation (lines 253, 264).
  - Alias can be removed by updating internal usages to `VideoMetadata` directly.
- **Impact**
  - **Very Low**: Minimal functional risk, but contributes to "compat creep" contrary to stateless batch constraints.
- **Confidence**: **High**
