
### 1. Executive Summary

Overall the repo is **well-structured and test-backed** (clear processor boundaries in `processors/`, shared utilities in `common/`, and broad test coverage under `tests/`). The highest-leverage cleanup is removing superseded code paths in the dedup subsystem and standardizing processor patterns.

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

##### Finding N+1 — `export_info` lacks a stable processor identity marker (forces heuristic “preprocessed” detection)

- **Category**: Schema/contract gap
- **Location**: `common/metadata/schema.py:59-86` (`ExportInfo`)
- **Description & evidence**
  - `ExportInfo` has required fields `export_path`, `export_username`, `processed_date`, but does not require any explicit marker like `processor_key` / `platform` / `service`.
  - Multiple processors implement “preprocessed directory” detection by loosely inspecting `metadata.json` keys (e.g., `conversations`) rather than checking a stable identity field.
- **Impact**
  - **High**: Increases risk of misclassification when pointing at a preprocessed directory or cache output (wrong processor may run).
- **Confidence**: **High**

### 2.2 Processor-Level Findings

##### Finding M — Unmaintained standalone scripts

- **Category**: Dead code / maintenance debt
- **Location**: `standalone/` directory
- **Description & evidence**
  - Scripts like `sample_metadata_audit.py`, `verify_metadata.py`, and `compare_exports.py` are not invoked by main `memoria.py` or processors.
  - They likely rely on older data structures (e.g., before the source_path-only refactor).
- **Impact**
  - **Low**: Clutter; potential confusion for new contributors.
- **Confidence**: **Medium** (Check utility before deletion).

##### Finding P1 — Duplicated `process_media_batch` worker functions

- **Category**: DRY violation
- **Location**: `processors/google_chat/processor.py:300-401`, `processors/google_voice/processor.py:304-402`, `processors/instagram_messages/processor.py:311-412`, `processors/instagram_public_media/processor.py:330-415`, `processors/instaloader/processor.py:304-395`
- **Description & evidence**
  - Near-identical ~100-line `process_media_batch()` function copy-pasted across 5 processors.
  - All implement: Phase 1 (copy files), Phase 2 (correct extensions via `batch_correct_extensions`), Phase 3 (update timestamps).
  - Only variations: tuple unpacking order, timestamp format string, result tuple structure.
- **Impact**
  - **High**: 500+ lines of duplicated code; changes must be applied 5 times.
- **Confidence**: **High**

##### Finding P2 — Duplicated `_process_working_directory` boilerplate

- **Category**: DRY violation
- **Location**: All 11 processors implement variants of this pattern
- **Description & evidence**
  - Every processor implements 200-300 lines of identical workflow: load metadata → init FailureTracker → generate filenames → iterate entries with dedup check → parallel processing → collect results → batch EXIF → track failures → save dedup metadata → print summary.
  - Minor variations in metadata structure extraction and task tuple construction.
- **Impact**
  - **High**: ~2500 lines of near-identical boilerplate across processors.
- **Confidence**: **High**

##### Finding P3 — Inconsistent parallel processing utility usage

- **Category**: Architectural drift
- **Location**: `processors/google_voice/processor.py:578-593`, `processors/instaloader/processor.py:575-590`, `processors/instagram_public_media/processor.py:613-628`, `processors/snapchat_memories/processor.py:792-800`, `processors/snapchat_messages/processor.py:995-1007`, `processors/imessage/processor.py:857-868`
- **Description & evidence**
  - Some processors use `process_batches_parallel()` from `common/processing.py` (google_chat, instagram_messages, google_photos).
  - Others implement manual batching with `multiprocessing.Pool.imap` (google_voice, instaloader, instagram_public_media).
  - Others use `Pool.imap` without batching (snapchat_memories, snapchat_messages, imessage).
- **Impact**
  - **Medium**: Inconsistent patterns; missed opportunity for shared progress bar handling.
- **Confidence**: **High**

##### Finding P4 — Duplicated `_build_description` and helper functions between iMessage processors

- **Category**: DRY violation
- **Location**: `processors/imessage/processor.py:290-464`, `processors/imessage_imazing/processor.py:231-383`
- **Description & evidence**
  - `_build_description()` is 95% identical between `imessage` (122 lines) and `imessage_imazing` (109 lines).
  - `_extract_date_from_source_export()` and `_extract_device_from_source_export()` are copy-pasted verbatim.
  - `snapchat_messages/processor.py:304-372` has similar but smaller duplication.
- **Impact**
  - **Medium**: 200+ lines of duplicated code across 2-3 processors.
- **Confidence**: **High**

##### Finding P5 — Duplicated timestamp extraction functions

