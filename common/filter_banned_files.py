#!/usr/bin/env python3
"""
Banned Files Filter

Provides filtering logic to skip system files and directories that should not be processed.
Supports various NAS systems (QNAP, Synology), photo management tools, and OS-specific files.
"""

from pathlib import Path
from typing import List, Optional


class BannedFilesFilter:
    """Filter for banned files and directories that should be skipped during processing"""

    # Banned files and directories to skip during processing
    BANNED_PATTERNS = [
        "@eaDir",  # QNAP NAS system directory
        "@__thumb",  # QNAP thumbnail directory
        "SYNOFILE_THUMB_",  # Synology NAS thumbnail files (prefix match)
        "Lightroom Catalog",  # Adobe Lightroom catalog directory
        "thumbnails",  # Android photo thumbnails
        ".DS_Store",  # macOS custom attributes file
        "._",  # macOS resource fork files (AppleDouble format, prefix match)
        ".photostructure",  # PhotoStructure application directory
    ]

    def __init__(self, additional_patterns: Optional[List[str]] = None):
        """
        Initialize the filter with optional additional patterns

        Args:
            additional_patterns: Optional list of additional patterns to ban
        """
        self.patterns = self.BANNED_PATTERNS.copy()
        if additional_patterns:
            self.patterns.extend(additional_patterns)

    def is_banned(self, path: Path) -> bool:
        """
        Check if a file or directory should be skipped based on banned patterns

        Args:
            path: Path object to check (can be file or directory)

        Returns:
            True if the path matches any banned pattern, False otherwise
        """
        name = path.name

        for pattern in self.patterns:
            # Exact match or prefix match (for patterns like ._ and SYNOFILE_THUMB_)
            if name == pattern or name.startswith(pattern):
                return True

        return False

    def add_pattern(self, pattern: str) -> None:
        """
        Add an additional pattern to the banned list

        Args:
            pattern: Pattern string to add
        """
        if pattern not in self.patterns:
            self.patterns.append(pattern)

    def remove_pattern(self, pattern: str) -> None:
        """
        Remove a pattern from the banned list

        Args:
            pattern: Pattern string to remove
        """
        if pattern in self.patterns:
            self.patterns.remove(pattern)

    def get_patterns(self) -> List[str]:
        """
        Get current list of banned patterns

        Returns:
            List of pattern strings
        """
        return self.patterns.copy()
