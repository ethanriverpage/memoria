#!/usr/bin/env python3
"""
Processing utility functions for media processors

Provides shared processing functionality used across multiple processors:
- Temporary directory management with automatic cleanup
- Batch parallel processing with progress bars
- Standardized summary printing
"""

import multiprocessing
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Generator, List, Optional

from common.progress import PHASE_PROCESS, progress_bar
from common.utils import should_cleanup_temp


@contextmanager
def temp_processing_directory(
    base_dir: str, prefix: str = "temp"
) -> Generator[Path, None, None]:
    """
    Context manager for temporary processing directories with automatic cleanup.

    Creates a unique temporary directory for preprocessing operations and
    automatically cleans it up when the context exits (unless cleanup is disabled
    via DISABLE_TEMP_CLEANUP environment variable).

    Args:
        base_dir: Base directory path where temp directory will be created
        prefix: Prefix for the temp directory name (default: "temp")

    Yields:
        Path to the created temporary directory

    Example:
        >>> with temp_processing_directory("../pre", "google_photos") as temp_dir:
        ...     # Process files in temp_dir
        ...     preprocessor.output_dir = temp_dir
        ...     preprocessor.process()
        ... # temp_dir is automatically cleaned up here
    """
    temp_base = Path(base_dir).resolve()
    temp_base.mkdir(parents=True, exist_ok=True)

    # Create unique temp subdirectory
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_id = str(uuid.uuid4())[:8]
    temp_dir = temp_base / f"{prefix}_{timestamp}_{unique_id}"
    temp_dir.mkdir(parents=True, exist_ok=True)

    try:
        yield temp_dir
    finally:
        if temp_dir.exists():
            if should_cleanup_temp():
                shutil.rmtree(temp_dir)
            # If cleanup is disabled, directory is preserved (no logging here,
            # caller should log if needed)


def process_batches_parallel(
    tasks: List[Any],
    worker_fn: Callable[[List[Any]], List[Any]],
    num_workers: int,
    batch_size: int = 100,
    phase: str = PHASE_PROCESS,
    description: str = "Processing",
) -> List[Any]:
    """
    Process tasks in batches using multiprocessing pool with progress bar.

    Groups tasks into batches and processes them in parallel using a
    multiprocessing pool, displaying a progress bar during execution.

    Args:
        tasks: List of task items to process
        worker_fn: Worker function that processes a batch of tasks
                   Signature: (batch: List[Any]) -> List[Any]
        num_workers: Number of parallel worker processes
        batch_size: Number of tasks per batch (default: 100)
        phase: Progress bar phase identifier (default: PHASE_PROCESS)
        description: Description shown in progress bar (default: "Processing")

    Returns:
        Flattened list of results from all batches

    Example:
        >>> def process_batch(batch):
        ...     return [(True, item) for item in batch]
        >>> results = process_batches_parallel(
        ...     tasks=file_list,
        ...     worker_fn=process_batch,
        ...     num_workers=4,
        ...     description="Creating files"
        ... )
    """
    if not tasks:
        return []

    # Group tasks into batches
    batched_tasks = []
    for i in range(0, len(tasks), batch_size):
        batched_tasks.append(tasks[i : i + batch_size])

    # Process batches in parallel
    with multiprocessing.Pool(processes=num_workers) as pool:
        batch_results = list(
            progress_bar(
                pool.imap(worker_fn, batched_tasks),
                phase,
                description,
                total=len(batched_tasks),
            )
        )

    # Flatten results
    results = []
    for batch_result in batch_results:
        results.extend(batch_result)

    return results


def print_processing_summary(
    success: int,
    failed: int,
    total: int,
    output_dir: str,
    extra_stats: Optional[Dict[str, int]] = None,
) -> None:
    """
    Print standardized processing completion summary.

    Displays a formatted summary of processing results including success/failure
    counts and output location.

    Args:
        success: Number of successfully processed items
        failed: Number of failed items
        total: Total number of items processed
        output_dir: Path to output directory (will be converted to absolute path)
        extra_stats: Optional dict of additional statistics to display
                     Keys are labels, values are counts

    Example:
        >>> print_processing_summary(
        ...     success=95,
        ...     failed=5,
        ...     total=100,
        ...     output_dir="./output",
        ...     extra_stats={"EXIF structures rebuilt": 3, "Skipped": 2}
        ... )
        ==================================================
        Processing complete!
          Successfully processed: 95
          Failed: 5
          EXIF structures rebuilt: 3
          Skipped: 2
          Total: 100

        Final files saved to: /absolute/path/to/output
    """
    print("\n" + "=" * 50)
    print("Processing complete!")
    print(f"  Successfully processed: {success}")
    print(f"  Failed: {failed}")

    if extra_stats:
        for label, count in extra_stats.items():
            print(f"  {label}: {count}")

    print(f"  Total: {total}")
    print(f"\nFinal files saved to: {os.path.abspath(output_dir)}")

