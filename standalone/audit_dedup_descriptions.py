#!/usr/bin/env python3
"""
Audit processed media files for cross-processor dedup description format.

This script scans a processed output directory (typically /mnt/media/processed),
extracts description-like metadata tags via exiftool, and reports files whose
description matches the cross-processor dedup format:

  - A primary line starting with: "Source: ..."
  - One or more secondary provenance lines starting with: "+<Platform>/..."

Why exiftool?
  - It reads ImageDescription/Comment/Description across image/video formats.
  - It mirrors what we write in common/media/exiftool.py.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple


PLATFORMS = ("Google", "Instagram", "Snapchat", "Discord", "iMessage")

# Primary source line; allow anywhere in the string.
SOURCE_RE = re.compile(r"(^|\n|\r)\s*Source:\s+\S+", re.MULTILINE)

# Secondary sources must be on their own line (exiftool typically preserves newlines).
SECONDARY_RE = re.compile(
    rf"(^|\n|\r)\s*\+(?:{'|'.join(re.escape(p) for p in PLATFORMS)})"
    r"(?:/[^:\s]+){0,2}:",
    re.MULTILINE,
)


DESCRIPTION_TAGS: Tuple[str, ...] = (
    "ImageDescription",
    "IPTC:Caption-Abstract",
    "XMP:Description",
    "XMP:Caption",
    "Comment",
    "Description",
)


DEFAULT_EXTENSIONS = {
    # Images
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
    ".dng",
    ".avif",
    # Videos
    ".mp4",
    ".mov",
    ".m4v",
    ".avi",
}


@dataclass(frozen=True)
class MatchResult:
    file_path: Path
    matched: bool
    primary_found: bool
    secondary_found: bool
    tag_used: Optional[str]
    description_excerpt: str


def _chunked(items: Sequence[str], chunk_size: int) -> Iterator[List[str]]:
    for i in range(0, len(items), chunk_size):
        yield list(items[i : i + chunk_size])


def _collect_files(
    base: Path,
    extensions: Optional[set[str]],
    limit: Optional[int],
) -> List[Path]:
    if base.is_file():
        return [base]

    found: List[Path] = []
    for p in base.rglob("*"):
        if not p.is_file():
            continue
        if extensions is not None and p.suffix.lower() not in extensions:
            continue
        found.append(p)
        if limit is not None and len(found) >= limit:
            break

    # Stable order helps reproducibility
    return sorted(found)


def _run_exiftool_json(file_paths: Sequence[Path]) -> List[Dict[str, Any]]:
    # -json output: list of dicts, one per file.
    # -s gives short tag names; we request both generic and qualified tags.
    cmd = ["exiftool", "-json", "-s"]
    for tag in DESCRIPTION_TAGS:
        cmd.append(f"-{tag}")
    cmd.extend([str(p) for p in file_paths])

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(
            f"exiftool failed (exit {proc.returncode}). stderr:\n{proc.stderr[:2000]}"
        )

    try:
        data = json.loads(proc.stdout) if proc.stdout.strip() else []
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Failed to parse exiftool JSON. stderr:\n{proc.stderr[:2000]}"
        ) from e

    if not isinstance(data, list):
        raise RuntimeError(f"Unexpected exiftool JSON shape: {type(data)}")
    return data


def _extract_best_description(item: Dict[str, Any]) -> Tuple[Optional[str], str]:
    # Prefer ImageDescription (our primary target), then other likely fields.
    for tag in DESCRIPTION_TAGS:
        value = item.get(tag)
        if isinstance(value, str) and value.strip():
            return tag, value
    return None, ""


def _matches_cross_processor_format(description: str) -> Tuple[bool, bool, bool]:
    if not description:
        return False, False, False
    primary = bool(SOURCE_RE.search(description))
    secondary = bool(SECONDARY_RE.search(description))
    return primary and secondary, primary, secondary


def _excerpt(text: str, max_chars: int = 240) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 3] + "..."


def audit(
    base: Path,
    *,
    extensions: Optional[set[str]],
    limit: Optional[int],
    chunk_size: int,
    workers: int,
) -> List[MatchResult]:
    files = _collect_files(base, extensions, limit)
    results: List[MatchResult] = []

    if not files:
        return []

    file_strs = [str(p) for p in files]
    chunks: List[List[str]] = list(_chunked(file_strs, chunk_size))

    # ExifTool is an external process; parallelizing chunks speeds up large scans.
    # Use threads (I/O bound; low overhead) rather than multiprocessing.
    effective_workers = max(1, int(workers))
    if effective_workers > len(chunks):
        effective_workers = len(chunks)

    def process_chunk(chunk_paths: List[str]) -> List[MatchResult]:
        items = _run_exiftool_json([Path(p) for p in chunk_paths])
        chunk_results: List[MatchResult] = []
        for item in items:
            source_file = item.get("SourceFile")
            if not isinstance(source_file, str) or not source_file:
                continue
            tag, desc = _extract_best_description(item)
            matched, primary, secondary = _matches_cross_processor_format(desc)
            chunk_results.append(
                MatchResult(
                    file_path=Path(source_file),
                    matched=matched,
                    primary_found=primary,
                    secondary_found=secondary,
                    tag_used=tag,
                    description_excerpt=_excerpt(desc),
                )
            )
        return chunk_results

    if effective_workers == 1:
        for chunk in chunks:
            results.extend(process_chunk(chunk))
    else:
        with ThreadPoolExecutor(max_workers=effective_workers) as executor:
            futures = [executor.submit(process_chunk, c) for c in chunks]
            for fut in as_completed(futures):
                results.extend(fut.result())

    # Provide stable ordering for reporting/reproducibility
    return sorted(results, key=lambda r: str(r.file_path))


def _parse_extensions(ext_csv: Optional[str]) -> Optional[set[str]]:
    if ext_csv is None:
        return set(DEFAULT_EXTENSIONS)
    if ext_csv.strip().lower() in {"*", "all"}:
        return None
    exts = set()
    for raw in ext_csv.split(","):
        raw = raw.strip()
        if not raw:
            continue
        if not raw.startswith("."):
            raw = "." + raw
        exts.add(raw.lower())
    return exts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan processed files for cross-processor dedup descriptions."
    )
    parser.add_argument(
        "path",
        nargs="?",
        default="/mnt/media/processed",
        help="Processed output directory (default: /mnt/media/processed)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of files to scan (default: unlimited)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=200,
        help="Files per exiftool invocation (default: 200)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel exiftool workers (default: 4)",
    )
    parser.add_argument(
        "--extensions",
        type=str,
        default=None,
        help=(
            "Comma-separated extensions to include (e.g. .jpg,.png,.mp4). "
            "Default: common image/video formats. Use '*' to scan all files."
        ),
    )
    parser.add_argument(
        "--out-json",
        type=str,
        default=None,
        help="Optional path to write full results JSON report",
    )
    parser.add_argument(
        "--print-samples",
        type=int,
        default=25,
        help="How many matching files to print (default: 25)",
    )

    args = parser.parse_args()

    base = Path(args.path)
    if not base.exists():
        raise SystemExit(f"Path does not exist: {base}")

    exts = _parse_extensions(args.extensions)

    results = audit(
        base,
        extensions=exts,
        limit=args.limit,
        chunk_size=args.chunk_size,
        workers=args.workers,
    )

    scanned = len(results)
    with_any_desc = sum(1 for r in results if r.tag_used and r.description_excerpt)
    matched = [r for r in results if r.matched]

    print(f"Scanned: {scanned} file(s)")
    print(f"With any description field: {with_any_desc}")
    print(f"Matched cross-processor dedup format: {len(matched)}")

    if matched:
        print("\nSample matches:")
        for r in matched[: max(0, int(args.print_samples))]:
            print(f"- {r.file_path} [{r.tag_used}] {r.description_excerpt}")

    if args.out_json:
        out_path = Path(args.out_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "base": str(base),
            "scanned": scanned,
            "matched": len(matched),
            "results": [
                {
                    "file_path": str(r.file_path),
                    "matched": r.matched,
                    "primary_found": r.primary_found,
                    "secondary_found": r.secondary_found,
                    "tag_used": r.tag_used,
                    "description_excerpt": r.description_excerpt,
                }
                for r in results
            ],
        }
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nWrote report: {out_path}")

    # Exit code 0 if any matches found, 2 if none found (useful for CI checks)
    return 0 if matched else 2


if __name__ == "__main__":
    raise SystemExit(main())
