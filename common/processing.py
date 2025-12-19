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
from typing import Any, Callable, Dict, Generator, List, Optional

from common.console import console
from common.progress import PHASE_PROCESS, progress_bar
from common.utils import should_cleanup_temp


def get_preprocessing_cache_path(input_dir: Path, processor_name: str) -> Path:
    """Get deterministic cache path for preprocessing a specific export."""
    # Import here to avoid circular imports
    from common.dedup_config import get_dedup_config

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
    """Return True if cache has metadata.json and media/ directory."""
    if not cache_path.exists():
        return False

    metadata_file = cache_path / "metadata.json"
    media_dir = cache_path / "media"

    return metadata_file.exists() and media_dir.exists()


def cleanup_preprocessing_cache() -> None:
    """Clean up preprocessing cache (respects CLEANUP_PREPROCESSING_CACHE env var)."""
    from common.dedup_config import get_dedup_config

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
    """Print preprocessing statistics as a compact Rich table."""
    from rich.table import Table

    table = Table(title=title, show_header=False, box=None, padding=(0, 1))
    table.add_column("Stat", style="dim")
    table.add_column("Value", justify="right", style="cyan")

    for label, value in stats.items():
        if show_zero or value != 0:
            table.add_row(label, str(value))

    console.print(table)
