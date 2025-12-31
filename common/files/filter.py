#!/usr/bin/env python3
"""
Banned Files Filter

Provides filtering logic to skip system files and directories that should not be processed.
Supports various NAS systems (QNAP, Synology), photo management tools, and OS-specific files.
"""

from pathlib import Path
from typing import List, Optional, Sequence


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
        ".pluginPayloadAttachment",  # iMessage plugin payload attachment files
    ]

    # Banned file extensions (with leading dot, lowercase)
    DEFAULT_BANNED_EXTENSIONS = [
        ".unknown",  # Snapchat export metadata files with unrecognized type
    ]

    # File extensions that ExifTool cannot write metadata to (tool limitation).
    # These should still be copied/kept, but metadata embedding should be skipped.
    EXIFTOOL_WRITE_UNSUPPORTED_EXTENSIONS = [
        ".mp3",
        ".webm",
        ".bmp",
    ]

    def __init__(
        self,
        additional_patterns: Optional[List[str]] = None,
        additional_extensions: Optional[Sequence[str]] = None,
    ):
        """
        Initialize the filter with optional additional patterns

        Args:
            additional_patterns: Optional list of additional patterns to ban
            additional_extensions: Optional list of additional file extensions to ban.
                Each entry may be provided with or without a leading dot.
        """
        self.patterns = self.BANNED_PATTERNS.copy()
        if additional_patterns:
            self.patterns.extend(additional_patterns)

        self.extensions = {ext.lower() for ext in self.DEFAULT_BANNED_EXTENSIONS}
        if additional_extensions:
            for ext in additional_extensions:
                normalized = ext.lower()
                if not normalized.startswith("."):
                    normalized = f".{normalized}"
                self.extensions.add(normalized)

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

        # Check banned extensions
        ext = path.suffix.lower()
        if ext in self.extensions:
            return True

        return False

    def get_patterns(self) -> List[str]:
        """
        Get current list of banned patterns

        Returns:
            List of pattern strings
        """
        return self.patterns.copy()
