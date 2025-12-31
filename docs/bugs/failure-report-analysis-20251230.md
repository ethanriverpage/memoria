# Failure Report Analysis: December 30, 2025

## Status: ANALYSIS COMPLETE - One code bug identified

## Summary

Analysis of all `failure-report.json` files across 9 processors in `/mnt/media/processed` reveals that **preprocessing/matching is working correctly**. All 78 failures are post-matching processing issues, not metadata-to-media matching failures.

**Key finding:** Every processor shows `failed_matching: {orphaned_media: [], orphaned_metadata: []}`.

## Processors Analyzed

| Processor | Export | Total Failures | Matching Failures |
|-----------|--------|----------------|-------------------|
| Snapchat Messages | snapchat-ethriverpage-20251017 | 7 | 0 |
| Snapchat Memories | snapchat-ethriverpage-20251017 | 1 | 0 |
| iMessage (iMazing) | imessage-iphxr-20210621 | 41 | 0 |
| iMessage | imessage-iph17pm-20251121 | 6 | 0 |
| iMessage | imessage-iph13p-20230925 | 1 | 0 |
| Google Chat | google-hexffedit-20250921 | 10 | 0 |
| Google Photos | google-ethanriverpage-20250526 | 8 | 0 |
| Google Voice | google-ethanriverpage-20250526 | 1 | 0 |
| Discord | discord-minmatic-20251204 | 4 | 0 |
| **Total** | | **79** | **0** |

## Failure Categories

### 1. Unsupported File Formats (49 failures) - ExifTool Limitation

ExifTool does not support writing metadata to certain formats:

| Format | Count | Affected Processors | Error Message |
|--------|-------|---------------------|---------------|
| MP3 | 41 | iMessage (iMazing) | "Writing of MP3 files is not yet supported" |
| WEBM | 7 | Google Chat (6), Discord (1) | "Writing of WEBM files is not yet supported" |
| BMP | 1 | iMessage | "Writing of BMP files is not yet supported" |

**Resolution:** Not a bug. These formats genuinely don't support the metadata fields we want to write.

**Code change:** We now **skip EXIF write attempts** for these extensions (via `BannedFilesFilter.EXIFTOOL_WRITE_UNSUPPORTED_EXTENSIONS` in `common/media/exiftool.py`) so they **do not show up as processing failures**. The files are still copied to the output; they just won’t have embedded metadata.

### 2. Corrupt Files in Original Exports (10 failures) - Data Issues

Files that were already corrupted in the source export:

| Processor | Files | Issue | Verification |
|-----------|-------|-------|--------------|
| Google Photos | 2 | Truncated mdat/atom | Corrupt MP4/MOV containers |
| Snapchat Memories | 1 | "Not a valid MP4" | `file` command returns "data" |
| iMessage (all exports) | 6 | IFD0/CRC issues in PNG | Malformed EXIF structure |
| Discord | 1 | IFD0 pointer issue in PNG | Malformed EXIF structure |

**Verification example:**

```bash
$ file /mnt/media/originals/snapchat-ethriverpage-20251017/memories/media/2016-07-25_14-59-54.mp4
/mnt/media/.../2016-07-25_14-59-54.mp4: data
```

**Resolution:** Not a bug. These are corrupted source files that cannot be processed.

### 3. File Extension Mismatches (5 failures) - Export Data Issues

Files where the export provided incorrect extensions:

| Processor | Files | Claimed Type | Actual Type | Verification |
|-----------|-------|--------------|-------------|--------------|
| Google Chat | 4 | `.jpg` | WebM | `file` command confirms |
| Discord | 1 | `.mov` | JPEG | `file` command confirms |

**Verification:**

```bash
$ file "/mnt/media/originals/google-hexffedit-20250921/Google Chat/Groups/Space AAAAY8TD5is/File-2016-03-22.jpg"
.../File-2016-03-22.jpg: WebM

$ file "/mnt/media/processed/Discord/minmatic/messages/issues/failed-processing/discord-minmatic-20250210.mov"
.../discord-minmatic-20250210.mov: JPEG image data, Exif standard...
```

**Resolution:** Not a bug. The source exports (Google Chat, Discord CDN) provided files with incorrect extensions. Our format detection correctly identified the mismatch.

### 4. Large Video Files (6 failures) - ExifTool Limitation

ExifTool cannot rewrite metadata in-place for very large video files:

| Processor | Files | Issue | Size Range |
|-----------|-------|-------|------------|
| Google Photos | 5 | "atom is too large for rewriting" | 268 MB - 3541 MB |
| Discord | 1 | "Can't yet handle sidx box" | N/A |

**Example error:**

```text
'0x6f091e88' atom is too large for rewriting (3541 MB)
```

**Resolution:** Not a bug. ExifTool has inherent limitations with large MP4 files. Consider using ffmpeg for metadata writing on large files as an enhancement.

### 5. Video Overlay Embedding Failures (7 failures) - CODE BUG

**This is the only identified code bug.**

| Processor | Files | Error |
|-----------|-------|-------|
| Snapchat Messages | 7 | `exif_target_missing` - "Output file missing at EXIF write time" |

**Symptoms:**

