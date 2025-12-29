#!/usr/bin/env python3
"""
Find Google Photos metadata files that have metadata but lack timestamps.

This script identifies cases where:
- A JSON sidecar file exists (media is NOT orphaned)
- But photoTakenTime.timestamp and creationTime.timestamp are both missing/empty

These files will cause a hard-fail (InvariantViolation) during processing because
FilenameGenerator requires a valid timestamp. This script helps identify such cases
before running the processor.
"""

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class MetadataResult:
    """Result of checking a single metadata file."""

    path: str
    has_timestamp: bool
    photo_taken_time: Optional[str] = None
    creation_time: Optional[str] = None
    # Other metadata that IS present
    has_title: bool = False
    has_description: bool = False
    has_gps: bool = False
    has_people: bool = False
    has_url: bool = False
    title: str = ""
    description: str = ""
    people: list = None

    def __post_init__(self):
        if self.people is None:
            self.people = []


def check_metadata_file(json_path: str) -> Optional[MetadataResult]:
    """Check a single metadata file for timestamp presence."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Skip non-media metadata files
        if "title" not in data:
            return None

        # Extract timestamps
        photo_taken = data.get("photoTakenTime", {})
        photo_ts = photo_taken.get("timestamp") if photo_taken else None

        creation = data.get("creationTime", {})
        creation_ts = creation.get("timestamp") if creation else None

        has_timestamp = bool(photo_ts or creation_ts)

        # Check for other metadata
        title = data.get("title", "")
        description = data.get("description", "")

        geo = data.get("geoData") or data.get("geoDataExif") or {}
        has_gps = bool(geo.get("latitude", 0) or geo.get("longitude", 0))

        people = data.get("people", [])
        people_names = [p.get("name", "") for p in people if p.get("name")]

        url = data.get("url", "")

        return MetadataResult(
            path=json_path,
            has_timestamp=has_timestamp,
            photo_taken_time=photo_taken.get("formatted") if photo_ts else None,
            creation_time=creation.get("formatted") if creation_ts else None,
            has_title=bool(title),
            has_description=bool(description),
            has_gps=has_gps,
            has_people=bool(people_names),
            has_url=bool(url),
            title=title,
            description=description[:100] if description else "",
            people=people_names,
        )

    except (json.JSONDecodeError, IOError, KeyError) as e:
        return None


def find_json_files(base_dirs: list[str]) -> list[str]:
    """Find all JSON metadata files in Google Photos exports."""
    json_files = []

    # Files to skip (album metadata, not media metadata)
    skip_names = {
        "metadata.json",
        "print-subscriptions.json",
        "shared_album_comments.json",
        "user-generated-memory-titles.json",
    }

    for base_dir in base_dirs:
        base_path = Path(base_dir)
        photos_dir = base_path / "Google Photos"

        if not photos_dir.exists():
            print(f"Skipping {base_dir}: No Google Photos directory found")
            continue

        for json_path in photos_dir.rglob("*.json"):
            if json_path.name not in skip_names:
                json_files.append(str(json_path))

    return json_files


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Find Google Photos metadata files missing timestamps"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=[],
        help="Paths to Google exports (default: /mnt/media/originals/google-*)",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=os.cpu_count(),
        help="Number of parallel workers",
    )
    parser.add_argument(
        "-n",
        "--max-examples",
        type=int,
        default=20,
        help="Maximum number of examples to show",
    )
    args = parser.parse_args()

    # Find Google exports
    if args.paths:
        export_dirs = args.paths
    else:
        import glob

        export_dirs = sorted(glob.glob("/mnt/media/originals/google-*"))
        # Filter to directories only
        export_dirs = [d for d in export_dirs if os.path.isdir(d)]

    if not export_dirs:
        print("No Google export directories found!")
        sys.exit(1)

    print(f"Scanning {len(export_dirs)} Google export(s):")
    for d in export_dirs:
        print(f"  - {d}")
    print()

    # Find all JSON files
    print("Finding JSON metadata files...")
    json_files = find_json_files(export_dirs)
    print(f"Found {len(json_files):,} metadata files to check")
    print()

    # Process in parallel
    print(f"Processing with {args.workers} workers...")

    missing_timestamps = []
    with_timestamps = 0
    skipped = 0
    processed = 0

    with ProcessPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(check_metadata_file, path): path for path in json_files
        }

        for future in as_completed(futures):
            processed += 1
            if processed % 5000 == 0:
                print(
                    f"  Processed {processed:,}/{len(json_files):,} "
                    f"({100*processed/len(json_files):.1f}%)"
                )

            result = future.result()
            if result is None:
                skipped += 1
            elif result.has_timestamp:
                with_timestamps += 1
            else:
                missing_timestamps.append(result)

    print()
    print("=" * 70)
    print("RESULTS")
    print("=" * 70)
    print(f"Total metadata files checked: {len(json_files):,}")
    print(f"  - With timestamps:          {with_timestamps:,}")
    print(f"  - Missing timestamps:       {len(missing_timestamps):,}")
    print(f"  - Skipped (not media):      {skipped:,}")
    print()

    if missing_timestamps:
        print(f"EXAMPLES OF METADATA WITHOUT TIMESTAMPS (showing up to {args.max_examples}):")
        print("-" * 70)

        for i, result in enumerate(missing_timestamps[: args.max_examples]):
            print(f"\n[{i+1}] {result.path}")
            print(f"    Title: {result.title}")

            other_metadata = []
            if result.has_description:
                other_metadata.append(f"description='{result.description}...'")
            if result.has_gps:
                other_metadata.append("has GPS coordinates")
            if result.has_people:
                other_metadata.append(f"people={result.people}")
            if result.has_url:
                other_metadata.append("has URL")

            if other_metadata:
                print(f"    Other metadata: {', '.join(other_metadata)}")
            else:
                print("    Other metadata: (none besides title)")

        if len(missing_timestamps) > args.max_examples:
            print(
                f"\n... and {len(missing_timestamps) - args.max_examples} more files"
            )
    else:
        print("No metadata files found missing timestamps!")
        print(
            "This means all media files with metadata have photoTakenTime or creationTime."
        )


if __name__ == "__main__":
    main()

