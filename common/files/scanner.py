#!/usr/bin/env python3
"""
Unified file scanning utilities for media processors.

Provides a consistent interface for scanning directories and filtering
files based on extension, banned patterns, and naming conventions.

Key functions:
- iter_media_files: Scan for media files with standard filtering
- iter_files: Scan for any files with extension/pattern filtering
- iter_subdirs: Scan for subdirectories with banned pattern filtering
- collect_media_files: Collect media files from multiple paths
"""

import logging
import os
from pathlib import Path
from typing import Callable, Iterator, Optional, Set

from common.files.filter import BannedFilesFilter
from common.files.types import ALL_MEDIA_EXTENSIONS

logger = logging.getLogger(__name__)


def iter_subdirs(
    directory: Path,
    banned_filter: Optional[BannedFilesFilter] = None,
    skip_hidden: bool = True,
    sorted_: bool = True,
    filter_fn: Optional[Callable[[Path], bool]] = None,
) -> Iterator[Path]:
    """
    Iterate over subdirectories with standard filtering.

    Applies consistent filtering rules:
    - Skips directories matching banned patterns
    - Skips hidden directories (starting with . or _) by default
    - Optionally filters with a custom predicate

    Args:
        directory: Root directory to scan
        banned_filter: Optional BannedFilesFilter instance for pattern matching
        skip_hidden: Skip directories starting with . or _ (default: True)
        sorted_: Return results sorted by name (default: True)
        filter_fn: Optional predicate to filter directories (receives Path, returns bool)

    Yields:
        Path objects for each matching subdirectory

    Example:
        >>> from common.files.scanner import iter_subdirs
        >>> for album_dir in iter_subdirs(photos_dir):
        ...     print(album_dir.name)

        # With custom filter for channels starting with 'c'
        >>> for channel in iter_subdirs(messages_dir, filter_fn=lambda d: d.name.startswith('c')):
        ...     process_channel(channel)
    """
    if banned_filter is None:
        banned_filter = BannedFilesFilter()

    try:
        # Use os.scandir() for efficiency - DirEntry.is_dir() uses cached info
        with os.scandir(directory) as scan_iter:
            entries = list(scan_iter)
    except PermissionError as e:
        logger.warning("Permission denied scanning %s: %s", directory, e)
        return
    except OSError as e:
        logger.warning("Error scanning %s: %s", directory, e)
        return

    if sorted_:
        entries.sort(key=lambda e: e.name)

    for entry in entries:
        if not entry.is_dir():
            continue

        # Skip hidden directories
        if skip_hidden and entry.name.startswith((".", "_")):
            continue

        entry_path = Path(entry.path)

        # Skip banned directories
        if banned_filter.is_banned(entry_path):
            continue

        # Apply custom filter
        if filter_fn is not None and not filter_fn(entry_path):
            continue

        yield entry_path


def iter_files(
    directory: Path,
    banned_filter: Optional[BannedFilesFilter] = None,
    extensions: Optional[Set[str]] = None,
    skip_hidden: bool = True,
    sorted_: bool = False,
) -> Iterator[Path]:
    """
    Iterate over files in a directory with standard filtering.

    More general than iter_media_files - allows any extension set or none.
    Useful for scanning JSON, HTML, CSV, and other non-media files.

    Args:
        directory: Directory to scan (non-recursive)
        banned_filter: Optional BannedFilesFilter instance for pattern matching
        extensions: Optional set of extensions to include (e.g., {".json", ".html"}).
                   If None, includes all non-banned files.
        skip_hidden: Skip files starting with . or _ (default: True)
        sorted_: Return results sorted by name (default: False)

    Yields:
        Path objects for each matching file

    Example:
        >>> from common.files.scanner import iter_files
        >>> for json_file in iter_files(conv_dir, extensions={".json"}):
        ...     process_json(json_file)
    """
    if banned_filter is None:
        banned_filter = BannedFilesFilter()

    try:
        # Use os.scandir() for efficiency - DirEntry.is_file() uses cached info
        with os.scandir(directory) as scan_iter:
            entries = list(scan_iter)
    except PermissionError as e:
        logger.warning("Permission denied scanning %s: %s", directory, e)
        return
    except OSError as e:
        logger.warning("Error scanning %s: %s", directory, e)
        return

    if sorted_:
        entries.sort(key=lambda e: e.name)

    for entry in entries:
        if not entry.is_file():
            continue

        filename = entry.name

        # Skip hidden files
        if skip_hidden and filename.startswith((".", "_")):
            continue

        entry_path = Path(entry.path)

        # Skip banned files
        if banned_filter.is_banned(entry_path):
            continue

        # Check extension if specified
        if extensions is not None:
            ext = entry_path.suffix.lower()
            if ext not in extensions:
                continue

        yield entry_path


