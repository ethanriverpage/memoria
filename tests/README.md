# Memoria Test Suite

This directory contains the pytest-based test infrastructure for Memoria.

## Directory Structure

```
tests/
├── conftest.py                    # Shared fixtures and configuration
├── helpers/
│   ├── __init__.py
│   └── processing.py              # Processing test helpers and assertions
├── fixtures/
│   ├── __init__.py
│   ├── generators.py              # Test export generator functions (internal use)
│   ├── media_samples.py           # Minimal valid media file bytes
│   ├── export_tree.py             # Utilities for committed fixtures
│   └── exports/                   # Schema-faithful export fixtures
│       ├── google_photos/
│       ├── google_chat/
│       ├── discord/
│       ├── snapchat_memories/
│       ├── snapchat_messages/
│       ├── instagram_messages/
│       ├── instagram_public/
│       ├── instaloader/
│       ├── imessage/
│       ├── imessage_imazing/
│       └── README.md
├── test_detection/                # Processor detect() tests
├── test_processing/               # Processor preprocess/process tests
├── test_fixtures/                 # Fixture fidelity tests
│   └── test_export_fixtures_fidelity.py
├── test_integration/              # Full pipeline integration tests
│   ├── test_dataset_validation.py # Originals-based validation (opt-in)
│   └── test_cli.py                # CLI integration tests
└── README.md                      # This file
```

## Fixture Strategy

The test suite uses two complementary fixture approaches:

### 1. Schema-Faithful Fixtures (Preferred for Export Tests)

Located in `tests/fixtures/exports/`, these are committed directory trees that
match the documented export schemas in `docs/`. Use these for:

- Detection tests
- Preprocessing tests
- Any test that validates real export structure parsing

```python
from tests.fixtures.export_tree import copy_export_fixture

def test_google_photos_detection(self, processor, tmp_path):
    copy_export_fixture("google_photos", "takeout_basic", tmp_path)
    assert processor.detect(tmp_path) is True
```

### 2. Generator Fixtures (For Internal/Preprocessed Formats)

Located in `tests/fixtures/generators.py`, these create synthetic structures for:

- Internal preprocessed metadata formats
- Targeted edge case testing
- Scenarios not covered by committed fixtures

```python
from tests.fixtures.generators import create_imessage_mac_export

def test_imessage_database_schema(self, tmp_path):
    create_imessage_mac_export(tmp_path, conversations=[...])
    # Test database queries, not export structure
```

**When to use which:**

| Scenario | Use Schema Fixtures | Use Generators |
|----------|---------------------|----------------|
| Detection tests | Yes | No |
| Export structure validation | Yes | No |
| Preprocessing tests | Yes | Rarely |
| Internal metadata format tests | No | Yes |
| Synthetic edge cases | Sometimes | Yes |

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

# Skip integration tests (require exiftool, ffmpeg, or originals)
pytest -m "not integration"

# Run only integration tests
pytest -m integration
```

### Integration Tests with Real Originals (Opt-in)

The `test_dataset_validation.py` tests can validate against real export data,
but require explicit opt-in:

```bash
# Run with auto-generated dataset from originals
ORIGINALS_PATH=/mnt/media/originals pytest -m integration --run-full-pipeline

# Use deterministic sampling for reproducible results
TEST_SAMPLER_SEED=12345 pytest -m integration --run-full-pipeline

# Keep the generated dataset for debugging
pytest -m integration --run-full-pipeline --keep-dataset
```

**Environment Variables:**

| Variable | Default | Description |
|----------|---------|-------------|
| `ORIGINALS_PATH` | `/mnt/media/originals` | Path to original exports |
| `TEST_DATASET_SOURCE` | `/mnt/media/temp/test_dataset` | Pre-sampled dataset |
| `TEST_DATASET_PROCESSED` | `/mnt/media/temp/test_dataset_proc` | Pre-processed output |
| `TEST_SAMPLER_SEED` | Random | Seed for deterministic sampling |

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

### Detection Tests (`test_detection/`)

Test each processor's `detect()` function with:
- Valid export structures (should return True)
- Missing required files (should return False)
- Empty directories (should return False)
- Wrong structure (should return False)
- Consolidated structure variants

### Processing Tests (`test_processing/`)

Test `preprocess()` and `process()` functions with AAA pattern:
- **Arrange**: Copy schema fixture to temp directory
- **Act**: Run preprocessor/processor
- **Assert**: Verify output files, metadata, failure reports

| Processor | Key Edge Cases |
|-----------|---------------|
| Google Photos | Missing JSON metadata, duplicate files, Live Photo pairs |
| Google Chat | Group conversations, DM conversations, file naming patterns |
| Google Voice | Text conversations, HTML parsing, orphaned media |
| Snapchat Memories | Missing overlays, video overlays, GPS filtering |
| Snapchat Messages | Orphaned media, ambiguous overlay matching, failure tracking |
| Instagram Messages | Current vs legacy format, expired media |
| Instagram Public | Carousel posts, YYYYMM organization |
| Instaloader | UTC timestamp pattern, carousel numbering |
| Discord | Expired CDN URLs, channel type detection, SPOILER filenames |
| iMessage | Mac vs iPhone structure, Live Photo linking, deduplication |

### Fixture Fidelity Tests (`test_fixtures/`)

Validate that committed fixtures:
- Match documented export schemas
- Are detected by exactly one processor
- Have all required files/structure

### Integration Tests (`test_integration/`)

Test full processing pipelines:
- CLI integration
- Multi-processor detection
- Output validation against real originals (opt-in)
- Processor-specific invariants

## Test Helpers

Import from `tests/helpers`:

```python
from tests.helpers import (
    run_preprocessor,
    run_processor,
    ProcessingResult,
    assert_no_failures,
    assert_has_media_files,
    assert_metadata_json_valid,
)

