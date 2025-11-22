# Common Gotchas

Important behaviors and surprises you should know before processing your exports.

## Directory Naming Requirements

**Gotcha**: Export folders must follow the `platform-username-YYYYMMDD` naming convention.

**Why it matters**: Platform exports don't include usernames in their internal structure. Without proper naming:
- Files will be labeled with "unknown" as the username
- Immich albums will be named incorrectly
- You'll have to manually rename thousands of files
- You can't distinguish between multiple accounts

**Example**: If you have exports from both your personal and work Instagram accounts, proper naming ensures they're processed into separate directories and uploaded to separate albums.

**What to do**: After extracting your export, rename the folder:
```
instagram-personal-20251122/
instagram-work-20251122/
google-john.doe-20251122/
snapchat-username-20251122/
```

## Album Folders Are Not Preserved

**Gotcha**: Google Photos album folders are flattened into a single directory per username.

**Why it matters**: If you expect your output to mirror the album folder structure from Google Photos, you'll be surprised to find all photos in one directory.

**The reason**: Google Photos exports contain 30-60% duplicates when the same photo appears in multiple albums. Flattening the structure allows Memoria to:
- Deduplicate effectively and save disk space
- Process faster (skip duplicate file operations)
- Upload cleaner data to Immich
- Avoid confusion about which copy is "canonical"

**What you get instead**: Album names are preserved in EXIF metadata fields (`ImageDescription`, `IPTC:Caption-Abstract`) and used to create Immich albums during upload. The information isn't lost, just stored differently.

**Related**: See [Deduplication](Deduplication.md) and [Design Decisions](Design-Decisions.md)

## Google Photos Deduplication Cannot Be Disabled

**Gotcha**: You cannot disable deduplication for Google Photos processing.

**Why it matters**: If you specifically want duplicate copies preserved in separate album folders, Memoria is not the right tool.

**The reason**: Deduplication is a core design principle. Processing duplicates would:
- Waste disk space (often 30-60% of export size)
- Slow processing by 2-3x
- Upload duplicate files to Immich
- Create confusion in photo management systems

**What to do**: If you absolutely need duplicates preserved, you'll need to use a different tool or manually organize your export.

## Processing Is Permanent

**Gotcha**: Once EXIF metadata is embedded in a file, it can only be changed with specialized tools.

**Why it matters**: If you make a mistake (wrong username, incorrect timezone, etc.), you can't easily undo the metadata embedding.

**What to do**:
- Test with a small subset of your export first
- Verify the output looks correct before processing your entire library
- Keep your original exports as backups
- Use `exiftool` if you need to modify embedded metadata later

## Large Exports Take Significant Time

**Gotcha**: Processing 10,000+ files can take several hours, even on fast hardware.

**Why it matters**: You might start processing thinking it will take 20 minutes and discover it's still running 3 hours later.

**The reason**: Memoria performs several operations per file:
- Copying the file (I/O bound)
- Parsing JSON metadata
- Embedding EXIF data (exiftool subprocess)
- Setting file timestamps
- Computing hashes for deduplication (Google Photos)

**What to do**:
- Use `--verbose` to monitor progress
- Use `--workers` to parallelize (default is CPU count - 1)
- Run processing overnight for very large exports
- Consider processing in batches if time-constrained

## Snapchat Memories Require Manual Download

**Gotcha**: Snapchat provides an HTML file with links to download memories. Memoria doesn't automate this download.

**Why it matters**: If you have 1,000 memories, you need to manually click and download each one before Memoria can process them.

**The reason**: Automation of the download process is planned but not yet implemented.

**What to do**:
- Be prepared for tedious manual work
- Consider whether the effort is worth it for your use case
- Organize downloaded files into the expected `media/` and `overlays/` structure
- See [Snapchat Export Guide](Snapchat-Export.md) for structure requirements

## Instagram Message Media May Be Incomplete

**Gotcha**: Instagram exports don't include media that has expired or was sent as "View Once".

