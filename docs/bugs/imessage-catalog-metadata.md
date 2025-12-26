# Bug: iMessage build_catalog() Does Not Populate Metadata

**Date**: 2025-12-26  
**Severity**: Medium  
**Component**: `processors/imessage/processor.py`  
**Affects**: Cross-processor deduplication description building

## Summary

The iMessage processor's `build_catalog()` method does not populate metadata (sender, conversation, timestamp) for catalog entries. This causes cross-processor deduplicated files with iMessage as the primary source to have incomplete descriptions missing iMessage-specific context.

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

## Proposed Fix

Update `build_catalog()` to:

1. Load `metadata.json` if the export is preprocessed
2. Build a filename index mapping media files to message metadata
3. Pass a `metadata_extractor` callback that looks up sender/conversation for each file

```python
@staticmethod
def build_catalog(input_dir: str, **kwargs):
    input_path = Path(input_dir)
    workers = kwargs.get("workers", 4)

    # Load metadata for preprocessed exports
    metadata_index = {}
    metadata_file = input_path / "metadata.json"
    if metadata_file.exists():
        try:
            metadata = IMessageProcessor.load_metadata(metadata_file)
            metadata_index = build_filename_index(metadata)
        except Exception as e:
            logger.warning(f"Could not load metadata.json: {e}")

    # Find attachments directory
    attachments_dir = None
    if (input_path / "Attachments").is_dir():
        attachments_dir = input_path / "Attachments"
    elif (input_path / "SMS" / "Attachments").is_dir():
        attachments_dir = input_path / "SMS" / "Attachments"
    elif (input_path / "media").is_dir():
        attachments_dir = input_path / "media"

    if not attachments_dir or not attachments_dir.exists():
        logger.warning(f"Attachments directory not found in: {input_path}")
        return

    media_files = []
    for file_path in attachments_dir.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in ALL_MEDIA_EXTENSIONS:
            media_files.append(file_path)

    def extract_metadata(file_path: Path) -> Dict[str, Any]:
        """Extract iMessage metadata for a media file."""
        relative_name = file_path.name
        message = metadata_index.get(relative_name, {})
        
        metadata = {}
        if message:
            # Extract sender
            sender = message.get("sender")
            if sender:
                metadata["sender"] = sender
            
            # Extract conversation
            conversation = message.get("conversation")
            if conversation:
                metadata["conversation"] = conversation
            
            # Extract timestamp
            created = message.get("created")
            if created:
                metadata["timestamp"] = created
        
        return metadata

    yield from catalog_media_files(
        processor_name="iMessage",
        input_path=input_path,
        media_files=media_files,
        workers=workers,
        metadata_extractor=extract_metadata if metadata_index else None,
    )
```

## Considerations

1. **Preprocessed vs raw exports**: The metadata.json only exists for preprocessed exports. Raw iMessage exports (direct from backup) would still have empty metadata unless we also query the chat.db during cataloging.

2. **Performance**: Loading metadata.json adds minimal overhead since it's already done during processing.

3. **Backward compatibility**: The fix is additive - exports without metadata.json will continue to work as before.

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
