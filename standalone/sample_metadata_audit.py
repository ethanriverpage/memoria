#!/usr/bin/env python3
"""
Random Metadata Audit Script

Randomly samples processed media files and validates:
1. Completeness: presence of description, GPS, datetime
2. Accuracy: reasonable values for each field

Usage:
    python scripts/sample_metadata_audit.py [--samples N] [--path /path/to/processed]
"""

import argparse
import json
import os
import random
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Media extensions to sample
MEDIA_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".mp4",
    ".mov",
    ".avi",
    ".mkv",
    ".m4v",
    ".webm",
}


@dataclass
class MetadataResult:
    """Result of metadata extraction for a single file."""

    file_path: str
    platform: str
    service: str

    # DateTime fields
    has_datetime: bool = False
    datetime_value: Optional[str] = None
    datetime_issues: List[str] = field(default_factory=list)

    # GPS fields
    has_gps: bool = False
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    gps_issues: List[str] = field(default_factory=list)

    # Description fields
    has_description: bool = False
    description_value: Optional[str] = None
    description_issues: List[str] = field(default_factory=list)

    # Overall
    is_complete: bool = False
    is_accurate: bool = False


def collect_media_files(base_path: Path) -> Dict[str, List[Path]]:
    """Collect all media files organized by platform/service."""
    files_by_category: Dict[str, List[Path]] = defaultdict(list)

    for root, dirs, files in os.walk(base_path):
        # Skip issues/failed directories
        if "issues" in root or "failed" in root:
            continue

        for filename in files:
            if Path(filename).suffix.lower() in MEDIA_EXTENSIONS:
                file_path = Path(root) / filename

                # Extract category from path structure
                # e.g., /mnt/media/processed/Instagram/username/messages/file.jpg
                rel_path = file_path.relative_to(base_path)
                parts = rel_path.parts

                if len(parts) >= 2:
                    platform = parts[0]
                    # Try to get service if available
                    if len(parts) >= 3:
                        service = parts[2] if len(parts) > 3 else parts[1]
                    else:
                        service = "root"
                    category = f"{platform}/{service}"
                else:
                    category = "unknown"

                files_by_category[category].append(file_path)

    return files_by_category


def sample_files(
    files_by_category: Dict[str, List[Path]], total_samples: int
) -> List[Tuple[Path, str]]:
    """Sample files proportionally from each category."""
    all_files = []
    for category, files in files_by_category.items():
        for f in files:
            all_files.append((f, category))

    if len(all_files) <= total_samples:
        return all_files

    return random.sample(all_files, total_samples)


def read_metadata_exiftool(file_paths: List[Path]) -> Dict[str, Dict[str, Any]]:
    """Read comprehensive metadata from files using exiftool."""
    if not file_paths:
        return {}

    cmd = [
        "exiftool",
        "-json",
        "-s",  # Short tag names
        "-G1",  # Group names
        # DateTime fields
        "-DateTimeOriginal",
        "-CreateDate",
        "-ModifyDate",
        "-MediaCreateDate",
        "-TrackCreateDate",
        "-Matroska:DateTimeOriginal",
        "-Matroska:Date",
        # GPS fields
        "-GPSLatitude#",
        "-GPSLongitude#",
        "-GPSLatitudeRef",
        "-GPSLongitudeRef",
        "-GPSAltitude",
        "-GPSPosition",
        # Description fields (images)
        "-ImageDescription",
        "-Caption-Abstract",
        "-UserComment",
        "-XMP:Description",
        "-XMP-tiff:ImageDescription",
        "-XMP-dc:Description",
        "-XMP-exif:DateTimeOriginal",
        "-XMP-xmp:CreateDate",
        # Description fields (videos - QuickTime/MOV)
        "-QuickTime:Comment",
        "-QuickTime:Description",
        "-ItemList:Comment",
        "-ItemList:Description",
        # Description fields (videos - Matroska/MKV)
        "-Matroska:Comment",
        "-Matroska:Description",
        # Generic
        "-Comment",
        "-Description",
        # Additional context
        "-Make",
        "-Model",
        "-FileType",
        "-MIMEType",
    ]
    cmd.extend([str(p) for p in file_paths])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )

    if not result.stdout:
        return {}

    try:
        metadata_list = json.loads(result.stdout)
        return {item.get("SourceFile", ""): item for item in metadata_list}
    except json.JSONDecodeError:
        return {}


