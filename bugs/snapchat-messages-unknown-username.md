# Bug: Snapchat Messages Processor Shows "unknown" Export Username

## Summary

The Snapchat Messages processor fails to extract the export username for consolidated exports, resulting in processed files being labeled with `Source: Snapchat/unknown/messages` instead of the correct username.

## Severity

**High** - This should be a hard-fail condition. The export username is always available from the directory name, so `unknown` should never occur.

## Observed Behavior

```
Source: Snapchat/unknown/messages
```

From logs:
```
20:52:32 - processors.snapchat_messages.processor - INFO - Export username: unknown
21:06:54 - processors.snapchat_messages.processor - INFO - Export username: unknown
```

## Expected Behavior

```
Source: Snapchat/ethanriverpage/messages
```

The Snapchat Memories processor correctly handles this:
```
20:55:26 - processors.snapchat_memories.processor - INFO - Export username (from parent): ethanriverpage
```

## Root Cause

In `processors/snapchat_messages/processor.py`, the `preprocess()` method detects consolidated export structures and correctly updates `input_path` to the `messages/` subdirectory. However, it **fails to extract and pass the username override** to the `SnapchatPreprocessor`.

### Problematic Code

```python
# processors/snapchat_messages/processor.py lines 171-202
@staticmethod
def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
    input_path = Path(input_dir)
    cache_path = Path(cache_dir)
    workers = kwargs.get("workers")

    # Check for consolidated export structure (messages/chat_media/)
    messages_subdir = input_path / "messages"
    if messages_subdir.exists() and messages_subdir.is_dir():
        chat_media_check = messages_subdir / "chat_media"
        json_check = messages_subdir / "json"
        if chat_media_check.exists() or json_check.exists():
            logger.info(
                "Detected consolidated export structure, preprocessing from: %s",
                messages_subdir,
            )
            input_path = messages_subdir  # <-- Changes to messages/ subdirectory

    # BUG: No username_override is extracted or passed!
    preprocessor = SnapchatPreprocessor(
        export_path=input_path,  # <-- Now points to messages/ subdirectory
        output_dir=cache_path,
        workers=workers,
        # username_override=??? <-- MISSING!
    )
```

When `export_path` is set to the `messages/` subdirectory, the `detect_username()` method in `preprocess.py` tries to match the directory name "messages" against the pattern `snapchat-(.+?)-\d{4}-?\d{2}-?\d{2}`, which fails:

```python
# processors/snapchat_messages/preprocess.py lines 85-101
def detect_username(self) -> DetectionResult:
    if self.username_override:  # Not set!
        return DetectionResult(username=self.username_override, source="override")

    dir_name = self.export_path.name  # = "messages" for consolidated exports
    match = re.match(r"snapchat-(.+?)-\d{4}-?\d{2}-?\d{2}", dir_name)
    if match:  # Fails - "messages" doesn't match the pattern
        return DetectionResult(username=match.group(1), source="directory_name")

    return DetectionResult(username="unknown", source="none")  # Falls through here
```

## Correct Implementation (Snapchat Memories)

The Snapchat Memories processor correctly handles this case:

```python
# processors/snapchat_memories/processor.py lines 310-328
export_username_override = None
if memories_subdir.exists() and memories_subdir.is_dir():
    # ...
    if (media_dir.exists() and overlays_dir.exists() and metadata_file.exists()):
        actual_input_dir = str(memories_subdir)
        # Extract username from parent directory for consolidated exports
        export_username_override = extract_username_from_export_dir(
            input_dir, "snapchat"
        )
# ...
process_logic(
    input_dir=actual_input_dir,
    # ...
    export_username_override=export_username_override,
)
```

## Proposed Fix

1. In `SnapchatMessagesProcessor.preprocess()`, extract the username from the parent directory before switching to the subdirectory:

```python
@staticmethod
def preprocess(input_dir: str, cache_dir: str, **kwargs) -> Optional[str]:
    input_path = Path(input_dir)
    cache_path = Path(cache_dir)
    workers = kwargs.get("workers")
    username_override = None  # ADD THIS

    # Check for consolidated export structure (messages/chat_media/)
    messages_subdir = input_path / "messages"
    if messages_subdir.exists() and messages_subdir.is_dir():
        chat_media_check = messages_subdir / "chat_media"
        json_check = messages_subdir / "json"
        if chat_media_check.exists() or json_check.exists():
            logger.info(
                "Detected consolidated export structure, preprocessing from: %s",
                messages_subdir,
            )
            # Extract username from parent directory BEFORE switching
            username_override = extract_username_from_export_dir(
                str(input_path), "snapchat"
            )
            input_path = messages_subdir

    preprocessor = SnapchatPreprocessor(
        export_path=input_path,
        output_dir=cache_path,
        workers=workers,
        username_override=username_override,  # ADD THIS
    )
```

2. Add a validation step to make unknown usernames a hard-fail condition. In `preprocess.py` or `processor.py`:

```python
if export_username == "unknown":
    raise ValueError(
        f"Failed to extract username from export directory. "
        f"Directory should be named 'snapchat-username-YYYYMMDD'. "
        f"Got: {export_path.name}"
    )
```

## Export Directory Structure

For reference, consolidated Snapchat exports follow this structure:

```
snapchat-username-YYYYMMDD/   <- Username should be extracted from HERE
├── memories/                  <- Snapchat Memories processor uses this
│   ├── media/
│   ├── overlays/
│   └── metadata.json
└── messages/                  <- Snapchat Messages processor uses this
    ├── json/
    │   ├── chat_history.json
    │   └── snap_history.json
    └── chat_media/
```

## Files Affected

- `processors/snapchat_messages/processor.py` - `preprocess()` method needs to pass username override
- `processors/snapchat_messages/preprocess.py` - Should validate username is not "unknown"

## Testing

After fix, verify:
1. Consolidated exports correctly show `Source: Snapchat/{actual_username}/messages`
2. Standalone exports (where input_dir IS the snapchat-username-YYYYMMDD directory) continue to work
3. Processing fails with clear error if username cannot be extracted

