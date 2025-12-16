#!/usr/bin/env python3
"""
Unified progress bar utilities for consistent UX across all processors.

Provides standardized progress bar formatting with phase prefixes to clearly
indicate which stage of processing is active.
"""

from concurrent.futures import as_completed
from typing import Dict, Iterable, List, Optional, TypeVar

from tqdm import tqdm

# Phase constants for consistent naming
PHASE_PREPROCESS = "Preprocess"
PHASE_PROCESS = "Process"
PHASE_EXIF = "EXIF"

T = TypeVar("T")


def progress_bar(
    iterable: Iterable[T],
    phase: str,
    action: str,
    total: Optional[int] = None,
    unit: str = "file",
) -> tqdm:
    """Wrap iterable with standardized progress bar.

    Args:
        iterable: The iterable to wrap
        phase: Phase name (use PHASE_* constants)
        action: Action description (e.g., "Creating files")
        total: Total count if known
        unit: Unit name for display

    Returns:
        tqdm progress bar wrapping the iterable
    """
    return tqdm(iterable, desc=f"[{phase}] {action}", total=total, unit=unit)


def futures_progress(
    futures_dict: Dict,
    phase: str,
    action: str,
    unit: str = "item",
) -> tqdm:
    """Progress bar for concurrent.futures.as_completed pattern.

    Args:
        futures_dict: Dictionary mapping futures to identifiers
        phase: Phase name (use PHASE_* constants)
        action: Action description (e.g., "Parsing albums")
        unit: Unit name for display

    Returns:
        tqdm progress bar wrapping as_completed iterator
    """
    return tqdm(
        as_completed(futures_dict),
        total=len(futures_dict),
        desc=f"[{phase}] {action}",
        unit=unit,
    )


def chunked_progress(
    items: List[T],
    chunk_size: int,
    phase: str,
    action: str,
    unit: str = "batch",
) -> Iterable[List[T]]:
    """Yield chunks with progress bar for batch processing.

    Args:
        items: List of items to chunk
        chunk_size: Size of each chunk
        phase: Phase name (use PHASE_* constants)
        action: Action description (e.g., "Writing metadata")
        unit: Unit name for display

    Yields:
        Chunks of items with progress bar
    """
    chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]
    yield from tqdm(chunks, desc=f"[{phase}] {action}", unit=unit)
