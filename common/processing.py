#!/usr/bin/env python3
"""
Processing utility functions for media processors

Provides shared processing functionality used across multiple processors:
- Temporary directory management with automatic cleanup
- Preprocessing cache for catalog phase
- Batch parallel processing with progress bars
- Standardized summary printing
"""

import hashlib
import multiprocessing
import os
import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple, TypeVar

from common.output.console import console, is_subprocess
from common.output.progress import (
    PHASE_PREPROCESS,
    PHASE_PROCESS,
    futures_progress,
    progress_bar,
)

T = TypeVar("T")
R = TypeVar("R")
from common.utils import should_cleanup_temp


def get_preprocessing_cache_path(input_dir: Path, processor_name: str) -> Path:
    """Get deterministic cache path for preprocessing a specific export."""
    # Import here to avoid circular imports
    from common.dedup.config import get_dedup_config

    config = get_dedup_config()
    cache_base = config.preprocessing_cache_dir.resolve()

    # Create deterministic hash from input path.
    #
    # This is used only for generating stable cache directory names (not security).
    # Bandit flags MD5 by default; usedforsecurity=False clarifies intent.
    dir_hash = hashlib.md5(  # nosec B324
        str(input_dir.resolve()).encode(),
        usedforsecurity=False,
    ).hexdigest()[:8]

    # Sanitize processor name for filesystem
    processor_slug = processor_name.lower().replace(" ", "_")

    return cache_base / f"{input_dir.name}_{processor_slug}_{dir_hash}"


def is_preprocessing_cache_valid(cache_path: Path) -> bool:
    """Return True if cache has metadata.json.

    The preprocessed cache uses source_path entries in metadata.json that
    point to original files. There is no media/ directory in the cache.
    """
    if not cache_path.exists():
        return False

    metadata_file = cache_path / "metadata.json"
    return metadata_file.exists()


def cleanup_preprocessing_cache() -> None:
    """Clean up preprocessing cache (respects CLEANUP_PREPROCESSING_CACHE env var)."""
    from common.dedup.config import get_dedup_config

    cleanup_enabled = os.environ.get("CLEANUP_PREPROCESSING_CACHE", "true").lower() in (
        "true",
        "1",
        "yes",
        "on",
    )

    if not cleanup_enabled:
        return

    config = get_dedup_config()
    cache_dir = config.preprocessing_cache_dir.resolve()

    if cache_dir.exists():
        shutil.rmtree(cache_dir)


@contextmanager
def temp_processing_directory(
    base_dir: str, prefix: str = "temp"
) -> Generator[Path, None, None]:
    """Context manager for temp directory with automatic cleanup."""
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
    """Process tasks in batches using multiprocessing pool with progress bar."""
    if not tasks:
        return []

    # Group tasks into batches
    batched_tasks = []
    for i in range(0, len(tasks), batch_size):
        batched_tasks.append(tasks[i : i + batch_size])

    # Process batches in parallel (or sequentially when num_workers <= 1)
    if num_workers <= 1:
        batch_results = [
            worker_fn(batch)
            for batch in progress_bar(
                batched_tasks,
                phase,
                description,
                total=len(batched_tasks),
            )
        ]
    else:
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


def process_items_parallel(
    tasks: List[T],
    worker_fn: Callable[[T], R],
    num_workers: int,
    phase: str = PHASE_PROCESS,
    description: str = "Processing",
    initializer: Optional[Callable[..., None]] = None,
    initargs: Tuple[Any, ...] = (),
) -> List[R]:
    """Process tasks in parallel using a multiprocessing pool with progress bar.

    Unlike process_batches_parallel(), this maps a worker function over individual
    tasks (no batching), and supports an optional pool initializer for worker
    process setup (e.g., logging configuration).
    """
    if not tasks:
        return []

    if num_workers <= 1:
        return [worker_fn(task) for task in progress_bar(tasks, phase, description)]

    with multiprocessing.Pool(
        processes=num_workers,
        initializer=initializer,
        initargs=initargs,
    ) as pool:
        return list(
            progress_bar(
                pool.imap(worker_fn, tasks),
                phase,
                description,
                total=len(tasks),
            )
        )


