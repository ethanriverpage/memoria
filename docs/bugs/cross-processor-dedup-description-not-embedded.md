# Bug Report: Cross-Processor Dedup Provenance Not Embedded in File Descriptions

## Status: ROOT CAUSE IDENTIFIED - Fix pending

## Summary

When `CROSS_PROCESSOR_DEDUP=true` is enabled, Memoria correctly detects cross-processor duplicates and applies deduplication decisions (secondary files are skipped, `dedup_references.json` is written), but **no processed output files contain the expected cross-processor provenance lines** in their `ImageDescription` / `Comment` / `Description` metadata.

The documented behavior (see `docs/Deduplication.md`) is that primary files in cross-processor duplicate groups should have descriptions like:

```text
Source: Google/johndoe/photos
+iMessage/iph13p: John in "Family Group": "Everyone needs to see this!" (2024-01-15)
+Instagram/johndoe/messages: bestfriend in "Travel Buddies": "Remember this trip?" (2024-01-25)
```

**Actual behavior:** All processed files have only the primary `Source:` line; zero files contain any `+OtherProcessor/...` provenance lines.

## Symptoms

1. **Audit of 56,912 processed files found zero with cross-processor provenance:**

   ```text
   $ python3 standalone/audit_dedup_descriptions.py /mnt/media/processed --workers 15
   Scanned: 56912 file(s)
   With any description field: 56862
   Matched cross-processor dedup format: 0
   ```

2. **Manual sampling confirms absence of `+...` lines:**

   Sampled 800 files from `/mnt/media/processed` and 2,000 from `/mnt/media/processed/iMessage`:
   - 798 files had `Source:` in their description
   - 0 files contained any `\n+...` secondary provenance line

3. **Example descriptions (actual):**

   ```text
   Source: Discord/minmatic\n  - in "DM with shortphase"
   Source: Google/ethanriverpage/photos
   Source: iMessage/iph17pm\n  - in "DM with +1404..."
   ```

   **Missing:** The `+OtherProcessor/...` lines that should appear for files in cross-processor duplicate groups.

## Evidence That Cross-Processor Dedup Is Working

1. **Dedup is enabled in the log:**

   ```text
   Cross-processor deduplication enabled (CROSS_PROCESSOR_DEDUP=true)
   ```

2. **Processors skip secondary files:**

   ```text
   Skipped 13636 secondary files (dedup)   # Google Photos
   Skipped 10632 secondary files (dedup)   # iMessage
   Skipped 260 secondary files (dedup)     # Discord
   ```

3. **Per-processor `dedup_references.json` contains cross-platform pointers:**

   ```json
   {
     "original_filename": "1167556868894380092_IMG_5668.jpg",
     "deduplicated": true,
     "canonical_location": "/mnt/media/originals/google-ethanriverpage-20250526/Google Photos/Photos from 2023/IMG_5668.HEIC",
     "canonical_processor": "Google Photos",
     "skipped_by_processor": "Discord"
   }
   ```

4. **Global manifest contains many cross-processor groups:**

   ```text
   dedup_manifest.json: 29,944 duplicate groups total
   Cross-processor groups (spanning 2+ processors): 9,827
   ```

   Example group spanning Google Photos and iMessage:

   ```text
   ('001722cb89667554', ['Google Photos', 'iMessage'], 1, 'iMessage', 'Google Photos')
   ```

## Affected Code

### Description Enhancement Logic (BUG LOCATION)

`common/media/exiftool.py` - `_get_description_for_file()` (lines 546-589)

```python
def _get_description_for_file(
    default_description: str,
    dedup_manifest: Optional[Dict[str, Any]] = None,
    content_hash: Optional[str] = None,
    processor_name: str = "unknown",
) -> str:
    if dedup_manifest is None or content_hash is None:
        return default_description

    secondary_sources = get_secondary_sources_for_hash(
        dedup_manifest, content_hash, processor_name
    )

    if not secondary_sources:
        return default_description

    # Append secondary source lines to processor's description
    lines = [default_description]
    for entry in secondary_sources:
        lines.append(format_secondary_source(entry))

    return "\n".join(lines)  # <-- BUG: should be "&#xa;".join(lines)
```

