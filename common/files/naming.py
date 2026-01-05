#!/usr/bin/env python3
"""Filename generation and sanitization utilities."""

import re
from typing import Optional, Set

from common.text.parse import parse_timestamp_flexible
from common.validation.invariants import InvariantViolation


def sanitize_filename(name: str, max_length: int = 50) -> str:
    """Sanitize a string for safe use in filenames."""
    # Replace whitespace with underscores
    sanitized = name.replace(" ", "_")

    # Remove special characters except alphanumeric, hyphens, underscores
    # This removes: / \ : * ? " < > | and other special characters
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "", sanitized)

    # Collapse multiple consecutive underscores/hyphens into single character
    sanitized = re.sub(r"_+", "_", sanitized)
    sanitized = re.sub(r"-+", "-", sanitized)

    # Remove leading/trailing underscores and hyphens
    sanitized = sanitized.strip("_-")

    # Truncate to max length
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length]

    # Convert to lowercase for consistency
    sanitized = sanitized.lower()

    # If sanitization resulted in empty string, return a default
    if not sanitized:
        sanitized = "unknown"

    return sanitized


class FilenameGenerator:
    """Generate unique filenames with sequence numbers for duplicates.

    Tracks used filenames to ensure uniqueness by appending _2, _3, etc.
    Filenames follow the Platform/User/Service order used across outputs:
      - Single-service: {platform}-{username}-{date}
      - Multi-service:  {platform}-{username}-{service}-{date}

    All timestamps are in canonical ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ).
    """

    def __init__(
        self,
        platform: str,
        username: str,
        service: Optional[str] = None,
        sequence_start: int = 2,
        include_content_hash: bool = False,
    ):
        """Initialize the filename generator.

        Args:
            platform: Platform name (e.g., "discord", "google").
            username: Username or account identifier.
            service: Optional service name for multi-service platforms.
            sequence_start: Starting number for uniqueness sequences (default 2).
            include_content_hash: If True, include a content hash suffix when
                provided via generate(). This ensures cross-process uniqueness
                when parallel workers may write to the same output directory.
        """
        # Sanitize components for safe filenames (lowercase, no special chars)
        self.platform = sanitize_filename(platform)
        self.username = sanitize_filename(username)
        self.service = sanitize_filename(service) if service else None
        self.sequence_start = sequence_start
        self.used_filenames: Set[str] = set()
        self.include_content_hash = include_content_hash

    def generate(
        self,
        timestamp_str: Optional[str],
        extension: str,
        content_hash: Optional[str] = None,
    ) -> str:
        """Generate a unique filename following Platform/User/Service order.

        Args:
            timestamp_str: ISO 8601 timestamp for date extraction.
            extension: File extension including the dot (e.g., ".jpg").
            content_hash: Optional content hash. If provided and include_content_hash
                is True, appends first 8 chars to filename for cross-process uniqueness.

        Returns:
            Unique filename string.
        """
        date_key = self._parse_date(timestamp_str)

        # Build base filename
        if self.service:
            base = f"{self.platform}-{self.username}-{self.service}-{date_key}"
        else:
            base = f"{self.platform}-{self.username}-{date_key}"

        # Add content hash suffix for cross-process uniqueness when enabled
        if self.include_content_hash and content_hash:
            base = f"{base}-{content_hash[:8]}"

        base = f"{base}{extension}"
        return self._ensure_unique(base, extension)

    def _parse_date(self, timestamp_str: Optional[str]) -> str:
        """Parse canonical ISO 8601 timestamp to YYYYMMDD format.

        Raises:
            InvariantViolation: If timestamp is missing or unparseable.
                This indicates a bug in the calling code - processors must
                ensure valid timestamps before calling generate().
        """
        if not timestamp_str:
            raise InvariantViolation(
                f"FilenameGenerator.generate() called with missing timestamp "
                f"(platform={self.platform}, username={self.username})"
            )
        dt = parse_timestamp_flexible(timestamp_str)
        if dt:
            return dt.strftime("%Y%m%d")
        raise InvariantViolation(
            f"FilenameGenerator.generate() called with unparseable timestamp "
            f"'{timestamp_str}' (platform={self.platform}, username={self.username})"
        )

    def _ensure_unique(self, base: str, extension: str) -> str:
        """Add sequence number if filename already used."""
        if base not in self.used_filenames:
            self.used_filenames.add(base)
            return base

        # Handle empty extension case to avoid "empty separator" ValueError
        if extension:
            stem = base.rsplit(extension, 1)[0]
        else:
            stem = base

        sequence = self.sequence_start
        while True:
            candidate = f"{stem}_{sequence}{extension}"
            if candidate not in self.used_filenames:
                self.used_filenames.add(candidate)
                return candidate
            sequence += 1
