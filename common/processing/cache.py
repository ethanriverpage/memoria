#!/usr/bin/env python3
"""Preprocessing cache management."""

import hashlib
import os
import shutil
from pathlib import Path

from common.dedup.config import get_dedup_config


def get_preprocessing_cache_path(input_dir: Path, processor_name: str) -> Path:
    """Get deterministic cache path for preprocessing a specific export."""
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
    from common.config.runtime import parse_bool_env

    cleanup_enabled = parse_bool_env(
        os.environ.get("CLEANUP_PREPROCESSING_CACHE"),
        default=True,
    )

    if not cleanup_enabled:
        return

    config = get_dedup_config()
    cache_dir = config.preprocessing_cache_dir.resolve()

    if cache_dir.exists():
        shutil.rmtree(cache_dir)
