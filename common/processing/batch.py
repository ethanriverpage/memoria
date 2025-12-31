#!/usr/bin/env python3
"""Batch parallel processing utilities."""

import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Optional, Tuple, TypeVar

from common.output.progress import (
    PHASE_PREPROCESS,
    PHASE_PROCESS,
    futures_progress,
    progress_bar,
)

T = TypeVar("T")
R = TypeVar("R")


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
