# Bug Report: Cross-Processor Dedup Merged Descriptions Not Being Written

**Date:** 2025-12-22  
**Severity:** Medium  
**Component:** `processors/base.py`, `common/media/exiftool.py`  
**Status:** Fixed (2025-12-22)

## Summary

The deduplication system correctly identifies cross-processor duplicates (e.g., same photo in Google Photos and iMessage), but the merged description containing provenance from all sources is not being written to output files. Files only contain single-source descriptions.

---

# Bug Report: Instagram Messages Not Extracting Message Content

**Date:** 2025-12-22  
**Severity:** Low  
**Component:** `processors/instagram_messages/preprocess.py`, `processors/instagram_messages/processor.py`  
**Status:** Open

## Summary

Instagram Messages descriptions do not include the message content/text, even though this data is available in the source HTML files. Other message processors (iMessage, Snapchat Messages, Discord) correctly include message content.

## Expected Behavior

```
Source: Instagram/ethanriverpage/messages
  - ethan page in "Kai Barton": "this is the message content"
```

## Actual Behavior

```
Source: Instagram/ethanriverpage/messages
  - ethan page in "Kai Barton"
```

## Root Cause

1. **Preprocessor** (`preprocess.py`): Does not extract message text from the HTML. The content is in `<div class="_a6-p">` elements but is not being parsed.

2. **Processor** (`processor.py` lines 254-261): Does not include content field in SourceEntry:

   ```python
   entry = SourceEntry(
       processor=PLATFORM_INSTAGRAM,
       username=export_username,
       service=SERVICE_MESSAGES,
       sender=message_data["sender"],
       conversation=conversation_title,
       # NO content field!
   )
   ```

## Data Availability Analysis

| Processor | Content in Source | Extracted | In Description |
|-----------|-------------------|-----------|----------------|
| iMessage | Yes (JSON) | Yes | Yes |
| Snapchat Messages | Yes (JSON) | Yes | Yes |
| Discord | Yes (CSV) | Yes | Yes |
| Google Chat | **No** (not in Takeout) | N/A | No |
| Instagram Messages | **Yes** (HTML `_a6-p` div) | **No** | **No** |

## Note on Google Chat

Google Chat is NOT a bug - the Google Takeout export format does not include message text for messages with attachments. This is a data source limitation.

## Proposed Fix for Instagram Messages

### 1. Update Preprocessor (`preprocess.py`)

In `parse_html_file()`, extract the message content from the `_a6-p` class:

```python
# After extracting sender and timestamp
content_div = container.find('div', class_='_a6-p')
if content_div:
    # Get text content (skip nested divs with media)
    text_divs = content_div.find_all('div', recursive=False)
    for div in text_divs:
        text = div.get_text(strip=True)
        if text and not text.startswith('http'):
            content = text
            break
```

### 2. Update Processor (`processor.py`)

Add content to the SourceEntry:

```python
entry = SourceEntry(
    processor=PLATFORM_INSTAGRAM,
    username=export_username,
    service=SERVICE_MESSAGES,
    sender=message_data["sender"],
    conversation=conversation_title,
    content=message_data.get("content", ""),  # Add this line
)
```

---

# Original Bug: Dedup Merged Descriptions

## Expected Behavior

Files that appear in multiple sources should have merged descriptions showing all sources:

```
Source: Google/ethanriverpage/photos
+iMessage/iph13p
+iMessage/m3_mbp
```

## Actual Behavior

All files have simple single-source descriptions:

```
Source: Google/ethanriverpage/photos
```

## Impact

- **10,162 cross-processor duplicate groups** are affected
- **85,711 total duplicates** identified in the manifest
- Users cannot see which platforms a photo was shared across
- Provenance tracking is incomplete

## Root Cause

In `processors/base.py` line 292, `batch_write_metadata_requests()` is called without the `dedup_manifest` parameter:

```python
# Current code (line 292):
batch_write_metadata_requests(requests, existing_metadata_map)
```

The function signature in `common/media/exiftool.py` accepts the manifest:

```python
def batch_write_metadata_requests(
    requests: List[MetadataWriteRequest],
    existing_metadata_map: Dict[str, Dict[str, bool]],
    dedup_manifest: Optional[Dict[str, Any]] = None,  # <-- Not being passed
    chunk_size: int = 500,
) -> None:
```

## Infrastructure That Exists (But Unused)

1. **`common/dedup/description.py`**
   - `build_image_description()` - formats merged descriptions with primary and secondary sources
   - `format_secondary_source()` - formats `+Platform/username/service` lines
   - `SourceEntry` dataclass for structured source data

2. **`common/dedup/service.py`**
   - `get_merged_description_for_file()` - looks up file in manifest and builds merged description

3. **`common/media/exiftool.py`**
   - `_get_description_for_file()` - checks manifest for merged description override
   - `batch_write_metadata_requests()` - accepts `dedup_manifest` parameter

## Proposed Fix

### Option 1: Pass manifest through base processor

Modify `ProcessorBase.batch_process_exif()` to accept and pass the manifest:

```python
@staticmethod
def batch_process_exif(
    file_paths: List[str],
    file_info: List[Tuple],
    metadata_request_builder: Callable[[List[Tuple]], List],
    dedup_manifest: Optional[Dict[str, Any]] = None,  # Add parameter
) -> int:
    # ... existing code ...
    
    # Build and write metadata requests
    requests = metadata_request_builder(file_info)
    batch_write_metadata_requests(requests, existing_metadata_map, dedup_manifest)  # Pass manifest
    
    return exif_rebuilt_count
```

Then update all processor calls to pass the manifest through.

### Option 2: Post-processing pass

Add a separate post-processing step after all processors complete that:

1. Reads the manifest
2. Identifies primary files from duplicate groups
3. Rewrites descriptions with merged provenance

## Files to Modify

1. `processors/base.py` - Add `dedup_manifest` parameter to `batch_process_exif()`
2. All 11 processors that call `batch_process_exif()`:
   - `processors/discord/processor.py`
   - `processors/google_chat/processor.py`
   - `processors/google_photos/processor.py`
   - `processors/google_voice/processor.py`
   - `processors/imessage/processor.py`
   - `processors/imessage_imazing/processor.py`
   - `processors/instagram_messages/processor.py`
   - `processors/instagram_public_media/processor.py`
   - `processors/instaloader/processor.py`
   - `processors/snapchat_memories/processor.py`
   - `processors/snapchat_messages/processor.py`

## Verification

After fix, run:

```bash
# Find files with merged descriptions
exiftool -r -if '$ImageDescription =~ /^\+/' -ImageDescription /mnt/media/processed | head -20
```

Or use the audit script:

```bash
python scripts/sample_metadata_audit.py --samples 250 --verbose
```

And check for descriptions containing `+` prefix indicating secondary sources.

## Additional Context

### Manifest Statistics (from current run)

```json
{
  "total_files_scanned": 158701,
  "unique_files": 72990,
  "duplicate_groups": 42600,
  "duplicates_found": 85711,
  "space_saved_bytes": 404678038220
}
```

### Sample Cross-Processor Duplicate

Hash: `00183fbb4f4f1212`

- Primary: Google Photos (`IMG_5895.heic`)
- Secondary sources: 7 from iMessage across multiple devices
- Albums: Ethan & friends, Photos from 2024, Family & friends, Lans with the boys_, Aidan

Current description: `Source: Google/ethanriverpage/photos`

Expected description:

```
Source: Google/ethanriverpage/photos
+iMessage/iph13p
+iMessage/m3_mbp
+iMessage/iph17pm
```