def validate_datetime(value: Optional[str]) -> Tuple[bool, List[str]]:
    """Validate datetime value for accuracy."""
    issues = []

    if not value or value == "-":
        return False, ["No datetime value"]

    # Check for invalid patterns
    if value.startswith("0000:00:00"):
        issues.append("Invalid zero date")
        return False, issues

    if value.startswith("1970:01:01"):
        issues.append("Suspicious epoch date (1970-01-01)")

    # Try to parse the date
    try:
        # Common EXIF format: "YYYY:MM:DD HH:MM:SS" or with timezone
        clean_value = value.split("+")[0].split("-")[0] if len(value) > 19 else value
        clean_value = clean_value.replace("Z", "").strip()

        # EXIF format uses colons in date: "2018:02:23 01:58:57"
        if len(clean_value) >= 10 and clean_value[4] == ":":
            # Parse EXIF format
            dt = datetime.strptime(clean_value[:19], "%Y:%m:%d %H:%M:%S")
        elif len(clean_value) >= 10 and clean_value[4] == "-":
            # ISO format: "2018-02-23T01:58:57"
            dt = datetime.fromisoformat(clean_value[:19].replace("T", " "))
        else:
            # Try generic parsing
            dt = datetime.strptime(clean_value[:19], "%Y:%m:%d %H:%M:%S")

        # Check for reasonable date range
        if dt.year < 1990:
            issues.append(f"Very old date: {dt.year}")
        if dt.year > datetime.now().year + 1:
            issues.append(f"Future date: {dt.year}")

    except (ValueError, TypeError) as e:
        issues.append(f"Parse error: {str(e)[:50]}")

    is_valid = len([i for i in issues if "Invalid" in i or "Parse error" in i]) == 0
    return is_valid, issues


def validate_gps(lat: Optional[float], lon: Optional[float]) -> Tuple[bool, List[str]]:
    """Validate GPS coordinates for accuracy."""
    issues = []

    if lat is None or lon is None:
        return False, ["No GPS coordinates"]

    # Check for zero coordinates (often means no real GPS)
    if lat == 0.0 and lon == 0.0:
        issues.append("Zero coordinates (0,0) - likely placeholder")
        return False, issues

    # Check valid ranges
    if not -90 <= lat <= 90:
        issues.append(f"Invalid latitude: {lat}")
        return False, issues

    if not -180 <= lon <= 180:
        issues.append(f"Invalid longitude: {lon}")
        return False, issues

    # Check for "null island" vicinity
    if abs(lat) < 0.1 and abs(lon) < 0.1:
        issues.append("Near null island - suspicious")

    return True, issues


def validate_description(value: Optional[str]) -> Tuple[bool, List[str]]:
    """Validate description for expected format."""
    issues = []

    if not value or value == "-":
        return False, ["No description"]

    # Check for expected format: "Source: Platform/username/service"
    if "Source:" not in value:
        issues.append("Missing 'Source:' prefix")

    # Check minimum length
    if len(value) < 10:
        issues.append(f"Very short description ({len(value)} chars)")

    # Check for platform markers
    platforms = ["Google", "Instagram", "Snapchat", "Discord", "iMessage"]
    has_platform = any(p in value for p in platforms)
    if not has_platform:
        issues.append("No recognized platform in description")

    is_valid = "Source:" in value and has_platform
    return is_valid, issues


