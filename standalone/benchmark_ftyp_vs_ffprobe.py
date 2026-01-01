#!/usr/bin/env python3
"""Benchmark placeholder detection: header 'ftyp' check vs ffprobe.

This is intentionally standalone-ish (no imports from the codebase required).

It samples MOV files from an iMessage export and compares:
- ftyp check: read first 64KB and search for b'ftyp'
- ffprobe check: run ffprobe once per file and only check return code

Outputs wall clock totals and per-file timings.
"""

from __future__ import annotations

import argparse
import os
import random
import statistics
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Timing:
    label: str
    total_s: float
    per_file_ms: list[float]


def iter_mov_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if fn.lower().endswith(".mov"):
                files.append(Path(dirpath) / fn)
    return files


def ftyp_check(path: Path, header_bytes: int) -> bool:
    with path.open("rb") as f:
        head = f.read(header_bytes)
    return b"ftyp" in head


def ffprobe_check(path: Path, timeout_s: float) -> bool:
    # Keep it minimal: return code only.
    # (Any ffprobe invocation has similar process startup + IO overhead.)
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        check=False,
        timeout=timeout_s,
    )
    return proc.returncode == 0


def bench(label: str, paths: list[Path], fn) -> Timing:
    per_ms: list[float] = []
    t0 = time.perf_counter()
    for p in paths:
        s0 = time.perf_counter()
        fn(p)
        per_ms.append((time.perf_counter() - s0) * 1000)
    total = time.perf_counter() - t0
    return Timing(label=label, total_s=total, per_file_ms=per_ms)


def summarize(t: Timing) -> str:
    ms = t.per_file_ms
    return (
        f"{t.label}: total={t.total_s:.3f}s for {len(ms)} files | "
        f"avg={statistics.mean(ms):.2f}ms p50={statistics.median(ms):.2f}ms "
        f"p95={statistics.quantiles(ms, n=20)[18]:.2f}ms"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--root",
        type=Path,
        default=Path("/mnt/media/originals/imessage-m3_mbp-20251202/Attachments"),
        help="Root directory to sample .mov/.MOV from",
    )
    ap.add_argument("--n", type=int, default=200, help="Number of files to sample")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--header-bytes", type=int, default=65536)
    ap.add_argument("--ffprobe-timeout", type=float, default=10.0)
    args = ap.parse_args()

    all_files = iter_mov_files(args.root)
    if not all_files:
        raise SystemExit(f"No .mov files found under: {args.root}")

    random.seed(args.seed)
    sample_n = min(args.n, len(all_files))
    sample = random.sample(all_files, sample_n)

    print(f"Found {len(all_files)} .mov files under {args.root}")
    print(f"Sample size: {len(sample)} (seed={args.seed})")

    # Warmup
    _ = ftyp_check(sample[0], args.header_bytes)

    t1 = bench(
        label=f"ftyp_check(read {args.header_bytes} bytes)",
        paths=sample,
        fn=lambda p: ftyp_check(p, args.header_bytes),
    )
    t2 = bench(
        label=f"ffprobe_check(timeout {args.ffprobe_timeout}s)",
        paths=sample,
        fn=lambda p: ffprobe_check(p, args.ffprobe_timeout),
    )

    # Also compute how many appear placeholder-like by ftyp heuristic
    placeholder_like = sum(1 for p in sample if not ftyp_check(p, args.header_bytes))

    print()
    print(summarize(t1))
    print(summarize(t2))
    ratio = (
        (statistics.mean(t2.per_file_ms) / statistics.mean(t1.per_file_ms))
        if statistics.mean(t1.per_file_ms) > 0
        else float("inf")
    )
    print(f"Speed ratio (avg ffprobe / avg ftyp): {ratio:.1f}x")
    print(f"ftyp-missing in sample: {placeholder_like}/{len(sample)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
