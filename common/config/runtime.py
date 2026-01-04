#!/usr/bin/env python3
"""Runtime configuration utilities."""

import math
import os
from multiprocessing import cpu_count
from typing import Optional


def default_thread_count(parallel_enabled: bool = False) -> int:
    """Compute default thread count for within-export processing.

    Args:
        parallel_enabled: If True, reduces threads to balance with parallel exports.

    Returns:
        Recommended thread count.
    """
    cpu_budget = max(1, cpu_count() - 1)
    if parallel_enabled:
        # With parallel exports, use sqrt to balance threads vs processes
        return max(1, int(math.floor(math.sqrt(cpu_budget))))
    else:
        # Single export: maximize throughput
        return cpu_budget


def auto_process_count(threads: int, exports_count: int) -> int:
    """Compute auto process count for parallel export processing.

    Args:
        threads: Thread count per export.
        exports_count: Number of exports to process.

    Returns:
        Recommended process count (capped at 8 to prevent runaway).
    """
    cpu_budget = max(1, cpu_count() - 1)
    auto_procs = max(1, cpu_budget // max(1, threads))
    # Cap at 8 to match existing dedup catalog behavior
    procs = min(auto_procs, exports_count, 8)
    return procs


def parse_bool_env(value: Optional[str], default: bool = False) -> bool:
    """Parse a boolean from string value, with default for None."""
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def parse_int_env(value: Optional[str], default: Optional[int] = None) -> Optional[int]:
    """Parse an integer from string value, with default for None or invalid."""
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def should_cleanup_preprocessing() -> bool:
    """Return False if DISABLE_PREPROCESSING_CLEANUP is set, True otherwise."""
    return not parse_bool_env(os.getenv("DISABLE_PREPROCESSING_CLEANUP", ""))
