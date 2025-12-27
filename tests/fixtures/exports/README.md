# Realistic Export Fixtures

This directory contains committed, schema-faithful export fixtures that match
the documented export formats in `docs/*.md`.

## Directory Structure

```
exports/
├── discord/
│   ├── raw_minimal/                    # Minimal valid Discord export
│   ├── raw_string_channel_types/       # DM, GROUP_DM, GUILD_TEXT, etc.
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
│   └── takeout_calls_basic/            # Takeout/Voice/Calls with doc filename pattern
├── instaloader/
│   └── basic_with_json_xz/             # Root-level Instaloader files, includes .json.xz
├── imessage/
│   ├── mac_basic/
│   ├── iphone_basic/
│   ├── live_photo_lp_image_pair/       # lp_image.HEIC + lp_image.MOV
│   ├── ventura_attributedbody/
│   └── with_contacts_vcf/
├── imessage_imazing/
│   └── basic_device_info/              # Device-Info.txt + Messages CSV + timestamped media
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

## Media Files

Media files use tiny valid binary samples from `tests/fixtures/media_samples.py`:
- JPEG: 267 bytes (1x1 red pixel)
- PNG: 68 bytes (1x1 red pixel)
- MP4/MOV: 20 bytes (minimal ftyp box)
- WebP: ~90 bytes

These are small enough to commit but valid enough for format detection.

## Adding New Fixtures

1. Create a new case directory under the appropriate platform
2. Add the minimal files needed to match the documented schema
3. Use the tiny media samples for any media files
4. Update tests to use the new fixture

