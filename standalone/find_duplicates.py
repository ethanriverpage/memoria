#!/usr/bin/env python3
"""
Analyze media exports for duplicate files across directories.
Uses xxhash for fast hashing to identify exact file matches.
Optimized with multiprocessing for large exports.
"""

import os
import json
import pickle
import sys
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple, Optional
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import cpu_count

import xxhash

# Import utilities from common module
sys.path.insert(0, str(Path(__file__).parent))
from common.utils import get_media_type, is_supported_media
from common.filter_banned_files import BannedFilesFilter


def calculate_file_hash(filepath: Path) -> Tuple[str, Path]:
    """
    Calculate xxHash (128-bit) hash of a file.
    xxHash is much faster than SHA-256 for non-cryptographic purposes.

    Args:
        filepath: Path to the file

    Returns:
        Tuple of (hash, filepath)
    """
    try:
        hasher = xxhash.xxh128()
        # Use larger buffer for faster I/O (8MB chunks)
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(8388608), b""):
                hasher.update(byte_block)
        return (hasher.hexdigest(), filepath)
    except Exception as e:
        print(f"\nError processing {filepath}: {e}", file=sys.stderr)
        return (None, filepath)


def calculate_file_hash_wrapper(filepath_str: str) -> Tuple[Optional[str], str]:
    """
    Wrapper for calculate_file_hash that works with string paths.
    Needed for multiprocessing serialization.

    Args:
        filepath_str: String path to the file

    Returns:
        Tuple of (hash, filepath_str)
    """
    filepath = Path(filepath_str)
    file_hash, _ = calculate_file_hash(filepath)
    return (file_hash, filepath_str)


# File validation uses common.utils.is_supported_media and common.filter_banned_files


def collect_media_files(
    base_paths: List[Path], banned_filter: BannedFilesFilter
) -> List[Path]:
    """
    Collect all media files from multiple export directories.
    Uses common utilities to filter valid media types and skip banned files.
    Also skips files with 'overlay' or 'thumbnail' in their filenames.

    Args:
        base_paths: List of root paths to scan
        banned_filter: Filter for skipping banned files and directories

    Returns:
        List of all valid media file paths
    """
    print("Scanning directories for media files...")
    media_files = []
    skipped_banned = 0
    skipped_unsupported = 0
    skipped_overlay_thumbnail = 0

    for base_path in base_paths:
        if not base_path.exists():
            print(f"Warning: Path does not exist: {base_path}")
            continue

        if base_path.is_file():
            # Single file specified
            print(f"\nProcessing file: {base_path.name}")
            if banned_filter.is_banned(base_path):
                skipped_banned += 1
            elif (
                "overlay" in base_path.name.lower()
                or "thumbnail" in base_path.name.lower()
            ):
                skipped_overlay_thumbnail += 1
            elif is_supported_media(base_path):
                media_files.append(base_path)
            else:
                skipped_unsupported += 1
        else:
            # Directory specified
            print(f"\nScanning directory: {base_path}")

            # Walk through all files in the directory tree
            for root, dirs, files in os.walk(base_path):
                root_path = Path(root)

                # Filter out banned directories in-place to skip walking into them
                dirs[:] = [
                    d for d in dirs if not banned_filter.is_banned(root_path / d)
                ]

                for filename in files:
                    filepath = root_path / filename

                    # Skip banned files
                    if banned_filter.is_banned(filepath):
                        skipped_banned += 1
                        continue

                    # Skip overlay and thumbnail files
                    if "overlay" in filename.lower() or "thumbnail" in filename.lower():
                        skipped_overlay_thumbnail += 1
                        continue

                    # Only collect supported media files (images and videos)
                    if is_supported_media(filepath):
                        media_files.append(filepath)
                    else:
                        skipped_unsupported += 1

    print(f"\nScan complete:")
    print(f"  Media files found: {len(media_files):,}")
    print(f"  Banned files skipped: {skipped_banned:,}")
    print(f"  Overlay/thumbnail files skipped: {skipped_overlay_thumbnail:,}")
    print(f"  Unsupported files skipped: {skipped_unsupported:,}")
    return media_files


