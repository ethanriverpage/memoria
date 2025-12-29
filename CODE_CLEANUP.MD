
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

---

### 2.3 CLI / Orchestration Findings (`memoria.py`)

##### Finding S — Hardcoded platform/service taxonomies in upload-only scanning will drift

- **Category**: DRY violation / architectural drift
- **Location**: `memoria.py:1150-1284` (`_scan_platform_username_service_structure`)
- **Description & evidence**
  - Upload-only scanning hardcodes `known_platforms` and `known_services` sets in `memoria.py`.
  - Similar “known platforms” constants also exist in `common/dedup/description.py` and `standalone/clear_immich_albums.py`, creating multiple sources of truth.
  - New processors/services or naming changes require edits in multiple places, and missed updates will cause silent upload omissions.
- **Impact**
  - **Medium**: Drift-prone behavior and silent functional gaps (missing uploads) when processors evolve.
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
  - **Low–Medium**: Maintenance overhead; changes to detection heuristics must be duplicated.
- **Confidence**: **Medium**
