# Memory Architecture Analysis

This document analyzes the memory usage patterns in Memoria's catalog phase and proposes solutions for OOM (Out of Memory) issues when processing large export collections.

## Problem Statement

When processing many exports with cross-processor deduplication enabled, the catalog phase can exhaust system memory, triggering the Linux OOM killer. This was observed when processing 15 exports with 22,000+ files on a system with 8GB RAM.

**Symptoms:**

- Process terminated with "Terminated" message
- Kernel logs show: `Out of memory: Killed process XXXXX (python)`
- Occurs during "Cataloging exports... [X/Y]" phase

## Current Architecture

### Data Flow (Non-Streaming)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MAIN PROCESS                                     │
│                                                                          │
│  catalogs = []  ◄───────────────────────────────────────────────────┐   │
│       │                                                              │   │
│       ▼                                                              │   │
│  ProcessPoolExecutor (up to 8 workers)                               │   │
│       │                                                              │   │
└───────┼──────────────────────────────────────────────────────────────┼───┘
        │                                                              │
        ▼                                                              │
┌───────────────┐  ┌───────────────┐       ┌───────────────┐          │
│   Worker 1    │  │   Worker 2    │  ...  │   Worker 8    │          │
│               │  │               │       │               │          │
│ catalog = {   │  │ catalog = {   │       │ catalog = {   │          │
│   files: [    │  │   files: [    │       │   files: [    │          │
│     {...},    │  │     {...},    │       │     {...},    │          │
│     {...},    │  │     {...},    │       │     {...},    │          │
│     ...       │  │     ...       │       │     ...       │          │
│   ]           │  │   ]           │       │   ]           │          │
│ }             │  │ }             │       │ }             │          │
│               │  │               │       │               │          │
│ return ───────┼──┼───────────────┼───────┼───────────────┼──────────┘
└───────────────┘  └───────────────┘       └───────────────┘
```

### Memory Accumulation Points

#### 1. Worker Process Memory

Each worker process:

- Loads the Python interpreter (~50MB baseline)
- Imports processor modules (~20MB)
- Builds catalog entries in memory
- Loads images for perceptual hashing (up to 36MB per 12MP image)

```python
# common/catalog.py:135-142
with ThreadPoolExecutor(max_workers=workers) as executor:
    futures = {executor.submit(process_file, fp): fp for fp in media_files}
    for future in futures_progress(...):
        result = future.result()
        if result:
            catalog["files"].append(result)  # Accumulates in memory
```

#### 2. Inter-Process Data Transfer

When a worker completes, its entire catalog is serialized (pickled) and sent to the main process:

```python
# memoria.py:184-239
def _build_catalog_worker(...) -> tuple[str, list[dict]]:
    catalogs = []
    for processor in dedup_processors:
        catalog = processor.build_catalog(...)
        catalogs.append(catalog)
    return export_dir.name, catalogs  # Entire list returned
```

#### 3. Main Process Accumulation

The main process collects all catalogs from all workers:

```python
# memoria.py:343-345
export_name, export_catalogs = future.result()
if export_catalogs:
    catalogs.extend(export_catalogs)  # All catalogs in memory
```

#### 4. Dedup Analysis Duplication

The dedup service creates another copy of all file entries:

```python
# common/dedup_service.py:122-128
all_files = []
for catalog in catalogs:
    for file_entry in catalog.get("files", []):
        file_entry["_processor"] = processor
        all_files.append(file_entry)  # Second copy
```

### Per-File Memory Cost

Each catalog entry contains:

| Field | Typical Size | Notes |
|-------|-------------|-------|
| `source_path` | ~100 bytes | Full filesystem path |
| `relative_path` | ~50 bytes | Path relative to export |
| `content_hash` | 16 bytes | xxHash64 hex string |
| `perceptual_hash` | 64 bytes | dhash 256-bit hex string |
| `file_size` | 8 bytes | Integer |
| `dimensions` | 16 bytes | (width, height) tuple |
| `timestamp` | ~25 bytes | ISO format string |
| `metadata` | ~200+ bytes | Processor-specific dict |
| **Total** | **~500 bytes** | Per file entry |

### Peak Memory Calculation

For a typical large processing run:

| Component | Files | Memory |
|-----------|-------|--------|
| 15 exports x ~1500 files avg | 22,500 | 11.25 MB (catalog entries) |
| Worker processes (8 x 70MB baseline) | - | 560 MB |
| Active image decoding (8 workers x 36MB) | - | 288 MB |
| FFmpeg/FFprobe subprocesses | ~16 active | 200 MB |
| Python object overhead (2-3x) | - | 700 MB |
| Duplicate in dedup analysis | 22,500 | 11.25 MB |
| **Peak Total** | | **~1.8 GB** |

However, the observed OOM at ~7GB suggests additional factors:

- Large video files being processed
- HEIC decoding memory spikes
- Memory fragmentation
- Swap pressure causing cascading failures

## Proposed Solution: Streaming Catalog Architecture

### Design Goals

1. **Bounded Memory**: Never hold more than one export's catalog in memory
2. **Incremental Processing**: Write results to disk as they complete
3. **Resume Support**: Allow interrupted runs to continue
4. **Backward Compatible**: Existing API unchanged

### Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MAIN PROCESS                                     │
│                                                                          │
│  catalog_db = SQLite("~/.memoria/catalogs.db")                          │
│       │                                                                  │
│       ▼                                                                  │
│  ProcessPoolExecutor (up to 8 workers)                                  │
│       │                                                                  │
└───────┼──────────────────────────────────────────────────────────────────┘
        │
        ▼
┌───────────────┐  ┌───────────────┐       ┌───────────────┐
│   Worker 1    │  │   Worker 2    │  ...  │   Worker 8    │
│               │  │               │       │               │
│ for file in   │  │ for file in   │       │ for file in   │
│   media_files │  │   media_files │       │   media_files │
│   entry = ... │  │   entry = ... │       │   entry = ... │
│   write_to_db │  │   write_to_db │       │   write_to_db │
│               │  │               │       │               │
│ return count  │  │ return count  │       │ return count  │
└───────────────┘  └───────────────┘       └───────────────┘
        │                  │                       │
        ▼                  ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     SQLite Database (WAL mode)                           │
│                                                                          │
│  catalog_entries: (id, export, processor, source_path, content_hash,    │
│                    perceptual_hash, file_size, dimensions, metadata)    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
        │
        ▼ (Phase 2: Dedup Analysis)
┌─────────────────────────────────────────────────────────────────────────┐
│  SELECT content_hash, COUNT(*) FROM catalog_entries                      │
│  GROUP BY content_hash HAVING COUNT(*) > 1                               │
│                                                                          │
│  -- Process duplicates in batches of 1000                                │
│  -- Write manifest entries incrementally                                 │
└─────────────────────────────────────────────────────────────────────────┘
```

