#!/usr/bin/env python3
"""
Memoria - Unified Media Processor

Automatically detects and runs all applicable processors for an input directory.
Unlike typical detection that finds ONE match, this finds ALL matches because
a single export can contain multiple types of data (e.g., Google export with
Photos + Chat + Voice).

Usage:
    memoria.py <input_dir> [-o output_dir] [--verbose] [--workers N]
    memoria.py --list-processors
"""

import argparse
import sys
import os
import threading
import multiprocessing
from pathlib import Path
from importlib import import_module
from concurrent.futures import ProcessPoolExecutor, as_completed
from queue import Empty as QueueEmpty

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from processors.registry import ProcessorRegistry
from common.utils import (
    setup_logging,
    add_export_log_handler,
    remove_export_log_handler,
)
from common.env_loader import load_dotenv_file
from common.dependency_checker import (
    check_immich_cli,
    print_immich_error,
)
from common.upload_targets import build_upload_targets
from common.immich_uploader import verify_auth, upload as immich_upload
from common.processor_config import get_effective_output_dir


def upload_worker_thread(
    upload_queue,
    immich_url: str,
    immich_key: str,
    immich_concurrency: int,
    immich_skip_hash: bool,
    stop_event: threading.Event,
) -> None:
    """Worker thread that processes upload tasks sequentially

    This thread runs concurrently with processing, consuming upload tasks
    from the queue and executing them one export at a time.

    Args:
        upload_queue: Multiprocessing queue containing (export_name, upload_tasks) tuples
        immich_url: Immich server URL
        immich_key: Immich API key
        immich_concurrency: Number of concurrent uploads per task
        immich_skip_hash: Whether to skip hash checking
        stop_event: Event to signal thread should stop
    """
    print("[Upload Worker] Started - will process uploads sequentially")
    print()

    while not stop_event.is_set():
        try:
            # Wait for an upload task (timeout so we can check stop_event)
            try:
                export_name, upload_tasks = upload_queue.get(timeout=0.5)
            except (QueueEmpty, TimeoutError):
                continue  # Queue empty, check stop_event and try again

            if upload_tasks is None:
                # Sentinel value to stop
                break

            # Process all uploads for this export
            separator = "=" * 70
            print()
            print(separator)
            print(
                f"[Upload Worker] Processing {export_name} ({len(upload_tasks)} upload(s))"
            )
            print(separator)
            print()

            for idx, (up_path, album_name, processor_name) in enumerate(
                upload_tasks, 1
            ):
                print(f"  [{idx}/{len(upload_tasks)}] {processor_name} → {album_name}")
                try:
                    rc = immich_upload(
                        path=up_path,
                        album=album_name,
                        url=immich_url,
                        key=immich_key,
                        skip_hash=immich_skip_hash,
                        concurrency=immich_concurrency,
                        include_hidden=False,
                        recursive=True,
                    )
                    prefix = "✓" if rc == 0 else "✗"
                    print(f"    {prefix} Completed (rc={rc})")
                except Exception as e:
                    print(f"    ✗ Failed: {e}")

            print()
            print(f"[Upload Worker] Completed {export_name}")
            print(separator)
            print()

            upload_queue.task_done()

        except Exception as e:
            print(f"[Upload Worker] Error: {e}")

    print("[Upload Worker] Stopped")


def load_all_processors(registry: ProcessorRegistry) -> None:
    """Load and register all available processors

    Args:
        registry: ProcessorRegistry instance to register processors with
    """
    processors_loaded = []
    processors_failed = []

    # Get base directory
    base_dir = Path(__file__).parent

    # Try to load each processor
    processors_dir = base_dir / "processors"

    for pkg_dir in sorted(
        (
            d
            for d in processors_dir.iterdir()
            if d.is_dir() and (d / "processor.py").exists()
        ),
        key=lambda x: x.name,
    ):
        pkg = pkg_dir.name
        module_name = f"processors.{pkg}.processor"
        try:
            module = import_module(module_name)
            if hasattr(module, "get_processor"):
                processor = module.get_processor()
                registry.register(processor)
                processors_loaded.append(pkg)
            else:
                processors_failed.append((pkg, "No get_processor() function found"))
        except ImportError as e:
            processors_failed.append((pkg, f"Import error: {e}"))
        except Exception as e:
            processors_failed.append((pkg, f"Error: {e}"))

    # Report loading status
    if processors_loaded:
        print(f"Loaded {len(processors_loaded)} processor(s)")

    if processors_failed:
        print(f"Warning: {len(processors_failed)} processor(s) failed to load:")
        for name, error in processors_failed:
            print(f"  - {name}: {error}")


