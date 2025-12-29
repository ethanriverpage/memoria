# Bug Report: Google Chat Files Getting 00000000 Filenames Despite Valid Timestamps

## Status: Root cause identified

## Summary

Google Chat media files were being assigned `00000000` date placeholders in their output filenames, even though:

1. The source metadata contains valid `created_date` timestamps
2. EXIF metadata is correctly embedded with proper timestamps
3. Timestamp parsing in the preprocessor works correctly

This results in filenames like `google-ethanriverpage-chat-00000000_100.png` instead of `google-ethanriverpage-chat-20170425_100.png`.

## Symptoms

Output files in `/mnt/media/processed/Google/{username}/chat/`:

```text
google-ethanriverpage-chat-00000000_100.png
google-ethanriverpage-chat-00000000_101.png
google-ethanriverpage-chat-00000000_102.png
...
```

**Key observations:**

1. **All media outputs affected**: In the observed run, **414/414 media files** in `/mnt/media/processed/Google/ethanriverpage/chat/` had `00000000` in their names (the only non-matching entry was `dedup_references.json`).
2. **EXIF timestamps are correct**: Running `exiftool` on these files shows proper timestamps:

   ```text
   Date/Time Original              : 2017:04:25 23:42:01
   Create Date                     : 2017:04:25 23:42:01
   ```

3. **Failure report shows timestamps**: Processing failures include valid timestamp metadata:

   ```json
   {
     "file_path": "/mnt/media/processed/.../google-ethanriverpage-chat-00000000_38.png",
     "metadata": {
       "timestamp": "2016-08-23 18:51:27",
       "canonical_timestamp": "2016-08-23T18:51:27"
     }
   }
   ```

## Affected Code

### Preprocessor - Timestamp Extraction (Works Correctly)

`processors/google_chat/preprocess.py` - `parse_messages_file()` (lines 599-653)

```python
# Extract timestamp
created_date = message.get("created_date")
timestamp = self.parse_timestamp(created_date) if created_date else None

message_data = {
    "sender": sender,
    "timestamp": timestamp,  # Stored here
    "timestamp_raw": created_date,
    ...
}
```

`processors/google_chat/preprocess.py` - `parse_timestamp()` (lines 214-230)

```python
def parse_timestamp(self, timestamp_str: str) -> Optional[str]:
    """
    Convert timestamp string to ISO format
    Input format: "Wednesday, May 4, 2016 at 4:20:19 AM UTC"
    Output: "2016-05-04 04:20:19"
    """
    try:
        dt = datetime.strptime(timestamp_str, "%A, %B %d, %Y at %I:%M:%S %p %Z")
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, AttributeError) as e:
        self.log_message("TIMESTAMP_PARSE_ERROR", ...)
        return None
```

**Verification:** Parsing works correctly:

```python
>>> datetime.strptime("Tuesday, August 23, 2016 at 6:51:27 PM UTC", "%A, %B %d, %Y at %I:%M:%S %p %Z")
datetime.datetime(2016, 8, 23, 18, 51, 27)
```

### Processor - Filename Generation (Bug Location)

`processors/google_chat/processor.py` - `_process_working_directory()` (lines 460-467)

```python
# Canonical timestamp (dedup merged earliest preferred)
canonical_ts = get_canonical_timestamp(
    dedup_manifest, content_hash, message.get("timestamp")
)
message["canonical_timestamp"] = canonical_ts

# Generate output filename using shared FilenameGenerator
output_filename = filename_gen.generate(canonical_ts, file_ext)
```

### FilenameGenerator - Date Parsing (note: code has since changed)

At the time this bug was observed, `FilenameGenerator` returned `"00000000"` when it could not parse the timestamp string.

**Important:** The codebase has since been updated so `FilenameGenerator` raises `InvariantViolation` for missing/unparseable timestamps instead of silently returning `"00000000"`. This changes the symptom (hard-fail vs placeholder) but not the underlying root cause documented below.

## Root Cause Analysis (confirmed)

### What actually happened

