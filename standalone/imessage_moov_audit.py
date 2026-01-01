#!/usr/bin/env python3
"""Audit iMessage MOV corruption at the *source export* using log basenames.

Goal: avoid N*(find+ffprobe) slowness.

Approach:
- Parse unique basenames from a run log like: "FFprobe failed for imessage-m3_mbp-20251202/<NAME>: ... moov atom not found"
- Single-pass walk the export to build {lower(basename): full_path}
- For each logged basename:
  - Read a small header window and look for 'ftyp' (QuickTime/ISO base marker)
  - Read a tail window and look for 'moov' atom near the end
  - Optionally run ffprobe for a small sample (sanity check only)

This is a heuristic, but it's fast and strongly correlates with ffprobe failures.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


LOG_BASENAME_RE = re.compile(r"imessage-m3_mbp-20251202/([^:]+):")


@dataclass(frozen=True)
class CheckResult:
    name: str
    path: Path | None
    size: int | None
    header_has_ftyp: bool | None
    tail_has_moov: bool | None


def extract_basenames(log_path: Path) -> list[str]:
    text = log_path.read_text(errors="replace")
    names = sorted(set(LOG_BASENAME_RE.findall(text)))
    return names


def index_mov_files(export_root: Path) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for root, _dirs, files in os.walk(export_root):
        for fn in files:
            if fn.lower().endswith(".mov"):
                # First hit wins; collisions are rare but possible.
                index.setdefault(fn.lower(), Path(root) / fn)
    return index


def read_window(path: Path, *, start: int, length: int) -> bytes:
    with path.open("rb") as f:
        f.seek(start)
        return f.read(length)


def analyze_file(
    path: Path, *, header_bytes: int, tail_bytes: int
) -> tuple[int, bool, bool]:
    size = path.stat().st_size

    header = read_window(path, start=0, length=min(header_bytes, size))
    header_has_ftyp = b"ftyp" in header

    if size <= 0:
        return size, header_has_ftyp, False

    tail_len = min(tail_bytes, size)
    tail_start = max(0, size - tail_len)
    tail = read_window(path, start=tail_start, length=tail_len)
    tail_has_moov = b"moov" in tail

    return size, header_has_ftyp, tail_has_moov


def ffprobe_ok(path: Path) -> bool:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--log",
        type=Path,
        default=Path(
            "/home/ethan/media-processing/logs/media_processor_20251227_184304.log"
        ),
    )
    ap.add_argument(
        "--export-root",
        type=Path,
        default=Path("/mnt/media/originals/imessage-m3_mbp-20251202"),
    )
    ap.add_argument("--header-bytes", type=int, default=64 * 1024)
    ap.add_argument("--tail-bytes", type=int, default=256 * 1024)
    ap.add_argument("--ffprobe-sample", type=int, default=5)
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of basenames checked (0 = all)",
    )
    args = ap.parse_args()

    names = extract_basenames(args.log)
    if args.limit and args.limit > 0:
        names = names[: args.limit]

    print(f"Log basenames (unique): {len(names)}")
    print(f"Indexing export: {args.export_root} ...")
    index = index_mov_files(args.export_root)
    print(f"Indexed .mov files: {len(index)}")

    results: list[CheckResult] = []

    for name in names:
        p = index.get(name.lower())
        if not p:
            results.append(
                CheckResult(
                    name=name,
                    path=None,
                    size=None,
                    header_has_ftyp=None,
                    tail_has_moov=None,
                )
            )
            continue

        size, has_ftyp, has_moov = analyze_file(
            p, header_bytes=args.header_bytes, tail_bytes=args.tail_bytes
        )
        results.append(
            CheckResult(
                name=name,
                path=p,
                size=size,
                header_has_ftyp=has_ftyp,
                tail_has_moov=has_moov,
            )
        )

    found = [r for r in results if r.path is not None]
    missing = [r for r in results if r.path is None]

    no_ftyp = [r for r in found if r.header_has_ftyp is False]
    no_moov_tail = [r for r in found if r.tail_has_moov is False]

    print("\n=== Summary ===")
    print(f"Found in export: {len(found)}")
    print(f"Missing in export: {len(missing)}")
    print(f"Header missing 'ftyp': {len(no_ftyp)}")
    print(f"Tail missing 'moov' (last {args.tail_bytes} bytes): {len(no_moov_tail)}")

    print("\n=== Examples (first 5) header missing 'ftyp' ===")
    for r in no_ftyp[:5]:
        print(f"- {r.name}  size={r.size}  path={r.path}")

    print("\n=== Examples (first 5) tail missing 'moov' ===")
    for r in no_moov_tail[:5]:
        print(f"- {r.name}  size={r.size}  path={r.path}")

    # Sanity-check with ffprobe for a tiny sample (to avoid hours)
    sample_paths = [r.path for r in found[: args.ffprobe_sample] if r.path is not None]
    if sample_paths:
        ok = 0
        for p in sample_paths:
            if ffprobe_ok(p):
                ok += 1
        print("\n=== ffprobe sanity sample ===")
        print(
            f"Sample size: {len(sample_paths)}  ffprobe OK: {ok}  ffprobe FAIL: {len(sample_paths) - ok}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