def show_supported_formats() -> None:
    """Display all supported export formats"""
    print("\nSupported formats (can match multiple per export):")
    print()
    print("  Google Services (can match multiple):")
    print("    • Google Photos: Google Photos/ directory with album folders")
    print("    • Google Chat: Google Chat/Groups/ and Google Chat/Users/")
    print("    • Google Voice: Voice/Calls/ with HTML files")
    print()
    print("  Snapchat:")
    print("    • Snapchat (Unified): memories/ and messages/ subdirectories")
    print("    • Snapchat Memories: media/, overlays/, metadata.json")
    print("    • Snapchat Messages: json/ with chat_history.json")
    print()
    print("  Instagram (can match multiple):")
    print("    • Instagram Messages: your_instagram_activity/messages/inbox/")
    print("    • Instagram Public Media: media/posts/ and media/archived_posts/")
    print("    • Instagram Old Format: YYYY-MM-DD_HH-MM-SS_UTC.* files in root")
    print()
    print("Examples:")
    print("  google-username-20250526/     → Google Photos + Chat + Voice")
    print("  instagram-username-20251007/  → Instagram Messages + Public Media")
    print("  snapchat-username-20251007/   → Snapchat Memories")
    print("  snapmsgs-username-20251017/   → Snapchat Messages")
    print()


def _get_structure_signature(path: Path) -> tuple:
    """Create a signature of directory structure for caching detection.

    Uses top-level subdirectory names to identify export type.

    Args:
        path: Path to directory to analyze

    Returns:
        Tuple of subdirectory names, or None if unable to scan
    """
    try:
        # Get sorted list of first-level subdirectories
        subdirs = sorted(
            [
                d.name
                for d in path.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            ][:20]
        )  # Limit to first 20 for performance
        return tuple(subdirs)
    except Exception:
        return None


def _process_export_worker(
    export_dir: Path,
    output_base: str,
    verbose: bool,
    workers: int,
    immich_enabled: bool,
    immich_url: str,
    immich_key: str,
    immich_concurrency: int,
    immich_skip_hash: bool,
    temp_dir: str,
    export_idx: int,
    total_exports: int,
    upload_queue=None,
    processor_filter: str = None,
) -> tuple[str, int, int]:
    """Worker function for parallel processing of exports

    This function is designed to be pickled for multiprocessing.
    It recreates the necessary objects internally.

    Args:
        export_dir: Path to the export directory
        output_base: Base output directory (or None)
        verbose: Verbose logging flag
        workers: Number of workers for internal processing
        immich_enabled: Whether Immich uploads are enabled
        immich_url: Immich server URL
        immich_key: Immich API key
        immich_concurrency: Number of concurrent uploads
        immich_skip_hash: Whether to skip hash checking
        temp_dir: Directory for temporary preprocessing files
        export_idx: Index of this export (for display)
        total_exports: Total number of exports being processed
        upload_queue: Optional multiprocessing queue to collect upload tasks
        processor_filter: Optional processor name to filter to (case-insensitive)

    Returns:
        Tuple of (export_name, success_count, failed_count)
    """
    # Recreate registry and load processors
    # Note: sys.path modification needed for pickled worker processes
    sys.path.insert(0, str(Path(__file__).parent))
    # Reimport needed for pickled worker processes
    from processors.registry import ProcessorRegistry  # noqa: F811
    from common.utils import (  # noqa: F811
        add_export_log_handler,
        remove_export_log_handler,
    )

    registry = ProcessorRegistry()
    load_all_processors(registry)

    # Set up per-export logging
    export_log_handler = add_export_log_handler(export_dir.name, verbose=verbose)

    try:
        # Create a simple args namespace
        class Args:
            def __init__(self):
                self.output = None
                self.verbose = False
                self.workers = None
                self.processor = None

        args = Args()
        args.output = output_base
        args.verbose = verbose
        args.workers = workers
        args.processor = processor_filter

        # Adjust output path for multi-export mode
        if output_base and total_exports > 1:
            export_output_dir = str(Path(output_base) / export_dir.name)
            args.output = export_output_dir

        # Print header for this export
        if total_exports > 1:
            mega_separator = "#" * 70
            print()
            print(mega_separator)
            print(f"EXPORT {export_idx}/{total_exports}: {export_dir.name}")
            print(mega_separator)
            print()

        # Process the export (no detection cache in parallel mode to avoid sharing issues)
        success_count, failed_count, _upload_tasks = process_single_export(
            export_dir,
            registry,
            args,
            immich_enabled,
            immich_url,
            immich_key,
            immich_concurrency,
            immich_skip_hash,
            temp_dir,
            detection_cache=None,
            upload_queue=upload_queue,
        )

        return export_dir.name, success_count, failed_count

    finally:
        # Clean up per-export log handler
        if export_log_handler:
            remove_export_log_handler(export_log_handler)


