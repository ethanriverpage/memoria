# Frequently Asked Questions

## General

### Does Memoria modify my original export files?

No. Memoria copies files and processes the copies. Your original export directory is never touched or modified in any way.

### Can I process the same export multiple times?

Yes, but re-processing will recreate all output files from scratch. If you only want to re-upload without reprocessing, use `--upload-only` mode.

### Why is processing so slow?

Large exports (10,000+ files) involve copying gigabytes of data and embedding metadata into each file using exiftool. This is inherently I/O and CPU intensive. Use `--workers N` to parallelize processing across multiple CPU cores for better performance.

### How do I know if processing is working?

Use `--verbose` to see detailed progress logs. Without verbose mode, Memoria prints summary statistics as it processes.

### Can I stop processing and resume later?

No, Memoria doesn't support resuming interrupted processing. If you stop mid-process, you'll need to start over. However, your original export is never modified, so you can safely retry.

### Do I need to use Immich?

No. Immich integration is optional. Use `--skip-upload` to process files without uploading. You can then manually import the processed files into any photo management system that reads EXIF metadata.

### What happens to files that fail processing?

Failed files are logged and skipped. Processing continues with remaining files. Check the logs (with `--verbose`) to see which files failed and why.

## Google Photos

### Where did my album folders go?

By design, album folders are flattened into a single directory per username. Album names are preserved in EXIF metadata (specifically in `ImageDescription` and `IPTC:Caption-Abstract` fields) and used to create Immich albums during upload.

This prevents massive duplication - Google Photos exports often contain the same photo in multiple albums, resulting in 30-60% duplicate files.