`canonical_ts` was **not** `None` — it was typically an **ISO-8601 timestamp** coming from the **dedup manifest merged metadata**, e.g.:

- `2016-08-23T18:51:04`
- `2022-01-02T18:55:00+00:00`

These ISO timestamps include a `T` separator and may include timezone offsets.

This also explains why EXIF timestamps appeared correct while filenames did not: the EXIF datetime builder supports ISO timestamps, while the (then-current) filename date parser did not.

### Why Google Chat was *especially* affected

In the observed run, dedup was enabled and Google Chat skipped many secondary files:

- Log shows `Skipped 509 secondary files (dedup)` right after `Pre-generating filenames...`

So Google Chat frequently used **dedup merged timestamps** (ISO-8601) for `canonical_ts`.

### Root cause

**Timestamp format mismatch** between:

- **Dedup manifest merged timestamps**: ISO-8601 (with `T`, sometimes timezone offsets)
- **Filename date parsing** (at time of bug): only supported the legacy `"YYYY-MM-DD HH:MM:SS"` format and fell back to `"00000000"` on parse failure

### Evidence

- **Dedup manifest timestamps are ISO-8601**: `/mnt/media/processed/dedup_manifest.json` contains merged timestamps like `2016-08-23T18:51:04` and `2022-01-02T18:55:00+00:00`.
- **Google Chat output filenames are overwhelmingly `00000000`**:
  - `/mnt/media/processed/Google/ethanriverpage/chat/`: 414/414 media files contained `-chat-00000000` in their name.
- **EXIF writing path supports ISO**: `common/media/exiftool.py` `build_datetime_tags()` explicitly handles ISO timestamps (checks for `T` and uses `datetime.fromisoformat(...)`).

## Impact

- **Filename dates lost**: Google Chat files have meaningless `00000000` dates, breaking sorting/organization
- **EXIF timestamps preserved**: Embedded metadata is correct
- **Filesystem timestamps unknown**: Not verified, may also be affected
- **Sorting/organization broken**: File explorers can't sort by filename date

## Recommended Fix

- **Fix the filename timestamp parser to accept ISO-8601** (the dedup manifest format), not just `"YYYY-MM-DD HH:MM:SS"`.
- **Do not** add `00000000` placeholder fallback; missing/unparseable timestamps should be treated as an invariant violation (bug in calling code).

Note: This is already the direction of the current code (`_parse_timestamp_flexible` + `InvariantViolation` in `FilenameGenerator`), but the observed outputs/logs were produced before those changes.

## Reproduction

Run processing on any Google Chat export:

```bash
python memoria.py --input /mnt/media/originals/google-*/ --output /mnt/media/processed
```

Check output:

```bash
ls /mnt/media/processed/Google/*/chat/ | head -20
# If you're running code from when this bug was observed, you'll see many `-chat-00000000` filenames.
# On current code, `-chat-00000000` should not appear; missing/unparseable timestamps should raise.
```

Verify EXIF has correct timestamps:

```bash
exiftool /mnt/media/processed/Google/*/chat/*00000000* | grep -E "Date/Time Original|Create Date" | head -20
# EXIF writing supports ISO timestamps; filenames were wrong due to filename date parsing.
```

## Related Files

- `processors/google_chat/preprocess.py` - timestamp extraction and message parsing
- `processors/google_chat/processor.py` - filename generation and EXIF processing
- `common/utils.py` - `FilenameGenerator` and `_parse_timestamp_flexible`
- `common/dedup/service.py` - `get_canonical_timestamp()`
- `processors/preprocess_base.py` - `save_metadata()` serialization

## Log References

- `/home/ethan/media-processing/logs/media_processor_20251228_185550.log` lines 4111, 4405 (Google Chat 00000000 references)

## Discovery Context

This bug was discovered while investigating the separate "timestamp update file not found" issue. The investigation confirmed that all Google Photos metadata files have valid timestamps, leading to the discovery that Google Chat has a systematic 00000000 filename problem despite correct EXIF timestamps.