def process_single_export(
    input_path: Path,
    registry: ProcessorRegistry,
    args,
    immich_enabled: bool,
    immich_url: str,
    immich_key: str,
    immich_concurrency: int,
    immich_skip_hash: bool,
    temp_dir: str,
    detection_cache: dict = None,
    upload_queue=None,
) -> tuple[int, int, list]:
    """Process a single export directory

    Args:
        input_path: Path to the export directory
        registry: ProcessorRegistry instance
        args: Command line arguments
        immich_enabled: Whether Immich uploads are enabled
        immich_url: Immich server URL
        immich_key: Immich API key
        immich_concurrency: Number of concurrent uploads
        immich_skip_hash: Whether to skip hash checking
        temp_dir: Directory for temporary preprocessing files
        detection_cache: Optional cache for processor detection
        upload_queue: Optional multiprocessing queue to collect upload tasks

    Returns:
        Tuple of (success_count, failed_count, upload_tasks)
        upload_tasks is a list of (up_path, album_name, processor_name) tuples
    """
    # Detect ALL matching processors with caching
    print(f"Analyzing: {input_path}")
    print()

    matching_processors = None
    cache_key = None

    if detection_cache is not None:
        cache_key = _get_structure_signature(input_path)
        if cache_key and cache_key in detection_cache:
            matching_processors = detection_cache[cache_key]
            print("(using cached processor detection)")

    if matching_processors is None:
        matching_processors = registry.detect_all(input_path)
        if detection_cache is not None and cache_key:
            detection_cache[cache_key] = matching_processors

    # Filter to specified processor if --processor is used
    processor_filter = getattr(args, "processor", None)
    if processor_filter:
        matching_processors = [
            p
            for p in matching_processors
            if p.get_name().lower() == processor_filter.lower()
        ]

    if not matching_processors:
        if processor_filter:
            # Specified processor not found in this export - warning, not error
            print(f"No {processor_filter} exports found in: {input_path}")
            return 0, 0, []
        else:
            # No processors matched at all - this is an error
            print(f"ERROR: No processors matched input directory: {input_path}")
            show_supported_formats()
            print("Run with --list-processors to see all available processors.")
            return 0, 1, []

    # Show detected processors
    print(f"Detected {len(matching_processors)} processor(s):")
    for processor in matching_processors:
        print(f"  • {processor.get_name()}")
    print()

    if args.output:
        print(f"Output base directory: {args.output}")
        print()
        # Ensure base output directory exists
        try:
            Path(args.output).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"ERROR: Failed to create output base directory '{args.output}': {e}")
            return 0, 1, []

    # Run ALL matching processors
    success_count = 0
    failed_count = 0
    results = []
    upload_tasks = []  # Collect upload tasks if queuing

    for idx, processor in enumerate(matching_processors, 1):
        separator = "=" * 70
        print(separator)
        print(f"[{idx}/{len(matching_processors)}] Running: {processor.get_name()}")
        print(separator)
        print()

        try:
            # Prepare kwargs for processor
            kwargs = {}
            if args.verbose:
                kwargs["verbose"] = True
            if args.workers:
                kwargs["workers"] = args.workers

            # Pass temp_dir to processors
            kwargs["temp_dir"] = temp_dir

            # Determine output directory
            output_dir = args.output if args.output else None

            # Run processor
            success = processor.process(str(input_path), output_dir, **kwargs)

            if success:
                success_count += 1
                status = "✓ SUCCESS"
                print()
                print(f"{status}: {processor.get_name()} completed successfully")

                # Compute processor-specific effective output directory
                pname = processor.get_name()
                effective_output = get_effective_output_dir(pname, args.output)

                # Upload to Immich if enabled
                if immich_enabled and effective_output:
                    targets = build_upload_targets(
                        pname, str(input_path), effective_output
                    )
                    if not targets:
                        print("No upload targets derived for this processor.")
                    else:
                        if upload_queue is not None:
                            # Queue uploads for the upload worker thread
                            print(
                                f"Queueing {len(targets)} upload(s) for sequential processing..."
                            )
                            for up_path, album_name in targets:
                                upload_tasks.append((up_path, album_name, pname))
                        else:
                            # Upload immediately (sequential mode)
                            print("Starting Immich uploads:")
                            for up_path, album_name in targets:
                                try:
                                    rc = immich_upload(
                                        path=up_path,
                                        album=album_name,
                                        url=immich_url,
                                        key=immich_key,
                                        skip_hash=immich_skip_hash,
                                        concurrency=immich_concurrency,
                                        include_hidden=False,
                                        recursive=True,
                                    )
                                    prefix = "✓" if rc == 0 else "✗"
                                    print(
                                        f"  {prefix} {pname} → {album_name} from {up_path}"
                                    )
                                except Exception as e:
                                    print(
                                        f"  ✗ Upload failed for {up_path} → {album_name}: {e}"
                                    )
            else:
                failed_count += 1
                status = "✗ FAILED"
                print()
                print(f"{status}: {processor.get_name()} failed")

            results.append((processor.get_name(), status))

        except KeyboardInterrupt:
            print()
            print()
            print("Interrupted by user")
            failed_count += 1
            results.append((processor.get_name(), "✗ INTERRUPTED"))
            raise  # Re-raise to stop processing other exports
        except Exception as e:
            failed_count += 1
            status = f"✗ ERROR: {e}"
            print()
            print(f"ERROR: {processor.get_name()} failed with exception:")
            print(f"  {e}")
            results.append((processor.get_name(), "✗ ERROR"))

        print()

    # Summary
    separator = "=" * 70
    print(separator)
    print("SUMMARY")
    print(separator)
    print()

    for processor_name, status in results:
        print(f"  {status:15s} {processor_name}")

    print()
    print(
        f"Total: {success_count} succeeded, {failed_count} failed out of {len(matching_processors)}"
    )
    print(separator)

    # If uploads were queued, add them to the queue
    if upload_queue is not None and upload_tasks:
        print(f"Adding {len(upload_tasks)} upload(s) to queue for {input_path.name}...")
        upload_queue.put((input_path.name, upload_tasks))

    return success_count, failed_count, upload_tasks


