#!/usr/bin/env python3
"""Temporary directory management."""

import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator

from common.config.runtime import should_cleanup_preprocessing


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
            if should_cleanup_preprocessing():
                shutil.rmtree(temp_dir)
            # If cleanup is disabled, directory is preserved (no logging here,
            # caller should log if needed)
