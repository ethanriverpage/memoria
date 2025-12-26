# Bug: content_hash Not Populated in MetadataWriteRequest

**Date**: 2025-12-26  
**Severity**: Critical  
**Component**: All 11 processors, `common/media/exiftool.py`  
**Status**: Open  
**Ref**: POTENTIAL_REFACTORS.md #46

## Summary

All processors create `MetadataWriteRequest` objects without populating the `content_hash` field. This causes `_get_description_for_file()` to always return the default description, completely breaking the cross-processor merged description feature.

## Current Behavior

When `CROSS_PROCESSOR_DEDUP=true` and files are processed, all output files receive single-source descriptions:

```
Source: Google/ethanriverpage/photos
```

**Expected**:

```
Source: Google/ethanriverpage/photos
+iMessage/iph13p
+iMessage/m3_mbp
```

## Root Cause

The `_get_description_for_file()` function in `common/media/exiftool.py` (lines 195-240) has an early return when `content_hash` is None:

```python
def _get_description_for_file(
    file_path: str,
    default_description: str,
    dedup_manifest: Optional[Dict[str, Any]] = None,
    content_hash: Optional[str] = None,  # <-- Never populated!
    processor_name: str = "unknown",
) -> str:
    if dedup_manifest is None or content_hash is None:
        return default_description  # <-- Always exits here!
    
    # Secondary source lookup never reached
    ...
```

All 11 processor `_build_metadata_requests()` functions create `MetadataWriteRequest` without `content_hash`:

| Processor | File | Lines |
|-----------|------|-------|
| Google Photos | `processors/google_photos/processor.py` | 313-324 |
| Google Chat | `processors/google_chat/processor.py` | 282-293 |
| Discord | `processors/discord/processor.py` | 346-357 |
| iMessage | `processors/imessage/processor.py` | 500-511 |
| iMessage (iMazing) | `processors/imessage_imazing/processor.py` | 383-423 |
| Instagram Messages | `processors/instagram_messages/processor.py` | 288-299 |
| Instagram Public | `processors/instagram_public_media/processor.py` | 292-303 |
| Instaloader | `processors/instaloader/processor.py` | 213-271 |
| Snapchat Memories | `processors/snapchat_memories/processor.py` | 373-384 |
| Snapchat Messages | `processors/snapchat_messages/processor.py` | 384-436 |
| Google Voice | `processors/google_voice/processor.py` | 235-288 |

Example from Google Photos:

```python
requests.append(
    MetadataWriteRequest(
        file_path=file_path,
        arg_preamble=PREAMBLE_WITH_IGNORE_ERRORS,
        datetime_tags=datetime_tags,
        gps=gps,
        description=DescriptionWriteSpec(
            default_description=default_description,
        ),
        processor_name="google_photos",
        # content_hash is missing!
    )
)
```

## Data Flow Gap

The `content_hash` is available during preprocessing (stored in `file_hashes` in metadata.json), but it is not threaded through to the `_build_metadata_requests()` function:

```
Preprocessing:
  compute_content_hash() -> file_hashes dict -> metadata.json

Processing:
  metadata.json -> file_hashes dict -> dedup filtering (hash used here)
                                    -> file_info tuples (hash NOT included)
                                    -> _build_metadata_requests() (hash unavailable)
                                    -> MetadataWriteRequest (content_hash=None)
```

## Impact

- **10,162 cross-processor duplicate groups** have broken descriptions
- **85,711 total duplicates** affected
- Cross-processor provenance tracking completely broken
- The entire merged description feature is non-functional

## Proposed Fix

### 1. Thread content_hash through file_info tuples

Each processor's processing loop needs to include the content_hash in the file_info tuple:

```python
# Current:
file_info.append((output_path, message_data, export_username))

# Fixed:
content_hash = file_hashes.get(media_file_name)
file_info.append((output_path, message_data, export_username, content_hash))
```

### 2. Update _build_metadata_requests() signatures

```python
# Current:
def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str]],
) -> List[MetadataWriteRequest]:

# Fixed:
def _build_metadata_requests(
    file_info: List[Tuple[str, dict, str, Optional[str]]],
) -> List[MetadataWriteRequest]:
```

### 3. Populate content_hash in MetadataWriteRequest

```python
for file_path, message_data, export_username, content_hash in file_info:
    ...
    requests.append(
        MetadataWriteRequest(
            file_path=file_path,
            ...
            processor_name="google_photos",
            content_hash=content_hash,  # Add this
        )
    )
```

## Files to Modify

1. **All 11 processors** - Update file_info tuple to include content_hash
2. **All 11 processors** - Update `_build_metadata_requests()` to use content_hash

## Testing

1. Run with `CROSS_PROCESSOR_DEDUP=true`
2. Find files that appear in multiple processors (e.g., same photo in Google Photos and iMessage)
3. Verify description contains secondary sources:

```bash
exiftool -ImageDescription /path/to/processed/file.jpg
```

Should show:
```
Source: Google/username/photos
+iMessage/device1
+iMessage/device2
```

## Related

- `docs/bugs/dedup-merged-description-not-wired.md` - Previous fix that added `dedup_manifest` to `batch_write_metadata_requests()` but missed the `content_hash` requirement
- POTENTIAL_REFACTORS.md #46

## Priority

**Critical** - This completely breaks a major feature (cross-processor deduplication provenance tracking). Should be fixed before any other refactoring work.