def analyze_file(
    file_path: Path, category: str, metadata: Dict[str, Any]
) -> MetadataResult:
    """Analyze a single file's metadata."""
    parts = category.split("/")
    platform = parts[0] if parts else "unknown"
    service = parts[1] if len(parts) > 1 else "unknown"

    result = MetadataResult(
        file_path=str(file_path),
        platform=platform,
        service=service,
    )

    # Check DateTime
    datetime_fields = [
        "EXIF:DateTimeOriginal",
        "ExifIFD:DateTimeOriginal",
        "QuickTime:CreateDate",
        "QuickTime:MediaCreateDate",
        "Matroska:DateTimeOriginal",
        "Matroska:Date",
        "XMP:CreateDate",
        "XMP-exif:DateTimeOriginal",
        "XMP-xmp:CreateDate",
        "DateTimeOriginal",
        "CreateDate",
        "MediaCreateDate",
    ]

    for field_name in datetime_fields:
        value = metadata.get(field_name)
        if value and value != "-":
            result.datetime_value = value
            result.has_datetime, result.datetime_issues = validate_datetime(value)
            break

    if not result.datetime_value:
        result.datetime_issues = ["No datetime field found"]

    # Check GPS
    lat = (
        metadata.get("EXIF:GPSLatitude")
        or metadata.get("Composite:GPSLatitude")
        or metadata.get("GPSLatitude")
    )
    lon = (
        metadata.get("EXIF:GPSLongitude")
        or metadata.get("Composite:GPSLongitude")
        or metadata.get("GPSLongitude")
    )

    if lat is not None and lon is not None:
        try:
            result.gps_lat = float(lat) if not isinstance(lat, float) else lat
            result.gps_lon = float(lon) if not isinstance(lon, float) else lon
            result.has_gps, result.gps_issues = validate_gps(
                result.gps_lat, result.gps_lon
            )
        except (ValueError, TypeError):
            result.gps_issues = [f"Cannot parse GPS: {lat}, {lon}"]
    else:
        result.gps_issues = ["No GPS coordinates"]

    # Check Description
    desc_fields = [
        "EXIF:ImageDescription",
        "IFD0:ImageDescription",
        "ImageDescription",
        "IPTC:Caption-Abstract",
        "XMP:Description",
        "XMP-tiff:ImageDescription",
        "XMP-dc:Description",
        "QuickTime:Comment",
        "QuickTime:Description",
        "ItemList:Comment",
        "ItemList:Description",
        "Matroska:Comment",
        "Matroska:Description",
        "Comment",
        "Description",
    ]

    for field_name in desc_fields:
        value = metadata.get(field_name)
        if value and value != "-":
            result.description_value = value
            result.has_description, result.description_issues = validate_description(
                value
            )
            break

    if not result.description_value:
        result.description_issues = ["No description field found"]

    # Overall assessment
    result.is_complete = result.has_datetime and result.has_description
    result.is_accurate = (
        result.has_datetime
        and result.has_description
        and len(result.datetime_issues) == 0
        and len(result.description_issues) == 0
    )

    return result


def print_summary(results: List[MetadataResult]) -> None:
    """Print a summary of the audit results."""
    total = len(results)

    # Overall stats
    has_datetime = sum(1 for r in results if r.has_datetime)
    has_gps = sum(1 for r in results if r.has_gps)
    has_description = sum(1 for r in results if r.has_description)
    is_complete = sum(1 for r in results if r.is_complete)
    is_accurate = sum(1 for r in results if r.is_accurate)

    print("\n" + "=" * 70)
    print("METADATA AUDIT SUMMARY")
    print("=" * 70)
    print(f"\nTotal files sampled: {total}")
    print()

    # Completeness
    print("COMPLETENESS:")
    print(
        f"  DateTime present:    {has_datetime:4d}/{total} ({100 * has_datetime / total:.1f}%)"
    )
    print(f"  GPS present:         {has_gps:4d}/{total} ({100 * has_gps / total:.1f}%)")
    print(
        f"  Description present: {has_description:4d}/{total} ({100 * has_description / total:.1f}%)"
    )
    print(
        f"  Fully complete:      {is_complete:4d}/{total} ({100 * is_complete / total:.1f}%)"
    )
    print()

    # Accuracy
    print("ACCURACY:")
    print(
        f"  Fully accurate:      {is_accurate:4d}/{total} ({100 * is_accurate / total:.1f}%)"
    )
    print()

    # By platform
    print("BY PLATFORM:")
    by_platform: Dict[str, List[MetadataResult]] = defaultdict(list)
    for r in results:
        by_platform[r.platform].append(r)

    for platform in sorted(by_platform.keys()):
        platform_results = by_platform[platform]
        p_total = len(platform_results)
        p_datetime = sum(1 for r in platform_results if r.has_datetime)
        p_gps = sum(1 for r in platform_results if r.has_gps)
        p_desc = sum(1 for r in platform_results if r.has_description)
        p_complete = sum(1 for r in platform_results if r.is_complete)

        print(f"\n  {platform} ({p_total} files):")
        print(
            f"    DateTime:    {p_datetime:3d}/{p_total} ({100 * p_datetime / p_total:.1f}%)"
        )
        print(f"    GPS:         {p_gps:3d}/{p_total} ({100 * p_gps / p_total:.1f}%)")
        print(f"    Description: {p_desc:3d}/{p_total} ({100 * p_desc / p_total:.1f}%)")
        print(
            f"    Complete:    {p_complete:3d}/{p_total} ({100 * p_complete / p_total:.1f}%)"
        )

    # Issues summary
    print("\n" + "-" * 70)
    print("COMMON ISSUES:")

    datetime_issues: Dict[str, int] = defaultdict(int)
    gps_issues: Dict[str, int] = defaultdict(int)
    desc_issues: Dict[str, int] = defaultdict(int)

    for r in results:
        for issue in r.datetime_issues:
            datetime_issues[issue] += 1
        for issue in r.gps_issues:
            gps_issues[issue] += 1
        for issue in r.description_issues:
            desc_issues[issue] += 1

    if datetime_issues:
        print("\n  DateTime issues:")
        for issue, count in sorted(datetime_issues.items(), key=lambda x: -x[1])[:5]:
            print(f"    - {issue}: {count}")

    if gps_issues:
        print("\n  GPS issues:")
        for issue, count in sorted(gps_issues.items(), key=lambda x: -x[1])[:5]:
            print(f"    - {issue}: {count}")

    if desc_issues:
        print("\n  Description issues:")
        for issue, count in sorted(desc_issues.items(), key=lambda x: -x[1])[:5]:
            print(f"    - {issue}: {count}")