def _collect_upload_files(path: Path, ignore_patterns: list[str]) -> list[Path]:
    """Collect all files that will be uploaded from a directory.

    Args:
        path: Path to scan for files
        ignore_patterns: List of glob patterns to ignore

    Returns:
        List of file paths that will be uploaded
    """
    import fnmatch

    files_to_upload = []

    if not path.exists():
        return files_to_upload

    if path.is_file():
        files_to_upload.append(path)
        return files_to_upload

    # Walk directory recursively
    for root, _dirs, files in os.walk(path):
        root_path = Path(root)

        # Check if this directory should be ignored
        relative_root = root_path.relative_to(path)
        skip_dir = False
        for pattern in ignore_patterns:
            # Check if any part of the path matches the ignore pattern
            for part_idx in range(len(relative_root.parts) + 1):
                test_path = (
                    str(Path(*relative_root.parts[:part_idx])) if part_idx > 0 else ""
                )
                if fnmatch.fnmatch(
                    test_path, pattern.replace("**/", "").replace("/**", "")
                ):
                    skip_dir = True
                    break
            if skip_dir:
                break

        if skip_dir:
            continue

        for file in files:
            file_path = root_path / file

            # Skip hidden files by default
            if file.startswith("."):
                continue

            # Check if file should be ignored based on patterns
            relative_file = file_path.relative_to(path)
            should_ignore = False

            for pattern in ignore_patterns:
                if fnmatch.fnmatch(
                    str(relative_file), pattern.replace("**/", "").replace("/**", "")
                ):
                    should_ignore = True
                    break

            if not should_ignore:
                files_to_upload.append(file_path)

    return sorted(files_to_upload)