- **Category**: DRY violation
- **Location**: `processors/imessage/processor.py:571-586`, `processors/imessage_imazing/processor.py:470-478`, `processors/snapchat_messages/processor.py:495-506`
- **Description & evidence**
  - `_extract_message_timestamp()` / `_get_message_timestamp()` implement identical logic for extracting timestamps from merged vs single messages.
  - All check for `primary_created`, then `messages[0].created`, then `created`.
- **Impact**
  - **Low**: ~50 lines duplicated; straightforward to consolidate.
- **Confidence**: **High**

##### Finding P6 — Inconsistent `is_preprocessed` check patterns

- **Category**: Architectural drift
- **Location**: `processors/imessage/processor.py:520-546`, `processors/imessage_imazing/processor.py:438-458`, `processors/snapchat_memories/processor.py:196-199`
- **Description & evidence**
  - Most processors use `is_preprocessed_directory()` from `common/utils.py`.
  - `imessage` and `imessage_imazing` define local `is_preprocessed()` functions with additional validation.
  - `snapchat_memories` uses `ProcessorBase.check_preprocessed_source_path()`.
- **Impact**
  - **Low**: Three different patterns for the same check.
- **Confidence**: **High**

##### Finding P7 — Nested function definitions in some processors

- **Category**: Code style drift
- **Location**: `processors/google_voice/processor.py:431-658`, `processors/imessage_imazing/processor.py:561-767`
- **Description & evidence**
  - `google_voice` and `imessage_imazing` define `_process_working_directory` as nested functions inside `process_logic()`.
  - All other processors define it at module level.
- **Impact**
  - **Low**: Harder to test and navigate; inconsistent style.
- **Confidence**: **High**

##### Finding P8 — Split EXIF processing patterns

- **Category**: Architectural drift
- **Location**: `processors/google_chat/`, `processors/google_voice/`, `processors/instagram_messages/`, `processors/instagram_public_media/`, `processors/instaloader/` vs `processors/imessage/`, `processors/imessage_imazing/`, `processors/snapchat_memories/`, `processors/snapchat_messages/`
- **Description & evidence**
  - Pattern A (5 processors): `batch_process_exif()` + manual failure tracking in 15-20 lines of boilerplate.
  - Pattern B (4 processors): `finalize_files()` with integrated failure tracking.
  - Pattern B is more encapsulated and should be the standard.
- **Impact**
  - **Medium**: Inconsistent error handling; Pattern A requires manual failure loop.
- **Confidence**: **High**

##### Finding P9 — Inconsistent return types from `process_logic()`

- **Category**: API inconsistency
- **Location**: All processors
- **Description & evidence**
  - Some `process_logic()` functions return `bool` consistently (imessage, imessage_imazing).
  - Others return `False` on error but have no return on success (google_chat, google_voice, instagram_messages).
  - Some have `-> bool` type hints, others don't.
- **Impact**
  - **Low**: Type inconsistency; potential for silent failures.
- **Confidence**: **High**

##### Finding P10 — Over-broad “preprocessed” detection in `SnapchatMessagesProcessor.detect()` can misclassify other processors’ metadata

- **Category**: Correctness risk / detection drift
- **Location**: `processors/snapchat_messages/processor.py:62-124` (`SnapchatMessagesProcessor.detect`)
- **Description & evidence**
  - The “preprocessed” detection branch treats `metadata.json` as Snapchat Messages if it contains `conversations` and either `export_info.export_username` exists or `conversations` is non-empty.
  - Many other processors’ preprocessed outputs also have `conversations` and `export_info.export_username` (schema-standard), so this condition can match unrelated preprocessed directories.
- **Impact**
  - **High**: Wrong processor selection can silently produce incorrect output naming + metadata embedding.
- **Confidence**: **High**

##### Finding P11 — Internal backwards-compat timestamp fallback in Snapchat Messages processing

- **Category**: Legacy internal compatibility path
- **Location**: `processors/snapchat_messages/processor.py:405-410`, `processors/snapchat_messages/processor.py:519-528`
- **Description & evidence**
  - EXIF datetime tags are built from `canonical_timestamp` / `primary_created` / `created` with special handling for “legacy date-only” strings.
  - `_update_snapchat_timestamps()` explicitly falls back to parsing `YYYY-MM-DD` “for backwards compatibility with old metadata”.
- **Impact**
  - **Medium**: Allows non-canonical internal metadata formats to persist; increases timestamp handling drift and makes downstream behavior harder to reason about.
- **Confidence**: **High**

##### Finding P12 — Instagram Messages declares support for an “oldest inbox/” layout that the preprocessor does not implement

