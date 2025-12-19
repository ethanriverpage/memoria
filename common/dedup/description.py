#!/usr/bin/env python3
"""
ImageDescription Builder for Cross-Processor Deduplication

Formats merged metadata from multiple sources into a standardized
EXIF ImageDescription field for embedded provenance tracking.

Unified Description Format:
    Source: {platform}/{username}[/{service}]
      - {sender} in "{conversation}": "{content}"

Examples:
    Source: Google/john.doe@gmail.com/photos
    Source: Instagram/johndoe/messages
      - jane_doe in "Best Friends": "Look at this sunset!"
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any

from common.dedup.config import get_dedup_config

logger = logging.getLogger(__name__)

# =============================================================================
# Platform and Service Constants
# =============================================================================

# Platform names (Title case, except iMessage)
PLATFORM_GOOGLE = "Google"
PLATFORM_INSTAGRAM = "Instagram"
PLATFORM_SNAPCHAT = "Snapchat"
PLATFORM_DISCORD = "Discord"
PLATFORM_IMESSAGE = "iMessage"

# Service names (lowercase)
SERVICE_PHOTOS = "photos"
SERVICE_CHAT = "chat"
SERVICE_VOICE = "voice"
SERVICE_MESSAGES = "messages"
SERVICE_MEMORIES = "memories"
SERVICE_STORIES = "stories"
SERVICE_POSTS = "posts"
SERVICE_REELS = "reels"


@dataclass
class SourceEntry:
    """Represents a source for a deduplicated file.

    Attributes:
        processor: Platform name (e.g., "Google", "Instagram", "iMessage")
        username: Username or identifier from the source
        service: Service type (e.g., "photos", "messages", "memories")
        is_primary: Whether this is the primary source
        albums: List of album names (for photo libraries) - deprecated, use service
        context_type: Type of context (e.g., "Story", "Post", "Message") - deprecated
        content: Message or caption content
        conversation: Conversation or chat name
        sender: Sender name for messages
        timestamp: When this was shared/posted
    """

    processor: str
    username: str = ""
    service: str = ""
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


# =============================================================================
# Unified Description Building Functions
# =============================================================================


def build_source_line(entry: SourceEntry) -> str:
    """Build the unified source line.

    Format: Source: {platform}/{username}[/{service}]

    Examples:
        Source: Google/john.doe@gmail.com/photos
        Source: Instagram/johndoe/messages
        Source: iMessage/mac
        Source: Discord/johndoe

    Args:
        entry: Source entry with platform, username, and optional service.

    Returns:
        Formatted source line string.
    """
    parts = [f"Source: {entry.processor}"]

    if entry.username:
        parts[0] += f"/{entry.username}"

    if entry.service:
        parts[0] += f"/{entry.service}"

    return parts[0]


def build_message_context(entry: SourceEntry, max_content: int = 100) -> str:
    """Build the message context line.

    Format:   - {sender} in "{conversation}": "{content}"

    Examples:
        - John Doe in "Family Group": "Check out this photo!"
        - in "DM with +14045551234": "Here's that picture"
        - jane_doe in "Best Friends": "Look at this sunset!"

    Args:
        entry: Source entry with sender, conversation, and content.
        max_content: Maximum characters for content (will be truncated).

    Returns:
        Formatted message context line, or empty string if no context.
    """
    if not entry.conversation and not entry.sender and not entry.content:
        return ""

    parts = []

    # Add sender if present
    if entry.sender:
        parts.append(entry.sender)

    # Add conversation
    if entry.conversation:
        if parts:
            parts.append(f'in "{entry.conversation}"')
        else:
            parts.append(f'in "{entry.conversation}"')

    # Add content if present
    if entry.content:
        content = entry.content.replace("\n", " ")
        content = truncate_text(content, max_content)
        if parts:
            parts.append(f': "{content}"')
        else:
            parts.append(f'"{content}"')

    if not parts:
        return ""

    # Build the line with proper formatting
    result = "  - "
    if entry.sender and entry.conversation:
        result += f'{entry.sender} in "{entry.conversation}"'
    elif entry.sender:
        result += entry.sender
    elif entry.conversation:
        result += f'in "{entry.conversation}"'

    if entry.content:
        content = entry.content.replace("\n", " ")
        content = truncate_text(content, max_content)
        if entry.sender or entry.conversation:
            result += f': "{content}"'
        else:
            result += f'"{content}"'

    return result


def build_description(entry: SourceEntry, max_content: int = 100) -> str:
    """Build complete single-source description for processor use.

    This is the main function processors should use to build descriptions.

    Format:
        Source: {platform}/{username}[/{service}]
        [  - {sender} in "{conversation}": "{content}"]

    Examples:
        Source: Google/john.doe@gmail.com/photos

        Source: Instagram/johndoe/messages
          - jane_doe in "Best Friends": "Look at this sunset!"

    Args:
        entry: Source entry with all metadata.
        max_content: Maximum characters for message content.

    Returns:
        Complete description string with &#xa; line separators.
    """
    lines = [build_source_line(entry)]

    # Add message context if we have conversation/sender info
    if entry.conversation or entry.sender:
        context_line = build_message_context(entry, max_content)
        if context_line:
            lines.append(context_line)

    return "&#xa;".join(lines)


def format_primary_source(entry: SourceEntry, max_chars: int = 100) -> str:
    """Format the primary source line using unified format.

    Format: Source: {platform}/{username}[/{service}]

    Args:
        entry: Primary source entry.
        max_chars: Maximum characters for this line.

    Returns:
        Formatted primary source string.
    """
    result = build_source_line(entry)
    return truncate_text(result, max_chars)


def format_secondary_source(entry: SourceEntry, max_chars: int = 250) -> str:
    """Format a secondary source line using unified format.

    Format: +{platform}/{username}[/{service}]: {sender} in "{conv}": "{content}" ({date})

    Examples:
        +iMessage/mac: Jane in "Family Group": "Everyone needs to see this!" (2024-01-15)
        +Instagram/johndoe/messages: bestfriend in "Close Friends": "Making you jealous" (2024-01-16)

    Args:
        entry: Secondary source entry.
        max_chars: Maximum characters for this line.

    Returns:
        Formatted secondary source string.
    """
    config = get_dedup_config()
    msg_truncate = config.msg_truncate

    # Build source prefix: +{platform}/{username}[/{service}]:
    source_prefix = f"+{entry.processor}"
    if entry.username:
        source_prefix += f"/{entry.username}"
    if entry.service:
        source_prefix += f"/{entry.service}"
    source_prefix += ":"

    # Build context using unified message format
    context_parts = []

    # Sender and conversation in unified format
    if entry.sender and entry.conversation:
        context_parts.append(f'{entry.sender} in "{entry.conversation}"')
    elif entry.sender:
        context_parts.append(entry.sender)
    elif entry.conversation:
        context_parts.append(f'in "{entry.conversation}"')

    # Add truncated content in quotes
    if entry.content:
        content = entry.content.replace("\n", " ")
        truncated_content = truncate_text(content, msg_truncate)
        context_parts.append(f'"{truncated_content}"')

    # Add timestamp
    date_str = format_timestamp(entry.timestamp)
    if date_str:
        context_parts.append(f"({date_str})")

    # Combine with proper separators
    if context_parts:
        # Join sender/conv and content with ": " if both exist
        if (
            len(context_parts) >= 2
            and entry.content
            and (entry.sender or entry.conversation)
        ):
            # Format: sender in "conv": "content" (date)
            result = (
                f"{source_prefix} {context_parts[0]}: {' '.join(context_parts[1:])}"
            )
        else:
            result = f"{source_prefix} {' '.join(context_parts)}"
    else:
        result = source_prefix.rstrip(":")

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


def _parse_source_path(path: str) -> tuple:
    """Parse platform/username/service from a source path.

    Args:
        path: Source path like "Google/user@gmail.com/photos" or "iMessage/mac"

    Returns:
        Tuple of (processor, username, service)
    """
    parts = path.split("/")
    processor = parts[0].strip() if len(parts) > 0 else "Unknown"
    username = parts[1].strip() if len(parts) > 1 else ""
    service = parts[2].strip() if len(parts) > 2 else ""
    return processor, username, service


def _parse_message_context(context: str) -> tuple:
    """Parse sender, conversation, and content from message context.

    Handles formats like:
        - jane_doe in "Best Friends": "Look at this sunset!"
        - in "DM with +1404...": "content"
        - John Doe in "Family Group"

    Args:
        context: Message context string.

    Returns:
        Tuple of (sender, conversation, content)
    """
    sender = ""
    conversation = ""
    content = ""

    # Extract content in quotes (last quoted string if multiple)
    if '": "' in context:
        # Format: sender in "conv": "content"
        parts = context.split('": "', 1)
        before_content = parts[0]
        content_with_end = parts[1] if len(parts) > 1 else ""
        if content_with_end.endswith('"'):
            content = content_with_end[:-1]
        else:
            # May have timestamp after: "content" (2024-01-15)
            end_quote = content_with_end.rfind('"')
            if end_quote > 0:
                content = content_with_end[:end_quote]
    else:
        before_content = context

    # Extract conversation in quotes
    if ' in "' in before_content:
        idx = before_content.find(' in "')
        sender = before_content[:idx].strip()
        rest = before_content[idx + 5 :]  # Skip ' in "'
        end_quote = rest.find('"')
        if end_quote > 0:
            conversation = rest[:end_quote]
        else:
            conversation = rest.rstrip('"')
    elif before_content.startswith('in "'):
        rest = before_content[4:]  # Skip 'in "'
        end_quote = rest.find('"')
        if end_quote > 0:
            conversation = rest[:end_quote]
        else:
            conversation = rest.rstrip('"')

    return sender, conversation, content


def parse_source_from_description(description: str) -> List[SourceEntry]:
    """Parse source entries from an existing ImageDescription.

    Handles unified format:
        Source: {platform}/{username}[/{service}]
          - {sender} in "{conversation}": "{content}"
        +{platform}/{username}[/{service}]: {sender} in "{conv}": "{content}" ({date})

    Args:
        description: ImageDescription string to parse.

    Returns:
        List of SourceEntry objects parsed from description.
    """
    entries = []
    current_entry = None

    for line in description.split("\n"):
        line = line.strip()
        if not line:
            continue

        # Parse primary source line
        if line.startswith("Source:"):
            # Save previous entry if exists
            if current_entry is not None:
                entries.append(current_entry)

            rest = line[7:].strip()
            processor, username, service = _parse_source_path(rest)

            current_entry = SourceEntry(
                processor=processor,
                username=username,
                service=service,
                is_primary=True,
            )

        # Parse message context line (belongs to current primary entry)
        elif line.startswith("  - ") or line.startswith("- "):
            if current_entry is not None:
                context = line.lstrip(" -")
                sender, conversation, content = _parse_message_context(context)
                current_entry.sender = sender
                current_entry.conversation = conversation
                current_entry.content = content

        # Parse secondary source line
        elif line.startswith("+"):
            # Save previous entry if exists
            if current_entry is not None:
                entries.append(current_entry)

            rest = line[1:].strip()

            # Split at first ": " to separate source from context
            if ": " in rest:
                source_part, context_part = rest.split(": ", 1)
            else:
                source_part = rest.rstrip(":")
                context_part = ""

            processor, username, service = _parse_source_path(source_part)
            sender, conversation, content = _parse_message_context(context_part)

            current_entry = SourceEntry(
                processor=processor,
                username=username,
                service=service,
                is_primary=False,
                sender=sender,
                conversation=conversation,
                content=content,
            )

            # Try to extract timestamp from end: (YYYY-MM-DD)
            if context_part and "(" in context_part:
                date_match = context_part.rfind("(")
                date_end = context_part.rfind(")")
                if date_match < date_end:
                    date_str = context_part[date_match + 1 : date_end]
                    try:
                        current_entry.timestamp = datetime.strptime(
                            date_str, "%Y-%m-%d"
                        )
                    except ValueError:
                        pass

    # Don't forget the last entry
    if current_entry is not None:
        entries.append(current_entry)

    return entries


def create_source_entry(
    processor: str,
    metadata: Dict[str, Any],
    is_primary: bool = False,
    service: str = "",
) -> SourceEntry:
    """Create a SourceEntry from processor metadata.

    Helper function to convert processor-specific metadata into
    a standardized SourceEntry for description building.

    Args:
        processor: Platform name (e.g., "Google", "Instagram").
        metadata: Metadata dictionary from processor.
        is_primary: Whether this is the primary source.
        service: Service name (e.g., "photos", "messages").

    Returns:
        SourceEntry populated from metadata.
    """
    entry = SourceEntry(
        processor=processor,
        is_primary=is_primary,
        service=service or metadata.get("service", ""),
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
