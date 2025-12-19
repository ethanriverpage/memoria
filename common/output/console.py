"""
Shared Rich Console Instance

This module provides a singleton Console instance used throughout the application
for coordinated terminal output. Using a shared console ensures that Rich's
logging handler and progress bars do not conflict with each other.

Example:
    >>> from common.output.console import console, section_header
    >>> console.print("[bold green]Success![/]")
    >>> section_header("Processing Export")
"""

from rich.console import Console

# Shared console instance for all Rich output
# Using a singleton ensures progress bars and logging coordinate properly
# stderr=False makes Rich output to stdout for consistency with previous behavior
console = Console(stderr=False)


def section_header(title: str, style: str = "bold cyan") -> None:
    """Print a styled section header using Rich rule.

    Creates a horizontal rule with centered title text for clear visual
    separation between processing sections.

    Args:
        title: The title text to display in the header.
        style: Rich style string for the title (default: "bold cyan").

    Example:
        >>> section_header("Processing: google-user-20251007")
        ──────────────── Processing: google-user-20251007 ────────────────
    """
    console.rule(f"[{style}]{title}[/{style}]")


def phase_header(phase_name: str) -> None:
    """Print a phase header for major processing phases.

    Creates a prominent header for top-level phases like CATALOG, DEDUP, PROCESS.

    Args:
        phase_name: The phase name to display.

    Example:
        >>> phase_header("PHASE 1: CATALOG")
        ══════════════════════ PHASE 1: CATALOG ══════════════════════
    """
    console.rule(f"[bold white]{phase_name}[/bold white]", characters="=")
