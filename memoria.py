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
import logging
import sys
import os
import threading
import multiprocessing
from pathlib import Path
from importlib import import_module
from concurrent.futures import ProcessPoolExecutor, as_completed
from queue import Empty as QueueEmpty
from typing import Optional

# Add current directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Set up module logger
logger = logging.getLogger(__name__)

from processors.registry import ProcessorRegistry  # noqa: E402
from common.output.logging import (  # noqa: E402
    setup_logging,
    add_export_log_handler,
    remove_export_log_handler,
)
from common.config.env import load_dotenv_file  # noqa: E402
from common.dependency_checker import (  # noqa: E402
    check_immich_cli,
    print_immich_error,
)
from common.upload.targets import build_upload_targets  # noqa: E402
from common.upload.immich import verify_auth, upload as immich_upload  # noqa: E402
from common.config.processor import get_effective_output_dir  # noqa: E402
from common.output.paths import build_processor_output_path  # noqa: E402
from common.dedup.config import is_dedup_enabled, get_dedup_config  # noqa: E402
from common.dedup.service import DeduplicationService, save_manifest  # noqa: E402
from common.dedup.database import HashDatabase  # noqa: E402
from common.output.console import console, section_header, phase_header  # noqa: E402


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
    logger.info("[Upload Worker] Started - will process uploads sequentially")

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
            logger.info(
                "[Upload Worker] Processing %s (%d upload(s))",
                export_name,
                len(upload_tasks),
            )

            for idx, (up_path, album_name, processor_name) in enumerate(
                upload_tasks, 1
            ):
                logger.info(
                    "  [%d/%d] %s -> %s",
                    idx,
                    len(upload_tasks),
                    processor_name,
                    album_name,
                )
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
                    if rc == 0:
                        logger.info("    Completed (rc=%d)", rc)
                    else:
                        logger.warning("    Completed with issues (rc=%d)", rc)
                except Exception as e:
                    logger.error("    Failed: %s", e)

            logger.info("[Upload Worker] Completed %s", export_name)

            upload_queue.task_done()

        except Exception as e:
            logger.error("[Upload Worker] Error: %s", e)

    logger.info("[Upload Worker] Stopped")