def process_parallel_threaded(
    tasks: List[T],
    worker_fn: Callable[[T], R],
    workers: int,
    phase: str = PHASE_PREPROCESS,
    description: str = "Processing",
    task_id_fn: Optional[Callable[[T], Any]] = None,
    on_error: Optional[Callable[[Any, Exception], None]] = None,
) -> List[R]:
    """Process tasks in parallel using ThreadPoolExecutor with progress bar.

    Provides a unified pattern for parallel task processing with:
    - ThreadPoolExecutor management
    - Progress bar display via futures_progress
    - Exception handling with optional callback
    - Result collection

    Args:
        tasks: List of tasks to process.
        worker_fn: Function to call for each task. Should accept a single task
            and return a result (or None to skip).
        workers: Number of parallel workers.
        phase: Phase name for progress bar (use PHASE_* constants).
        description: Action description for progress bar.
        task_id_fn: Optional function to extract task ID for error messages.
            If not provided, uses str(task).
        on_error: Optional callback for errors, receives (task_id, exception).
            If not provided, errors are logged and the task is skipped.

    Returns:
        List of non-None results from successful worker calls.

    Example:
        def process_conversation(conv_tuple):
            conv_path, conv_id = conv_tuple
            # ... process and return conversation data
            return conversation_data

        results = process_parallel_threaded(
            tasks=conversation_list,
            worker_fn=self._process_single_conversation,
            workers=self.workers,
            phase=PHASE_PREPROCESS,
            description="Parsing conversations",
            task_id_fn=lambda t: t[1],  # Extract conversation ID
            on_error=lambda tid, e: self.log_message(
                "PROCESSING_ERROR", f"Failed: {tid}", str(e)
            ),
        )
    """
    if not tasks:
        return []

    results: List[R] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_task = {executor.submit(worker_fn, task): task for task in tasks}

        for future in futures_progress(future_to_task, phase, description):
            task = future_to_task[future]
            task_id = task_id_fn(task) if task_id_fn else str(task)

            try:
                result = future.result()
                if result is not None:
                    results.append(result)
            except Exception as e:
                if on_error:
                    on_error(task_id, e)
                # If no error handler, silently skip failed tasks

    return results


def print_processing_summary(
    success: int,
    failed: int,
    total: int,
    output_dir: str,
    extra_stats: Optional[Dict[str, int]] = None,
) -> None:
    """Print formatted processing summary with success/failure counts."""
    from rich.panel import Panel
    from rich.text import Text

    # Build content with styled lines
    content = Text()

    # Success line (always green)
    content.append("Successfully processed: ", style="dim")
    content.append(f"{success}\n", style="green bold")

    # Failed line (red if any, dim otherwise)
    content.append("Failed: ", style="dim")
    if failed > 0:
        content.append(f"{failed}\n", style="red bold")
    else:
        content.append(f"{failed}\n", style="dim")

    # Extra stats
    if extra_stats:
        for label, count in extra_stats.items():
            content.append(f"{label}: ", style="dim")
            content.append(f"{count}\n", style="cyan")

    # Total line
    content.append("Total: ", style="dim")
    content.append(f"{total}", style="bold")

    # Determine border style based on failures
    border_style = "red" if failed > 0 else "green"

    console.print()
    console.print(
        Panel(content, title="Processing Complete", border_style=border_style)
    )
    console.print(f"[bold]Output:[/bold] {os.path.abspath(output_dir)}")


def print_preprocessing_stats(
    title: str,
    stats: Dict[str, int],
    show_zero: bool = False,
) -> None:
    """Print preprocessing statistics as a compact Rich table.

    Note: This function is a no-op when called from subprocess workers
    to prevent interleaved/corrupted terminal output.
    """
    # Skip console output in subprocess workers to avoid terminal corruption
    if is_subprocess():
        return

    from rich.table import Table

    table = Table(title=title, show_header=False, box=None, padding=(0, 1))
    table.add_column("Stat", style="dim")
    table.add_column("Value", justify="right", style="cyan")

    for label, value in stats.items():
        if show_zero or value != 0:
            table.add_row(label, str(value))

    console.print(table)