def load_checkpoint(
    checkpoint_file: Path, auto_resume: bool = True
) -> Optional[Dict[str, List[str]]]:
    """
    Load checkpoint from previous run if it exists.

    Args:
        checkpoint_file: Path to checkpoint file
        auto_resume: If True, automatically resume without prompting

    Returns:
        Hash map if checkpoint exists, None otherwise
    """
    if checkpoint_file.exists():
        file_size_mb = checkpoint_file.stat().st_size / (1024 * 1024)
        print(f"\nFound checkpoint file: {checkpoint_file} ({file_size_mb:.2f} MB)")

        if auto_resume:
            print("Auto-resuming from checkpoint...")
            with open(checkpoint_file, "rb") as f:
                return pickle.load(f)
        else:
            response = input("Resume from checkpoint? (y/n): ").lower().strip()
            if response == "y":
                with open(checkpoint_file, "rb") as f:
                    return pickle.load(f)
    return None


def save_checkpoint(hash_map: Dict[str, List[str]], checkpoint_file: Path):
    """
    Save checkpoint for resuming later.

    Args:
        hash_map: Current hash map state
        checkpoint_file: Path to save checkpoint
    """
    with open(checkpoint_file, "wb") as f:
        pickle.dump(hash_map, f)


def scan_media_directories(
    base_paths: List[Path],
    checkpoint_file: Path,
    banned_filter: BannedFilesFilter,
    use_checkpoint: bool = True,
) -> Dict[str, List[Path]]:
    """
    Scan media directories and build a hash map of all media files.
    Uses multiprocessing for parallel hashing with xxHash.

    Args:
        base_paths: List of root paths to scan
        checkpoint_file: Path to checkpoint file
        banned_filter: Filter for skipping banned files and directories
        use_checkpoint: Whether to use checkpoint functionality

    Returns:
        Dictionary mapping file hashes to list of file paths
    """
    # Try to load checkpoint
    hash_map_raw = load_checkpoint(checkpoint_file) if use_checkpoint else None
    already_processed = set()

    if hash_map_raw:
        # Convert string paths back to Path objects
        already_processed = set()
        for paths in hash_map_raw.values():
            already_processed.update(paths)
        print(f"Resuming with {len(already_processed):,} already processed files")
    else:
        hash_map_raw = defaultdict(list)

    # Collect all media files
    all_media_files = collect_media_files(base_paths, banned_filter)

    # Filter out already processed files
    files_to_process = [f for f in all_media_files if str(f) not in already_processed]

    if not files_to_process:
        print("\nAll files already processed!")
        # Convert back to Path objects
        hash_map = defaultdict(list)
        for file_hash, paths in hash_map_raw.items():
            hash_map[file_hash] = [Path(p) for p in paths]
        return hash_map

    print(f"Files to process: {len(files_to_process):,}")

    # Determine optimal number of workers
    num_workers = min(cpu_count(), 16)  # Cap at 16 to avoid too many processes
    print(f"Using {num_workers} worker processes")

    print("\nHashing files in parallel...")
    processed = 0
    errors = 0
    checkpoint_interval = 1000  # Save checkpoint every 1000 files

    # Convert Path objects to strings for serialization
    files_to_process_str = [str(f) for f in files_to_process]

    # Process files in parallel
    with ProcessPoolExecutor(max_workers=num_workers) as executor:
        # Submit all tasks
        futures = {
            executor.submit(calculate_file_hash_wrapper, f): f
            for f in files_to_process_str
        }

        # Process results as they complete
        for future in as_completed(futures):
            file_hash, filepath_str = future.result()

            if file_hash:
                hash_map_raw[file_hash].append(filepath_str)
                processed += 1
            else:
                errors += 1

            # Print progress
            if processed % 100 == 0:
                progress_pct = (processed / len(files_to_process)) * 100
                print(
                    f"  Progress: {processed:,}/{len(files_to_process):,} "
                    f"({progress_pct:.1f}%) - Errors: {errors}",
                    end="\r",
                )

            # Save checkpoint periodically
            if use_checkpoint and processed % checkpoint_interval == 0:
                save_checkpoint(hash_map_raw, checkpoint_file)

    print(f"\n\nHashing complete!")
    print(f"Successfully processed: {processed:,}")
    print(f"Errors: {errors}")

    # Final checkpoint save
    if use_checkpoint:
        save_checkpoint(hash_map_raw, checkpoint_file)

    # Convert string paths back to Path objects
    hash_map = defaultdict(list)
    for file_hash, paths in hash_map_raw.items():
        hash_map[file_hash] = [Path(p) for p in paths]

    return hash_map