**Why it matters**: You might expect all conversation media to be in the export and find gaps.

**The reason**: This is a limitation of Instagram's export system, not Memoria.

**What to do**: Accept that some ephemeral content is lost. Instagram only exports media that still exists in their system.

## Immich Upload Is Enabled by Default

**Gotcha**: If you have Immich configured, Memoria will automatically upload processed files unless you explicitly skip it.

**Why it matters**: You might accidentally upload test runs or incomplete processing to your Immich server.

**What to do**:
- Use `--skip-upload` for test runs
- Or don't configure Immich credentials until you're ready to upload
- Use `--upload-only` mode to re-upload later without reprocessing

## Parallel Processing Can Over-Subscribe Your System

**Gotcha**: Using `--parallel-exports 4 --workers 15` on an 8-core system will create ~64 competing processes.

**Why it matters**: Over-subscription causes context switching overhead, making processing slower than sequential mode.

**What to do**:
- Follow the formula: `parallel_exports × (workers + 1) ≈ CPU cores`
- Memoria warns you when you exceed cores by 50%
- See [Parallel Processing](Parallel-Processing.md) for optimal configurations

## Original Exports Are Never Modified

**Gotcha**: This is actually good news, but worth emphasizing.

**Why it matters**: Some users worry that Memoria will alter or delete their original export files.

**The truth**: Memoria only reads from the original export and writes to the output directory. Your original export remains completely untouched. All operations work on copies.

**What to do**: Keep your original exports as backups, but rest assured Memoria won't harm them.

## Filesystem Timestamps Can Be Fragile

**Gotcha**: Some platforms don't include proper metadata files, requiring processors to rely on filesystem modification times. These timestamps can be accidentally updated during processing if not handled carefully.

**Why it matters**: If a processor needs to read a file's modification time (because no JSON metadata exists), but the file gets copied or modified first, the timestamp will change to "now" instead of preserving the original date. This results in photos being dated to the processing date rather than the capture date.

**When this happens**:
- Instagram Old Format exports use UTC timestamps in filenames but also rely on file modification times as fallback
- Some Google Photos items without JSON metadata use EXIF data and file times
- **Snapchat Messages** - CRITICAL: Uses file modification timestamps to match overlay files to media files (explained below)
- Any platform where metadata is missing or incomplete

**Snapchat Messages - Critical Case**:

Snapchat Messages has the most critical dependency on filesystem timestamps. The export provides media files (photos/videos) and overlay files (PNG with text/drawings/stickers) as separate files with no explicit links between them. The **only** way to determine which overlay belongs to which media file is by comparing their modification timestamps.

What would happen if timestamps were corrupted:
1. Original: `video.mp4` (timestamp: Jan 15 10:30:45), `overlay.png` (timestamp: Jan 15 10:30:45) → Matched ✓
2. After corrupt copying: `video.mp4` (timestamp: Nov 22 15:00:01), `overlay.png` (timestamp: Nov 22 15:00:05) → Different by 4 seconds → No match ✗
3. Result: Video processed without overlay, overlay never applied, your snap's text/drawings/stickers are lost

**How Memoria handles it**: Processors are designed to read timestamps BEFORE any file operations occur. The preprocessing step extracts all metadata (including filesystem times) before copying files. For Snapchat Messages specifically, ALL timestamps are read and stored during preprocessing, then overlay matching happens using the stored timestamps, ensuring accuracy even after files are copied.

**What you should know**: This is handled internally by Memoria's processors, but if you're developing custom processors, you must read filesystem timestamps before copying or modifying files.

**Related**: See [Adding Processors](Adding-Processors.md) for developer guidelines on timestamp handling.

## Related Documentation

- [Design Decisions](Design-Decisions.md) - Rationale for these behaviors
- [FAQ](FAQ.md) - Common questions and answers
- [Getting Started](Getting-Started.md) - Setup and preparation guide