def iter_media_files(
    directory: Path,
    banned_filter: Optional[BannedFilesFilter] = None,
    extensions: Optional[Set[str]] = None,
    skip_hidden: bool = True,
    skip_overlays: bool = True,
    skip_thumbnails: bool = True,
    recursive: bool = True,
) -> Iterator[Path]:
    """
    Iterate over media files in a directory with standard filtering.

    Applies consistent filtering rules used across all processors:
    - Skips files matching banned patterns (if filter provided)
    - Skips hidden files (starting with . or _) by default
    - Skips overlay files (files with 'overlay' in name)
    - Skips thumbnail files (files with 'thumbnail' in name)
    - Only yields files with supported media extensions

    Args:
        directory: Root directory to scan
        banned_filter: Optional BannedFilesFilter instance for pattern matching
        extensions: File extensions to include (default: ALL_MEDIA_EXTENSIONS)
        skip_hidden: Skip files/dirs starting with . or _ (default: True)
        skip_overlays: Skip files with 'overlay' in name (default: True)
        skip_thumbnails: Skip files with 'thumbnail' in name (default: True)
        recursive: Walk subdirectories (default: True)

    Yields:
        Path objects for each matching media file

    Example:
        >>> from common.files.scanner import iter_media_files
        >>> for media_file in iter_media_files(Path("/exports/photos")):
        ...     print(media_file)
    """
    if extensions is None:
        extensions = ALL_MEDIA_EXTENSIONS

    if banned_filter is None:
        banned_filter = BannedFilesFilter()

    try:
        if recursive:
            # Use os.walk for recursive traversal with directory pruning
            for root, dirs, files in os.walk(directory):
                root_path = Path(root)

                # Filter out banned and hidden directories in-place
                dirs[:] = [
                    d
                    for d in dirs
                    if not banned_filter.is_banned(root_path / d)
                    and not (skip_hidden and d.startswith((".", "_")))
                ]

                for filename in files:
                    filepath = root_path / filename

                    if _should_include_file(
                        filepath,
                        filename,
                        extensions,
                        banned_filter,
                        skip_hidden,
                        skip_overlays,
                        skip_thumbnails,
                    ):
                        yield filepath
        else:
            # Non-recursive: use os.scandir() for efficiency
            with os.scandir(directory) as scan_iter:
                for entry in scan_iter:
                    if not entry.is_file():
                        continue

                    filepath = Path(entry.path)
                    if _should_include_file(
                        filepath,
                        entry.name,
                        extensions,
                        banned_filter,
                        skip_hidden,
                        skip_overlays,
                        skip_thumbnails,
                    ):
                        yield filepath
    except PermissionError as e:
        logger.warning("Permission denied scanning %s: %s", directory, e)
    except OSError as e:
        logger.warning("Error scanning %s: %s", directory, e)


