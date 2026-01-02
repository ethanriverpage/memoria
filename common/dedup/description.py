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
from dataclasses import dataclass
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

# =============================================================================
# Processor Name to Platform/Service Mapping
# =============================================================================

# Maps full processor names (from get_name()) to (platform, service) tuples
# Note: Instaloader format is handled by "Instagram Public Media" processor
PROCESSOR_TO_PLATFORM_SERVICE: Dict[str, tuple] = {
    "Google Photos": (PLATFORM_GOOGLE, SERVICE_PHOTOS),
    "Google Chat": (PLATFORM_GOOGLE, SERVICE_CHAT),
    "Google Voice": (PLATFORM_GOOGLE, SERVICE_VOICE),
    "iMessage": (PLATFORM_IMESSAGE, ""),
    "Instagram Messages": (PLATFORM_INSTAGRAM, SERVICE_MESSAGES),
    "Instagram Public Media": (PLATFORM_INSTAGRAM, SERVICE_POSTS),
    "Snapchat Messages": (PLATFORM_SNAPCHAT, SERVICE_MESSAGES),
    "Snapchat Memories": (PLATFORM_SNAPCHAT, SERVICE_MEMORIES),
    "Discord": (PLATFORM_DISCORD, ""),
}

# Processors that consolidate multiple exports as a single logical source
# For these, different usernames (device IDs) are treated as the same source
CONSOLIDATING_PROCESSORS = {"iMessage"}


def get_platform_service(processor_name: str) -> tuple:
    """Convert processor name to (platform, service) tuple.

    Args:
        processor_name: Full processor name from get_name() (e.g., "Google Photos").

    Returns:
        Tuple of (platform, service). If processor not found, returns
        (processor_name, "") as fallback.
    """
    return PROCESSOR_TO_PLATFORM_SERVICE.get(processor_name, (processor_name, ""))


def is_consolidating_processor(processor_name: str) -> bool:
    """Check if processor consolidates multiple exports as single source.

    Consolidating processors treat different usernames (e.g., device IDs like
    iph13p, mac) as the same logical source. This affects how same-source
    detection works in description building.

    Args:
        processor_name: Full processor name or platform name.

    Returns:
        True if processor consolidates exports.
    """
    return processor_name in CONSOLIDATING_PROCESSORS


@dataclass
class SourceEntry:
    """Represents a source for a deduplicated file.

    Attributes:
        processor: Platform name (e.g., "Google", "Instagram", "iMessage")
        username: Username or identifier from the source
        service: Service type (e.g., "photos", "messages", "memories")
        is_primary: Whether this is the primary source
        content: Message or caption content
        conversation: Conversation or chat name
        sender: Sender name for messages
        timestamp: When this was shared/posted
    """

    processor: str
    username: str = ""
    service: str = ""
    is_primary: bool = False
    content: str = ""
    conversation: str = ""
    sender: str = ""
    timestamp: Optional[datetime] = None


def truncate_text(text: Optional[str], max_length: int, suffix: str = "...") -> str:
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
# Source Comparison and Relative Path Helpers
# =============================================================================


def _same_source(primary: SourceEntry, secondary: SourceEntry) -> bool:
    """Check if entries are from the same logical source.

    For consolidating processors (like iMessage), different usernames (device IDs)
    are treated as the same source since they represent the same logical export.

    Args:
        primary: Primary source entry.
        secondary: Secondary source entry to compare.

    Returns:
        True if entries are from the same logical source.
    """
    # For consolidating processors, only platform matters (ignore username)
    if is_consolidating_processor(primary.processor):
        return primary.processor == secondary.processor

    # For other processors, platform/username/service must all match
    return (
        primary.processor == secondary.processor
        and primary.username == secondary.username
        and primary.service == secondary.service
    )