def analyze_duplicates(hash_map: Dict[str, List[Path]], common_base: Path) -> Dict:
    """
    Analyze the hash map to identify duplicates across directories/albums.

    Args:
        hash_map: Dictionary mapping file hashes to list of file paths
        common_base: Common root path to make paths relative

    Returns:
        Dictionary with analysis results
    """
    # Find all hashes that appear in multiple locations
    duplicates = {h: paths for h, paths in hash_map.items() if len(paths) > 1}

    # Organize duplicates by location
    location_duplicates = []

    for file_hash, paths in duplicates.items():
        # Get location info for each path
        locations = []
        for path in paths:
            try:
                relative_path = path.relative_to(common_base)
                location_name = (
                    relative_path.parts[0]
                    if len(relative_path.parts) > 0
                    else "Unknown"
                )
            except ValueError:
                # Path is not relative to common_base, use absolute path
                location_name = path.parts[0] if len(path.parts) > 0 else "Unknown"
                relative_path = path

            locations.append(
                {
                    "location": location_name,
                    "path": str(relative_path),
                    "size": path.stat().st_size,
                }
            )

        location_duplicates.append(
            {"hash": file_hash, "count": len(paths), "locations": locations}
        )

    # Sort by number of duplicates (most duplicated first)
    location_duplicates.sort(key=lambda x: x["count"], reverse=True)

    # Calculate statistics
    total_unique_files = len(hash_map)
    total_duplicate_groups = len(duplicates)
    total_duplicate_instances = sum(len(paths) - 1 for paths in duplicates.values())

    # Calculate wasted space
    wasted_space = 0
    for file_hash, paths in duplicates.items():
        file_size = paths[0].stat().st_size
        # Wasted space = file_size * (number of copies - 1)
        wasted_space += file_size * (len(paths) - 1)

    return {
        "total_unique_files": total_unique_files,
        "total_files_with_duplicates": total_duplicate_groups,
        "total_duplicate_instances": total_duplicate_instances,
        "wasted_space_bytes": wasted_space,
        "wasted_space_mb": wasted_space / (1024 * 1024),
        "wasted_space_gb": wasted_space / (1024 * 1024 * 1024),
        "duplicates": location_duplicates,
    }


def print_summary(results: Dict):
    """Print a summary of the analysis results."""
    print("\n" + "=" * 80)
    print("DUPLICATE ANALYSIS SUMMARY")
    print("=" * 80)
    print(f"\nTotal unique files (by hash): {results['total_unique_files']:,}")
    print(f"Files that have duplicates: {results['total_files_with_duplicates']:,}")
    print(f"Total duplicate instances: {results['total_duplicate_instances']:,}")
    print(f"\nWasted space from duplicates:")
    print(f"  {results['wasted_space_mb']:.2f} MB")
    print(f"  {results['wasted_space_gb']:.2f} GB")

    # Show top 10 most duplicated files
    print("\n" + "-" * 80)
    print("TOP 10 MOST DUPLICATED FILES")
    print("-" * 80)

    for i, dup in enumerate(results["duplicates"][:10], 1):
        print(f"\n{i}. Hash: {dup['hash'][:16]}...")
        print(f"   Found in {dup['count']} locations:")
        for loc in dup["locations"]:
            size_mb = loc["size"] / (1024 * 1024)
            print(f"   - {loc['location']}: {loc['path']} ({size_mb:.2f} MB)")

    # Show some examples of cross-location duplicates
    print("\n" + "-" * 80)
    print("SAMPLE CROSS-LOCATION DUPLICATES")
    print("-" * 80)

    cross_location = [
        d
        for d in results["duplicates"]
        if len(set(loc["location"] for loc in d["locations"])) > 1
    ]

    print(
        f"\nTotal files duplicated across different locations: {len(cross_location):,}"
    )

    if cross_location:
        print("\nFirst 5 examples:")
        for i, dup in enumerate(cross_location[:5], 1):
            locations = set(loc["location"] for loc in dup["locations"])
            print(f"\n{i}. Found in locations: {', '.join(sorted(locations))}")
            print(f"   Hash: {dup['hash'][:16]}...")
            for loc in dup["locations"][:3]:  # Show first 3 locations
                print(f"   - {loc['path']}")