### Implementation Changes

#### 1. Streaming Catalog Writer

```python
# common/catalog_db.py (new file)
class CatalogDatabase:
    """SQLite-backed catalog storage for streaming writes."""
    
    def __init__(self, db_path: Path):
        self.conn = sqlite3.connect(db_path, isolation_level=None)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
    
    def write_entry(self, export_name: str, entry: Dict[str, Any]) -> None:
        """Write a single catalog entry (called from worker)."""
        self.conn.execute("""
            INSERT OR REPLACE INTO catalog_entries 
            (export, processor, source_path, content_hash, perceptual_hash,
             file_size, dimensions, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (...))
    
    def iter_duplicates(self, batch_size: int = 1000):
        """Iterate over duplicate groups without loading all into memory."""
        cursor = self.conn.execute("""
            SELECT content_hash FROM catalog_entries
            GROUP BY content_hash HAVING COUNT(*) > 1
        """)
        while batch := cursor.fetchmany(batch_size):
            yield batch
```

#### 2. Modified Worker Function

```python
# memoria.py
def _build_catalog_worker_streaming(
    export_dir_str: str,
    workers: int,
    db_path: str,
) -> tuple[str, int]:
    """Build catalog with streaming writes to shared database."""
    
    db = CatalogDatabase(db_path)
    export_dir = Path(export_dir_str)
    file_count = 0
    
    for processor in dedup_processors:
        for entry in processor.build_catalog_streaming(str(export_dir), workers):
            db.write_entry(export_dir.name, entry)
            file_count += 1
    
    return export_dir.name, file_count  # Only count returned, not data
```

#### 3. Generator-Based Catalog Building

```python
# common/catalog.py
def catalog_media_files_streaming(
    processor_name: str,
    input_path: Path,
    media_files: List[Path],
    workers: int = 4,
) -> Iterator[Dict[str, Any]]:
    """Yield catalog entries one at a time (streaming)."""
    
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(process_file, fp): fp for fp in media_files}
        for future in as_completed(futures):
            result = future.result()
            if result:
                yield result  # Yield instead of accumulate
```

### Memory Comparison

| Scenario | Current | Streaming |
|----------|---------|-----------|
| 15 exports, 22k files | ~1.8 GB peak | ~200 MB peak |
| 50 exports, 100k files | ~7+ GB (OOM) | ~250 MB peak |
| Worker memory | Full catalog | Single entry |
| Main process | All catalogs | Database handle |

### Trade-offs

| Aspect | Current | Streaming |
|--------|---------|-----------|
| Speed | Faster (all in memory) | Slightly slower (disk I/O) |
| Memory | O(total files) | O(batch size) |
| Complexity | Simple | More complex |
| Resume support | None | Built-in |
| Debugging | Easy (inspect lists) | Requires DB queries |

## Related Documentation

- [Performance-Analysis.md](Performance-Analysis.md) - CPU and I/O bottlenecks
- [Global-Deduplication.md](Global-Deduplication.md) - Cross-processor dedup overview
- [DRY-Violations.md](DRY-Violations.md) - Code duplication analysis

## Appendix: Diagnosing OOM Issues

### Check Kernel Logs

```bash
# View recent OOM kills
journalctl -k --since "1 hour ago" | grep -i "out of memory"

# Get details about killed process
journalctl -k | grep -A5 "oom-kill"
```

### Monitor Memory During Processing

```bash
# In a separate terminal
watch -n 1 'free -h; echo "---"; ps aux --sort=-%mem | head -10'
```

### Profile Memory Usage

```python
# Add to memoria.py for debugging
import tracemalloc
tracemalloc.start()

# ... after catalog phase ...
current, peak = tracemalloc.get_traced_memory()
print(f"Current: {current / 1024 / 1024:.1f} MB")
print(f"Peak: {peak / 1024 / 1024:.1f} MB")
tracemalloc.stop()
```
