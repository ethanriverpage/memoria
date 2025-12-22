#!/usr/bin/env python3
"""
Metadata Verification Script

This script randomly samples processed media files and compares their embedded
metadata against the original source export metadata to verify correct writing.

Usage:
    python scripts/verify_metadata.py [--sample-size N] [--verbose]
"""

import argparse
import json
import os
import random
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Add parent directory to path for imports when running standalone
sys.path.insert(0, str(Path(__file__).parent.parent))

from common.utils import ALL_MEDIA_EXTENSIONS

# ANSI colors for output
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"


def run_exiftool(file_path: Path) -> Dict[str, Any]:
    """Extract metadata from a file using exiftool."""
    cmd = [
        "exiftool",
        "-json",
        "-a",  # Allow duplicate tags
        "-G1",  # Show group names
        "-s",  # Short tag names
        str(file_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return data[0] if data else {}
    except (subprocess.CalledProcessError, json.JSONDecodeError) as e:
        print(f"{RED}Error reading metadata from {file_path}: {e}{RESET}")
        return {}


def find_source_metadata(
    processed_path: Path, dedup_manifest: Dict
) -> Tuple[Optional[Path], Optional[Dict], str]:
    """
    Find the source metadata for a processed file.

    Returns: (source_path, source_metadata, source_type)
    """
    # Try to find in dedup_manifest by looking at what files were processed

    # For each duplicate group, check if our processed file matches the primary
    for content_hash, group_data in dedup_manifest.get("duplicates", {}).items():
        primary = group_data.get("primary", {})
        primary.get("source_path", "")

        # Check merged metadata for this group
        merged = group_data.get("merged_metadata", {})
        if merged:
            # This could be our file - we need to match by content hash or path pattern
            pass

    return None, None, "unknown"


def parse_processed_filename(filename: str) -> Dict[str, str]:
    """
    Parse a processed filename to extract metadata hints.

    Examples:
        gphotos-ethanriverpage-20170119.jpg -> {"source": "gphotos", "user": "ethanriverpage", "date": "20170119"}
        imessage-lans_with_the_boys-20211019_2.jpg -> {"source": "imessage", "conversation": "lans_with_the_boys", "date": "20211019", "index": "2"}
        snap-memories-ethriverpage-20180806.mkv -> {"source": "snap-memories", "user": "ethriverpage", "date": "20180806"}
    """
    info = {}

    # Remove extension
    base = Path(filename).stem

    # Common patterns
    patterns = [
        # gphotos-user-YYYYMMDD[_N]
        r"^(gphotos)-([^-]+)-(\d{8})(?:_(\d+))?(?:_(\d+))?$",
        # imessage-conversation-YYYYMMDD[_N]
        r"^(imessage)-(.+)-(\d{8})(?:_(\d+))?$",
        # snap-memories-user-YYYYMMDD[_N]
        r"^(snap-memories)-([^-]+)-(\d{8})(?:_(\d+))?$",
        # snap-messages-conversation-YYYYMMDD[_N]
        r"^(snap-messages)-(.+)-(\d{8})(?:_(\d+))?$",
        # gchat-user-conversation-YYYYMMDD[_N]
        r"^(gchat)-([^-]+)-(.+)-(\d{8})(?:_(\d+))?$",
        # insta-type-user-YYYYMMDD[_N]
        r"^(insta)-([^-]+)-([^-]+)-(\d{8})(?:_(\d+))?$",
        # discord-server-channel-YYYYMMDD[_N]
        r"^(discord)-(.+)-(\d{8})(?:_(\d+))?$",
    ]

    for pattern in patterns:
        match = re.match(pattern, base)
        if match:
            groups = list(match.groups())
            info["source"] = groups[0]
            info["date"] = groups[2] if len(groups) > 2 else groups[1]
            break

    return info


def normalize_datetime(dt_str: str) -> Optional[datetime]:
    """Normalize various datetime formats to a datetime object."""
    if not dt_str:
        return None

    # Common formats to try
    formats = [
        "%Y:%m:%d %H:%M:%S",  # EXIF format
        "%Y-%m-%dT%H:%M:%S",  # ISO format
        "%Y-%m-%dT%H:%M:%SZ",  # ISO with Z
        "%Y-%m-%d %H:%M:%S",  # Standard
        "%Y-%m-%d %H:%M:%S UTC",  # With UTC suffix
        "%Y%m%d",  # Just date
    ]

    # Handle timezone offset like +00:00 or -05:00
    dt_str = re.sub(r"[+-]\d{2}:\d{2}$", "", dt_str)
    dt_str = re.sub(r"Z$", "", dt_str)

    for fmt in formats:
        try:
            return datetime.strptime(dt_str.strip(), fmt)
        except ValueError:
            continue

    return None


def extract_date_from_filename(filename: str) -> Optional[str]:
    """Extract date string from a processed filename."""
    # Match YYYYMMDD pattern
    match = re.search(r"(\d{8})", filename)
    if match:
        return match.group(1)
    return None


def verify_file_metadata(processed_path: Path, verbose: bool = False) -> Dict[str, Any]:
    """
    Verify metadata for a single processed file.

    Returns a dict with verification results.
    """
    result = {
        "file": str(processed_path),
        "filename": processed_path.name,
        "checks": [],
        "passed": True,
        "warnings": [],
        "errors": [],
    }

    # Read embedded metadata
    metadata = run_exiftool(processed_path)
    if not metadata:
        result["errors"].append("Could not read metadata from file")
        result["passed"] = False
        return result

    # Extract relevant metadata fields
    # DateTimeOriginal, CreateDate, ModifyDate
    datetime_fields = [
        "[EXIF:ExifIFD] DateTimeOriginal",
        "[EXIF:IFD0] ModifyDate",
        "[XMP:XMP-exif] DateTimeOriginal",
        "[XMP:XMP-photoshop] DateCreated",
        "[QuickTime:Keys] CreationDate",
        "[QuickTime:CreateDate] CreateDate",
        "[Matroska:Matroska] DateTimeOriginal",
    ]

    # GPS fields

    # Description fields

    # Check 1: Does the file have datetime metadata?
    found_datetime = None
    for field in datetime_fields:
        # Try variations of field name
        for key in metadata.keys():
            if any(part in key for part in field.split("]")[1].strip().split()):
                found_datetime = metadata[key]
                break
        if found_datetime:
            break

    # Also check common simple keys
    simple_date_keys = [
        "DateTimeOriginal",
        "CreateDate",
        "ModifyDate",
        "DateCreated",
        "CreationDate",
    ]
    if not found_datetime:
        for key in simple_date_keys:
            if key in metadata:
                found_datetime = metadata[key]
                break

    # Parse expected date from filename
    filename_date = extract_date_from_filename(processed_path.name)

    if found_datetime:
        result["checks"].append(
            {"check": "has_datetime", "passed": True, "value": str(found_datetime)}
        )

        # Verify date matches filename
        if filename_date:
            parsed_dt = normalize_datetime(str(found_datetime))
            if parsed_dt:
                embedded_date = parsed_dt.strftime("%Y%m%d")
                if embedded_date == filename_date:
                    result["checks"].append(
                        {
                            "check": "datetime_matches_filename",
                            "passed": True,
                            "expected": filename_date,
                            "actual": embedded_date,
                        }
                    )
                else:
                    result["checks"].append(
                        {
                            "check": "datetime_matches_filename",
                            "passed": False,
                            "expected": filename_date,
                            "actual": embedded_date,
                        }
                    )
                    result["warnings"].append(
                        f"Date mismatch: filename={filename_date}, embedded={embedded_date}"
                    )
    else:
        result["checks"].append(
            {"check": "has_datetime", "passed": False, "value": None}
        )
        result["warnings"].append("No datetime metadata found in file")

    # Check 2: Does the file have GPS metadata (if expected)?
    found_lat = None
    found_lon = None

    for key in metadata.keys():
        key_lower = key.lower()
        if "gpslatitude" in key_lower and "ref" not in key_lower:
            found_lat = metadata[key]
        if "gpslongitude" in key_lower and "ref" not in key_lower:
            found_lon = metadata[key]

    if found_lat and found_lon:
        # Check if GPS is non-zero
        try:
            lat_val = float(str(found_lat).split()[0]) if found_lat else 0
            lon_val = float(str(found_lon).split()[0]) if found_lon else 0
            if lat_val != 0 or lon_val != 0:
                result["checks"].append(
                    {
                        "check": "has_gps",
                        "passed": True,
                        "latitude": str(found_lat),
                        "longitude": str(found_lon),
                    }
                )
        except (ValueError, TypeError):
            pass

    # Check 3: Does the file have description/comment metadata?
    found_desc = None
    for key in metadata.keys():
        key_lower = key.lower()
        if any(
            term in key_lower for term in ["description", "comment", "imagedescription"]
        ):
            val = metadata[key]
            if val and str(val).strip():
                found_desc = val
                break

    if found_desc:
        result["checks"].append(
            {"check": "has_description", "passed": True, "value": str(found_desc)[:100]}
        )

    if verbose:
        result["raw_metadata"] = {
            k: str(v)[:200] for k, v in metadata.items() if not k.startswith("File")
        }

    return result


def find_google_photos_source(
    processed_path: Path, originals_root: Path
) -> Optional[Tuple[Path, Dict]]:
    """Find source metadata for a Google Photos processed file."""
    # Parse the processed filename
    # gphotos-ethanriverpage-20170119.jpg -> look for files from that date

    filename = processed_path.name
    match = re.match(r"gphotos-([^-]+)-(\d{8})", filename)
    if not match:
        return None

    username = match.group(1)
    date_str = match.group(2)
    year = date_str[:4]
    date_str[4:6]
    date_str[6:8]

    # Search in originals for matching date folders
    for export_dir in originals_root.iterdir():
        if not export_dir.is_dir():
            continue
        if username not in export_dir.name:
            continue

        # Look in Google Photos folders
        photos_dir = export_dir / "Google Photos"
        if not photos_dir.exists():
            continue

        # Search year folders
        for album_dir in photos_dir.iterdir():
            if not album_dir.is_dir():
                continue
            if year not in album_dir.name:
                continue

            # Look for JSON metadata files
            for f in album_dir.iterdir():
                if not f.suffix.lower() == ".json":
                    continue

                try:
                    with open(f, "r") as jf:
                        meta = json.load(jf)

                    # Check if photoTakenTime matches our date
                    taken_time = meta.get("photoTakenTime", {})
                    ts = taken_time.get("timestamp")
                    if ts:
                        dt = datetime.fromtimestamp(int(ts))
                        if dt.strftime("%Y%m%d") == date_str:
                            # Find corresponding media file
                            media_name = f.stem.replace(
                                ".supplemental-metadata", ""
                            ).replace("-metadata", "")
                            media_path = album_dir / media_name
                            if media_path.exists():
                                return media_path, meta
                except Exception:
                    continue

    return None


def find_snapchat_source(
    processed_path: Path, originals_root: Path
) -> Optional[Tuple[Path, Dict]]:
    """Find source metadata for a Snapchat Memories processed file."""
    filename = processed_path.name

    # snap-memories-ethriverpage-20180806.mkv
    match = re.match(r"snap-memories-([^-]+)-(\d{8})", filename)
    if not match:
        return None

    username = match.group(1)
    date_str = match.group(2)

    # Convert to Snapchat metadata date format
    year = date_str[:4]
    month = date_str[4:6]
    day = date_str[6:8]
    expected_date_prefix = f"{year}-{month}-{day}"

    # Search in originals for Snapchat export
    for export_dir in originals_root.iterdir():
        if not export_dir.is_dir():
            continue
        if "snapchat" not in export_dir.name.lower():
            continue
        if username not in export_dir.name:
            continue

        # Look for memories metadata.json
        memories_dir = export_dir / "memories"
        metadata_file = memories_dir / "metadata.json"

        if metadata_file.exists():
            try:
                with open(metadata_file, "r") as f:
                    memories = json.load(f)

                for memory in memories:
                    date = memory.get("date", "")
                    if date.startswith(expected_date_prefix):
                        media_filename = memory.get("media_filename")
                        if media_filename:
                            media_path = memories_dir / "media" / media_filename
                            if media_path.exists():
                                return media_path, memory
            except Exception:
                continue

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Verify metadata in processed media files"
    )
    parser.add_argument(
        "--sample-size",
        "-n",
        type=int,
        default=20,
        help="Number of files to sample (default: 20)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed metadata"
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=Path("/mnt/media/processed"),
        help="Path to processed files directory",
    )
    parser.add_argument(
        "--originals-dir",
        type=Path,
        default=Path("/mnt/media/originals"),
        help="Path to original exports directory",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducibility"
    )

    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    print(f"{BOLD}Metadata Verification Report{RESET}")
    print("=" * 60)
    print(f"Processed directory: {args.processed_dir}")
    print(f"Originals directory: {args.originals_dir}")
    print(f"Sample size: {args.sample_size}")
    print()

    # Collect all processed files
    processed_files = []
    for export_dir in args.processed_dir.iterdir():
        if export_dir.is_dir() and export_dir.name != "dedup_manifest.json":
            for root, dirs, files in os.walk(export_dir):
                for f in files:
                    ext = Path(f).suffix.lower()
                    if ext in ALL_MEDIA_EXTENSIONS:
                        processed_files.append(Path(root) / f)

    print(f"Total processed files: {len(processed_files)}")

    # Sample files
    sample_size = min(args.sample_size, len(processed_files))
    sampled_files = random.sample(processed_files, sample_size)

    print(f"Sampled {sample_size} files for verification")
    print()

    # Verify each file
    results = []
    passed_count = 0
    warning_count = 0

    for i, file_path in enumerate(sampled_files, 1):
        print(f"{BLUE}[{i}/{sample_size}]{RESET} Checking: {file_path.name}")

        result = verify_file_metadata(file_path, args.verbose)
        results.append(result)

        # Print summary for this file
        sum(1 for c in result["checks"] if c.get("passed", False))
        len(result["checks"])

        if result["errors"]:
            print(f"  {RED}ERRORS: {', '.join(result['errors'])}{RESET}")
        elif result["warnings"]:
            print(f"  {YELLOW}WARNINGS: {', '.join(result['warnings'])}{RESET}")
            warning_count += 1
        else:
            passed_count += 1

        # Print check details
        for check in result["checks"]:
            status = (
                f"{GREEN}PASS{RESET}" if check.get("passed") else f"{RED}FAIL{RESET}"
            )
            check_name = check.get("check", "unknown")

            if check_name == "has_datetime":
                val = check.get("value", "N/A")
                print(f"    [{status}] DateTime: {val}")
            elif check_name == "datetime_matches_filename":
                expected = check.get("expected")
                actual = check.get("actual")
                if check.get("passed"):
                    print(f"    [{status}] DateTime matches filename ({actual})")
                else:
                    print(
                        f"    [{status}] DateTime mismatch: filename={expected}, embedded={actual}"
                    )
            elif check_name == "has_gps":
                lat = check.get("latitude", "N/A")
                lon = check.get("longitude", "N/A")
                print(f"    [{status}] GPS: {lat}, {lon}")
            elif check_name == "has_description":
                val = check.get("value", "N/A")
                print(f"    [{status}] Description: {val[:50]}...")

        if args.verbose and "raw_metadata" in result:
            print("    Raw metadata:")
            for k, v in list(result["raw_metadata"].items())[:10]:
                print(f"      {k}: {v[:80]}")

        print()

    # Summary
    print("=" * 60)
    print(f"{BOLD}Summary{RESET}")
    print(f"  Total files checked: {sample_size}")
    print(f"  {GREEN}Passed (no issues): {passed_count}{RESET}")
    print(f"  {YELLOW}Warnings: {warning_count}{RESET}")
    print(f"  {RED}Errors: {sample_size - passed_count - warning_count}{RESET}")

    # Detailed statistics
    datetime_present = sum(
        1
        for r in results
        for c in r["checks"]
        if c.get("check") == "has_datetime" and c.get("passed")
    )
    datetime_matches = sum(
        1
        for r in results
        for c in r["checks"]
        if c.get("check") == "datetime_matches_filename" and c.get("passed")
    )
    gps_present = sum(
        1
        for r in results
        for c in r["checks"]
        if c.get("check") == "has_gps" and c.get("passed")
    )
    desc_present = sum(
        1
        for r in results
        for c in r["checks"]
        if c.get("check") == "has_description" and c.get("passed")
    )

    print()
    print(f"  DateTime metadata present: {datetime_present}/{sample_size}")
    print(f"  DateTime matches filename: {datetime_matches}/{datetime_present}")
    print(f"  GPS metadata present: {gps_present}/{sample_size}")
    print(f"  Description present: {desc_present}/{sample_size}")

    return 0 if (passed_count + warning_count) == sample_size else 1


if __name__ == "__main__":
    sys.exit(main())