### MetadataWriteRequest Construction (processors set content_hash)

Example from `processors/discord/processor.py` - `_build_metadata_requests()` (lines 293-306):

```python
requests.append(
    MetadataWriteRequest(
        file_path=file_path,
        arg_preamble=DEFAULT_PREAMBLE,
        datetime_tags=datetime_tags,
        gps=None,
        description=DescriptionWriteSpec(
            default_description=default_description,
        ),
        processor_name="discord",
        content_hash=content_hash,  # <-- Set here
        original_filename=original_filename,
    )
)
```

All 11 processors follow this pattern and set `content_hash` from their `file_hashes` lookup.

### Manifest Lookup (correct implementation)

`common/dedup/service.py` - `get_secondary_sources_for_hash()` (lines 1334-1408)

This function correctly looks up a content hash in the manifest and returns deduplicated secondary source entries from other processors.

## Root Cause Analysis

### CONFIRMED: Newline escaping bug in exiftool argfile format

The root cause is in `_get_description_for_file()` which uses `"\n".join(lines)` to concatenate the primary description with secondary source lines:

```python
return "\n".join(lines)  # BUG: literal newlines break argfile format
```

This produces a description string containing literal newline characters, e.g.:

```
Source: iMessage/test
+Google Photos
```

When this string is written to the exiftool argfile, each line after the newline becomes a separate argument:

```
-ImageDescription=Source: iMessage/test
+Google Photos              <-- interpreted as a new (invalid) argument
```

Exiftool then:
1. Writes only `Source: iMessage/test` to the file
2. Logs a warning about `+Google Photos` being an unknown argument
3. Silently drops all cross-processor provenance lines

### Verification

Manual test confirmed the behavior:

```python
# Argfile contents:
-overwrite_original
-ImageDescription=Source: iMessage/test
  - me in "DM with Friend"
+Google Photos
+Discord/user
/tmp/test_multiline.jpg
-execute

# Exiftool output:
Warning: Unknown argument in arg file: +Google Photos
Warning: Unknown argument in arg file: +Discord/user
    1 image files updated

# Result in file:
Image Description: Source: iMessage/test
```

Only the first line is written; all `+Processor/...` provenance lines are silently dropped.

### Why other description code works

The `build_description()` function in `common/dedup/description.py` correctly uses `"&#xa;".join()` for EXIF-safe newlines:

```python
return "&#xa;".join(lines)  # Correct: XML entity for newline
```

The `&#xa;` entity is interpreted by exiftool as a newline when writing to EXIF metadata, but does not break the argfile format because it appears as a continuous string on one line.

### The fix

Change `_get_description_for_file()` from:

```python
return "\n".join(lines)
```

To:

```python
return "&#xa;".join(lines)
```

This ensures the entire enhanced description (primary + secondary sources) is written as a single argfile argument.

## Impact

- **High UX impact**: Cross-processor dedup provenance is a key feature and currently invisible
- **Potential data provenance loss**: Users cannot see that a kept file represents items from other platforms
- **Immich display affected**: Since provenance isn't on disk, Immich cannot display it

## Recommended Fix

1. **Fix newline escaping in `_get_description_for_file()`** (`common/media/exiftool.py`):

   ```python
   # Change from:
   return "\n".join(lines)
   
   # To:
   return "&#xa;".join(lines)
   ```

2. **Remove debug logging** added during investigation (no longer needed):
   - Remove debug/info logs from `_get_description_for_file()`
   - Remove debug logs from `batch_write_metadata_requests()`
   - Remove debug logs from `processors/imessage/processor.py`

3. **Add integration regression test**:
   - Create synthetic dataset with a known cross-processor duplicate
   - Run cross-processor dedup
   - Read output `ImageDescription` via `exiftool`
   - Assert it contains `&#xa;+` (EXIF-encoded provenance line)

## Reproduction

1. Run processing with cross-processor dedup enabled:

   ```bash
   CROSS_PROCESSOR_DEDUP=true python3 memoria.py \
     --originals /mnt/media/originals \
     -o /mnt/media/processed
   ```

