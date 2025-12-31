#!/usr/bin/env python3
"""Runtime configuration utilities."""

import os
from multiprocessing import cpu_count
from typing import Optional


def default_worker_count() -> int:
    """Compute default worker count as CPU count minus one, minimum 1."""
    return max(1, cpu_count() - 1)


def parse_bool_env(value: Optional[str], default: bool = False) -> bool:
    """Parse a boolean from string value, with default for None."""
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def should_cleanup_preprocessing() -> bool:
    """Return False if DISABLE_PREPROCESSING_CLEANUP is set, True otherwise."""
    return not parse_bool_env(os.getenv("DISABLE_PREPROCESSING_CLEANUP", ""))
