# Parallel Processing Guide

## Overview

The `memoria.py` script supports parallel processing when using the `--originals` flag, allowing you to process multiple export directories simultaneously for significant performance improvements.

**New in this version:** Automatic upload queuing ensures Immich uploads happen sequentially while processing continues in parallel. See [Upload Queuing](Upload-Queuing) for details.

## Usage

### Basic Parallel Processing

```bash
# Process 2 exports in parallel
./memoria.py --originals /path/to/exports -o /path/to/output --parallel-exports 2

# Process 4 exports in parallel
./memoria.py --originals /path/to/exports -o /path/to/output --parallel-exports 4
```

### With Worker Adjustment

```bash
# 2 exports with 7 workers each = 16 total processes (good for 16-core CPU)
./memoria.py --originals /path/to/exports -o /path/to/output --parallel-exports 2 --workers 7

# 4 exports with 3 workers each = 16 total processes (balanced)
./memoria.py --originals /path/to/exports -o /path/to/output --parallel-exports 4 --workers 3
```

## Performance Considerations

### CPU Core Allocation

The script automatically calculates total process count and warns if you're over-subscribing:

```
Total Processes = parallel_exports × (workers_per_export + 1)
```

**Example on 16-core system:**

- `--parallel-exports 2 --workers 7` → 2 × 8 = 16 processes ✓ Good
- `--parallel-exports 4 --workers 3` → 4 × 4 = 16 processes ✓ Good
- `--parallel-exports 4 --workers 15` → 4 × 16 = 64 processes ✗ Over-subscribed

### Over-Subscription Warnings

If you exceed CPU cores by 50%, the script will display:

```
WARNING: Running 4 exports with 15 workers each
         will create ~64 processes on 16 CPU cores
         This may cause performance degradation due to over-subscription.

SUGGESTION: Try --parallel-exports 4 --workers 3
            or reduce --parallel-exports to 1
```

### When to Use Parallel Processing

**Good use cases:**

- Processing multiple large export directories
- I/O-bound operations (file copying, EXIF reading/writing)
- System with fast NVMe SSD storage
- Plenty of available RAM

**When sequential is better:**

- Single export directory (use `--workers` for internal parallelism)
- Limited RAM (each process needs 100MB-1GB depending on export size)
- HDD storage (parallel I/O can cause thrashing)
- Network storage (bandwidth limitations)

## Recommended Configurations

### Conservative (safest)

```bash
# 2 exports in parallel, moderate workers per export
./memoria.py --originals /exports -o /output --parallel-exports 2 --workers 7
```

### Balanced (recommended)

```bash
# 4 exports in parallel, fewer workers per export
./memoria.py --originals /exports -o /output --parallel-exports 4 --workers 3
```

### Aggressive (I/O-heavy workloads)

```bash
# 4 exports with 4 workers (mild over-subscription is OK for I/O bound work)
./memoria.py --originals /exports -o /output --parallel-exports 4 --workers 4
```

## Expected Performance Gains

**Example scenario:** Processing 5 exports, each taking 10 minutes sequentially

| Configuration | Total Time | Speedup |
|--------------|------------|---------|
| Sequential (default) | 50 minutes | 1.0x |
| `--parallel-exports 2` | ~25-28 minutes | 1.8-2.0x |
| `--parallel-exports 4` | ~15-18 minutes | 2.8-3.3x |
| `--parallel-exports 5` | ~12-15 minutes | 3.3-4.2x |

Actual speedup depends on:

- Disk I/O throughput
- CPU availability
- Memory bandwidth
- Export sizes and types
- Network speed (if uploading to Immich)

## Technical Details

### Implementation

- Uses `ProcessPoolExecutor` from `concurrent.futures`
- Each export runs in a separate process
- Processors are reloaded in each worker process
- Output is organized per-export to avoid conflicts
- Detection cache is disabled in parallel mode

### Memory Usage

- Each parallel export process: ~100MB-1GB base + export data
- Example: 4 parallel exports ≈ 0.4-4GB additional RAM

### Output Handling

- Each export gets its own subdirectory: `output/export-name/`
- Progress is printed as exports complete
- Final summary shows all results

## Troubleshooting

### Performance is worse with parallel processing

- Reduce `--parallel-exports` (try 2 instead of 4)
- Increase `--workers` per export
- Check disk I/O utilization (might be saturated)
- Check RAM usage (might be swapping)

### "Too many open files" error

Increase system limits:

```bash
ulimit -n 4096
```

### Interleaved output

This is normal with parallel processing. Each export's output is still grouped together, but multiple exports may print simultaneously.

### Out of memory

- Reduce `--parallel-exports`
- Reduce `--workers`
- Process exports sequentially

## Additional Examples

### Auto-calculate workers

```bash
# Script automatically uses (CPU_COUNT - 1) workers per export
./memoria.py --originals /exports -o /output --parallel-exports 2
```

### With Immich upload

```bash
# Parallel processing with automatic Immich uploads
./memoria.py --originals /exports -o /output \
  --parallel-exports 2 --workers 7 \
  --immich-url http://localhost:2283 \
  --immich-key YOUR_API_KEY
```

### Verbose logging

```bash
# Parallel processing with verbose logs
./memoria.py --originals /exports -o /output \
  --parallel-exports 2 --verbose
```

## Related Documentation

- [Usage](Usage) - Command-line options
- [Upload Queuing](Upload-Queuing) - Upload queuing for parallel processing
- [Logging](Logging) - Logging configuration