- All 7 are MKV files (videos with overlays that should be embedded)
- Error context shows `"move_error": "Source file not found"`
- Source MP4 files in originals are valid:

```bash
$ file "/mnt/media/originals/snapchat-ethriverpage-20251017/messages/chat_media/2018-05-26_media~zip-BAE31043-1BB2-4989-9CE0-A53FBB35A8BD.mp4"
.../2018-05-26_media~zip-....mp4: ISO Media, MP4 v2 [ISO 14496-14]
```

**Root Cause Hypothesis:**

The FFmpeg overlay embedding process is failing for these specific videos (all from 2017-2018), but:

1. The failure is not being properly caught/logged
2. Temporary files are cleaned up before the error is detected
3. The processor then tries to move a non-existent file

**Affected Files:**

| Date | Media File | Overlay File |
|------|------------|--------------|
| 2017-09-03 | media~zip-1B6A2E06-3020-44F7-9D92-BF5EC07812DB.mp4 | overlay~zip-40B95AB7-C12F-46FE-B7AB-F055D1E9295B.png |
| 2017-09-29 | media~zip-18A2A17A-DEC5-44CC-895C-180CCA7C23BA.mp4 | overlay~zip-A7777A16-0E73-4504-AB2C-93809C35EF9A.png |
| 2017-11-14 | media~zip-95B1CF39-9003-422C-A87A-12D8D1ACFE11.mp4 | overlay~zip-2EB007C0-5328-45E8-BD7C-19C5C4EC413A.png |
| 2017-11-14 | media~zip-4DB679EB-084C-4A2B-8EA4-F318FD033B10.mp4 | overlay~zip-726E6729-36D2-4C0B-B29F-451ECFAE2891.png |
| 2017-11-26 | media~zip-1E905A17-9FBE-4E2D-AE6E-C50C4D2DE9B7.mp4 | overlay~zip-4BE08FDA-EE6E-4AE9-B2DC-A65656A16925.png |
| 2018-05-26 | media~zip-BAE31043-1BB2-4989-9CE0-A53FBB35A8BD.mp4 | overlay~zip-32A76417-029D-43D7-B0BC-B55003E2BA81.png |
| 2018-06-14 | media~zip-55EE53BC-99BE-494F-A08A-3EE9B25B0168.mp4 | overlay~zip-8108E597-8FA9-43F1-BFCC-484043969EEC.png |

**Recommended Investigation:**

1. Run FFmpeg overlay embedding manually on one of these files to capture the actual error
2. Check if there's a codec/format issue with 2017-2018 era Snapchat videos
3. Add better error handling in the overlay embedding pipeline to capture and log FFmpeg failures

### 6. No Media in Export (1 failure) - Expected Behavior

| Processor | Issue |
|-----------|-------|
| Google Voice | "No media files found in export to process" |

**Resolution:** Not a bug. The Google Voice export simply contains no media files.

## Failure Distribution Summary

| Category | Count | Percentage | Type |
|----------|-------|------------|------|
| Unsupported format (MP3/WEBM/BMP) | 49 | 62% | Tool limitation |
| Corrupt files in export | 10 | 13% | Data issue |
| Large video files | 6 | 8% | Tool limitation |
| File extension mismatch | 5 | 6% | Data issue |
| Video overlay embedding | 7 | 9% | **Code bug** |
| No media in export | 1 | 1% | Expected |
| **Matching failures** | **0** | **0%** | N/A |

## Conclusions

1. **Preprocessing/matching works correctly** - Zero orphaned media or metadata across all processors.

2. **Most failures are expected** - 75% are tool limitations (unsupported formats, large files) or source data issues (corrupt files, wrong extensions).

3. **One code bug identified** - Snapchat Messages video overlay embedding is failing silently for 7 videos from 2017-2018. The FFmpeg failure is not being properly caught and reported.

## Recommended Actions

### High Priority

1. **Investigate Snapchat overlay embedding failures** - Run FFmpeg manually on the affected files to identify the root cause.

### Low Priority (Enhancements)

1. **Consider ffmpeg for large video metadata** - ExifTool cannot handle very large MP4 files; ffmpeg could write metadata instead.

2. **Consider adding format support warnings** - Log upfront when processing exports with MP3/WEBM/BMP files that will fail metadata embedding.

## Related Documentation

- `docs/Snapchat-Export.md` - Overlay embedding process
- `docs/Google-Export.md` - Google Chat schema
- `docs/Discord-Export.md` - Discord export schema

## Files Analyzed

```text
/mnt/media/processed/Snapchat/ethriverpage/messages/issues/failure-report.json
/mnt/media/processed/Snapchat/ethriverpage/memories/issues/failure-report.json
/mnt/media/processed/iMessage/iphxr/issues/failure-report.json
/mnt/media/processed/iMessage/iph17pm/messages/issues/failure-report.json
/mnt/media/processed/iMessage/iph13p/messages/issues/failure-report.json
/mnt/media/processed/Google/hexffedit/chat/issues/failure-report.json
/mnt/media/processed/Google/ethanriverpage/photos/issues/failure-report.json
/mnt/media/processed/Google/ethanriverpage/voice/issues/failure-report.json
/mnt/media/processed/Discord/minmatic/messages/issues/failure-report.json
```