def print_samples(results: List[MetadataResult], limit: int = 10) -> None:
    """Print sample files with issues for investigation."""
    print("\n" + "=" * 70)
    print("SAMPLE FILES WITH ISSUES")
    print("=" * 70)

    # Files with incomplete metadata
    incomplete = [r for r in results if not r.is_complete]
    if incomplete:
        print(
            f"\nIncomplete metadata ({min(limit, len(incomplete))} of {len(incomplete)} shown):"
        )
        for r in incomplete[:limit]:
            print(f"\n  File: {r.file_path}")
            print(f"  Platform: {r.platform}, Service: {r.service}")
            if not r.has_datetime:
                print(f"  [MISSING] DateTime: {r.datetime_issues}")
            if not r.has_description:
                print(f"  [MISSING] Description: {r.description_issues}")

    # Files with GPS missing (informational - not all sources have GPS)
    no_gps = [r for r in results if not r.has_gps and r.is_complete]
    if no_gps:
        print(
            f"\n\nComplete but missing GPS ({min(5, len(no_gps))} of {len(no_gps)} shown):"
        )
        for r in no_gps[:5]:
            print(f"  - {Path(r.file_path).name} ({r.platform}/{r.service})")


def print_description_samples(results: List[MetadataResult], limit: int = 5) -> None:
    """Print sample descriptions to verify format."""
    print("\n" + "=" * 70)
    print("SAMPLE DESCRIPTIONS (for format verification)")
    print("=" * 70)

    # Get files with descriptions
    with_desc = [r for r in results if r.has_description and r.description_value]

    # Sample from different platforms
    by_platform: Dict[str, List[MetadataResult]] = defaultdict(list)
    for r in with_desc:
        by_platform[r.platform].append(r)

    for platform in sorted(by_platform.keys()):
        print(f"\n{platform}:")
        samples = by_platform[platform][:limit]
        for r in samples:
            desc = r.description_value or ""
            # Truncate long descriptions
            if len(desc) > 200:
                desc = desc[:197] + "..."
            print(f"\n  File: {Path(r.file_path).name}")
            print(f"  Description: {desc}")


def main():
    parser = argparse.ArgumentParser(
        description="Randomly sample processed media and audit metadata"
    )
    parser.add_argument(
        "--samples",
        "-n",
        type=int,
        default=100,
        help="Number of files to sample (default: 100)",
    )
    parser.add_argument(
        "--path",
        "-p",
        type=str,
        default="/mnt/media/processed",
        help="Path to processed exports (default: /mnt/media/processed)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output including sample descriptions",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducibility"
    )
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    base_path = Path(args.path)
    if not base_path.exists():
        print(f"Error: Path does not exist: {base_path}")
        sys.exit(1)

    print(f"Collecting media files from: {base_path}")
    files_by_category = collect_media_files(base_path)

    total_files = sum(len(files) for files in files_by_category.values())
    print(f"Found {total_files} media files across {len(files_by_category)} categories")

    for category, files in sorted(files_by_category.items()):
        print(f"  {category}: {len(files)} files")

    print(f"\nSampling {args.samples} files...")
    sampled = sample_files(files_by_category, args.samples)

    # Read metadata in batches
    print("Reading metadata with exiftool...")
    file_paths = [f for f, _ in sampled]

    # Process in batches of 100
    batch_size = 100
    all_metadata = {}
    for i in range(0, len(file_paths), batch_size):
        batch = file_paths[i : i + batch_size]
        batch_metadata = read_metadata_exiftool(batch)
        all_metadata.update(batch_metadata)
        print(
            f"  Processed {min(i + batch_size, len(file_paths))}/{len(file_paths)} files..."
        )

    # Analyze each file
    print("Analyzing metadata...")
    results = []
    for file_path, category in sampled:
        metadata = all_metadata.get(str(file_path), {})
        result = analyze_file(file_path, category, metadata)
        results.append(result)

    # Print summary
    print_summary(results)
    print_samples(results)

    if args.verbose:
        print_description_samples(results)

    print("\n" + "=" * 70)
    print("AUDIT COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