def _should_include_file(
    filepath: Path,
    filename: str,
    extensions: Set[str],
    banned_filter: BannedFilesFilter,
    skip_hidden: bool,
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
        skip_hidden: Whether to skip hidden files (starting with . or _)
        skip_overlays: Whether to skip overlay files
        skip_thumbnails: Whether to skip thumbnail files

    Returns:
        True if file should be included, False otherwise
    """
    # Skip hidden files
    if skip_hidden and filename.startswith((".", "_")):
        return False

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
    skip_hidden: bool = True,
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
        skip_hidden: Skip files/dirs starting with . or _ (default: True)
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
                skip_hidden,
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
                    skip_hidden=skip_hidden,
                    skip_overlays=skip_overlays,
                    skip_thumbnails=skip_thumbnails,
                    recursive=recursive,
                )
            )

    return media_files


def iter_all_files(
    directory: Path,
    ignore_patterns: Optional[list[str]] = None,
    skip_hidden: bool = True,
    recursive: bool = True,
) -> Iterator[Path]:
    """
    Iterate over all files in a directory with glob-based ignore patterns.

    Unlike iter_media_files, this scans all files regardless of extension.
    Supports glob-style patterns for ignoring files and directories.

    Pattern syntax:
    - "dirname" - ignore directories with this exact name
    - "dirname/**" - same as above (trailing ** is stripped)
    - "**/dirname/**" - same as above (leading/trailing ** is stripped)
    - "*pattern*" - fnmatch wildcard matching on directory/file names

    Args:
        directory: Root directory to scan
        ignore_patterns: List of glob patterns for files/directories to ignore
        skip_hidden: Skip files/dirs starting with . (default: True)
        recursive: Walk subdirectories (default: True)

    Yields:
        Path objects for each matching file (sorted)

    Example:
        >>> from common.files.scanner import iter_all_files
        >>> for f in iter_all_files(Path("/output"), ignore_patterns=["issues/**"]):
        ...     print(f)
    """
    import fnmatch

    if ignore_patterns is None:
        ignore_patterns = []

    # Normalize patterns by stripping **/ prefix and /** suffix
    normalized_patterns = []
    for pattern in ignore_patterns:
        normalized = pattern.replace("**/", "").replace("/**", "")
        if normalized:
            normalized_patterns.append(normalized)

    def should_ignore_dir(relative_path: Path) -> bool:
        """Check if a directory path should be ignored."""
        # Check each component of the path
        for part in relative_path.parts:
            for pattern in normalized_patterns:
                if fnmatch.fnmatch(part, pattern):
                    return True
        return False

    def should_ignore_file(relative_path: Path) -> bool:
        """Check if a file path should be ignored."""
        # Check directory components
        if relative_path.parent != Path("."):
            if should_ignore_dir(relative_path.parent):
                return True
        # Check filename
        for pattern in normalized_patterns:
            if fnmatch.fnmatch(relative_path.name, pattern):
                return True
        return False

    files = []

    try:
        if recursive:
            for root, dirs, filenames in os.walk(directory):
                root_path = Path(root)

                # Filter out ignored and hidden directories in-place
                if root_path != directory:
                    relative_root = root_path.relative_to(directory)
                    if should_ignore_dir(relative_root):
                        dirs[:] = []
                        continue

                # Prune hidden and ignored directories
                dirs[:] = [
                    d
                    for d in dirs
                    if not (skip_hidden and d.startswith("."))
                    and not any(fnmatch.fnmatch(d, p) for p in normalized_patterns)
                ]

                for filename in filenames:
                    # Skip hidden files
                    if skip_hidden and filename.startswith("."):
                        continue

                    filepath = root_path / filename
                    relative_file = filepath.relative_to(directory)

                    if not should_ignore_file(relative_file):
                        files.append(filepath)
        else:
            # Non-recursive: use os.scandir() for efficiency
            with os.scandir(directory) as scan_iter:
                for entry in scan_iter:
                    if not entry.is_file():
                        continue

                    filename = entry.name

                    # Skip hidden files
                    if skip_hidden and filename.startswith("."):
                        continue

                    # Check ignore patterns on filename
                    if any(fnmatch.fnmatch(filename, p) for p in normalized_patterns):
                        continue

                    files.append(Path(entry.path))

    except PermissionError as e:
        logger.warning("Permission denied scanning %s: %s", directory, e)
    except OSError as e:
        logger.warning("Error scanning %s: %s", directory, e)

    return iter(sorted(files))
