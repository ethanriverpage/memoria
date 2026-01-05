# Parallel Processing Guide

## Overview

The `memoria.py` script supports parallel processing when using the `--originals` flag, allowing you to process multiple export directories simultaneously for significant performance improvements.

**Key concepts:**

- **Threads** (`--threads`): Control within-export parallelism (how many threads work on a single export)
- **Parallel mode** (`--parallel`): Enable multi-export parallelism (process multiple exports simultaneously)
- **Process count**: Auto-calculated based on CPU cores and thread count, or override via `MEMORIA_PROCESSES`

**Automatic upload queuing** ensures Immich uploads happen sequentially while processing continues in parallel. See [Immich Upload](Immich-Upload#upload-queuing-in-parallel-mode) for details.

## Usage

### Basic Parallel Processing

```bash
# Enable parallel processing (auto-sizes based on CPU and threads)
./memoria.py --originals /path/to/exports -o /path/to/output --parallel
```

### With Thread Adjustment

```bash
# Parallel with custom thread count per export
./memoria.py --originals /path/to/exports -o /path/to/output --parallel --threads 4
```

### With Process Count Override

```bash
# Override auto-calculated process count via environment variable
MEMORIA_PROCESSES=4 ./memoria.py --originals /path/to/exports -o /path/to/output --parallel
```

## How Auto-Sizing Works

### Thread Count (`--threads`)

When `--threads` is not specified:

- **Single export mode**: `threads = CPU_COUNT - 1` (maximize throughput)
- **Parallel mode** (`--parallel`): `threads = sqrt(CPU_COUNT - 1)` (balance with processes)

### Process Count (parallel mode)

Auto-calculated as:

```
cpu_budget = CPU_COUNT - 1
auto_procs = cpu_budget // threads
procs = min(auto_procs, num_exports, 8)
```

The cap at 8 prevents excessive process creation on large export sets.

**Example on 16-core system with 4 exports:**

| Threads | Auto Processes | Total Concurrent Work |
|---------|----------------|----------------------|
| Auto (4) | 3 | 3 exports x 4 threads |
| 2 | 7 | 7 exports x 2 threads (capped at 4) |
| 8 | 1 | 1 export x 8 threads |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MEMORIA_THREADS` | Default thread count (overridden by `--threads`) | Auto |
| `MEMORIA_PARALLEL` | Enable parallel mode by default (`true`/`false`) | `false` |
| `MEMORIA_PROCESSES` | Override auto-calculated process count | Auto |

## When to Use Parallel Processing

**Good use cases:**

- Processing multiple large export directories
- I/O-bound operations (file copying, EXIF reading/writing)
- System with fast NVMe SSD storage
- Plenty of available RAM

**When sequential is better:**

- Single export directory (use `--threads` for internal parallelism)
- Limited RAM (each process needs 100MB-1GB depending on export size)
- HDD storage (parallel I/O can cause thrashing)
- Network storage (bandwidth limitations)

## Recommended Configurations

### Auto (simplest)

```bash
# Let Memoria auto-size everything
./memoria.py --originals /exports -o /output --parallel
```

### Custom Thread Count

```bash
# More threads per export, fewer parallel exports
./memoria.py --originals /exports -o /output --parallel --threads 8
```

### Override Process Count

```bash
# Force specific process count
MEMORIA_PROCESSES=2 ./memoria.py --originals /exports -o /output --parallel
```

## Expected Performance Gains

**Example scenario:** Processing 5 exports, each taking 10 minutes sequentially

| Configuration | Total Time | Speedup |
|--------------|------------|---------|
| Sequential (default) | 50 minutes | 1.0x |
| `--parallel` (auto) | ~15-20 minutes | 2.5-3.3x |
| `--parallel` + `MEMORIA_PROCESSES=4` | ~15-18 minutes | 2.8-3.3x |

Actual speedup depends on:

- Disk I/O throughput
- CPU availability
- Memory bandwidth
- Export sizes and types
- Network speed (if uploading to Immich)

## Cross-Processor Deduplication Mode

When `CROSS_PROCESSOR_DEDUP=true`, parallel processing works across all three execution phases:

| Phase | Description | Parallelism |
|-------|-------------|-------------|
| Phase 0 | Preprocessing | Parallel across exports |
| Phase 1 | Catalog building | Parallel across exports |
| Phase 2 | Dedup analysis | Global barrier (single-threaded) |
| Phase 3 | Processing | Parallel across exports |

**Key differences from normal parallel mode:**

- Phase 2 (dedup analysis) runs globally after all Phase 1 cataloging completes
- Output filenames include a content hash suffix (`-{hash[:8]}`) to ensure uniqueness across parallel workers
- `PARALLEL_DEDUP` (env var) independently controls perceptual hash comparisons within Phase 2

**Example:**

```bash
# Parallel dedup processing
CROSS_PROCESSOR_DEDUP=true ./memoria.py --originals /exports -o /output --parallel
```

## Technical Details

### Implementation

- Uses `ProcessPoolExecutor` from `concurrent.futures`
- Each export runs in a separate process
- Processors are reloaded in each worker process
- Output is organized per-export to avoid conflicts
- Detection cache is disabled in parallel mode
- In dedup mode, output filenames include content hash prefix for cross-process uniqueness

### Memory Usage

- Each parallel export process: ~100MB-1GB base + export data
- Example: 4 parallel exports ~ 0.4-4GB additional RAM

### Output Handling

- Each export gets its own subdirectory: `output/export-name/`
- Progress is printed as exports complete
- Final summary shows all results

## Troubleshooting

### Performance is worse with parallel processing

- Reduce `MEMORIA_PROCESSES` (try 2)
- Increase `--threads` per export
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

- Reduce `MEMORIA_PROCESSES`
- Reduce `--threads`
- Process exports sequentially (omit `--parallel`)

## Additional Examples

### With Immich upload

```bash
# Parallel processing with automatic Immich uploads
./memoria.py --originals /exports -o /output --parallel \
  --immich-url http://localhost:2283 \
  --immich-key YOUR_API_KEY
```

### Verbose logging

```bash
# Parallel processing with verbose logs
./memoria.py --originals /exports -o /output --parallel --verbose
```

### Environment-based configuration

```bash
# Set defaults in .env or environment
export MEMORIA_PARALLEL=true
export MEMORIA_PROCESSES=4
export MEMORIA_THREADS=4

# Then just run
./memoria.py --originals /exports -o /output
```

## Related Documentation

- [Usage](Usage) - Command-line options
- [Immich Upload](Immich-Upload) - Immich configuration and upload queuing
- [Logging](Logging) - Logging configuration
- [Deduplication](Deduplication) - Cross-processor deduplication details