def _get_context_key(entry: SourceEntry) -> tuple:
    """Get a hashable key representing the conversation context.

    Used to deduplicate entries with identical context.

    Args:
        entry: Source entry.

    Returns:
        Tuple of (sender, conversation, content_prefix) for deduplication.
    """
    content_prefix = entry.content[:50] if entry.content else ""
    return (entry.sender, entry.conversation, content_prefix)


def _has_unique_context(entry: SourceEntry, seen_contexts: set) -> bool:
    """Check if entry has conversation context not yet seen.

    Args:
        entry: Source entry to check.
        seen_contexts: Set of already-seen context keys.

    Returns:
        True if entry has unique, non-empty context.
    """
    context_key = _get_context_key(entry)

    # Empty context is not unique
    if context_key == ("", "", ""):
        return False

    # Already seen this context
    if context_key in seen_contexts:
        return False

    return True


def format_relative_source_path(
    primary: SourceEntry,
    secondary: SourceEntry,
) -> str:
    """Format secondary source path relative to primary using . notation.

    Uses '.' to represent components that match the primary source,
    reducing visual redundancy in the output.

    Args:
        primary: Primary source entry.
        secondary: Secondary source entry.

    Returns:
        - "" if same source (should be folded into message detail lines)
        - "././service" if same platform/user, different service
        - "./user/service" if same platform, different user
        - "Platform/user/service" if different platform
    """
    # Same source - should be folded into message detail lines
    if _same_source(primary, secondary):
        return ""

    # Different platform - full path
    if primary.processor != secondary.processor:
        parts = [secondary.processor]
        if secondary.username:
            parts.append(secondary.username)
        if secondary.service:
            parts.append(secondary.service)
        return "/".join(parts)

    # Same platform, check username
    if primary.username == secondary.username:
        # Same platform/user, different service: ././service
        parts = [".", "."]
        if secondary.service:
            parts.append(secondary.service)
        return "/".join(parts)
    else:
        # Same platform, different user: ./user/service
        parts = ["."]
        if secondary.username:
            parts.append(secondary.username)
        if secondary.service:
            parts.append(secondary.service)
        return "/".join(parts)


def _format_message_line(
    entry: SourceEntry,
    is_secondary: bool = False,
    max_content: int = 100,
) -> str:
    """Format a message detail line for inclusion in the description.

    Builds a line showing sender, conversation, and content for embedding
    in the ImageDescription field.

    Args:
        entry: Source entry with message details.
        is_secondary: If True, prefix with '+' instead of '  - '.
        max_content: Maximum characters for content.

    Returns:
        Formatted message detail line, or empty string if no message info.
    """
    if not entry.conversation and not entry.sender and not entry.content:
        return ""

    parts = []

    # Build sender/conversation part
    if entry.sender and entry.conversation:
        parts.append(f'{entry.sender} in "{entry.conversation}"')
    elif entry.sender:
        parts.append(entry.sender)
    elif entry.conversation:
        parts.append(f'in "{entry.conversation}"')

    # Add content in quotes
    if entry.content:
        content = entry.content.replace("\n", " ")
        content = truncate_text(content, max_content)
        if parts:
            parts.append(f': "{content}"')
        else:
            parts.append(f'"{content}"')

    # Add timestamp for secondary sources
    if is_secondary and entry.timestamp:
        date_str = format_timestamp(entry.timestamp)
        if date_str:
            parts.append(f" ({date_str})")

    if not parts:
        return ""

    # Build the line with proper prefix
    prefix = "+" if is_secondary else "  - "

    # Combine parts
    if entry.sender and entry.conversation:
        result = f'{entry.sender} in "{entry.conversation}"'
    elif entry.sender:
        result = entry.sender
    elif entry.conversation:
        result = f'in "{entry.conversation}"'
    else:
        result = ""

    if entry.content:
        content = entry.content.replace("\n", " ")
        content = truncate_text(content, max_content)
        if result:
            result += f': "{content}"'
        else:
            result = f'"{content}"'

    if is_secondary and entry.timestamp:
        date_str = format_timestamp(entry.timestamp)
        if date_str:
            result += f" ({date_str})"

    return prefix + result


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


