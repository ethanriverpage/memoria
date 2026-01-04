#!/usr/bin/env python3
"""Batch parallel processing utilities.

All functions in this module use threading (ThreadPoolExecutor) for within-export
parallelism. Cross-export parallelism uses ProcessPoolExecutor directly in memoria.py.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, List, Optional, TypeVar

from common.output.progress import (
    PHASE_PREPROCESS,
    PHASE_PROCESS,
    futures_progress,
    progress_bar,
)

T = TypeVar("T")
R = TypeVar("R")


def process_batches_threaded(
    tasks: List[T],
    worker_fn: Callable[[List[T]], List[R]],
    num_threads: int,
    batch_size: int = 100,
    phase: str = PHASE_PROCESS,
    description: str = "Processing",
) -> List[R]:
    """Process tasks in batches using ThreadPoolExecutor with progress bar.

    Use this for I/O-bound operations or when calling native libraries that release
    the GIL.

    Args:
        tasks: List of tasks to process.
        worker_fn: Function to call for each batch. Should accept a list of tasks
            and return a list of results.
        num_threads: Number of parallel threads.
        batch_size: Number of tasks per batch (default 100).
        phase: Phase name for progress bar (use PHASE_* constants).
        description: Action description for progress bar.

    Returns:
        Flattened list of results from all batches.
    """
    if not tasks:
        return []

    # Group tasks into batches
    batched_tasks = []
    for i in range(0, len(tasks), batch_size):
        batched_tasks.append(tasks[i : i + batch_size])

    # Process batches (sequentially when num_threads <= 1)
    if num_threads <= 1:
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
        with ThreadPoolExecutor(max_workers=num_threads) as executor:
            future_to_batch = {
                executor.submit(worker_fn, batch): batch for batch in batched_tasks
            }
            batch_results = []
            for future in futures_progress(future_to_batch, phase, description):
                batch_results.append(future.result())

    # Flatten results
    results: List[R] = []
    for batch_result in batch_results:
        results.extend(batch_result)

    return results


def process_items_threaded(
    tasks: List[T],
    worker_fn: Callable[[T], R],
    num_threads: int,
    phase: str = PHASE_PROCESS,
    description: str = "Processing",
) -> List[R]:
    """Process tasks using ThreadPoolExecutor with progress bar.

    Use this for I/O-bound operations or when calling native libraries that release
    the GIL.

    Args:
        tasks: List of tasks to process.
        worker_fn: Function to call for each task. Should accept a single task
            and return a result.
        num_threads: Number of parallel threads.
        phase: Phase name for progress bar (use PHASE_* constants).
        description: Action description for progress bar.

    Returns:
        List of results from all tasks (in completion order).
    """
    if not tasks:
        return []

    if num_threads <= 1:
        return [worker_fn(task) for task in progress_bar(tasks, phase, description)]

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        future_to_task = {executor.submit(worker_fn, task): task for task in tasks}
        results = []
        for future in futures_progress(future_to_task, phase, description):
            results.append(future.result())
        return results


def process_parallel_threaded(
    tasks: List[T],
    worker_fn: Callable[[T], R],
    threads: int,
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
        threads: Number of parallel threads.
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
            threads=self.threads,
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

    with ThreadPoolExecutor(max_workers=threads) as executor:
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
