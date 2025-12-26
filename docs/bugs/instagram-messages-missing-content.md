# Bug: Instagram Messages Not Including Message Content in Descriptions

**Date**: 2025-12-26  
**Severity**: Low  
**Component**: `processors/instagram_messages/preprocess.py`, `processors/instagram_messages/processor.py`  
**Status**: Open  
**Ref**: POTENTIAL_REFACTORS.md #45

## Summary

Instagram Messages descriptions do not include the message content/text, even though this data is available in the source HTML files. Other messaging processors (iMessage, Snapchat Messages, Discord) correctly include message content in descriptions.

## Current Behavior

```
Source: Instagram/ethanriverpage/messages
  - ethan page in "Kai Barton"
```

## Expected Behavior

```
Source: Instagram/ethanriverpage/messages
  - ethan page in "Kai Barton": "Look at this sunset!"
```

## Root Cause

### 1. Processor Does Not Include Content Field

`processors/instagram_messages/processor.py` lines 279-285:

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

### 2. Preprocessor Does Not Extract Content

The preprocessor (`preprocess.py`) parses HTML files but does not extract the message text. The content is available in `<div class="_a6-p">` elements in the Instagram HTML export format.

## Data Availability Comparison

| Processor | Content in Source | Extracted | In Description |
|-----------|-------------------|-----------|----------------|
| iMessage | Yes (JSON/SQLite) | Yes | Yes |
| Snapchat Messages | Yes (JSON) | Yes | Yes |
| Discord | Yes (CSV) | Yes | Yes |
| Google Chat | **No** (not in Takeout) | N/A | No |
| Instagram Messages | **Yes** (HTML `_a6-p` div) | **No** | **No** |

Note: Google Chat is NOT a bug - the Google Takeout export format does not include message text for messages with attachments. This is a data source limitation.

## Impact

- Instagram Messages descriptions lack context about what the message said
- Inconsistent with other messaging processors
- Makes it harder to understand why a photo was shared

## Proposed Fix

### 1. Update Preprocessor (`preprocess.py`)

In the HTML parsing function, extract the message content from the `_a6-p` class:

```python
# After extracting sender and timestamp
content_div = container.find('div', class_='_a6-p')
if content_div:
    # Get text content (skip nested divs with media)
    text_divs = content_div.find_all('div', recursive=False)
    for div in text_divs:
        text = div.get_text(strip=True)
        if text and not text.startswith('http'):
            message_data['content'] = text
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

### 3. Update Catalog Metadata Extraction

In `build_catalog()`, also extract content for catalog entries:

```python
def extract_metadata(file_path: Path) -> Dict[str, Any]:
    info = metadata_index.get(file_path.name, {})
    result: Dict[str, Any] = {}
    if info:
        message = info.get("message", {})
        if info.get("conversation_title"):
            result["conversation"] = info["conversation_title"]
        if message.get("sender"):
            result["sender"] = message["sender"]
        if message.get("timestamp"):
            result["timestamp"] = message["timestamp"]
        if message.get("content"):  # Add this
            result["content"] = message["content"]
    return result
```

## Files to Modify

1. `processors/instagram_messages/preprocess.py` - Extract content from HTML
2. `processors/instagram_messages/processor.py`:
   - `_build_metadata_requests()` - Include content in SourceEntry
   - `build_catalog()` - Include content in metadata extraction

## Testing

1. Run preprocessing on an Instagram Messages export
2. Check `metadata.json` for `content` field in messages
3. Run processing and verify descriptions include content:

```bash
exiftool -ImageDescription /path/to/processed/instagram-messages*.jpg | head -5
```

Should show:

```
Source: Instagram/username/messages
  - sender in "Conversation": "message text here"
```

## Related

- POTENTIAL_REFACTORS.md #45

## Priority

**Low** - Descriptions work correctly, just missing the message content text. Functionality is not broken, just incomplete.
