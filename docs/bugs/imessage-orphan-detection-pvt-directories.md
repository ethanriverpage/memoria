# iMessage Orphan Detection Regression: `.pvt` Directories

**Date**: 2026-01-02  
**Severity**: Medium  
**Status**: Fixed  
**Affected Processors**: `imessage`  
**Fixed**: 2026-01-02 - Added `.pvt` to `BannedFilesFilter.BANNED_DIRECTORY_SUFFIXES`

## Summary

The iMessage preprocessor reports an excessively high count of "orphaned media files" due to scanning Apple's `.pvt` bundle directories, which contain duplicate copies of files already tracked in the database.

## Symptoms

Processing iMessage exports shows thousands of orphaned media files:

```
21:24:21 - Tracked 7295 orphaned media (Media file not referenced in iMessage database)
21:25:23 - Copied 7295/7295 orphaned media files
```

Example affected exports from `run_bfed7443b277`:

| Export | Orphaned Media | Orphaned Metadata |
|--------|----------------|-------------------|
| imessage-iph17pm-20251121 | 7,295 | 1,377 |
| imessage-m3_mbp-20251202 | 4,925 | 1,235 |
| imessage-iph13p-20230925 | 1,847 | 110 |
| imessage-iph13p-20220426 | 432 | 6 |

## Root Cause

### Regression Introduction

Commit `b49b526` ("Enhance type hinting and logging across modules", 2025-12-21) introduced a new `_detect_orphaned_media()` function that scans the Attachments directory for files not referenced in the database.

**Before this commit**, the code explicitly returned an empty list:

```python
# No orphaned media in iMessage (all attachments are linked to messages)
orphaned_media: List[Dict] = []
return conversations, orphaned_media
```

**After this commit**, it scans the filesystem and reports any file not in the database query as orphaned.

### The `.pvt` Directory Issue

Apple's iMessage uses `.pvt` (private) bundle directories to store Live Photo content:

```
/SMS/Attachments/86/06/6E235607-FBFA-46BB-BF94-B4D939E64921/
├── 78527531199__A2D4146E-2448-4DD3-AE72-9DE04A38DA51.HEIC   <- DB references this
├── 78527531199__A2D4146E-2448-4DD3-AE72-9DE04A38DA51.MOV
└── 6E235607-FBFA-46BB-BF94-B4D939E64921.pvt/                <- Not in DB
    ├── 78527531199__A2D4146E-2448-4DD3-AE72-9DE04A38DA51.HEIC
    ├── 78527531199__A2D4146E-2448-4DD3-AE72-9DE04A38DA51.MOV
    └── metadata.plist
```

The database only references the "public" version of files, not the bundled copies inside `.pvt` directories. The new orphan scanner finds these bundled copies and incorrectly reports them as orphaned.

### Quantitative Analysis (imessage-iph17pm-20251121)

| Category | Count |
|----------|-------|
| Total files in Attachments folder | 43,371 |
| Media files (by extension) | ~26,676 |
| **Files in `.pvt` directories** | **5,352** |
| Attachments found in DB query | 19,616 |
| Reported as orphaned | 7,295 |

The 5,352 files in `.pvt` directories account for ~73% of the reported orphans. These are not truly orphaned - they are Apple's internal packaging format containing duplicates of already-processed files.

### Remaining Orphans

After excluding `.pvt` files, approximately 1,943 files remain orphaned:

- Files with transfer_state != 5 (incomplete downloads)
- Files without proper database join relationships
- Files with non-media extensions (.caf audio, extensionless files)

These represent genuine orphans or edge cases.

## Database Verification

```sql
-- Transfer states in the database
SELECT transfer_state, COUNT(*) FROM attachment GROUP BY transfer_state;
-- Results: 5|35877, 0|202, 6|99, -1|2

-- Attachments with full join chain (what the query uses)
SELECT COUNT(DISTINCT a.ROWID) FROM attachment a 
JOIN message_attachment_join maj ON a.ROWID = maj.attachment_id 
JOIN message m ON maj.message_id = m.ROWID 
JOIN chat_message_join cmj ON m.ROWID = cmj.message_id 
JOIN chat c ON cmj.chat_id = c.ROWID 
WHERE a.transfer_state = 5 AND a.filename IS NOT NULL;
-- Result: 35,620
```

The JOIN chain only loses ~257 attachments (35,877 - 35,620), confirming the database is not the source of the high orphan count.

## Fix Implemented

Option 1 was implemented: Added `.pvt` suffix matching to `BannedFilesFilter`.

### Changes to `common/files/filter.py`

1. Added new class constant for directory suffixes:

```python
# Banned directory suffixes (bundle directories to skip)
BANNED_DIRECTORY_SUFFIXES = [
    ".pvt",  # Apple private bundle directories (Live Photos in iMessage)
]
```

1. Updated `is_banned()` to check directory suffixes:

```python
# Check banned directory suffixes (e.g., .pvt bundle directories)
for suffix in self.BANNED_DIRECTORY_SUFFIXES:
    if name.endswith(suffix):
        return True
```

This approach centralizes the filtering logic in `BannedFilesFilter`, making it automatically apply to all processors that use `iter_media_files()` or check `banned_filter.is_banned()`.

## Impact

- **Files affected**: ~5,000-7,000 per large iMessage export
- **Processing overhead**: Unnecessary copying of duplicate files to issues folder
- **Storage waste**: Duplicates stored in `issues/failed-matching/media/`
- **Confusion**: Large orphan counts suggest data loss when none exists

## Testing

After fix, verify:

1. `.pvt` directory contents are not scanned
2. Legitimate orphans (incomplete downloads, missing join relationships) are still detected
3. Live Photos are still processed correctly from their public paths

## Related Files

- `processors/imessage/preprocess.py` - `_detect_orphaned_media()` function
- `common/files/scanner.py` - `iter_media_files()` function
- `common/files/filter.py` - `BannedFilesFilter` class