def load_all_processors(registry: ProcessorRegistry, quiet: bool = False) -> None:
    """Load and register all available processors

    Args:
        registry: ProcessorRegistry instance to register processors with
        quiet: If True, suppress status messages (used in worker processes)
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

    # Report loading status (only if not in quiet mode)
    if not quiet:
        if processors_loaded:
            logger.info("Loaded %d processor(s)", len(processors_loaded))

        if processors_failed:
            logger.warning(
                "Warning: %d processor(s) failed to load:", len(processors_failed)
            )
            for name, error in processors_failed:
                logger.warning("  - %s: %s", name, error)


def _build_catalog_worker(
    export_dir_str: str,
    workers: int,
    log_file: Optional[str] = None,
    db_path: Optional[str] = None,
    run_id: Optional[str] = None,
) -> tuple[str, int]:
    """Build catalogs for a single export directory (multiprocessing worker).

    This function is designed to be called by ProcessPoolExecutor for parallel
    catalog building. It re-creates the ProcessorRegistry in the worker process
    since registry instances cannot be pickled across process boundaries.

    When db_path and run_id are provided, entries are written directly to the
    database for memory-efficient streaming. Otherwise, entries are collected
    but not returned (legacy mode returns empty count for compatibility).

    Args:
        export_dir_str: Path to the export directory as a string.
        workers: Number of worker threads for hashing within this process.
        log_file: Optional path to log file for verbose output.
        db_path: Optional path to SQLite database for streaming writes.
        run_id: Optional unique identifier for this processing run.

    Returns:
        Tuple of (export_dir_name, file_count).
    """
    from processors.registry import ProcessorRegistry
    from common.output.logging import init_worker_logging

    # Mark as subprocess to disable progress bars and prevent interleaved output
    os.environ["_MEMORIA_SUBPROCESS"] = "1"

    # Initialize logging for this worker process
    init_worker_logging(
        log_filename=log_file,
        app_modules=["processors", "common"],
    )

    export_dir = Path(export_dir_str)

    # Create and populate registry in this worker process
    # Use quiet=True to suppress "Loaded X processors" messages from workers
    registry = ProcessorRegistry()
    load_all_processors(registry, quiet=True)

    file_count = 0

    # Initialize database connection if streaming mode
    db = None
    if db_path and run_id:
        from common.dedup.database import HashDatabase

        db = HashDatabase(Path(db_path))

    # Detect matching processors that support dedup
    matching_processors = registry.detect_all(export_dir)
    dedup_processors = [p for p in matching_processors if p.supports_dedup()]

    for processor in dedup_processors:
        try:
            processor_name = processor.get_name()

            if db and run_id:
                # Streaming mode: iterate generator and write entries to database
                # This never accumulates all entries in memory
                batch = []
                for entry in processor.build_catalog(str(export_dir), workers=workers):
                    # Ensure _processor is set
                    if "_processor" not in entry:
                        entry["_processor"] = processor_name
                    batch.append(entry)
                    file_count += 1

                    # Write in batches of 100 for efficiency
                    if len(batch) >= 100:
                        db.write_catalog_entries_batch(run_id, export_dir.name, batch)
                        batch = []

                # Write remaining entries
                if batch:
                    db.write_catalog_entries_batch(run_id, export_dir.name, batch)
            else:
                # Legacy mode: just count files from generator
                for _ in processor.build_catalog(str(export_dir), workers=workers):
                    file_count += 1

        except Exception as e:
            logging.warning(
                f"Catalog build failed for {processor.get_name()} "
                f"in {export_dir.name}: {e}"
            )

    return export_dir.name, file_count


def _print_catalog_summary_table(
    catalog_results: list[tuple[str, int, str]], total_files: int
) -> None:
    """Print a Rich summary table of catalog results.

    Args:
        catalog_results: List of (export_name, file_count, status) tuples.
        total_files: Total number of files across all catalogs.
    """
    from rich.table import Table

    table = Table(title="Catalog Results", show_header=True, header_style="bold")
    table.add_column("Export", style="cyan", no_wrap=True)
    table.add_column("Files", justify="right", style="green")
    table.add_column("Status")

    for name, count, status in sorted(catalog_results, key=lambda x: x[0]):
        status_style = "green" if status == "OK" else "yellow" if count == 0 else "red"
        table.add_row(
            name, str(count) if count > 0 else "-", f"[{status_style}]{status}[/]"
        )

    # Add total row
    table.add_section()
    table.add_row("[bold]Total[/bold]", f"[bold]{total_files}[/bold]", "")

    console.print()
    console.print(table)
    console.print()


def run_phased_dedup_processing(
    dirs_to_process: list[Path],
    registry: ProcessorRegistry,
    args,
    temp_dir: str,
    log_file: Optional[Path] = None,
) -> tuple[int, int, list]:
    """Run phased processing with cross-processor deduplication.

    Phase 1: CATALOG - Build catalogs from all processors (streaming to DB)
    Phase 2: DEDUP ANALYSIS - Analyze for duplicates (from DB)
    Phase 3: PROCESS - Process with dedup manifest

    Uses streaming architecture to avoid OOM when processing large export
    collections. Catalog entries are written directly to SQLite database
    rather than accumulated in memory.

    Args:
        dirs_to_process: List of export directories
        registry: ProcessorRegistry instance
        args: Command line arguments
        temp_dir: Directory for temporary files
        log_file: Optional path to log file for worker process logging

    Returns:
        Tuple of (success_count, failed_count, results_list)
    """
    import uuid

    config = get_dedup_config()
    phase_header("CROSS-PROCESSOR DEDUPLICATION MODE")
    logger.info("Database: %s", config.database_path)
    logger.info(
        "Perceptual hashing: %s", "enabled" if config.use_perceptual else "disabled"
    )
    logger.info("Perceptual threshold: %s", config.perceptual_threshold)

    # Generate unique run_id for this processing session
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    logger.info("Run ID: %s", run_id)

    # Initialize database
    db = HashDatabase(config.database_path)

    # Phase 1: Build catalogs (streaming to database)
    phase_header("PHASE 1: CATALOG")

    catalog_results = []  # List of (export_name, file_count, status) for summary table
    total_files = 0
    use_parallel_catalog = os.environ.get("PARALLEL_CATALOG", "true").lower() == "true"

    if use_parallel_catalog and len(dirs_to_process) > 1:
        # Parallel catalog building across exports with streaming to DB
        max_catalog_workers = min(len(dirs_to_process), 8)
        logger.info(
            "Building catalogs in parallel (%d workers, streaming to DB)...",
            max_catalog_workers,
        )

        from rich.progress import (
            Progress,
            SpinnerColumn,
            TextColumn,
            BarColumn,
            MofNCompleteColumn,
        )

        with ProcessPoolExecutor(max_workers=max_catalog_workers) as executor:
            futures = {
                executor.submit(
                    _build_catalog_worker,
                    str(export_dir),
                    args.workers,
                    str(log_file) if log_file else None,
                    str(config.database_path),  # Pass DB path for streaming
                    run_id,  # Pass run_id for streaming
                ): export_dir
                for export_dir in dirs_to_process
            }

            total = len(futures)
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]Cataloging exports..."),
                BarColumn(),
                MofNCompleteColumn(),
                console=console,
                transient=False,
                refresh_per_second=4,
            ) as progress:
                task = progress.add_task("Cataloging", total=total)
                for future in as_completed(futures):
                    export_dir = futures[future]
                    progress.advance(task)
                    try:
                        export_name, file_count = future.result()
                        if file_count > 0:
                            catalog_results.append((export_name, file_count, "OK"))
                            total_files += file_count
                        else:
                            catalog_results.append((export_name, 0, "No processors"))
                    except Exception as e:
                        catalog_results.append((export_dir.name, 0, f"ERROR: {e}"))
                        logger.error("Catalog failed for %s: %s", export_dir.name, e)
    else:
        # Sequential catalog building with streaming to DB
        for export_dir in dirs_to_process:
            logger.debug("Scanning: %s", export_dir.name)

            # Detect matching processors that support dedup
            matching_processors = registry.detect_all(export_dir)
            dedup_processors = [p for p in matching_processors if p.supports_dedup()]

            if not dedup_processors:
                catalog_results.append((export_dir.name, 0, "No processors"))
                continue

            export_file_count = 0
            for processor in dedup_processors:
                logger.debug("  Building catalog: %s", processor.get_name())
                processor_name = processor.get_name()
                try:
                    # Stream entries directly from generator to database
                    batch = []
                    processor_file_count = 0
                    for entry in processor.build_catalog(
                        str(export_dir),
                        workers=args.workers,
                    ):
                        if "_processor" not in entry:
                            entry["_processor"] = processor_name
                        batch.append(entry)
                        export_file_count += 1
                        processor_file_count += 1

                        if len(batch) >= 100:
                            db.write_catalog_entries_batch(
                                run_id, export_dir.name, batch
                            )
                            batch = []

                    if batch:
                        db.write_catalog_entries_batch(run_id, export_dir.name, batch)

                    logger.debug("    Found %d files", processor_file_count)
                except Exception as e:
                    logger.error("    ERROR: %s", e)

            if export_file_count > 0:
                catalog_results.append((export_dir.name, export_file_count, "OK"))
                total_files += export_file_count
            else:
                catalog_results.append((export_dir.name, 0, "No files"))

    # Display catalog results summary table
    _print_catalog_summary_table(catalog_results, total_files)

    if total_files == 0:
        logger.info("No files to process. Exiting.")
        # Clean up empty run
        db.clear_run(run_id)
        return 0, 0, []

    # Phase 2: Deduplication Analysis (from database)
    phase_header("PHASE 2: DEDUP ANALYSIS")

    dedup_service = DeduplicationService(db=db, config=config)
    manifest = dedup_service.analyze_from_db(run_id)

    # Save manifest
    output_base = Path(args.output) if args.output else Path("processed")
    manifest_path = output_base / "dedup_manifest.json"
    save_manifest(manifest, manifest_path)
    logger.info("Manifest saved: %s", manifest_path)

    stats = manifest.get("statistics", {})
    logger.info("Statistics:")
    logger.info("  Total files: %d", stats.get("total_files_scanned", 0))
    logger.info("  Unique files: %d", stats.get("unique_files", 0))
    logger.info("  Duplicates found: %d", stats.get("duplicates_found", 0))
    logger.info(
        "  Space saved: %.1f MB", stats.get("space_saved_bytes", 0) / (1024 * 1024)
    )

    # Phase 3: Process with manifest
    phase_header("PHASE 3: PROCESS")

    success_count = 0
    failed_count = 0
    results = []

    for export_dir in dirs_to_process:
        section_header(f"Processing: {export_dir.name}")

        matching_processors = registry.detect_all(export_dir)

        for processor in matching_processors:
            logger.info("  Running: %s", processor.get_name())
            try:
                # Determine output directory using Platform/User structure
                if args.output:
                    output_dir = build_processor_output_path(
                        processor.get_output_info(),
                        str(export_dir),
                        args.output,
                    )
                else:
                    output_dir = None

                kwargs = {
                    "verbose": args.verbose,
                    "workers": args.workers,
                    "temp_dir": temp_dir,
                }

                success = processor.process(
                    str(export_dir), output_dir, dedup_manifest=manifest, **kwargs
                )

                if success:
                    success_count += 1
                    results.append(
                        (f"{export_dir.name}/{processor.get_name()}", "SUCCESS")
                    )
                else:
                    failed_count += 1
                    results.append(
                        (f"{export_dir.name}/{processor.get_name()}", "FAILED")
                    )

            except Exception as e:
                logger.error("    ERROR: %s", e)
                failed_count += 1
                results.append((f"{export_dir.name}/{processor.get_name()}", "ERROR"))

    phase_header("DEDUP PROCESSING COMPLETE")
    logger.info("Results: %d succeeded, %d failed", success_count, failed_count)

    # Clean up preprocessing cache
    from common.processing import cleanup_preprocessing_cache

    cleanup_preprocessing_cache()

    # Clean up catalog entries from this run to free database space
    db.clear_run(run_id)

    return success_count, failed_count, results


def show_supported_formats() -> None:
    """Display all supported export formats"""
    logger.info("Supported formats (can match multiple per export):")
    logger.info("  Google Services (can match multiple):")
    logger.info("    - Google Photos: Google Photos/ directory with album folders")
    logger.info("    - Google Chat: Google Chat/Groups/ and Google Chat/Users/")
    logger.info("    - Google Voice: Voice/Calls/ with HTML files")
    logger.info("  Snapchat:")
    logger.info("    - Snapchat (Unified): memories/ and messages/ subdirectories")
    logger.info("    - Snapchat Memories: media/, overlays/, metadata.json")
    logger.info("    - Snapchat Messages: json/ with chat_history.json")
    logger.info("  Instagram (can match multiple):")
    logger.info("    - Instagram Messages: your_instagram_activity/messages/inbox/")
    logger.info("    - Instagram Public Media: media/posts/ and media/archived_posts/")
    logger.info(
        "    - Instaloader: YYYY-MM-DD_HH-MM-SS_UTC.* files in root (scrape data)"
    )
    logger.info("Examples:")
    logger.info("  google-username-20250526/     -> Google Photos + Chat + Voice")
    logger.info("  instagram-username-20251007/  -> Instagram Messages + Public Media")
    logger.info("  snapchat-username-20251007/   -> Snapchat Memories")
    logger.info("  snapmsgs-username-20251017/   -> Snapchat Messages")


def _get_structure_signature(path: Path) -> tuple[str, ...] | None:
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
    output_base: str | None,
    verbose: bool,
    workers: int,
    immich_enabled: bool,
    immich_url: str | None,
    immich_key: str | None,
    immich_concurrency: int,
    immich_skip_hash: bool,
    temp_dir: str,
    export_idx: int,
    total_exports: int,
    upload_queue=None,
    processor_filter: str | None = None,
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
    from common.output.logging import (  # noqa: F811
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
            output: Optional[str]
            verbose: bool
            workers: Optional[int]
            processor: Optional[str]

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

        # Log header for this export
        if total_exports > 1:
            mega_separator = "#" * 70
            logger.info(mega_separator)
            logger.info("EXPORT %d/%d: %s", export_idx, total_exports, export_dir.name)
            logger.info(mega_separator)

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
    immich_url: Optional[str],
    immich_key: Optional[str],
    immich_concurrency: int,
    immich_skip_hash: bool,
    temp_dir: str,
    detection_cache: Optional[dict] = None,
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
    logger.info("Analyzing: %s", input_path)

    matching_processors = None
    cache_key = None

    if detection_cache is not None:
        cache_key = _get_structure_signature(input_path)
        if cache_key and cache_key in detection_cache:
            matching_processors = detection_cache[cache_key]
            logger.debug("(using cached processor detection)")

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
            logger.warning("No %s exports found in: %s", processor_filter, input_path)
            return 0, 0, []
        else:
            # No processors matched at all - this is an error
            logger.error("No processors matched input directory: %s", input_path)
            show_supported_formats()
            logger.info("Run with --list-processors to see all available processors.")
            return 0, 1, []

    # Show detected processors
    logger.info("Detected %d processor(s):", len(matching_processors))
    for processor in matching_processors:
        logger.info("  - %s", processor.get_name())

    if args.output:
        logger.info("Output base directory: %s", args.output)
        # Ensure base output directory exists
        try:
            Path(args.output).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(
                "Failed to create output base directory '%s': %s", args.output, e
            )
            return 0, 1, []

    # Run ALL matching processors
    success_count = 0
    failed_count = 0
    results = []
    upload_tasks = []  # Collect upload tasks if queuing

    for idx, processor in enumerate(matching_processors, 1):
        separator = "=" * 70
        logger.info(separator)
        logger.info(
            "[%d/%d] Running: %s", idx, len(matching_processors), processor.get_name()
        )
        logger.info(separator)

        try:
            # Prepare kwargs for processor
            kwargs = {}
            if args.verbose:
                kwargs["verbose"] = True
            if args.workers:
                kwargs["workers"] = args.workers

            # Pass temp_dir to processors
            kwargs["temp_dir"] = temp_dir

            # Determine output directory using Platform/User structure
            if args.output:
                output_dir = build_processor_output_path(
                    processor.get_output_info(),
                    str(input_path),
                    args.output,
                )
            else:
                output_dir = None

            # Run processor
            success = processor.process(str(input_path), output_dir, **kwargs)

            if success:
                success_count += 1
                status = "SUCCESS"
                logger.info(
                    "%s: %s completed successfully", status, processor.get_name()
                )

                # Compute effective output directory for uploads
                # This is the Platform/User path plus service subdirectory
                output_info = processor.get_output_info()
                if output_dir and output_info.service:
                    effective_output = str(Path(output_dir) / output_info.service)
                else:
                    effective_output = output_dir

                # Upload to Immich if enabled
                if immich_enabled and effective_output:
                    targets = build_upload_targets(
                        processor, str(input_path), effective_output
                    )
                    if not targets:
                        logger.info("No upload targets derived for this processor.")
                    else:
                        if upload_queue is not None:
                            # Queue uploads for the upload worker thread
                            logger.info(
                                "Queueing %d upload(s) for sequential processing...",
                                len(targets),
                            )
                            for up_path, album_name in targets:
                                upload_tasks.append(
                                    (up_path, album_name, processor.get_name())
                                )
                        else:
                            # Upload immediately (sequential mode)
                            logger.info("Starting Immich uploads:")
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
                                    if rc == 0:
                                        logger.info(
                                            "  %s -> %s from %s",
                                            processor.get_name(),
                                            album_name,
                                            up_path,
                                        )
                                    else:
                                        logger.warning(
                                            "  %s -> %s from %s (rc=%d)",
                                            processor.get_name(),
                                            album_name,
                                            up_path,
                                            rc,
                                        )
                                except Exception as e:
                                    logger.error(
                                        "  Upload failed for %s -> %s: %s",
                                        up_path,
                                        album_name,
                                        e,
                                    )
            else:
                failed_count += 1
                status = "FAILED"
                logger.error("%s: %s failed", status, processor.get_name())

            results.append((processor.get_name(), status))

        except KeyboardInterrupt:
            logger.warning("Interrupted by user")
            failed_count += 1
            results.append((processor.get_name(), "INTERRUPTED"))
            raise  # Re-raise to stop processing other exports
        except Exception as e:
            failed_count += 1
            status = f"ERROR: {e}"
            logger.error("%s failed with exception: %s", processor.get_name(), e)
            results.append((processor.get_name(), "ERROR"))

    # Summary
    separator = "=" * 70
    logger.info(separator)
    logger.info("SUMMARY")
    logger.info(separator)

    for processor_name, status in results:
        logger.info("  %-15s %s", status, processor_name)

    logger.info(
        "Total: %d succeeded, %d failed out of %d",
        success_count,
        failed_count,
        len(matching_processors),
    )
    logger.info(separator)

    # If uploads were queued, add them to the queue
    if upload_queue is not None and upload_tasks:
        logger.info(
            "Adding %d upload(s) to queue for %s...", len(upload_tasks), input_path.name
        )
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

    logger.info("Upload-only mode")
    logger.info("Original export: %s", input_path)
    logger.info("Processed output: %s", upload_path)

    # Create logs directory and upload log file
    logs_dir = Path("logs")
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"upload_only_{input_path.name}_{timestamp}.log"
    log_file = logs_dir / log_filename

    logger.info("Upload log will be saved to: %s", log_file)

    # Validate paths
    if not input_path.exists():
        logger.error("Original export directory does not exist: %s", input_path)
        return 1

    if not upload_path.exists():
        logger.error("Processed output directory does not exist: %s", upload_path)
        return 1

    # Detect processors from original export
    logger.info("Analyzing original export to detect processor types...")
    matching_processors = registry.detect_all(input_path)

    if not matching_processors:
        logger.error("No processors matched original export directory: %s", input_path)
        show_supported_formats()
        return 1

    logger.info("Detected %d processor(s):", len(matching_processors))
    for processor in matching_processors:
        logger.info("  - %s", processor.get_name())

    # Map processor names to their expected output subdirectories within upload_path
    processor_output_map = {}

    for processor in matching_processors:
        pname = processor.get_name()

        # Get effective output directory using centralized config
        effective_output_str = get_effective_output_dir(pname, str(upload_path))

        if not effective_output_str:
            logger.warning("Unknown processor '%s', skipping uploads", pname)
            continue

        effective_output = Path(effective_output_str)

        # Verify the directory exists
        if not effective_output.exists():
            logger.warning(
                "Expected output directory does not exist: %s", effective_output
            )
            logger.warning("         Skipping uploads for %s", pname)
            continue

        processor_output_map[pname] = str(effective_output)

    if not processor_output_map:
        logger.error("No valid output directories found for detected processors")
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
        logger.info("Starting Immich uploads:")

        success_count = 0
        failed_count = 0
        total_files_uploaded = 0

        for idx, processor in enumerate(matching_processors, 1):
            pname = processor.get_name()

            if pname not in processor_output_map:
                continue

            effective_output = processor_output_map[pname]

            separator = "=" * 70
            logger.info(separator)
            logger.info("[%d/%d] Uploading: %s", idx, len(matching_processors), pname)
            logger.info(separator)

            try:
                # Build upload targets using the original export path and effective output
                targets = build_upload_targets(pname, str(input_path), effective_output)

                if not targets:
                    logger.info("No upload targets derived for %s", pname)
                    log.write(f"Processor: {pname}\n")
                    log.write("  No upload targets\n\n")
                    continue

                logger.info("Found %d upload target(s):", len(targets))
                for up_path, album_name in targets:
                    logger.info("  - %s -> %s", up_path, album_name)

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
                        if rc == 0:
                            logger.info(
                                "  %s -> %s (%d files)",
                                pname,
                                album_name,
                                len(files_to_upload),
                            )
                        else:
                            logger.warning(
                                "  %s -> %s (%d files) - rc=%d",
                                pname,
                                album_name,
                                len(files_to_upload),
                                rc,
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
                        logger.error(
                            "  Upload failed for %s -> %s: %s", up_path, album_name, e
                        )
                        log.write(f"  Upload result: EXCEPTION - {e}\n\n")
                        failed_count += 1

            except Exception as e:
                logger.error("Failed to upload %s: %s", pname, e)
                log.write(f"Processor: {pname}\n")
                log.write(f"  ERROR: {e}\n\n")
                failed_count += 1

        # Write summary to log
        log.write(f"{'=' * 70}\n")
        log.write("SUMMARY\n")
        log.write(f"{'=' * 70}\n")
        log.write(f"Total files logged: {total_files_uploaded}\n")
        log.write(f"Upload tasks: {success_count} succeeded, {failed_count} failed\n")
        log.write(f"{'=' * 70}\n")

    # Summary
    separator = "=" * 70
    logger.info(separator)
    logger.info("UPLOAD SUMMARY")
    logger.info(separator)
    logger.info("Total files: %d", total_files_uploaded)
    logger.info("Upload tasks: %d succeeded, %d failed", success_count, failed_count)
    logger.info("Upload log saved to: %s", log_file)
    logger.info(separator)

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
        action="count",
        default=0,
        help="Enable verbose logging (-v for summaries, -vv for file-level details)",
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
    parser.add_argument(
        "--file-log-level",
        choices=["debug", "info"],
        default="debug",
        help="Log level for file output: 'debug' (default) captures all details, "
        "'info' produces smaller logs with only phase summaries",
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
    # Map string log level to logging constant
    file_log_level = logging.DEBUG if args.file_log_level == "debug" else logging.INFO
    setup_logging(
        verbosity=args.verbose,
        log_file=str(log_file) if log_file else None,
        file_log_level=file_log_level,
    )

    # Initialize registry and load all processors
    registry = ProcessorRegistry()
    load_all_processors(registry)

    # Validate --processor argument if provided
    if args.processor:
        if not registry.get_by_name(args.processor):
            logger.error("Unknown processor '%s'", args.processor)
            logger.info("Available processors:")
            for p in registry.get_all_processors():
                logger.info("  - %s", p.get_name())
            return 1

    # Handle --list-processors
    if args.list_processors:
        console.print("[bold]Available processors:[/bold]")
        console.print()
        processors = registry.get_all_processors()
        if not processors:
            console.print("  No processors loaded.")
        else:
            for processor in processors:
                console.print(
                    f"  [cyan]{processor.get_name():30s}[/cyan] (priority: {processor.get_priority()})"
                )
        console.print()
        console.print(f"Total: {registry.get_processor_count()} processor(s)")
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
            logger.error("Originals directory does not exist: %s", originals_path)
            return 1

        if not originals_path.is_dir():
            logger.error("Originals path is not a directory: %s", originals_path)
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
            logger.error("No subdirectories found in: %s", originals_path)
            return 1

        logger.info(
            "Found %d export(s) to process in: %s", len(subdirs), originals_path
        )
        for subdir in subdirs:
            logger.info("  - %s", subdir.name)

        dirs_to_process = subdirs
    else:
        # Single directory mode
        input_path = Path(args.input_dir).resolve()

        if not input_path.exists():
            logger.error("Input directory does not exist: %s", input_path)
            return 1

        if not input_path.is_dir():
            logger.error("Input path is not a directory: %s", input_path)
            return 1

        dirs_to_process = [input_path]

    # Resolve Immich configuration
    cpu_count = multiprocessing.cpu_count()

    # Validate and set parallel_exports
    parallel_exports = args.parallel_exports if args.parallel_exports else 1

    # Validate parallel_exports for --originals mode
    if args.parallel_exports and not args.originals:
        logger.warning("--parallel-exports is only used with --originals; ignoring")
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
            logger.warning(
                "Running %d exports with %d workers each",
                parallel_exports,
                workers_per_export,
            )
            logger.warning(
                "         will create ~%d processes on %d CPU cores",
                total_processes,
                cpu_count,
            )
            logger.warning(
                "         This may cause performance degradation due to over-subscription."
            )

            # Suggest better configuration
            suggested_workers = max(1, (cpu_count // parallel_exports) - 1)
            logger.info(
                "SUGGESTION: Try --parallel-exports %d --workers %d",
                parallel_exports,
                suggested_workers,
            )
            logger.info(
                "            or reduce --parallel-exports to %d",
                max(1, cpu_count // (workers_per_export + 1)),
            )

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
            logger.warning(
                "Continuing without upload (use --skip-upload to hide this message)."
            )
            immich_enabled = False
        elif not verify_auth(immich_url, immich_key):
            logger.warning(
                "Immich authentication failed (server-info). Skipping upload."
            )
            immich_enabled = False

    # Handle upload-only mode
    if args.upload_only:
        if not immich_enabled:
            logger.error("Upload-only mode requires valid Immich configuration")
            return 1

        input_path = Path(args.input_dir).resolve()
        upload_path = Path(args.upload_only).resolve()

        # Type guard: immich_enabled is True only if these are set (checked at line 1579)
        assert immich_url is not None and immich_key is not None

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

    # Handle cross-processor deduplication mode
    if is_dedup_enabled() and len(dirs_to_process) >= 1:
        logger.info(
            "Cross-processor deduplication enabled (CROSS_PROCESSOR_DEDUP=true)"
        )

        success_count, failed_count, results = run_phased_dedup_processing(
            dirs_to_process,
            registry,
            args,
            temp_dir,
            log_file,
        )

        # Handle uploads if enabled
        if immich_enabled and args.output:
            logger.info("Uploading processed files...")
            # Upload logic would go here

        return 0 if failed_count == 0 else 1

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
            logger.info(separator)
            logger.info(
                "Consolidating %d %s exports...", len(paths), processor.get_name()
            )
            logger.info(separator)
            for p in paths:
                logger.info("  - %s", p.name)

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
                logger.error("Consolidation failed for %s: %s", processor.get_name(), e)
                total_exports_failed += 1
                export_results.append(
                    (f"{processor.get_name()} (consolidated)", "ERROR")
                )

        # Remove consolidated exports from normal processing
        dirs_to_process = [d for d in dirs_to_process if d not in consolidated_paths]

    try:
        if parallel_exports > 1 and len(dirs_to_process) > 1:
            # ============== PARALLEL PROCESSING MODE ==============
            logger.info(
                "Processing %d exports in parallel (%d at a time)...",
                len(dirs_to_process),
                parallel_exports,
            )

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
                logger.info(
                    "Upload worker started - uploads will be processed sequentially as exports complete"
                )

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
                            export_results.append((export_name, "SUCCESS"))
                        else:
                            total_exports_failed += 1
                            export_results.append((export_name, "FAILED"))

                    except KeyboardInterrupt:
                        logger.warning("Processing interrupted by user")
                        total_exports_failed += 1
                        export_results.append((export_dir.name, "INTERRUPTED"))
                        # Cancel remaining futures
                        for f in future_to_export:
                            f.cancel()
                        raise
                    except Exception as e:
                        logger.error(
                            "Export %s failed with exception: %s", export_dir.name, e
                        )
                        total_exports_failed += 1
                        export_results.append((export_dir.name, "ERROR"))

            # Wait for upload queue to finish if it exists
            if upload_queue is not None:
                logger.info(
                    "All processing complete. Waiting for remaining uploads to finish..."
                )
                upload_queue.put((None, None))  # Sentinel to stop upload worker
                if upload_thread:
                    upload_thread.join(timeout=3600)  # Wait up to 1 hour for uploads
                logger.info("All uploads complete.")

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
                        logger.info(mega_separator)
                        logger.info(
                            "EXPORT %d/%d: %s",
                            export_idx,
                            len(dirs_to_process),
                            export_dir.name,
                        )
                        logger.info(mega_separator)

                        # Note: Output directory is now built per-processor using
                        # build_processor_output_path() which creates Platform/User paths.
                        # No need to override args.output here.

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

                        if failed_count == 0:
                            total_exports_success += 1
                            export_results.append((export_dir.name, "SUCCESS"))
                        else:
                            total_exports_failed += 1
                            export_results.append((export_dir.name, "FAILED"))

                    except KeyboardInterrupt:
                        # Restore original output path if we modified it
                        if len(dirs_to_process) > 1 and output_base:
                            args.output = str(output_base)
                        total_exports_failed += 1
                        export_results.append((export_dir.name, "INTERRUPTED"))
                        raise  # Stop processing remaining exports

                finally:
                    # Clean up per-export log handler
                    if export_log_handler:
                        remove_export_log_handler(export_log_handler)

    except KeyboardInterrupt:
        logger.warning("Processing interrupted by user")

    # Overall summary for multi-export mode
    if len(dirs_to_process) > 1:
        mega_separator = "#" * 70
        logger.info(mega_separator)
        logger.info("OVERALL SUMMARY")
        logger.info(mega_separator)

        for export_name, status in export_results:
            logger.info("  %-15s %s", status, export_name)

        logger.info(
            "Total: %d succeeded, %d failed out of %d export(s)",
            total_exports_success,
            total_exports_failed,
            len(dirs_to_process),
        )
        logger.info(mega_separator)

    return 0 if total_exports_failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        # Restore terminal state in case Rich progress bars left it in a bad state
        # This handles cases where Ctrl+C or crashes occur during progress rendering
        try:
            os.system("stty sane 2>/dev/null")
        except Exception:
            pass
