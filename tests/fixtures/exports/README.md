# Schema-Faithful Export Fixtures

This directory contains committed, schema-faithful export fixtures that match
the documented export formats in `docs/*.md`.

**These are the preferred fixtures for tests that validate export structure
parsing, detection, and preprocessing.**

## When to Use These Fixtures

Use schema-faithful fixtures for:
- Detection tests (processor `detect()` method)
- Export structure validation
- Preprocessing tests that parse real export formats
- Fixture fidelity tests

Do NOT use these for:
- Internal preprocessed metadata format tests (use generators instead)
- Synthetic edge cases not representable in real exports
- Tests requiring dynamic/parameterized structures

## Directory Structure

```
exports/
├── discord/
│   ├── raw_minimal/                    # Minimal valid Discord export
│   ├── raw_string_channel_types/       # DM, GROUP_DM, GUILD_TEXT
│   ├── raw_attachments_with_query_params/
│   ├── raw_spoiler_filename/
│   └── with_servers_optional/
├── google_photos/
│   ├── takeout_basic/                  # Standard Takeout structure
│   ├── takeout_duplicate_album_vs_library/
│   └── takeout_missing_json_some/
├── google_chat/
│   ├── doc_timestamp_format/           # Real timestamp strings
│   ├── file_naming_patterns/
│   └── groups_and_users/
├── google_voice/
│   └── takeout_calls_basic/            # Takeout/Voice/Calls
├── instaloader/
│   └── basic_with_json_xz/             # Root-level files, includes .json.xz
├── imessage/
│   ├── mac_basic/
│   ├── iphone_basic/
│   ├── live_photo_lp_image_pair/       # lp_image.HEIC + lp_image.MOV
│   ├── ventura_attributedbody/
│   └── with_contacts_vcf/
├── imessage_imazing/
│   └── basic_device_info/              # Device-Info.txt + Messages CSV
├── instagram_messages/
│   ├── current_format_doc_html/        # 2023+ format with <h2> sender
│   ├── legacy_format_doc_html/         # Pre-2023 with <div> sender
│   └── missing_media_reference/
├── instagram_public/
│   └── posts_and_archived_posts/
├── snapchat_messages/
│   ├── raw_conversation_id_map/        # Doc-style chat_history.json
│   ├── raw_media_ids_pipe_separated/
│   └── overlay_ambiguous_mtime/
└── snapchat_memories/
    └── memories_basic/
```

## Usage

Use the helper in `tests/fixtures/export_tree.py`:

```python
from tests.fixtures.export_tree import copy_export_fixture

def test_something(temp_export_dir):
    copy_export_fixture("discord", "raw_minimal", temp_export_dir)
    # temp_export_dir now contains the fixture tree
```

Or use the pre-defined fixtures in conftest.py:

```python
def test_discord_detection(discord_export, discord_processor):
    # discord_export is already populated with raw_minimal
    assert discord_processor.detect(discord_export) is True
```

## Media Files

Media files use tiny valid binary samples from `tests/fixtures/media_samples.py`:
- JPEG: 267 bytes (1x1 red pixel)
- PNG: 68 bytes (1x1 red pixel)
- MP4/MOV: 20 bytes (minimal ftyp box)
- WebP: ~90 bytes

These are small enough to commit but valid enough for format detection.

For tests requiring realistic media (e.g., ffprobe inspection), use:
- `write_real_mp4_if_available()` - generates real MP4 with ffmpeg
- `write_scof_wrapped_png()` - creates Snapchat overlay format

## Fixture Fidelity

All fixtures are validated by `tests/test_fixtures/test_export_fixtures_fidelity.py`:
- Each fixture is detected by exactly one processor
- Required files/structure per documentation exist
- Fixtures don't drift from documented schemas

## Adding New Fixtures

1. **Check documentation**: Ensure the case is documented in `docs/*.md`
2. **Create case directory**: `exports/<platform>/<case_name>/`
3. **Add minimal files**: Match the documented schema exactly
4. **Use tiny media**: Import from `tests/fixtures/media_samples.py`
5. **Add fidelity test**: Update `test_export_fixtures_fidelity.py`

### Example: Adding a New Discord Case

```bash
mkdir -p tests/fixtures/exports/discord/new_case_name/Messages/c123456/
```

Create `channel.json`:
```json
{
  "id": "123456",
  "type": "DM",
  "name": "Example"
}
```

Create `messages.json`:
```json
[
  {
    "ID": "msg001",
    "Timestamp": "2021-01-01 12:00:00",
    "Contents": "Test message",
    "Attachments": ""
  }
]
```

Create `index.json` in Messages/:
```json
{
  "c123456": "Example"
}
```

Add fidelity test:
```python
def test_new_case_detected(self, processor, tmp_path):
    copy_export_fixture("discord", "new_case_name", tmp_path)
    assert processor.detect(tmp_path) is True
```

## Documentation Reference

Each platform's fixtures should match the schemas documented in:

| Platform | Documentation |
|----------|---------------|
| Discord | `docs/Discord-Export.md` |
| Google Photos/Chat/Voice | `docs/Google-Export.md` |
| iMessage/iMazing | `docs/iMessage-Export.md` |
| Instagram | `docs/Instagram-Export.md` |
| Snapchat | `docs/Snapchat-Export.md` |