- **Category**: Dead path / incomplete layout support
- **Location**: `processors/instagram_messages/processor.py:130-145` (`needs_preprocessing`), `processors/instagram_messages/preprocess.py:48-63` (`__init__`)
- **Description & evidence**
  - `InstagramMessagesProcessor.needs_preprocessing()` returns True if `input_path / "inbox"` exists (commented as “Oldest location”).
  - `InstagramMessagesPreprocessor` only resolves inbox at:
    - `your_instagram_activity/messages/inbox` (current)
    - `messages/inbox` (legacy)
  - There is no corresponding `inbox/`-root handling in the preprocessor’s directory resolution.
- **Impact**
  - **Medium**: Can route a directory into preprocessing that will fail validation (or never correctly process that layout).
- **Confidence**: **High**

##### Finding P13 — Instaloader `detect()` and `needs_preprocessing()` accept different filename patterns

- **Category**: Detection drift / correctness risk
- **Location**: `processors/instaloader/processor.py:64-90` (`detect`), `processors/instaloader/processor.py:117-132` (`needs_preprocessing`)
- **Description & evidence**
  - `detect()` accepts carousel suffixes and metadata files: `_UTC(_\d+)?` and `.txt` / `.json(.xz)`.
  - `needs_preprocessing()` only matches `_UTC.(jpg|jpeg|mp4)` (no `_\d+` carousel suffix).
  - Processing later requires `metadata.json` to exist after preprocessing (see `_process_working_directory` metadata check at `processors/instaloader/processor.py:421-434`).
- **Impact**
  - **High**: A valid Instaloader scrape consisting only of `_UTC_1.jpg`/`_UTC_2.jpg` can be detected but skip preprocessing, then fail processing due to missing `metadata.json`.
- **Confidence**: **High**

##### Finding P14 — Instaloader output subdirectory and `OutputInfo.service` disagree

- **Category**: Output contract inconsistency
- **Location**: `processors/instaloader/processor.py:104-110` (`get_output_info`), `processors/instaloader/processor.py:216-230` (`process`)
- **Description & evidence**
  - `get_output_info()` reports `service="posts"`.
  - `process()` writes under `Path(output_dir) / "public-media"`.
- **Impact**
  - **Medium**: Confusing and potentially incorrect downstream grouping/album labeling where `OutputInfo` is used to build paths.
- **Confidence**: **Medium–High**

##### Finding P15 — Duplicated Instagram username extraction and timestamp parsing across two preprocessors

- **Category**: DRY violation
- **Location**: `processors/instagram_messages/preprocess.py:114-169`, `processors/instagram_messages/preprocess.py:171-199`; `processors/instagram_public_media/preprocess.py:119-172`, `processors/instagram_public_media/preprocess.py:174-202`
- **Description & evidence**
  - Both preprocessors duplicate:
    - `personal_information.html` username extraction (new vs legacy path variants)
    - `parse_timestamp()` with “new format vs legacy comma after year” parsing formats
- **Impact**
  - **Medium**: Fixes for future Instagram export HTML changes must be applied twice; easy to drift.
- **Confidence**: **High**

##### Finding P16 — Duplicated Google username detection logic across preprocessors

- **Category**: DRY violation
- **Location**: `processors/google_photos/preprocess.py:131-196`, `processors/google_chat/preprocess.py:95-160`
- **Description & evidence**
  - Both implement similar detection priority and parsing:
    - `Profile/Profile.json` → email → username
    - `Google Chat/Users/*/user_info.json` → email → username
    - directory name fallback
- **Impact**
  - **Low–Medium**: Maintenance overhead; changes to detection heuristics must be duplicated.
- **Confidence**: **Medium**

##### Finding P17 — Instaloader preprocessor re-implements text normalization while another processor uses a shared helper

- **Category**: Bespoke utility bypass
- **Location**: `processors/instaloader/preprocess.py:72-103` (`normalize_text`), `processors/instagram_public_media/preprocess.py:22` (imports `common.utils.normalize_text`)
- **Description & evidence**
  - Instaloader defines its own Unicode punctuation normalization function.
  - Instagram Public Media imports a shared `common.utils.normalize_text`, implying a shared utility already exists for this family of behavior.
- **Impact**
  - **Low**: Easy to drift between two “normalization” implementations.
- **Confidence**: **High**

##### Finding P18 — Documentation drift: Instaloader `.json.xz` is supported in code but described as “manual extraction required”

- **Category**: Docs/code mismatch
- **Location**: `docs/Instagram-Export.md:268-287`, `processors/instaloader/preprocess.py:131-156`
- **Description & evidence**
  - Docs instruct users to manually extract `.json.xz` for Instaloader.
  - Instaloader preprocessor explicitly searches for `*.json.xz` and reads them (compressed JSON support is implemented).
- **Impact**
  - **Low–Medium**: Misleads users and maintainers; increases confusion and redundant work.
- **Confidence**: **High**
