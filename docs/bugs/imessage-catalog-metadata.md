# Bug: iMessage build_catalog() Does Not Populate Metadata

**Date**: 2025-12-26  
**Severity**: Medium  
**Component**: `processors/imessage/processor.py`  
**Affects**: Cross-processor deduplication description building  
**Status**: FIXED (2025-12-26)

## Summary

The iMessage processor's `build_catalog()` method did not populate metadata (sender, conversation, timestamp) for catalog entries. This caused cross-processor deduplicated files with iMessage as the primary source to have incomplete descriptions missing iMessage-specific context.

## Resolution

The iMessage processor now includes a proper `extract_metadata()` function in `build_catalog()` (lines 195-216) that populates:

- `sender`: Message sender
- `conversation`: Conversation title
- `timestamp`: Message timestamp
- `content`: Message text

## Current Behavior

When `CROSS_PROCESSOR_DEDUP=true` and an iMessage file is selected as primary for a duplicate group, the generated description lacks iMessage metadata:

```
Source: iMessage
+Google Photos: Quinn Harrison (2021-02-12)
+Google Photos: Quinn Harrison (2021-02-12)
```

**Expected**:

```
Source: iMessage
  - John Doe in "Lans with the boys"
+Google Photos: Quinn Harrison (2021-02-12)
+Google Photos: Quinn Harrison (2021-02-12)
```

## Root Cause

The `build_catalog()` method in `processors/imessage/processor.py` (lines 150-190) calls `catalog_media_files()` without passing a `metadata_extractor` callback:

```python
@staticmethod
def build_catalog(input_dir: str, **kwargs):
    # ... file discovery logic ...
    
    yield from catalog_media_files(
        processor_name="iMessage",
        input_path=input_path,
        media_files=media_files,
        workers=workers,
        # NOTE: No metadata_extractor passed!
    )
```

The `catalog_media_files()` function supports an optional `metadata_extractor` parameter that allows processors to attach metadata to each catalog entry. Other processors like Google Photos use this to include albums, people, and timestamps.

## Impact

1. **Incomplete descriptions**: When iMessage is the primary source in a cross-processor duplicate group, the description lacks sender/conversation context.

2. **Lost context**: The iMessage metadata (who sent the file, which conversation) is not preserved in the dedup manifest's `merged_metadata`.

3. **Inconsistent behavior**: Google Photos and other processors that populate metadata have full context in cross-processor dedup, while iMessage does not.

## Affected Files

- `processors/imessage/processor.py` - `build_catalog()` method
- `common/dedup/catalog.py` - `catalog_media_files()` (provides the interface)

## Implemented Fix

The fix was implemented in `processors/imessage/processor.py` lines 150-216. The `build_catalog()` method now:

1. Loads `metadata.json` if the export is preprocessed
2. Builds a filename index mapping media files to message metadata
3. Passes a `metadata_extractor` callback that populates sender, conversation, timestamp, and content

```python
def extract_metadata(file_path: Path) -> Dict[str, Any]:
    """Extract iMessage metadata for a media file."""
    message = metadata_index.get(file_path.name, {})
    result: Dict[str, Any] = {}
    if message:
        if message.get("sender"):
            result["sender"] = message["sender"]
        if message.get("conversation_title"):
            result["conversation"] = message["conversation_title"]
        if message.get("created"):
            result["timestamp"] = message["created"]
        if message.get("content"):
            result["content"] = message["content"]
    return result
```

## Remaining Considerations

1. **Preprocessed vs raw exports**: The metadata.json only exists for preprocessed exports. Raw iMessage exports (direct from backup) would still have empty metadata unless we also query the chat.db during cataloging.

2. **Related Bug**: Bug #46 (content_hash not in MetadataWriteRequest) still prevents merged descriptions from being written. This fix populates the catalog metadata, but the merged descriptions won't appear until #46 is fixed.

## Related

- Cross-processor deduplication feature (`CROSS_PROCESSOR_DEDUP=true`)
- `common/dedup/catalog.py` - `catalog_media_files()` function
- `common/dedup/service.py` - `get_merged_description_for_file()`

## Testing

1. Run preprocessing on an iMessage export
2. Run catalog phase with `CROSS_PROCESSOR_DEDUP=true`
3. Verify catalog entries have metadata populated:

   ```sql
   SELECT source_path, metadata FROM catalog_entries 
   WHERE source_path LIKE '%imessage%' LIMIT 5;
   ```

4. Process with cross-processor dedup and verify descriptions include iMessage context