def upload_only_mode(
    input_path: Path,
    upload_path: Path,
    registry: ProcessorRegistry,
    immich_url: str,
    immich_key: str,
    immich_concurrency: int,
    immich_skip_hash: bool,
) -> int:
    """Handle upload-only mode: detect processors and upload existing processed files.

    Args:
        input_path: Original export directory (for processor detection and username extraction)
        upload_path: Path to processed output directory to upload
        registry: ProcessorRegistry instance
        immich_url: Immich server URL
        immich_key: Immich API key
        immich_concurrency: Number of concurrent uploads
        immich_skip_hash: Whether to skip hash checking

    Returns:
        0 for success, 1 for failure
    """
    from datetime import datetime

    print("Upload-only mode")
    print(f"Original export: {input_path}")
    print(f"Processed output: {upload_path}")
    print()

    # Create logs directory and upload log file
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"upload_only_{input_path.name}_{timestamp}.log"
    log_file = logs_dir / log_filename

    print(f"Upload log will be saved to: {log_file}")
    print()

    # Validate paths
    if not input_path.exists():
        print(f"ERROR: Original export directory does not exist: {input_path}")
        return 1

    if not upload_path.exists():
        print(f"ERROR: Processed output directory does not exist: {upload_path}")
        return 1

    # Detect processors from original export
    print("Analyzing original export to detect processor types...")
    matching_processors = registry.detect_all(input_path)

    if not matching_processors:
        print(f"ERROR: No processors matched original export directory: {input_path}")
        show_supported_formats()
        return 1

    print(f"Detected {len(matching_processors)} processor(s):")
    for processor in matching_processors:
        print(f"  • {processor.get_name()}")
    print()

    # Map processor names to their expected output subdirectories within upload_path
    processor_output_map = {}

    for processor in matching_processors:
        pname = processor.get_name()

        # Get effective output directory using centralized config
        effective_output_str = get_effective_output_dir(pname, str(upload_path))

        if not effective_output_str:
            print(f"WARNING: Unknown processor '{pname}', skipping uploads")
            continue

        effective_output = Path(effective_output_str)

        # Verify the directory exists
        if not effective_output.exists():
            print(
                f"WARNING: Expected output directory does not exist: {effective_output}"
            )
            print(f"         Skipping uploads for {pname}")
            continue

        processor_output_map[pname] = str(effective_output)

    if not processor_output_map:
        print("ERROR: No valid output directories found for detected processors")
        return 1

    # Get default ignore patterns (same as used by immich_upload)
    env_ignore = os.environ.get("IMMICH_IGNORE_PATTERNS")
    if env_ignore:
        ignore_patterns = [p.strip() for p in env_ignore.split(",") if p.strip()]
    else:
        # Note: needs_matching requires **/*matching/** pattern due to underscore handling
        ignore_patterns = ["issues/**", "**/*matching/**"]

    # Open log file for writing
    with open(log_file, "w", encoding="utf-8") as log:
        log.write("Upload-Only Mode Log\n")
        log.write(f"{'=' * 70}\n")
        log.write(f"Timestamp: {timestamp}\n")
        log.write(f"Original export: {input_path}\n")
        log.write(f"Processed output: {upload_path}\n")
        log.write(f"Ignore patterns: {', '.join(ignore_patterns)}\n")
        log.write(f"{'=' * 70}\n\n")

        # Perform uploads for each processor
        print("Starting Immich uploads:")
        print()

        success_count = 0
        failed_count = 0
        total_files_uploaded = 0

        for idx, processor in enumerate(matching_processors, 1):
            pname = processor.get_name()

            if pname not in processor_output_map:
                continue

            effective_output = processor_output_map[pname]

            separator = "=" * 70
            print(separator)
            print(f"[{idx}/{len(matching_processors)}] Uploading: {pname}")
            print(separator)
            print()

            try:
                # Build upload targets using the original export path and effective output
                targets = build_upload_targets(pname, str(input_path), effective_output)

                if not targets:
                    print(f"No upload targets derived for {pname}")
                    log.write(f"Processor: {pname}\n")
                    log.write("  No upload targets\n\n")
                    continue

                print(f"Found {len(targets)} upload target(s):")
                for up_path, album_name in targets:
                    print(f"  • {up_path} → {album_name}")
                print()

                # Perform uploads
                for up_path, album_name in targets:
                    try:
                        # Collect files that will be uploaded
                        files_to_upload = _collect_upload_files(
                            Path(up_path), ignore_patterns
                        )

                        # Log upload details
                        log.write(f"Processor: {pname}\n")
                        log.write(f"Upload path: {up_path}\n")
                        log.write(f"Album: {album_name}\n")
                        log.write(f"Files ({len(files_to_upload)}):\n")

                        for file_path in files_to_upload:
                            log.write(f"  {file_path}\n")
                        log.write("\n")

                        total_files_uploaded += len(files_to_upload)

                        # Perform the actual upload
                        rc = immich_upload(
                            path=up_path,
                            album=album_name,
                            url=immich_url,
                            key=immich_key,
                            skip_hash=immich_skip_hash,
                            concurrency=immich_concurrency,
                            include_hidden=False,
                            recursive=True,
                        )
                        prefix = "✓" if rc == 0 else "✗"
                        print(
                            f"  {prefix} {pname} → {album_name} ({len(files_to_upload)} files)"
                        )

                        # Log result
                        log.write(
                            f"  Upload result: {'SUCCESS' if rc == 0 else f'FAILED (rc={rc})'}\n\n"
                        )

                        if rc == 0:
                            success_count += 1
                        else:
                            failed_count += 1
                    except Exception as e:
                        print(f"  ✗ Upload failed for {up_path} → {album_name}: {e}")
                        log.write(f"  Upload result: EXCEPTION - {e}\n\n")
                        failed_count += 1

            except Exception as e:
                print(f"ERROR: Failed to upload {pname}: {e}")
                log.write(f"Processor: {pname}\n")
                log.write(f"  ERROR: {e}\n\n")
                failed_count += 1

            print()

        # Write summary to log
        log.write(f"{'=' * 70}\n")
        log.write("SUMMARY\n")
        log.write(f"{'=' * 70}\n")
        log.write(f"Total files logged: {total_files_uploaded}\n")
        log.write(f"Upload tasks: {success_count} succeeded, {failed_count} failed\n")
        log.write(f"{'=' * 70}\n")

    # Summary
    separator = "=" * 70
    print(separator)
    print("UPLOAD SUMMARY")
    print(separator)
    print()
    print(f"Total files: {total_files_uploaded}")
    print(f"Upload tasks: {success_count} succeeded, {failed_count} failed")
    print()
    print(f"Upload log saved to: {log_file}")
    print(separator)

    return 0 if failed_count == 0 else 1


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Process media exports. Automatically detects and runs all applicable processors.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Process export in current directory with default outputs
  %(prog)s /path/to/export
  
  # Process with custom output base directory
  %(prog)s /path/to/export -o /path/to/output
  
  # Upload previously processed export (skips processing)
  %(prog)s /path/to/original/export --upload-only /path/to/processed/output
  
  # Process multiple exports sequentially
  %(prog)s --originals /path/to/originals_folder -o /path/to/output
  
  # Process multiple exports in parallel (2 at a time)
  %(prog)s --originals /path/to/originals_folder -o /path/to/output --parallel-exports 2
  
  # Process with verbose logging and 8 workers
  %(prog)s /path/to/export --verbose --workers 8
  
  # List all available processors
  %(prog)s --list-processors
        """,
    )

    parser.add_argument(
        "input_dir",
        nargs="?",
        help="Input directory containing media export",
    )
    parser.add_argument(
        "--originals",
        help="Directory containing multiple export folders to process",
    )
    parser.add_argument(
        "--processor",
        metavar="NAME",
        help="Only run the specified processor (case-insensitive). Use --list-processors to see available names.",
    )
    parser.add_argument(
        "--parallel-exports",
        type=int,
        default=None,
        metavar="N",
        help="Process N exports in parallel when using --originals (default: 1 for sequential; recommended: 2-4)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output base directory (each processor creates its own subdirectory)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        help="Number of parallel workers for processing (default: CPU count - 1)",
    )
    parser.add_argument(
        "--list-processors",
        action="store_true",
        help="List all available processors and exit",
    )

    # Immich/.env integration
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip uploading processed output to Immich",
    )
    parser.add_argument(
        "--upload-only",
        metavar="PATH",
        help="Upload-only mode: provide path to processed output directory (skips processing). "
        "Still requires input_dir to detect processor type and extract username for album naming.",
    )
    parser.add_argument(
        "--immich-url",
        help="Immich server URL (overrides env/.env IMMICH_INSTANCE_URL)",
    )
    parser.add_argument(
        "--immich-key",
        help="Immich API key (overrides env/.env IMMICH_API_KEY)",
    )
    parser.add_argument(
        "--immich-concurrency",
        type=int,
        default=None,
        help="Number of concurrent uploads (default 4; overrides env/.env IMMICH_UPLOAD_CONCURRENCY)",
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to .env file to load (default: ./.env if present)",
    )

    args = parser.parse_args()

    # Validate --upload-only arguments
    if args.upload_only:
        if args.skip_upload:
            parser.error("--upload-only and --skip-upload are mutually exclusive")
            return 1
        if args.originals:
            parser.error(
                "--upload-only is not compatible with --originals (process multiple exports separately)"
            )
            return 1
        if not args.input_dir:
            parser.error(
                "--upload-only requires input_dir to detect processor type and extract username"
            )
            return 1

    # Load .env early (CLI > env > .env precedence is enforced later when reading values)
    load_dotenv_file(args.env_file)

    # Configure logging
    log_file = None
    if args.verbose:
        from datetime import datetime

        # Create logs directory if it doesn't exist
        logs_dir = Path("logs")
        logs_dir.mkdir(parents=True, exist_ok=True)

        log_file = (
            logs_dir / f"media_processor_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
    setup_logging(verbose=args.verbose, log_file=log_file)

    # Initialize registry and load all processors
    registry = ProcessorRegistry()
    load_all_processors(registry)

    # Validate --processor argument if provided
    if args.processor:
        if not registry.get_by_name(args.processor):
            print(f"ERROR: Unknown processor '{args.processor}'")
            print("Available processors:")
            for p in registry.get_all_processors():
                print(f"  - {p.get_name()}")
            return 1

    # Handle --list-processors
    if args.list_processors:
        print("Available processors:")
        print()
        processors = registry.get_all_processors()
        if not processors:
            print("  No processors loaded.")
        else:
            for processor in processors:
                print(
                    f"  • {processor.get_name():30s} (priority: {processor.get_priority()})"
                )
        print()
        print(f"Total: {registry.get_processor_count()} processor(s)")
        return 0

    # Require input_dir or --originals if not listing processors
    if not args.input_dir and not args.originals:
        parser.error("either input_dir or --originals is required")
        return 1

    # Ensure only one is specified
    if args.input_dir and args.originals:
        parser.error("cannot specify both input_dir and --originals")
        return 1

    # Build list of directories to process
    dirs_to_process = []

    if args.originals:
        originals_path = Path(args.originals).resolve()

        if not originals_path.exists():
            print(f"ERROR: Originals directory does not exist: {originals_path}")
            return 1

        if not originals_path.is_dir():
            print(f"ERROR: Originals path is not a directory: {originals_path}")
            return 1

        # Get all subdirectories, skip hidden directories
        subdirs = sorted(
            (
                d
                for d in originals_path.iterdir()
                if d.is_dir() and not d.name.startswith(".")
            ),
            key=lambda x: x.name,
        )

        if not subdirs:
            print(f"ERROR: No subdirectories found in: {originals_path}")
            return 1

        print(f"Found {len(subdirs)} export(s) to process in: {originals_path}")
        for subdir in subdirs:
            print(f"  • {subdir.name}")
        print()

        dirs_to_process = subdirs
    else:
        # Single directory mode
        input_path = Path(args.input_dir).resolve()

        if not input_path.exists():
            print(f"ERROR: Input directory does not exist: {input_path}")
            return 1

        if not input_path.is_dir():
            print(f"ERROR: Input path is not a directory: {input_path}")
            return 1

        dirs_to_process = [input_path]

    # Resolve Immich configuration
    cpu_count = multiprocessing.cpu_count()

    # Validate and set parallel_exports
    parallel_exports = args.parallel_exports if args.parallel_exports else 1

    # Validate parallel_exports for --originals mode
    if args.parallel_exports and not args.originals:
        print("WARNING: --parallel-exports is only used with --originals; ignoring")
        parallel_exports = 1

    # Smart defaults and warnings for parallel processing
    if parallel_exports > 1 and args.originals:
        # Calculate total processes that will be spawned
        workers_per_export = args.workers if args.workers else max(1, cpu_count - 1)
        total_processes = parallel_exports * (
            workers_per_export + 1
        )  # +1 for main process per export

        # Warn if over-subscribing significantly
        if total_processes > cpu_count * 1.5:
            print(
                f"WARNING: Running {parallel_exports} exports with {workers_per_export} workers each"
            )
            print(
                f"         will create ~{total_processes} processes on {cpu_count} CPU cores"
            )
            print(
                "         This may cause performance degradation due to over-subscription."
            )
            print()

            # Suggest better configuration
            suggested_workers = max(1, (cpu_count // parallel_exports) - 1)
            print(
                f"SUGGESTION: Try --parallel-exports {parallel_exports} --workers {suggested_workers}"
            )
            print(
                f"            or reduce --parallel-exports to {max(1, cpu_count // (workers_per_export + 1))}"
            )
            print()

    immich_url = args.immich_url or os.environ.get("IMMICH_INSTANCE_URL")
    immich_key = args.immich_key or os.environ.get("IMMICH_API_KEY")
    immich_concurrency = (
        args.immich_concurrency
        if args.immich_concurrency is not None
        else int(os.environ.get("IMMICH_UPLOAD_CONCURRENCY", "4"))
    )

    # Read skip_hash from environment variable (default to True for speed)
    from common.utils import parse_bool_env

    env_skip_hash = os.environ.get("IMMICH_SKIP_HASH")
    immich_skip_hash = (
        parse_bool_env(env_skip_hash) if env_skip_hash is not None else True
    )

    # Read temp_dir from environment variable
    temp_dir = os.environ.get("TEMP_DIR", "../pre")

    # Read consolidation mode from environment variable (defaults to True)
    env_consolidate = os.environ.get("CONSOLIDATE_EXPORTS")
    consolidate_exports = parse_bool_env(env_consolidate) if env_consolidate else True

    # Preflight Immich availability when uploads are enabled
    immich_enabled = not args.skip_upload
    if immich_enabled:
        # Only check for Immich if credentials are configured
        if not immich_url or not immich_key:
            # Silently disable Immich if env vars not set (not an error condition)
            immich_enabled = False
        elif not check_immich_cli():
            # Only error if user has configured Immich but CLI is missing
            print_immich_error()
            print("Continuing without upload (use --skip-upload to hide this message).")
            immich_enabled = False
        elif not verify_auth(immich_url, immich_key):
            print(
                "WARNING: Immich authentication failed (server-info). Skipping upload."
            )
            immich_enabled = False

    # Handle upload-only mode
    if args.upload_only:
        if not immich_enabled:
            print("ERROR: Upload-only mode requires valid Immich configuration")
            return 1

        input_path = Path(args.input_dir).resolve()
        upload_path = Path(args.upload_only).resolve()

        return upload_only_mode(
            input_path,
            upload_path,
            registry,
            immich_url,
            immich_key,
            immich_concurrency,
            immich_skip_hash,
        )

    # Process all directories
    total_exports_success = 0
    total_exports_failed = 0
    export_results = []

    # Resolve output base path once to avoid redundant Path operations
    output_base_str = args.output if args.output else None
    output_base = Path(args.output) if args.output else None

    # Handle consolidation mode
    consolidated_paths = set()
    if consolidate_exports and len(dirs_to_process) > 1:
        consolidation_groups = registry.group_for_consolidation(dirs_to_process)

        # Filter to specified processor if --processor is used
        if args.processor:
            target_name = args.processor.lower()
            consolidation_groups = {
                p: paths
                for p, paths in consolidation_groups.items()
                if p.get_name().lower() == target_name
            }

        for processor, paths in consolidation_groups.items():
            separator = "=" * 70
            print(separator)
            print(f"Consolidating {len(paths)} {processor.get_name()} exports...")
            print(separator)
            for p in paths:
                print(f"  - {p.name}")
            print()

            # Determine output directory for consolidated export
            if output_base:
                if args.processor:
                    # Single processor specified - use output directory directly
                    consolidated_output = str(output_base)
                else:
                    # Multiple processors possible - add suffix to distinguish
                    consolidated_output = str(
                        output_base
                        / f"{processor.get_name().lower().replace(' ', '_')}_consolidated"
                    )
            else:
                consolidated_output = None

            try:
                success = processor.process_consolidated(
                    [str(p) for p in paths],
                    consolidated_output,
                    verbose=args.verbose,
                    workers=args.workers,
                    temp_dir=temp_dir,
                )

                if success:
                    consolidated_paths.update(paths)
                    total_exports_success += 1
                    export_results.append(
                        (f"{processor.get_name()} (consolidated)", "SUCCESS")
                    )
                else:
                    total_exports_failed += 1
                    export_results.append(
                        (f"{processor.get_name()} (consolidated)", "FAILED")
                    )
            except Exception as e:
                print(f"ERROR: Consolidation failed for {processor.get_name()}: {e}")
                total_exports_failed += 1
                export_results.append(
                    (f"{processor.get_name()} (consolidated)", "ERROR")
                )

        # Remove consolidated exports from normal processing
        dirs_to_process = [d for d in dirs_to_process if d not in consolidated_paths]

    try:
        if parallel_exports > 1 and len(dirs_to_process) > 1:
            # ============== PARALLEL PROCESSING MODE ==============
            print(
                f"Processing {len(dirs_to_process)} exports in parallel ({parallel_exports} at a time)..."
            )
            print()

            # Create upload queue and start upload worker thread if Immich is enabled
            upload_queue = None
            upload_thread = None
            stop_event = threading.Event()
            manager = None

            if immich_enabled:
                # Use multiprocessing Manager for a queue that can be shared across processes
                manager = multiprocessing.Manager()
                upload_queue = manager.Queue()
                upload_thread = threading.Thread(
                    target=upload_worker_thread,
                    args=(
                        upload_queue,
                        immich_url,
                        immich_key,
                        immich_concurrency,
                        immich_skip_hash,
                        stop_event,
                    ),
                    daemon=True,
                )
                upload_thread.start()
                print(
                    "Upload worker started - uploads will be processed sequentially as exports complete"
                )
                print()

            # Use ProcessPoolExecutor for parallel processing
            with ProcessPoolExecutor(max_workers=parallel_exports) as executor:
                # Submit all export processing jobs
                future_to_export = {}
                for idx, export_dir in enumerate(dirs_to_process, 1):
                    future = executor.submit(
                        _process_export_worker,
                        export_dir,
                        output_base_str,
                        args.verbose,
                        args.workers,
                        immich_enabled,
                        immich_url,
                        immich_key,
                        immich_concurrency,
                        immich_skip_hash,
                        temp_dir,
                        idx,
                        len(dirs_to_process),
                        upload_queue,  # Pass the upload queue
                        args.processor,  # Pass the processor filter
                    )
                    future_to_export[future] = (idx, export_dir)

                # Process completed exports as they finish
                for future in as_completed(future_to_export):
                    idx, export_dir = future_to_export[future]
                    try:
                        export_name, _success_count, failed_count = future.result()

                        if failed_count == 0:
                            total_exports_success += 1
                            export_results.append((export_name, "✓ SUCCESS"))
                        else:
                            total_exports_failed += 1
                            export_results.append((export_name, "✗ FAILED"))

                    except KeyboardInterrupt:
                        print()
                        print()
                        print("Processing interrupted by user")
                        total_exports_failed += 1
                        export_results.append((export_dir.name, "✗ INTERRUPTED"))
                        # Cancel remaining futures
                        for f in future_to_export:
                            f.cancel()
                        raise
                    except Exception as e:
                        print(
                            f"ERROR: Export {export_dir.name} failed with exception: {e}"
                        )
                        total_exports_failed += 1
                        export_results.append((export_dir.name, "✗ ERROR"))

            # Wait for upload queue to finish if it exists
            if upload_queue is not None:
                print()
                print(
                    "All processing complete. Waiting for remaining uploads to finish..."
                )
                upload_queue.put((None, None))  # Sentinel to stop upload worker
                if upload_thread:
                    upload_thread.join(timeout=3600)  # Wait up to 1 hour for uploads
                print("All uploads complete.")
                print()

            # Clean up the manager
            if manager is not None:
                manager.shutdown()
        else:
            # ============== SEQUENTIAL PROCESSING MODE ==============
            # Initialize detection cache for multi-export processing
            detection_cache = {} if len(dirs_to_process) > 1 else None

            for export_idx, export_dir in enumerate(dirs_to_process, 1):
                # Set up per-export logging
                export_log_handler = add_export_log_handler(
                    export_dir.name, verbose=args.verbose
                )

                try:
                    if len(dirs_to_process) > 1:
                        # Multi-export mode: add visual separator and export indicator
                        mega_separator = "#" * 70
                        print()
                        print(mega_separator)
                        print(
                            f"EXPORT {export_idx}/{len(dirs_to_process)}: {export_dir.name}"
                        )
                        print(mega_separator)
                        print()

                        # Create export-specific output directory to avoid mixing data
                        if output_base:
                            # Create a subdirectory for this export within the base output dir
                            export_output_dir = str(output_base / export_dir.name)
                            # Temporarily override args.output for this export
                            args.output = export_output_dir

                    # Process this export
                    try:
                        success_count, failed_count, _upload_tasks = (
                            process_single_export(
                                export_dir,
                                registry,
                                args,
                                immich_enabled,
                                immich_url,
                                immich_key,
                                immich_concurrency,
                                immich_skip_hash,
                                temp_dir,
                                detection_cache,
                            )
                        )

                        # Restore original output path if we modified it
                        if len(dirs_to_process) > 1 and output_base:
                            args.output = str(output_base)

                        if failed_count == 0:
                            total_exports_success += 1
                            export_results.append((export_dir.name, "✓ SUCCESS"))
                        else:
                            total_exports_failed += 1
                            export_results.append((export_dir.name, "✗ FAILED"))

                    except KeyboardInterrupt:
                        # Restore original output path if we modified it
                        if len(dirs_to_process) > 1 and output_base:
                            args.output = str(output_base)
                        total_exports_failed += 1
                        export_results.append((export_dir.name, "✗ INTERRUPTED"))
                        raise  # Stop processing remaining exports

                finally:
                    # Clean up per-export log handler
                    if export_log_handler:
                        remove_export_log_handler(export_log_handler)

    except KeyboardInterrupt:
        print()
        print()
        print("Processing interrupted by user")

    # Overall summary for multi-export mode
    if len(dirs_to_process) > 1:
        mega_separator = "#" * 70
        print()
        print(mega_separator)
        print("OVERALL SUMMARY")
        print(mega_separator)
        print()

        for export_name, status in export_results:
            print(f"  {status:15s} {export_name}")

        print()
        print(
            f"Total: {total_exports_success} succeeded, {total_exports_failed} failed "
            f"out of {len(dirs_to_process)} export(s)"
        )
        print(mega_separator)

    return 0 if total_exports_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