def main():
    """Main execution function."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyze media exports for duplicate files using xxHash",
        epilog="Examples:\n"
        "  %(prog)s /path/to/originals/google-export\n"
        "  %(prog)s /path/to/dir1 /path/to/dir2 --output results.json\n"
        "  %(prog)s /path/to/originals --no-checkpoint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "input_dirs",
        nargs="+",
        type=str,
        help="One or more directories to scan for duplicate media files",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output JSON file path (default: duplicate_analysis.json in script directory)",
    )
    parser.add_argument(
        "--checkpoint",
        "-c",
        type=str,
        default=None,
        help="Checkpoint file path (default: duplicate_analysis_checkpoint.pkl in script directory)",
    )
    parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Disable checkpoint/resume functionality",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=None,
        help="Number of worker processes (default: auto-detect based on CPU count)",
    )
    parser.add_argument(
        "--skip-banned",
        action="store_true",
        default=True,
        help="Skip banned files/directories (default: True)",
    )

    args = parser.parse_args()

    # Set up paths
    script_dir = Path(__file__).parent
    output_file = (
        Path(args.output) if args.output else script_dir / "duplicate_analysis.json"
    )
    checkpoint_file = (
        Path(args.checkpoint)
        if args.checkpoint
        else script_dir / "duplicate_analysis_checkpoint.pkl"
    )

    # Convert input directories to Path objects
    base_paths = [Path(d).resolve() for d in args.input_dirs]

    # Validate input directories
    invalid_paths = [p for p in base_paths if not p.exists()]
    if invalid_paths:
        print("Error: The following paths do not exist:")
        for path in invalid_paths:
            print(f"  - {path}")
        return 1

    # Find common base path for relative path display
    try:
        common_base = Path(os.path.commonpath(base_paths))
    except ValueError:
        # Paths on different drives (Windows) or no common path
        common_base = Path("/")

    # Initialize banned files filter
    banned_filter = (
        BannedFilesFilter()
        if args.skip_banned
        else BannedFilesFilter(additional_patterns=[])
    )

    print("=" * 80)
    print("MEDIA DUPLICATE ANALYSIS (xxHash)")
    print("=" * 80)
    print(f"\nScanning {len(base_paths)} path(s):")
    for path in base_paths:
        print(f"  - {path}")
    print(f"\nCommon base path: {common_base}")
    print(f"Output file: {output_file}")
    print(f"Checkpoint file: {checkpoint_file}")
    print(f"Skip banned files: {args.skip_banned}")
    print("-" * 80)

    # Scan and hash all files
    use_checkpoint = not args.no_checkpoint
    hash_map = scan_media_directories(
        base_paths, checkpoint_file, banned_filter, use_checkpoint
    )

    # Analyze duplicates
    print("\nAnalyzing duplicates...")
    results = analyze_duplicates(hash_map, common_base)

    # Print summary
    print_summary(results)

    # Save detailed results to JSON
    print(f"\n\nSaving detailed results to: {output_file}")

    # Convert Path objects to strings for JSON serialization
    json_results = {
        "scan_info": {
            "input_directories": [str(p) for p in base_paths],
            "common_base": str(common_base),
            "skip_banned": args.skip_banned,
        },
        "statistics": {
            "total_unique_files": results["total_unique_files"],
            "total_files_with_duplicates": results["total_files_with_duplicates"],
            "total_duplicate_instances": results["total_duplicate_instances"],
            "wasted_space_bytes": results["wasted_space_bytes"],
            "wasted_space_mb": results["wasted_space_mb"],
            "wasted_space_gb": results["wasted_space_gb"],
        },
        "duplicates": results["duplicates"],
    }

    with open(output_file, "w") as f:
        json.dump(json_results, f, indent=2)

    print("Analysis complete!")
    print(f"\nDetailed results saved to: {output_file}")

    # Clean up checkpoint file after successful completion
    if use_checkpoint and checkpoint_file.exists():
        print("Cleaning up checkpoint file...")
        checkpoint_file.unlink()

    return 0


if __name__ == "__main__":
    sys.exit(main())
