# Upload Queuing in Parallel Mode

## Overview

When using `--parallel-exports` with Immich uploads enabled, upload queuing automatically ensures:

- **Processing happens in parallel** - Multiple exports processed simultaneously
- **Uploads happen sequentially** - One export uploads at a time
- **Concurrent operation** - Uploads begin as soon as first export completes

This maximizes efficiency while preventing network saturation and maintaining upload reliability.

## Architecture

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  Export 1   │     │  Export 2   │     │  Export 3   │
│  Processing │────▶│  Processing │────▶│  Processing │
└─────────────┘     └─────────────┘     └─────────────┘
      │                    │                    │
      │ (queues uploads)   │                    │
      ▼                    ▼                    ▼
┌───────────────────────────────────────────────────┐
│           Upload Queue (shared)                   │
└───────────────────────────────────────────────────┘
                      │
                      ▼
            ┌──────────────────┐
            │  Upload Worker   │
            │  (one at a time) │
            └──────────────────┘
```

### Components

1. **Processing Workers** (parallel) - Controlled by `--parallel-exports`
2. **Upload Queue** (shared) - `multiprocessing.Manager().Queue()` for process-safe sharing
3. **Upload Worker** (single) - Runs in main process, processes one export at a time

## Usage

Upload queuing is **automatically enabled** with `--parallel-exports`:

```bash
./memoria.py --originals /path/to/exports \
  -o /output \
  --parallel-exports 4 \
  --immich-url http://localhost:2283 \
  --immich-key YOUR_API_KEY
```

### Sequential Mode (No Queuing)

Without `--parallel-exports`, uploads happen immediately after each processor completes:

```bash
./memoria.py --originals /path/to/exports \
  -o /output \
  --immich-url http://localhost:2283 \
  --immich-key YOUR_API_KEY
```

## Benefits

1. **Prevents Network Saturation** - Consistent bandwidth usage
2. **Maintains Upload Reliability** - Fewer timeouts and connection errors
3. **Maximizes Efficiency** - Uploads overlap with processing
4. **Better Resource Management** - Minimal file handles

## Performance Impact

**Example:** 4 exports, each 10 min processing + 5 min upload

| Approach | Processing | Uploads | Total | Speedup |
|----------|-----------|---------|-------|---------|
| Sequential | 40 min | 20 min | 60 min | 1.0x |
| Parallel + queued | 10 min | 20 min | ~25 min | 2.4x |

Total time ≈ max(processing, uploads) + overhead

## Advanced Configuration

### Adjust Upload Concurrency

Control files uploaded simultaneously within each export:

```bash
./memoria.py --originals /exports -o /output \
  --parallel-exports 4 \
  --immich-concurrency 8  # More concurrent uploads per export
```

**Recommendation**: Keep between 4-8 for best balance

### Balance Processing and Uploads

If processing is faster than uploads:

```bash
# More parallel exports to keep queue fed
./memoria.py --originals /exports -o /output \
  --parallel-exports 6 --workers 2
```

If uploads are faster than processing:

```bash
# Fewer parallel exports with more workers each
./memoria.py --originals /exports -o /output \
  --parallel-exports 2 --workers 7
```

## Troubleshooting

### Uploads seem slow

This is intentional! Sequential uploads prevent network saturation and server overload.

To speed up:

1. Check network bandwidth
2. Increase `--immich-concurrency` (default: 4)
3. Verify Immich server performance

### Upload worker stuck

Check:

1. Immich server logs
2. Network connectivity
3. Disk space on Immich server

Upload worker has a 1-hour timeout.

### Want to disable upload queuing

Use sequential mode:

```bash
./memoria.py --originals /exports -o /output
```

Or skip uploads:

```bash
./memoria.py --originals /exports -o /output \
  --parallel-exports 4 --skip-upload
```

## Technical Details

- **Queue Implementation**: `multiprocessing.Manager().Queue()` for process and thread safety
- **Memory Usage**: ~100KB per export's tasks (only paths stored, not file data)
- **Error Handling**: Processing errors skip uploads; upload errors are logged but don't stop queue
- **Timeout**: 1-hour wait for queue completion

## Related Documentation

- [Parallel Processing](Parallel-Processing) - Parallel export processing
- [Immich Upload](Immich-Upload) - Immich upload configuration
- [Usage](Usage) - Command-line options

