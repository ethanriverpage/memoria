#!/usr/bin/env python3
"""
ImageDescription Builder for Cross-Processor Deduplication

Formats merged metadata from multiple sources into a standardized
EXIF ImageDescription field for embedded provenance tracking.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

from common.dedup.config import get_dedup_config

logger = logging.getLogger(__name__)


@dataclass
class SourceEntry:
    """Represents a source for a deduplicated file.

    Attributes:
        processor: Name of the processor (e.g., "Google Photos", "Instagram")
        username: Username or identifier from the source
        is_primary: Whether this is the primary source
        albums: List of album names (for photo libraries)
        context_type: Type of context (e.g., "Story", "Post", "Message")
        content: Message or caption content
        conversation: Conversation or chat name
        sender: Sender name for messages
        timestamp: When this was shared/posted
    """

    processor: str
    username: str = ""
    is_primary: bool = False
    albums: List[str] = field(default_factory=list)
    context_type: str = ""
    content: str = ""
    conversation: str = ""
    sender: str = ""
    timestamp: Optional[datetime] = None


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """Truncate text to max length, preserving word boundaries.

    Args:
        text: Text to truncate.
        max_length: Maximum length including suffix.
        suffix: Suffix to add if truncated.

    Returns:
        Truncated text.
    """
    if text is None:
        return ""
    if not text or len(text) <= max_length:
        return text

    # Leave room for suffix
    truncate_at = max_length - len(suffix)
    if truncate_at <= 0:
        return suffix[:max_length]

    # Try to break at word boundary
    truncated = text[:truncate_at]
    last_space = truncated.rfind(" ")
    if last_space > truncate_at * 0.7:  # Only break if we keep most of text
        truncated = truncated[:last_space]

    return truncated + suffix


def format_timestamp(ts: Optional[datetime]) -> str:
    """Format timestamp for display in description.

    Args:
        ts: Datetime object or None.

    Returns:
        Formatted date string (YYYY-MM-DD) or empty string.
    """
    if ts is None:
        return ""
    return ts.strftime("%Y-%m-%d")


def format_primary_source(entry: SourceEntry, max_chars: int = 100) -> str:
    """Format the primary source line.

    Format: Source: {Processor}/{username} | {albums/context}

    Args:
        entry: Primary source entry.
        max_chars: Maximum characters for this line.

    Returns:
        Formatted primary source string.
    """
    parts = [f"Source: {entry.processor}"]

    if entry.username:
        parts[0] += f"/{entry.username}"

    # Add albums or context
    context_parts = []
    if entry.albums:
        context_parts.append(", ".join(entry.albums[:3]))  # Limit to 3 albums
        if len(entry.albums) > 3:
            context_parts.append(f"+{len(entry.albums) - 3} more")

    if context_parts:
        parts.append(" | ".join(context_parts))

    result = " | ".join(parts) if len(parts) > 1 else parts[0]
    return truncate_text(result, max_chars)


def format_secondary_source(entry: SourceEntry, max_chars: int = 250) -> str:
    """Format a secondary source line.

    Formats:
    - Story/Post: +{Processor}/{username}: {type} "{content}" ({date})
    - Message: +{Processor}/{username}: {Sender} [{Conv}] "{content}" ({date})

    Args:
        entry: Secondary source entry.
        max_chars: Maximum characters for this line.

    Returns:
        Formatted secondary source string.
    """
    config = get_dedup_config()
    msg_truncate = config.msg_truncate

    parts = [f"+{entry.processor}"]

    if entry.username:
        parts[0] += f"/{entry.username}"

    parts[0] += ":"

    # Format content based on type
    content_parts = []

    if entry.context_type:
        content_parts.append(entry.context_type)

    # Message format with sender and conversation
    if entry.sender or entry.conversation:
        sender_conv = []
        if entry.sender:
            sender_conv.append(entry.sender)
        if entry.conversation:
            sender_conv.append(f"[{entry.conversation}]")
        if sender_conv:
            content_parts.append(" ".join(sender_conv))

    # Add truncated content in quotes
    if entry.content:
        truncated_content = truncate_text(entry.content, msg_truncate)
        content_parts.append(f'"{truncated_content}"')

    # Add timestamp
    date_str = format_timestamp(entry.timestamp)
    if date_str:
        content_parts.append(f"({date_str})")

    result = " ".join([parts[0]] + content_parts)
    return truncate_text(result, max_chars)


def build_image_description(
    primary: SourceEntry,
    secondary: List[SourceEntry],
    max_total: Optional[int] = None,
    max_per_source: Optional[int] = None,
) -> str:
    """Build complete ImageDescription from multiple sources.

    Format:
    ```
    Source: Google Photos/johndoe | Vacation 2023, Summer Highlights
    +Instagram/johndoe: Story "Beach day with the squad!..." (2024-01-15)
    +iMessage/iph13: John [Family] "Everyone needs to see..." | Jane [Work] "Making you jealous" (2024-01-16)
    ```

    Args:
        primary: Primary source entry.
        secondary: List of secondary source entries.
        max_total: Maximum total characters (default from config).
        max_per_source: Maximum characters per source (default from config).

    Returns:
        Complete ImageDescription string.
    """
    config = get_dedup_config()

    if max_total is None:
        max_total = config.desc_max_total
    if max_per_source is None:
        max_per_source = config.desc_max_per_source

    lines = []

    # Add primary source (shorter budget)
    primary_max = min(100, max_per_source)
    lines.append(format_primary_source(primary, primary_max))

    # Calculate remaining budget for secondary sources
    remaining = max_total - len(lines[0])

    # Add secondary sources
    for entry in secondary:
        if remaining <= 0:
            break

        # Calculate budget for this source
        source_budget = min(max_per_source, remaining)
        if source_budget < 50:  # Don't add truncated entries
            break

        line = format_secondary_source(entry, source_budget)
        lines.append(line)
        remaining -= len(line) + 1  # +1 for newline

    return "\n".join(lines)


def merge_descriptions(
    descriptions: List[str],
    max_total: Optional[int] = None,
) -> str:
    """Merge multiple existing ImageDescription strings.

    Attempts to parse and combine descriptions from multiple sources,
    deduplicating and preserving provenance information.

    Args:
        descriptions: List of existing ImageDescription strings.
        max_total: Maximum total characters for result.

    Returns:
        Merged description string.
    """
    config = get_dedup_config()
    if max_total is None:
        max_total = config.desc_max_total

    if not descriptions:
        return ""

    if len(descriptions) == 1:
        return truncate_text(descriptions[0], max_total)

    # Collect unique lines from all descriptions
    seen_prefixes = set()
    lines = []

    for desc in descriptions:
        for line in desc.split("\n"):
            line = line.strip()
            if not line:
                continue

            # Extract prefix for deduplication (processor name)
            prefix = line.split(":")[0] if ":" in line else line[:20]
            if prefix not in seen_prefixes:
                seen_prefixes.add(prefix)
                lines.append(line)

    # Build result within budget
    result_lines = []
    remaining = max_total

    for line in lines:
        if remaining <= 0:
            break
        truncated = truncate_text(line, remaining)
        result_lines.append(truncated)
        remaining -= len(truncated) + 1

    return "\n".join(result_lines)


def parse_source_from_description(description: str) -> List[SourceEntry]:
    """Parse source entries from an existing ImageDescription.

    Attempts to extract structured source information from the
    standard description format.

    Args:
        description: ImageDescription string to parse.

    Returns:
        List of SourceEntry objects parsed from description.
    """
    entries = []

    for line in description.split("\n"):
        line = line.strip()
        if not line:
            continue

        entry = SourceEntry(processor="Unknown")

        # Parse primary source line
        if line.startswith("Source:"):
            entry.is_primary = True
            rest = line[7:].strip()

            # Extract processor/username
            if "/" in rest:
                parts = rest.split("/", 1)
                entry.processor = parts[0].strip()
                if "|" in parts[1]:
                    username_part, context = parts[1].split("|", 1)
                    entry.username = username_part.strip()
                    # Parse albums from context
                    entry.albums = [a.strip() for a in context.split(",")]
                else:
                    entry.username = parts[1].strip()
            else:
                entry.processor = rest.split("|")[0].strip()

        # Parse secondary source line
        elif line.startswith("+"):
            rest = line[1:].strip()

            # Extract processor/username
            if ":" in rest:
                source_part, content_part = rest.split(":", 1)
                if "/" in source_part:
                    parts = source_part.split("/", 1)
                    entry.processor = parts[0].strip()
                    entry.username = parts[1].strip()
                else:
                    entry.processor = source_part.strip()

                # Try to extract content in quotes
                content_part = content_part.strip()
                if '"' in content_part:
                    start = content_part.find('"')
                    end = content_part.rfind('"')
                    if start < end:
                        entry.content = content_part[start + 1 : end]

        entries.append(entry)

    return entries


def create_source_entry(
    processor: str,
    metadata: Dict[str, Any],
    is_primary: bool = False,
) -> SourceEntry:
    """Create a SourceEntry from processor metadata.

    Helper function to convert processor-specific metadata into
    a standardized SourceEntry for description building.

    Args:
        processor: Processor name.
        metadata: Metadata dictionary from processor.
        is_primary: Whether this is the primary source.

    Returns:
        SourceEntry populated from metadata.
    """
    entry = SourceEntry(
        processor=processor,
        is_primary=is_primary,
    )

    # Extract common fields
    entry.username = metadata.get("username", "")
    entry.albums = metadata.get("albums", [])
    entry.content = metadata.get("content", metadata.get("caption", ""))
    entry.conversation = metadata.get("conversation", metadata.get("chat", ""))
    entry.sender = metadata.get("sender", "")
    entry.context_type = metadata.get("type", metadata.get("context_type", ""))

    # Parse timestamp
    ts = metadata.get("timestamp")
    if isinstance(ts, datetime):
        entry.timestamp = ts
    elif isinstance(ts, str):
        try:
            # Try ISO format
            entry.timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    return entry