def test_preprocessing_produces_metadata(self, tmp_path):
    from processors.google_photos.preprocess import GooglePhotosPreprocessor
    from tests.fixtures.export_tree import copy_export_fixture

    copy_export_fixture("google_photos", "takeout_basic", tmp_path / "export")
    
    result = run_preprocessor(
        GooglePhotosPreprocessor,
        tmp_path / "export",
        tmp_path / "output",
    )

    assert_no_failures(result)
    assert_metadata_json_valid(result)
    assert_has_media_files(result, min_count=1)
```

## Fixtures

### Session-scoped Fixtures

- `project_root`: Project root directory
- `test_exports_dir`: Shared test export directory
- `processor_registry`: Pre-populated processor registry
- `exiftool_available`: Whether exiftool is installed
- `ffmpeg_available`: Whether ffmpeg is installed

### Function-scoped Fixtures

- `temp_output_dir`: Temporary output directory per test
- `temp_export_dir`: Temporary export directory per test

### Export Fixtures

Each processor has a fixture that copies the default schema-faithful export:
- `google_photos_export`
- `google_chat_export`
- `google_voice_export`
- `snapchat_memories_export`
- `snapchat_messages_export`
- `instagram_messages_export`
- `instagram_public_export`
- `instaloader_export`
- `discord_export`
- `imessage_mac_export`
- `imessage_iphone_export`
- `imessage_imazing_export`

### Processor Class Fixtures

Direct access to processor classes:
- `google_photos_processor`
- `google_chat_processor`
- `google_voice_processor`
- `snapchat_memories_processor`
- `snapchat_messages_processor`
- `instagram_messages_processor`
- `instagram_public_processor`
- `instaloader_processor`
- `discord_processor`
- `imessage_processor`

## Writing New Tests

### Adding a Processing Test

```python
def test_process_edge_case(self, processor_class, temp_export_dir, temp_output_dir):
    """Should handle the edge case correctly."""
    from tests.fixtures.export_tree import copy_export_fixture
    from tests.helpers import run_preprocessor, assert_no_failures

    # Arrange: copy schema fixture
    copy_export_fixture("processor_name", "case_name", temp_export_dir)

    # Act: run preprocessing
    from processors.example.preprocess import ExamplePreprocessor
    result = run_preprocessor(ExamplePreprocessor, temp_export_dir, temp_output_dir)

    # Assert: verify outputs
    assert_no_failures(result)
    assert result.metadata is not None
    assert len(result.metadata.get("media_files", [])) > 0
```

### Adding a New Fixture

1. Create directory under `tests/fixtures/exports/<processor>/<case_name>/`
2. Add files matching documented schema (see `docs/*.md`)
3. Add media files using `tests/fixtures/media_samples.py` helpers
4. Add fidelity test in `tests/test_fixtures/test_export_fixtures_fidelity.py`

### Creating New Media Samples

Add to `tests/fixtures/media_samples.py`:

```python
# For committed minimal samples (base64-encoded)
MINIMAL_NEW_FORMAT = base64.b64decode("...")

# For runtime-generated samples (with tool dependencies)
def write_real_new_format_if_available(path: Path) -> bool:
    """Generate real format file using external tool if available."""
    if shutil.which("tool") is None:
        write_media_file(path, "fallback")
        return False
    # Generate real file
    return True
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
| Snapchat Memories | GPS 0,0 filtering | test_snapchat_memories.py |
| Snapchat Messages | Orphaned media | test_snapchat_messages.py |
| Snapchat Messages | Ambiguous overlay match | test_snapchat_messages.py |
| Snapchat Messages | Overlay embed failure | test_snapchat_messages.py |
| Instagram | Current vs legacy format | test_instagram_messages.py |
| Instagram | Expired media placeholder | test_instagram_messages.py |
| Instaloader | Carousel numbering | test_instaloader.py |
| Instaloader | Orphaned metadata | test_instaloader.py |
| Discord | Expired CDN URLs | test_discord.py |
| Discord | Channel type detection | test_discord.py |
| Discord | SPOILER filenames | test_discord.py |
| iMessage | Mac vs iPhone structure | test_imessage.py |
| iMessage | Live Photo linking | test_imessage.py |
| iMessage | Cross-export dedup | test_imessage.py |
| iMessage | Placeholder attachments | test_imessage.py |