2. Run the audit script:

   ```bash
   python3 standalone/audit_dedup_descriptions.py /mnt/media/processed --workers 15
   ```

3. Expected: `Matched cross-processor dedup format: 0`

4. Verify manifest has cross-processor groups:

   ```python
   import json
   from pathlib import Path
   
   manifest = json.loads(Path('/mnt/media/processed/dedup_manifest.json').read_text())
   dups = manifest.get('duplicates', {})
   
   cross_count = 0
   for k, g in dups.items():
       procs = {s.get('processor') for s in g.get('secondary', []) if s.get('processor')}
       procs.add(g.get('best_quality', {}).get('processor'))
       procs.add(g.get('primary', {}).get('processor'))
       procs.discard(None)
       if len(procs) >= 2:
           cross_count += 1
   
   print(f"Cross-processor groups: {cross_count}")
   # Expected: thousands of groups (observed: 9,827)
   ```

## Related Files

- `common/media/exiftool.py` - `_get_description_for_file()`, `batch_write_metadata_requests()`
- `common/dedup/service.py` - `get_secondary_sources_for_hash()`
- `common/dedup/description.py` - `format_secondary_source()`, `build_image_description()`
- `processors/*/processor.py` - `_build_metadata_requests()` in each processor
- `docs/Deduplication.md` - documented expected behavior

## Log References

- `/home/ethan/media-processing/logs/media_processor_20251229_151236.log`
  - Line 24: `Cross-processor deduplication enabled (CROSS_PROCESSOR_DEDUP=true)`
  - Line 844: `Manifest built: 60763 unique, 49240 duplicates, 121075.4 MB saved`
  - Lines 864, 898, 919, etc.: `Skipped N secondary files (dedup)`

## Investigation Findings (2025-12-30)

### Verified Working Components

1. **`_get_description_for_file()` works correctly in isolation:**

   ```python
   >>> _get_description_for_file("Source: iMessage/test", manifest, "001722cb89667554", "iMessage")
   'Source: iMessage/test\n+Google Photos'
   ```

2. **`get_secondary_sources_for_hash()` correctly returns cross-processor secondaries:**

   ```python
   >>> get_secondary_sources_for_hash(manifest, "001722cb89667554", "iMessage")
   [SourceEntry(processor='Google Photos', ...)]
   ```

3. **Manifest structure is correct:**
   - 2,050 groups where iMessage is best quality with cross-processor secondaries
   - 2,977 groups where Google Photos is best quality with cross-processor secondaries

4. **Preprocessing correctly populates `file_hashes`:**
   - Keys match between `file_hashes` and `media_files` in conversations
   - Lookup `file_hashes.get(media_filename)` returns correct hash in unit tests

5. **Specific file verified missing provenance:**
   - File: `/mnt/media/processed/iMessage/iph13p/messages/imessage-iph13p-20220102.jpg`
   - Original: `IMG_0270.JPG`
   - Content hash: `001722cb89667554`
   - Manifest shows: Best quality = iMessage, Secondary = Google Photos
   - Actual description: `Source: iMessage/iph13p.  - me in "DM with Michael Page"`
   - Expected: Should have `+Google Photos` line appended

### Root Cause Confirmed (2025-12-30)

Debug logging during a full run confirmed:

1. **`dedup_manifest` is correctly passed** - 29,944 duplicate groups available
2. **`content_hash` values are correctly looked up** - no `None` values in sample
3. **`get_secondary_sources_for_hash()` returns correct secondary sources**
4. **`_get_description_for_file()` produces enhanced descriptions** with `+Processor` lines

The descriptions are correctly built but **silently truncated during exiftool argfile writing** due to literal newlines breaking the argument format.

Log evidence:

```
_get_description_for_file: Found 1 secondary sources for hash=001722cb89667554 processor=iMessage
```

But output file only contains the first line of the description.

## Discovery Context

Discovered while investigating why Immich uploads did not display cross-processor dedup provenance. The audit script `standalone/audit_dedup_descriptions.py` was created to verify whether provenance was missing on disk (it is) vs being stripped during upload (it is not).

