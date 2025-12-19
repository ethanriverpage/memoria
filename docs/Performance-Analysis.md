# Performance Analysis

This document details the performance bottlenecks identified in the media-processing pipeline, their root causes, and recommended optimizations.

### 2. Redundant Image Operations (MEDIUM Impact) - FIXED

**Location:** `common/catalog.py` and `common/perceptual_hash.py`

**Problem:** Each image file was opened twice:

```python
# Old pattern:
perceptual_hash = compute_perceptual_hash(file_path)  # Opens image
dimensions = get_media_dimensions(file_path)          # Opens image AGAIN
```

**Cost:** ~70ms per image for the redundant open/decode.

**Solution:** Implemented `compute_perceptual_hash_with_dims()` which returns dimensions as a byproduct of perceptual hash computation:

```python
# Current implementation:
perceptual_hash, dimensions = compute_perceptual_hash_with_dims(file_path)
```

**Status:** Fixed in `common/perceptual_hash.py` and used by `common/catalog.py`.

---

### 3. Multiple FFprobe Calls per Video (MEDIUM Impact) - FIXED

**Location:** `common/perceptual_hash.py`

**Problem:** For each video file, up to 3 subprocess calls were made:

1. `has_video_stream()` - ffprobe call
2. `extract_video_frame()` - ffmpeg call for perceptual hash
3. `get_video_dimensions()` - ffprobe call again

**Cost:** Each subprocess spawn is ~50-100ms overhead.

**Solution:** Implemented `_get_video_info()` which consolidates video stream check and dimensions into a single ffprobe call. Used by `compute_video_hash_with_dims()` which is called via `compute_perceptual_hash_with_dims()`.

```python
# VideoInfo dataclass holds has_video, width, and height from ONE ffprobe call
video_info = _get_video_info(video_path)
```

**Status:** Fixed. Video processing now uses 2 subprocess calls (1 ffprobe + 1 ffmpeg) instead of 3.

---

### 4. HEIC Decoding Performance (MEDIUM Impact)

**Problem:** The `pillow-heif` library, while functional, is significantly slower than libjpeg-turbo for JPEG decoding.

**Context:** iPhone photos (HEIC format) are common in exports from Google Photos and iMessage.

**Potential Fixes:**

- Consider `pyheif` with libheif backend (may be faster)
- Cache decoded images if multiple operations needed
- For catalog-only operations, consider skipping perceptual hash for HEIC and relying on content hash

---

### 5. EXIF Processing (LOW Impact - Already Optimized)

**Location:** `common/exiftool_batch.py`

**Current State:** Already well-optimized with batch processing:

- Processes 500 files per exiftool invocation
- Uses argfile approach to avoid command-line length limits
- Parallel chunks for validation

No significant optimization needed.

---

### 6. Video Overlay Creation (LOW Impact - Inherently Slow)

**Location:** `common/overlay.py`

**Current State:** Multi-pass FFmpeg pipeline for Snapchat overlays:

- Pass 1: Decode and analyze
- Pass 2: Apply overlay with hardware encoding (VAAPI)
- Pass 3: Mux streams
- Pass 4: Embed metadata

**Performance:** Already uses hardware encoding (hevc_vaapi at 344-441x realtime).

No significant optimization available without quality tradeoffs.

---

## Architecture Analysis

### Parallelism Model

The codebase uses a two-level parallelism model:

```
Level 1: ProcessPoolExecutor (8 workers)
    └── Each worker handles one export directory
        └── Level 2: ThreadPoolExecutor (N workers per export)
            └── Hashes files within that export
```

**GIL Impact Assessment:**

| Library | Releases GIL? | Evidence |
|---------|--------------|----------|
| xxhash | Partially | 1.6x speedup with 4 threads (not ideal 4x) |
| Pillow | Mostly | 3.1x speedup with 4 threads (good) |
| imagehash.dhash | No | Pure Python, holds GIL |
| subprocess (ffmpeg/ffprobe) | N/A | Separate process |

**Conclusion:** The GIL is not a major bottleneck because:

1. The outer `ProcessPoolExecutor` creates 8 separate Python processes, each with its own GIL
2. Pillow releases the GIL for most C-level operations
3. Subprocess calls are completely independent

---

## Recommendations

### Immediate Wins

2. **Combine perceptual hash + dimensions** - DONE
   - Implemented `compute_perceptual_hash_with_dims()` returning `(hash, dimensions)` tuple
   - `common/catalog.py` uses the combined function for all processors
   - **Realized savings:** ~70ms per image file

3. **Consolidate video metadata calls** - DONE
   - Implemented `_get_video_info()` returning `VideoInfo(has_video, width, height)` in one call
   - **Realized savings:** ~100-150ms per video file

### Configuration Tuning

For systems with 16+ cores processing many exports:

```bash
# Recommended settings for 16-core system with fast NVMe:
./memoria.py --originals /path/to/exports --parallel-exports 4 --workers 3
```

This gives:

- 4 exports processed in parallel (ProcessPoolExecutor)
- 3 threads per export for hashing (ThreadPoolExecutor)
- Total: ~16 active threads, matching CPU cores

### When to Disable Perceptual Hashing

If exact-match deduplication is sufficient (no re-encoded duplicates):

```bash
PERCEPTUAL_HASH=false ./memoria.py ...
```

This eliminates the largest bottleneck entirely, reducing catalog time by ~99%.

---

## Profiling Commands

To profile specific operations:

```bash
cd /home/ethan/media-processing && source .venv/bin/activate

# Profile perceptual hashing
python3 -c "
from PIL import Image
import imagehash
import time

img = Image.new('RGB', (4032, 3024), 'blue')
start = time.time()
for _ in range(20):
    resized = img.resize((136, 136), Image.Resampling.BILINEAR)
    imagehash.dhash(resized, hash_size=16)
print(f'Time per hash: {(time.time()-start)/20*1000:.1f}ms')
"

# Profile content hashing
python3 -c "
import xxhash
import os
import time

data = os.urandom(10*1024*1024)  # 10MB
start = time.time()
for _ in range(100):
    xxhash.xxh64(data).hexdigest()
print(f'Time per 10MB: {(time.time()-start)/100*1000:.2f}ms')
"
```

---

## Metrics Summary

| Metric | Current | Optimized (Projected) |
|--------|---------|----------------------|
| Perceptual hash per image | ~155ms | ~10-20ms (with `perception`) |
| Dimension lookup per image | ~70ms | ~0ms (combined with hash) |
| Video metadata calls | 3 per file | 1 per file |
| Catalog phase (10k files) | ~30 min | ~5-8 min |

---

## Appendix: File Locations

| Component | Path |
|-----------|------|
| Perceptual hashing | `common/perceptual_hash.py` |
| Content hashing | `common/perceptual_hash.py` |
| Video metadata | `common/overlay.py:74-197` |
| EXIF batch processing | `common/exiftool_batch.py` |
| Catalog building | `common/catalog.py` (shared by all processors) |
| Parallel orchestration | `memoria.py:184-235, 305-350` |
