# Memoria Test Suite

This directory contains the pytest-based test infrastructure for Memoria.

## Directory Structure

```
tests/
├── conftest.py                    # Shared fixtures and configuration
├── fixtures/
│   ├── __init__.py
│   ├── generators.py              # Test export generator functions
│   └── media_samples.py           # Minimal valid media file bytes
├── test_exports/                  # Generated test data (gitignored)
│   └── .gitkeep
├── test_detection/
│   ├── test_google_detection.py   # Google Photos, Chat, Voice detection
│   ├── test_snapchat_detection.py # Snapchat Memories, Messages detection
│   ├── test_instagram_detection.py # Instagram Messages, Public, Old detection
│   ├── test_discord_detection.py  # Discord detection
│   └── test_imessage_detection.py # iMessage Mac/iPhone detection
├── test_processing/
│   ├── test_google_photos.py      # Google Photos edge cases
│   ├── test_google_chat.py        # Google Chat edge cases
│   ├── test_google_voice.py       # Google Voice edge cases
│   ├── test_snapchat_memories.py  # Snapchat Memories edge cases
│   ├── test_snapchat_messages.py  # Snapchat Messages edge cases
│   ├── test_instagram_messages.py # Instagram Messages edge cases
│   ├── test_instagram_public.py   # Instagram Public Media edge cases
│   ├── test_instagram_old.py      # Instagram Old Format edge cases
│   ├── test_discord.py            # Discord edge cases
│   └── test_imessage.py           # iMessage edge cases
├── test_integration/
│   ├── test_cli.py                # CLI integration tests
│   └── test_multi_processor.py    # Multi-processor detection
└── README.md                      # This file
```

## Running Tests

### Basic Usage

```bash
# Activate virtual environment first
cd /home/ethan/media-processing && source .venv/bin/activate

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/test_detection/test_google_detection.py

# Run specific test class
pytest tests/test_detection/test_google_detection.py::TestGooglePhotosDetection

# Run specific test
pytest tests/test_detection/test_google_detection.py::TestGooglePhotosDetection::test_detect_valid_export
```

### Test Markers

```bash
# Skip slow tests
pytest -m "not slow"

# Skip integration tests (require exiftool, ffmpeg)
pytest -m "not integration"

# Run only integration tests
pytest -m integration
```

### Coverage Reports

```bash
# Run with coverage
pytest --cov=processors --cov=common --cov-report=html

# View coverage report
open htmlcov/index.html
```

### Parallel Execution

```bash
# Run tests in parallel (requires pytest-xdist)
pytest -n auto
```

## Test Categories

### Detection Tests

Test each processor's `detect()` function with:
- Valid export structures (should return True)
- Missing required files (should return False)
- Empty directories (should return False)
- Wrong structure (should return False)
- Consolidated structure variants

### Processing Tests

Test `process()` function edge cases per processor:

| Processor | Key Edge Cases |
|-----------|---------------|
| Google Photos | Missing JSON metadata, duplicate files, Live Photo pairs |
| Google Chat | Group conversations, DM conversations, empty folders |
| Google Voice | Text conversations, HTML parsing, orphaned media |
| Snapchat Memories | Missing overlays, video overlays, empty metadata |
| Snapchat Messages | Orphaned media, ambiguous overlay matching |
| Instagram Messages | Current vs legacy format, expired media |
| Instagram Public | Carousel posts, YYYYMM organization |
| Instagram Old | UTC timestamp pattern, carousel numbering |
| Discord | Expired CDN URLs, channel type detection |
| iMessage | Mac vs iPhone structure, Live Photo linking, deduplication |

### Integration Tests

Test full processing via CLI:
- Single export processing
- Multi-processor detection (Google with Photos + Chat)
- Output directory structure validation
- Error handling for invalid inputs

## Fixtures

### Session-scoped Fixtures

- `project_root`: Project root directory
- `test_exports_dir`: Shared test export directory
- `processor_registry`: Pre-populated processor registry

### Function-scoped Fixtures

- `temp_output_dir`: Temporary output directory per test
- `temp_export_dir`: Temporary export directory per test

### Export Generator Fixtures

Each processor has a corresponding fixture that creates a minimal valid export:
- `google_photos_export`
- `google_chat_export`
- `google_voice_export`
- `snapchat_memories_export`
- `snapchat_messages_export`
- `instagram_messages_export`
- `instagram_public_export`
- `instagram_old_export`
- `discord_export`
- `imessage_mac_export`
- `imessage_iphone_export`

### Processor Class Fixtures

Direct access to processor classes:
- `google_photos_processor`
- `google_chat_processor`
- `google_voice_processor`
- `snapchat_memories_processor`
- `snapchat_messages_processor`
- `instagram_messages_processor`
- `instagram_public_processor`
- `instagram_old_processor`
- `discord_processor`
- `imessage_processor`

## Writing New Tests

### Adding a Detection Test

```python
def test_detect_new_case(self, processor_class, temp_export_dir):
    """Should detect the new case."""
    from tests.fixtures.generators import create_export_function
    create_export_function(temp_export_dir, special_option=True)
    assert processor_class.detect(temp_export_dir) is True
```

### Adding a Processing Test

```python
def test_process_edge_case(self, processor_class, temp_export_dir, temp_output_dir):
    """Should handle the edge case correctly."""
    from tests.fixtures.generators import create_export_function
    create_export_function(temp_export_dir, edge_case=True)
    
    # Verify setup
    assert (temp_export_dir / "expected_file").exists()
```

### Creating New Media Samples

Add to `tests/fixtures/media_samples.py`:

```python
# Base64-encoded minimal valid file
MINIMAL_NEW_FORMAT = base64.b64decode("...")

def write_new_format_file(path: Path) -> Path:
    """Write minimal new format file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MINIMAL_NEW_FORMAT)
    return path
```

## Edge Cases Checklist

Based on documentation, these edge cases are covered:

| Processor | Edge Case | Test File |
|-----------|-----------|-----------|
| Google Photos | Missing JSON metadata | test_google_photos.py |
| Google Photos | Duplicate across albums | test_google_photos.py |
| Google Photos | Live Photo pairs | test_google_photos.py |
| Snapchat Memories | Missing overlay file | test_snapchat_memories.py |
| Snapchat Memories | Video with overlay | test_snapchat_memories.py |
| Snapchat Messages | Orphaned media | test_snapchat_messages.py |
| Snapchat Messages | Ambiguous overlay match | test_snapchat_messages.py |
| Instagram | Current vs legacy format | test_instagram_messages.py |
| Instagram | Expired media placeholder | test_instagram_messages.py |
| Instagram Old | Carousel numbering | test_instagram_old.py |
| Discord | Expired CDN URLs | test_discord.py |
| Discord | Channel type detection | test_discord.py |
| iMessage | Mac vs iPhone structure | test_imessage.py |
| iMessage | Live Photo linking | test_imessage.py |
| iMessage | Cross-export dedup | test_imessage.py |

