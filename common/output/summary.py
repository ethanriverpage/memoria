#!/usr/bin/env python3
"""Processing summary display utilities."""

import os
from typing import Dict, Optional

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from common.output.console import console, is_subprocess


def print_processing_summary(
    success: int,
    failed: int,
    total: int,
    output_dir: str,
    extra_stats: Optional[Dict[str, int]] = None,
) -> None:
    """Print formatted processing summary with success/failure counts."""
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

    table = Table(title=title, show_header=False, box=None, padding=(0, 1))
    table.add_column("Stat", style="dim")
    table.add_column("Value", justify="right", style="cyan")

    for label, value in stats.items():
        if show_zero or value != 0:
            table.add_row(label, str(value))

    console.print(table)
