#!/usr/bin/env python3
"""
Unified progress bar utilities for consistent UX across all processors.

Provides standardized progress bar formatting with phase prefixes to clearly
indicate which stage of processing is active. Uses Rich for styled terminal output.
"""

from concurrent.futures import as_completed, Future
from typing import Any, Dict, Generator, Iterable, List, Optional, TypeVar

from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from common.output.console import console, is_subprocess

# Phase constants for consistent naming
PHASE_PREPROCESS = "Preprocess"
PHASE_PROCESS = "Process"
PHASE_EXIF = "EXIF"
PHASE_CATALOG = "Catalog"
PHASE_DEDUP = "Dedup"

T = TypeVar("T")


def _create_progress() -> Progress:
    """Create a Progress instance with standard columns.

    Returns:
        Configured Progress instance with spinner, description, bar, and timing.
        In subprocess workers, returns a disabled Progress to prevent interleaving.
    """
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]|[/]"),
        TimeElapsedColumn(),
        TextColumn("[dim]<[/]"),
        TimeRemainingColumn(),
        console=console,
        transient=False,
        disable=is_subprocess(),
    )


def progress_bar(
    iterable: Iterable[T],
    phase: str,
    action: str,
    total: Optional[int] = None,
) -> Generator[T, None, None]:
    """Wrap iterable with standardized progress bar.

    Args:
        iterable: The iterable to wrap
        phase: Phase name (use PHASE_* constants)
        action: Action description (e.g., "Creating files")
        total: Total count if known

    Yields:
        Items from the iterable with progress updates
    """
    description = f"[{phase}] {action}"

    # Convert to list if we need to count and no total provided
    if total is None:
        items = list(iterable)
        total = len(items)
    else:
        items = iterable

    with _create_progress() as progress:
        task = progress.add_task(description, total=total)
        for item in items:
            yield item
            progress.advance(task)


def futures_progress(
    futures_dict: Dict[Future, Any],
    phase: str,
    action: str,
) -> Generator[Future, None, None]:
    """Progress bar for concurrent.futures.as_completed pattern.

    Args:
        futures_dict: Dictionary mapping futures to identifiers
        phase: Phase name (use PHASE_* constants)
        action: Action description (e.g., "Parsing albums")

    Yields:
        Completed futures with progress updates
    """
    description = f"[{phase}] {action}"
    total = len(futures_dict)

    with _create_progress() as progress:
        task = progress.add_task(description, total=total)
        for future in as_completed(futures_dict):
            yield future
            progress.advance(task)


def chunked_progress(
    items: List[T],
    chunk_size: int,
    phase: str,
    action: str,
) -> Generator[List[T], None, None]:
    """Yield chunks with progress bar for batch processing.

    Args:
        items: List of items to chunk
        chunk_size: Size of each chunk
        phase: Phase name (use PHASE_* constants)
        action: Action description (e.g., "Writing metadata")

    Yields:
        Chunks of items with progress bar updates
    """
    chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]
    description = f"[{phase}] {action}"

    with _create_progress() as progress:
        task = progress.add_task(description, total=len(chunks))
        for chunk in chunks:
            yield chunk
            progress.advance(task)
