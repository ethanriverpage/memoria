#!/usr/bin/env python3
"""
Unified file scanning utilities for media processors.

Provides a consistent interface for scanning directories and filtering
media files based on extension, banned patterns, and naming conventions.
"""

import os
from pathlib import Path
from typing import Iterator, Optional, Set

from common.filter_banned_files import BannedFilesFilter
from common.utils import ALL_MEDIA_EXTENSIONS


def iter_media_files(
    directory: Path,
    banned_filter: Optional[BannedFilesFilter] = None,
    extensions: Optional[Set[str]] = None,
    skip_overlays: bool = True,
    skip_thumbnails: bool = True,
    recursive: bool = True,
) -> Iterator[Path]:
    """
    Iterate over media files in a directory with standard filtering.

    Applies consistent filtering rules used across all processors:
    - Skips files matching banned patterns (if filter provided)
    - Skips overlay files (files with 'overlay' in name)
    - Skips thumbnail files (files with 'thumbnail' in name)
    - Only yields files with supported media extensions

    Args:
        directory: Root directory to scan
        banned_filter: Optional BannedFilesFilter instance for pattern matching
        extensions: File extensions to include (default: ALL_MEDIA_EXTENSIONS)
        skip_overlays: Skip files with 'overlay' in name (default: True)
        skip_thumbnails: Skip files with 'thumbnail' in name (default: True)
        recursive: Walk subdirectories (default: True)

    Yields:
        Path objects for each matching media file

    Example:
        >>> from common.file_scanner import iter_media_files
        >>> from common.filter_banned_files import BannedFilesFilter
        >>> banned = BannedFilesFilter()
        >>> for media_file in iter_media_files(Path("/exports/photos"), banned):
        ...     print(media_file)
    """
    if extensions is None:
        extensions = ALL_MEDIA_EXTENSIONS

    if banned_filter is None:
        banned_filter = BannedFilesFilter()

    if recursive:
        # Use os.walk for recursive traversal with directory pruning
        for root, dirs, files in os.walk(directory):
            root_path = Path(root)

            # Filter out banned directories in-place to skip walking into them
            dirs[:] = [d for d in dirs if not banned_filter.is_banned(root_path / d)]

            for filename in files:
                filepath = root_path / filename

                if _should_include_file(
                    filepath,
                    filename,
                    extensions,
                    banned_filter,
                    skip_overlays,
                    skip_thumbnails,
                ):
                    yield filepath
    else:
        # Non-recursive: only scan immediate children
        for filepath in directory.iterdir():
            if not filepath.is_file():
                continue

            if _should_include_file(
                filepath,
                filepath.name,
                extensions,
                banned_filter,
                skip_overlays,
                skip_thumbnails,
            ):
                yield filepath


def _should_include_file(
    filepath: Path,
    filename: str,
    extensions: Set[str],
    banned_filter: BannedFilesFilter,
    skip_overlays: bool,
    skip_thumbnails: bool,
) -> bool:
    """
    Check if a file should be included based on filtering rules.

    Args:
        filepath: Full path to the file
        filename: Filename (for pattern matching)
        extensions: Set of allowed extensions
        banned_filter: Filter for banned patterns
        skip_overlays: Whether to skip overlay files
        skip_thumbnails: Whether to skip thumbnail files

    Returns:
        True if file should be included, False otherwise
    """
    # Skip banned files
    if banned_filter.is_banned(filepath):
        return False

    filename_lower = filename.lower()

    # Skip overlay files
    if skip_overlays and "overlay" in filename_lower:
        return False

    # Skip thumbnail files
    if skip_thumbnails and "thumbnail" in filename_lower:
        return False

    # Check extension
    ext = filepath.suffix.lower()
    if ext not in extensions:
        return False

    return True


def collect_media_files(
    base_paths: list,
    banned_filter: Optional[BannedFilesFilter] = None,
    extensions: Optional[Set[str]] = None,
    skip_overlays: bool = True,
    skip_thumbnails: bool = True,
    recursive: bool = True,
) -> list:
    """
    Collect all media files from multiple directories.

    Convenience wrapper around iter_media_files for collecting from multiple
    base paths, handling both files and directories.

    Args:
        base_paths: List of Path objects (files or directories) to scan
        banned_filter: Optional BannedFilesFilter instance
        extensions: File extensions to include (default: ALL_MEDIA_EXTENSIONS)
        skip_overlays: Skip files with 'overlay' in name
        skip_thumbnails: Skip files with 'thumbnail' in name
        recursive: Walk subdirectories

    Returns:
        List of Path objects for all matching media files
    """
    if banned_filter is None:
        banned_filter = BannedFilesFilter()

    if extensions is None:
        extensions = ALL_MEDIA_EXTENSIONS

    media_files = []

    for base_path in base_paths:
        if not base_path.exists():
            continue

        if base_path.is_file():
            # Single file - check if it passes filters
            if _should_include_file(
                base_path,
                base_path.name,
                extensions,
                banned_filter,
                skip_overlays,
                skip_thumbnails,
            ):
                media_files.append(base_path)
        else:
            # Directory - use iterator
            media_files.extend(
                iter_media_files(
                    base_path,
                    banned_filter=banned_filter,
                    extensions=extensions,
                    skip_overlays=skip_overlays,
                    skip_thumbnails=skip_thumbnails,
                    recursive=recursive,
                )
            )

    return media_files