def format_message_line(entry: SourceEntry, max_content: int = 100) -> str:
    """Build a message detail line for inclusion in the description.

    Creates the line showing sender, conversation, and content that gets
    embedded in the ImageDescription field (part of the description, not
    raw context metadata).

    Format:   - {sender} in "{conversation}": "{content}"

    Examples:
        - John Doe in "Family Group": "Check out this photo!"
        - in "DM with +14045551234": "Here's that picture"
        - jane_doe in "Best Friends": "Look at this sunset!"

    Args:
        entry: Source entry with sender, conversation, and content.
        max_content: Maximum characters for content (will be truncated).

    Returns:
        Formatted message detail line, or empty string if no message info.
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

    # Add message detail line if we have conversation/sender/content info
    if entry.conversation or entry.sender or entry.content:
        message_line = format_message_line(entry, max_content)
        if message_line:
            lines.append(message_line)

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

    # Build message detail parts
    detail_parts = []

    # Sender and conversation in unified format
    if entry.sender and entry.conversation:
        detail_parts.append(f'{entry.sender} in "{entry.conversation}"')
    elif entry.sender:
        detail_parts.append(entry.sender)
    elif entry.conversation:
        detail_parts.append(f'in "{entry.conversation}"')

    # Add truncated content in quotes
    if entry.content:
        content = entry.content.replace("\n", " ")
        truncated_content = truncate_text(content, msg_truncate)
        detail_parts.append(f'"{truncated_content}"')

    # Add timestamp
    date_str = format_timestamp(entry.timestamp)
    if date_str:
        detail_parts.append(f"({date_str})")

    # Combine with proper separators
    if detail_parts:
        # Join sender/conv and content with ": " if both exist
        if (
            len(detail_parts) >= 2
            and entry.content
            and (entry.sender or entry.conversation)
        ):
            # Format: sender in "conv": "content" (date)
            result = (
                f"{source_prefix} {detail_parts[0]}: {' '.join(detail_parts[1:])}"
            )
        else:
            result = f"{source_prefix} {' '.join(detail_parts)}"
    else:
        result = source_prefix.rstrip(":")

    return truncate_text(result, max_chars)


def _format_secondary_with_relative_path(
    entry: SourceEntry,
    relative_path: str,
    max_chars: int = 250,
) -> str:
    """Format a secondary source line with relative path notation.

    Uses relative path (e.g., '././service', './user/service') instead of
    full path when source shares components with primary.

    Args:
        entry: Secondary source entry.
        relative_path: Pre-computed relative path (or full path if different platform).
        max_chars: Maximum characters for this line.

    Returns:
        Formatted secondary source string.
    """
    config = get_dedup_config()
    msg_truncate = config.msg_truncate

    # Build source prefix with relative path
    source_prefix = f"+{relative_path}:"

    # Build message detail parts
    detail_parts = []

    # Sender and conversation in unified format
    if entry.sender and entry.conversation:
        detail_parts.append(f'{entry.sender} in "{entry.conversation}"')
    elif entry.sender:
        detail_parts.append(entry.sender)
    elif entry.conversation:
        detail_parts.append(f'in "{entry.conversation}"')

    # Add truncated content in quotes
    if entry.content:
        content = entry.content.replace("\n", " ")
        truncated_content = truncate_text(content, msg_truncate)
        detail_parts.append(f'"{truncated_content}"')

    # Add timestamp
    date_str = format_timestamp(entry.timestamp)
    if date_str:
        detail_parts.append(f"({date_str})")

    # Combine with proper separators
    if detail_parts:
        # Join sender/conv and content with ": " if both exist
        if (
            len(detail_parts) >= 2
            and entry.content
            and (entry.sender or entry.conversation)
        ):
            # Format: sender in "conv": "content" (date)
            result = (
                f"{source_prefix} {detail_parts[0]}: {' '.join(detail_parts[1:])}"
            )
        else:
            result = f"{source_prefix} {' '.join(detail_parts)}"
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

    Uses relative path notation to minimize redundancy:
    - Same platform/user, different service: +././service: details
    - Same platform, different user: +./user/service: details
    - Different platform: +Platform/user/service: details
    - Same source, different message details: +details (no path, just message line)

    Format examples:
        Source: Google/ethanriverpage/photos
        +iMessage/iph13p: Jane in "Family Group": "Check out this photo!" (2024-01-15)

        Source: Google/ethanriverpage/photos
        +././chat: in "Work Team": "Here's the file" (2024-01-15)

        Source: Instagram/johndoe/messages
          - alice in "Friends": "Look at this!"
        +bob in "Family": "Sharing this photo" (2024-01-20)

    Args:
        primary: Primary source entry.
        secondary: List of secondary source entries.
        max_total: Maximum total characters (default from config).
        max_per_source: Maximum characters per source (default from config).

    Returns:
        Complete ImageDescription string with &#xa; line separators (XML entity
        for newline, required for exiftool argfile compatibility).
    """
    config = get_dedup_config()

    if max_total is None:
        max_total = config.desc_max_total
    if max_per_source is None:
        max_per_source = config.desc_max_per_source

    lines = []

    # 1. Add primary source line
    primary_max = min(100, max_per_source)
    lines.append(format_primary_source(primary, primary_max))

    # 2. Add primary message detail line if present
    primary_message_line = format_message_line(primary)
    if primary_message_line:
        lines.append(primary_message_line)

    # Track seen message details to avoid duplicates in description
    seen_message_keys: set = set()
    primary_message_key = _get_context_key(primary)
    if primary_message_key != ("", "", ""):
        seen_message_keys.add(primary_message_key)

    # Calculate remaining budget (account for &#xa; separators between lines)
    current_len = (
        sum(len(line) for line in lines) + 5 * (len(lines) - 1) if lines else 0
    )
    remaining = max_total - current_len

    # 3. Group secondaries by source identity
    same_source_entries = []
    different_source_entries = []

    for entry in secondary:
        if _same_source(primary, entry):
            same_source_entries.append(entry)
        else:
            different_source_entries.append(entry)

    # 4. Add unique message details from same-source entries as additional lines
    for entry in same_source_entries:
        if remaining <= 0:
            break

        if _has_unique_context(entry, seen_message_keys):
            # Add message key to seen set
            seen_message_keys.add(_get_context_key(entry))

            # Format as +details (secondary message detail line)
            message_line = _format_message_line(entry, is_secondary=True)
            if message_line:
                if len(message_line) + 5 <= remaining:  # +5 for &#xa; separator
                    lines.append(message_line)
                    remaining -= len(message_line) + 5

    # 5. Add different-source entries with relative path notation
    # Track seen rendered lines to avoid duplicates (post-truncation deduplication)
    seen_secondary_lines: set = set()

    for entry in different_source_entries:
        if remaining <= 0:
            break

        # Calculate budget for this source
        source_budget = min(max_per_source, remaining)
        if source_budget < 50:  # Don't add truncated entries
            break

        # Get relative path
        relative_path = format_relative_source_path(primary, entry)
        if not relative_path:
            # Same source - shouldn't happen but handle gracefully
            continue

        line = _format_secondary_with_relative_path(entry, relative_path, source_budget)

        # Skip if this exact rendered line was already emitted
        # (handles cases where distinct metadata collapses to identical display)
        if line in seen_secondary_lines:
            continue

        seen_secondary_lines.add(line)
        lines.append(line)
        remaining -= len(line) + 5  # +5 for &#xa; separator

    return "&#xa;".join(lines)


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
    entry.content = metadata.get("content", metadata.get("caption", ""))
    entry.conversation = metadata.get("conversation", metadata.get("chat", ""))
    entry.sender = metadata.get("sender", "")

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