See: [Common Gotchas](Common-Gotchas.md#album-folders-are-not-preserved)

### Why are some of my photos missing?

They're not missing - they're deduplicated. When the same photo appears in multiple albums, Memoria keeps only one copy. Check the deduplication summary at the end of processing:
- "Media files copied" shows unique files processed
- "Deduplicated files (not copied)" shows duplicate instances skipped

See: [Deduplication](Deduplication.md)

### Can I disable deduplication?

No. Deduplication is a core design decision and cannot be disabled. Processing duplicates would waste disk space, processing time, and upload bandwidth with no practical benefit.

If you specifically need duplicate files preserved in separate album folders, Memoria is not the right tool for your use case.

### What are Live Photos and Motion Photos?

Live Photos (iOS) and Motion Photos (Android) are photos with a short video component. Google Photos exports these as separate files (`.jpg` + `.mp4`). Memoria processes both components and links them with matching sequence numbers.

### Some photos don't have JSON metadata files. What happens?

Memoria falls back to EXIF data already embedded in the file. Older uploads to Google Photos may not have companion JSON files, but most photos already contain EXIF data from the camera.

### Why does the JSON timestamp differ from EXIF data in my photos?

When there's a conflict, Memoria uses the JSON timestamp because it represents the "taken time" as recorded by Google Photos, which may have been manually corrected by the user.

## Google Chat

### How are group messages organized?

All chat media (both groups and direct messages) is organized into a single directory per username. Filenames include the conversation name to distinguish the source.

Example: `gchat-username-Family_Group-20230115.jpg`

### Why are some group names different from what I see in the app?

If a group was renamed, Google exports use the most recent name. The original name at the time of the message is not preserved.

### Are deleted messages included?

No. Google's export only includes messages and media that still exist in your account. Deleted messages and their media are not included.

## Google Voice

### Where are my call recordings?

Google Voice exports typically only include media files from text conversations. Call recordings and voicemails are not usually included unless they were shared as messages.

### What are the phone number formats in filenames?

Filenames include the phone number as it appears in the export, typically in E.164 format (e.g., `+1234567890`).

## Instagram

### Some conversation media is missing. Why?

Instagram only includes media that hasn't expired. Photos and videos sent with "View Once" or that expired from temporary messages won't be in the export.

### How do I know which export format I have?

Instagram has changed export formats over time:
- **New format**: Contains `media/posts/YYYYMM/` directories
- **Messages**: Contains `your_instagram_activity/messages/inbox/`
- **Old format**: Photos/videos in root with UTC timestamps in filenames

Memoria automatically detects which format you have.

### Do I need to export in JSON or HTML format?

JSON format is recommended for best metadata extraction, but Memoria can work with both formats.

### Are Instagram Stories included?

Only if they were saved to your archive. Regular 24-hour stories that disappeared are not included in Instagram's export.

## Snapchat

### Can Memoria automatically download my Snapchat memories?

Not yet. Snapchat provides a large HTML file with links to download each memory individually. You must manually download all memories and organize them into the expected `media/` and `overlays/` structure before Memoria can process them.

Automation is planned but not yet implemented.

See: [Snapchat Export Guide](Snapchat-Export.md)

### Why are overlays embedded instead of kept separate?

Embedding overlays (text, drawings, stickers) into the media file ensures they're always displayed together and never get separated or lost. This creates a single, portable file that looks correct in any viewer.

### Do video overlays require re-encoding?

Yes. Embedding overlays in video files requires re-encoding with ffmpeg, which can take time for large videos. Image overlays are much faster.

### Some memories don't have overlays. Is this a problem?

No. Not all memories have overlays. Memoria processes the base media normally when no overlay is present.

### How does Snapchat overlay matching work?

Snapchat exports provide media files and overlay files (PNG images with text/drawings/stickers) separately without explicit links between them.

**For Snapchat Memories**: The export includes `metadata.json` which explicitly links each media file to its overlay file. Matching is straightforward.

**For Snapchat Messages**: No metadata links exist. Memoria matches overlays to media files by comparing **file modification timestamps**. If a video and overlay have the same timestamp, they're matched together.

**Why this matters**: File modification timestamps must be preserved from the original export. If timestamps are changed during processing, overlay matching fails and snaps lose their text/drawings/stickers.

**How Memoria handles it**: The preprocessor reads and stores ALL timestamps before any file operations, ensuring accurate matching even after files are copied.

### Why are some Snapchat messages in the "needs_matching" folder?

When a message contains multiple videos with multiple overlays all at the same timestamp, Memoria can't automatically determine which overlay belongs to which video. These ambiguous cases are saved to `needs_matching/` for manual review.

For example: A message sent at 10:30:45 containing 2 videos and 2 overlays - all timestamped 10:30:45 - creates 4 possible pairings. Only manual review of the content can determine the correct matches.

This is relatively rare - most messages contain only 1 video or don't have overlays.

## Immich Integration

### Do I need to create albums in Immich first?

No. Memoria automatically creates albums during upload using the naming scheme:
- `Google Photos/{username}`
- `Instagram/{username}/messages`
- `Snapchat/{username}/memories`
- etc.

### Can I customize album names?

Not currently. Album names are automatically generated based on platform, username, and content type.

### What if I want to upload to multiple Immich servers?

You'll need to run the upload process separately for each server. Use `--upload-only` mode with different `--immich-url` and `--immich-key` values.

### Does Immich detect duplicates?

Yes. Immich has its own deduplication system based on content hashing. If you upload the same file twice, Immich will recognize it as a duplicate.

### Why are uploads so slow in parallel mode?

This is intentional. When processing multiple exports in parallel (`--parallel-exports`), uploads happen sequentially to prevent network saturation and reduce server load. Processing continues in parallel, but uploads queue up one export at a time.

See: [Upload Queuing](Upload-Queuing.md)

## Timestamps and Dates

### Why are some of my photos dated to today instead of when they were taken?

This should not happen with Memoria's built-in processors. If you see this:
- Check if you're using a custom processor that might not handle timestamps correctly
- Verify the original export files had proper metadata or filesystem timestamps
- Report it as a bug if using official processors

Memoria's processors are specifically designed to read timestamps BEFORE any file operations to prevent this exact issue.

### What timestamp sources does Memoria use?

In order of preference:
1. Platform-specific JSON/metadata files (highest priority)
2. EXIF data already embedded in the file
3. Filesystem modification times (fallback for files lacking metadata)

All timestamps are extracted during preprocessing, before any file operations that could modify them.

### Can I adjust timestamps during processing?

No. Memoria preserves original timestamps exactly as they appear in the export. If you need to adjust timestamps:
- Use photo management software after processing
- Use exiftool directly to modify timestamps
- Or edit EXIF data in the output files after processing completes

## Performance

### How many workers should I use?

The default is `CPU cores - 1`, which works well for most cases. If you have fast storage (NVMe SSD) and large exports, you can experiment with higher values, but avoid over-subscribing your CPU.

See: [Parallel Processing](Parallel-Processing.md)

### Can I process multiple exports faster?

Yes. Use `--originals /path/to/exports --parallel-exports N` to process N exports simultaneously. Balance this with `--workers` to avoid over-subscribing your CPU.

Example for 16-core system:
- `--parallel-exports 2 --workers 7` (good)
- `--parallel-exports 4 --workers 3` (good)

### Processing is using too much RAM. What can I do?

Reduce parallelism:
- Lower `--workers` count
- Lower `--parallel-exports` count
- Process one export at a time

### My disk is slow. Will parallel processing help?

Probably not. Parallel processing is best for fast NVMe SSDs. On HDDs or network storage, parallel I/O can cause thrashing and actually slow things down. Use sequential processing with moderate `--workers`.

## Troubleshooting

### "exiftool is not installed"

Install exiftool for your platform:
- **macOS**: `brew install exiftool`
- **Linux**: `sudo apt-get install libimage-exiftool-perl`
- **Windows**: Download from https://exiftool.org/

### "ffmpeg not found"

Install ffmpeg:
- **macOS**: `brew install ffmpeg`
- **Linux**: `sudo apt-get install ffmpeg`
- **Windows**: Download from https://ffmpeg.org/

### "No processors matched input directory"

Possible causes:
- Directory structure doesn't match any supported platform format
- Export is incomplete or corrupted
- Directory naming doesn't follow conventions

Solutions:
- Run `./memoria.py --list-processors` to see available processors
- Check platform-specific guides for required directory structure
- Verify export was fully extracted

### Import errors when running memoria.py

Try installing in development mode:
```bash
pip install -e .
```

This makes the `common` module and processors properly available.

### Processing completed but output directory is empty

Check:
- Did processing actually succeed? Look for error messages
- Are you looking in the correct output directory?
- Did you use `--skip-upload` when you meant to process files?

### Immich upload fails with "unauthorized"

- Verify your API key is correct
- Check that `IMMICH_INSTANCE_URL` includes `/api` at the end
- Ensure your Immich server is accessible from your machine

### "Too many open files" error

Increase system limits:
```bash
ulimit -n 4096
```

This is common when processing very large exports with high parallelism.

## Related Documentation

- [Common Gotchas](Common-Gotchas.md) - Important behaviors to know
- [Getting Started](Getting-Started.md) - Installation and setup
- [Usage](Usage.md) - Complete command-line reference
- Platform guides: [Google](Google-Export.md), [Instagram](Instagram-Export.md), [Snapchat](Snapchat-Export.md)

