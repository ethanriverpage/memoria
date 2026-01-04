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
    # Normalize whitespace/newlines in content prefix for stable comparison
    content = entry.content.replace("\n", " ").strip() if entry.content else ""
    content_prefix = content[:50]
    return (entry.sender, entry.conversation, content_prefix)


def _get_source_identity_key(entry: SourceEntry) -> tuple:
    """Get a hashable key representing the source identity.

    Used to group secondary entries by their source (platform/username/service)
    for within-source deduplication.

    For consolidating processors (like iMessage), username is ignored since
    different device IDs represent the same logical source.

    Args:
        entry: Source entry.

    Returns:
        Tuple of (processor, username, service) for source identity grouping.
    """
    if is_consolidating_processor(entry.processor):
        # For consolidating processors, ignore username (device ID)
        return (entry.processor, "", entry.service)
    return (entry.processor, entry.username, entry.service)


def _get_secondary_dedup_key(entry: SourceEntry) -> tuple:
    """Get a stable, hashable key for secondary entry deduplication.

    Combines source identity with context to determine if two secondary entries
    should be considered duplicates. This key is computed BEFORE formatting,
    ensuring truncation budgets don't affect deduplication.

    Args:
        entry: Source entry.

    Returns:
        Tuple combining source identity and context for deduplication.
    """
    source_key = _get_source_identity_key(entry)
    context_key = _get_context_key(entry)
    # Note: timestamp is NOT included in the key because timestamps are
    # normalized to the canonical merged timestamp (Bug 3 fix)
    return source_key + context_key


def deduplicate_secondary_entries(
    secondary: List[SourceEntry],
    primary: Optional[SourceEntry] = None,
) -> List[SourceEntry]:
    """Deduplicate secondary entries using stable keys.

    This is the canonical deduplication helper for secondary entries. It:
    - Uses `_get_secondary_dedup_key()` for stable, pre-format deduplication (Bug 1)
    - Deduplicates entries among themselves by source identity (Bug 2)
    - Does NOT include timestamp in dedup key (Bug 3 normalizes timestamps separately)
    - Uses partial matching for consolidating processors (e.g., iMessage across devices)

    For entries with empty context, keeps one representative per source identity.

    Args:
        secondary: List of secondary source entries to deduplicate.
        primary: Optional primary entry. If provided, entries matching its
            source identity and context are also excluded.

    Returns:
        Deduplicated list of secondary entries preserving order of first occurrence.
    """
    # Group entries by source identity for within-group deduplication
    source_groups: Dict[tuple, List[SourceEntry]] = {}
    for entry in secondary:
        source_key = _get_source_identity_key(entry)
        source_groups.setdefault(source_key, []).append(entry)

    # Build initial seen_contexts from primary if provided
    primary_contexts: Dict[tuple, set] = {}
    if primary is not None:
        primary_source_key = _get_source_identity_key(primary)
        primary_context_key = _get_context_key(primary)
        if primary_context_key != ("", "", ""):
            primary_contexts[primary_source_key] = {primary_context_key}

    deduplicated: List[SourceEntry] = []

    for source_key, entries in source_groups.items():
        # Get seen contexts for this source group (from primary if same source)
        seen_contexts: set = set(primary_contexts.get(source_key, set()))

        # Check if this source is consolidating (e.g., iMessage)
        # For consolidating sources, use partial matching via _has_unique_context
        is_consolidating = entries and is_consolidating_processor(entries[0].processor)

        # Track if we've seen empty context for this source
        has_empty_context_rep = False

        for entry in entries:
            context_key = _get_context_key(entry)

            if context_key == ("", "", ""):
                # Empty context: keep one representative per source identity
                if not has_empty_context_rep:
                    has_empty_context_rep = True
                    deduplicated.append(entry)
            elif is_consolidating:
                # For consolidating processors, use partial matching
                if _has_unique_context(entry, seen_contexts, is_consolidating=True):
                    seen_contexts.add(context_key)
                    deduplicated.append(entry)
            else:
                # For non-consolidating, use exact key matching
                if context_key not in seen_contexts:
                    seen_contexts.add(context_key)
                    deduplicated.append(entry)

    return deduplicated


def _has_unique_context(
    entry: SourceEntry,
    seen_contexts: set,
    is_consolidating: bool = False,
) -> bool:
    """Check if entry has conversation context not yet seen.

    For consolidating processors (like iMessage), uses partial matching:
    if the secondary's non-empty fields all match a seen context's fields,
    it's not considered unique. This handles cases where the same message
    appears in multiple device exports with varying metadata completeness.

    Args:
        entry: Source entry to check.
        seen_contexts: Set of already-seen context keys.
        is_consolidating: Whether this is a consolidating processor.

    Returns:
        True if entry has unique, non-empty context.
    """
    context_key = _get_context_key(entry)
    sender, conv, content = context_key

    # Empty context is not unique
    if context_key == ("", "", ""):
        return False

    # Already seen exact match
    if context_key in seen_contexts:
        return False

    # For consolidating processors, check partial matches
    # If all non-empty fields in entry match a seen context, not unique
    if is_consolidating:
        for seen_sender, seen_conv, seen_content in seen_contexts:
            matches = True
            # Check each non-empty field in the entry
            if sender and sender != seen_sender:
                matches = False
            if conv and conv != seen_conv:
                matches = False
            if content and content != seen_content:
                matches = False
            if matches:
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
    reference_timestamp: Optional[datetime] = None,
) -> str:
    """Format a message detail line for inclusion in the description.

    Builds a line showing sender, conversation, and content for embedding
    in the ImageDescription field.

    Args:
        entry: Source entry with message details.
        is_secondary: If True, prefix with '+' instead of '  - '.
        max_content: Maximum characters for content.
        reference_timestamp: If provided, dates matching this timestamp's date
            are suppressed (redundant with EXIF-written date).

    Returns:
        Formatted message detail line, or empty string if no message info.
    """
    if not entry.conversation and not entry.sender and not entry.content:
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

    # Add timestamp for secondary sources, unless it matches reference date (Bug 4 fix)
    if is_secondary and entry.timestamp:
        entry_date_str = format_timestamp(entry.timestamp)
        ref_date_str = format_timestamp(reference_timestamp) if reference_timestamp else ""
        # Only show date if it differs from reference (non-redundant)
        if entry_date_str and entry_date_str != ref_date_str:
            result += f" ({entry_date_str})"

    if not result:
        return ""

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


def format_secondary_source(
    entry: SourceEntry,
    max_chars: int = 250,
    reference_timestamp: Optional[datetime] = None,
) -> str:
    """Format a secondary source line using unified format.

    Format: +{platform}/{username}[/{service}]: {sender} in "{conv}": "{content}" ({date})

    Examples:
        +iMessage/mac: Jane in "Family Group": "Everyone needs to see this!" (2024-01-15)
        +Instagram/johndoe/messages: bestfriend in "Close Friends": "Making you jealous" (2024-01-16)

    Args:
        entry: Secondary source entry.
        max_chars: Maximum characters for this line.
        reference_timestamp: If provided, dates matching this timestamp's date
            are suppressed (redundant with EXIF-written date).

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

    # Add timestamp, unless it matches reference date (Bug 4 fix)
    entry_date_str = format_timestamp(entry.timestamp)
    ref_date_str = format_timestamp(reference_timestamp) if reference_timestamp else ""
    if entry_date_str and entry_date_str != ref_date_str:
        detail_parts.append(f"({entry_date_str})")

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
    reference_timestamp: Optional[datetime] = None,
) -> str:
    """Format a secondary source line with relative path notation.

    Uses relative path (e.g., '././service', './user/service') instead of
    full path when source shares components with primary.

    Args:
        entry: Secondary source entry.
        relative_path: Pre-computed relative path (or full path if different platform).
        max_chars: Maximum characters for this line.
        reference_timestamp: If provided, dates matching this timestamp's date
            are suppressed (redundant with EXIF-written date).

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

    # Add timestamp, unless it matches reference date (Bug 4 fix)
    entry_date_str = format_timestamp(entry.timestamp)
    ref_date_str = format_timestamp(reference_timestamp) if reference_timestamp else ""
    if entry_date_str and entry_date_str != ref_date_str:
        detail_parts.append(f"({entry_date_str})")

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
    reference_timestamp: Optional[datetime] = None,
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
        reference_timestamp: If provided, secondary dates matching this timestamp's
            date are suppressed (redundant with EXIF-written date).

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

    # 3. Group secondaries by source identity (relative to primary)
    same_source_entries = []
    different_source_entries = []

    for entry in secondary:
        if _same_source(primary, entry):
            same_source_entries.append(entry)
        else:
            different_source_entries.append(entry)

    # 4. Add unique message details from same-source entries as additional lines
    is_consolidating = is_consolidating_processor(primary.processor)
    for entry in same_source_entries:
        if remaining <= 0:
            break

        if _has_unique_context(entry, seen_message_keys, is_consolidating):
            # Add message key to seen set
            seen_message_keys.add(_get_context_key(entry))

            # Format as +details (secondary message detail line)
            message_line = _format_message_line(
                entry, is_secondary=True, reference_timestamp=reference_timestamp
            )
            if message_line:
                if len(message_line) + 5 <= remaining:  # +5 for &#xa; separator
                    lines.append(message_line)
                    remaining -= len(message_line) + 5

    # 5. Deduplicate different-source entries BEFORE formatting (Bug 1 + Bug 2 fix)
    # Use canonical deduplicate_secondary_entries() helper with _get_secondary_dedup_key()
    deduplicated_different_source = deduplicate_secondary_entries(
        different_source_entries, primary=primary
    )

    # 6. Add deduplicated different-source entries with relative path notation
    # Use fixed truncation budget (max_per_source) to avoid order-dependent dedup (Bug 1)
    # But respect max_total by checking if the line fits before adding
    for entry in deduplicated_different_source:
        if remaining <= 0:
            break

        # Get relative path
        relative_path = format_relative_source_path(primary, entry)
        if not relative_path:
            # Same source - shouldn't happen but handle gracefully
            continue

        # Format with fixed max_per_source budget (consistent truncation for dedup)
        line = _format_secondary_with_relative_path(
            entry, relative_path, max_per_source, reference_timestamp
        )

        # Only add if it fits within remaining budget (respects max_total)
        if len(line) + 5 <= remaining:  # +5 for &#xa; separator
            lines.append(line)
            remaining -= len(line) + 5
        # If it doesn't fit, skip this entry (don't try to truncate further)

    return "&#xa;".join(lines)


def filter_secondary_sources(
    primary: SourceEntry,
    secondary: List[SourceEntry],
) -> List[SourceEntry]:
    """Filter secondary sources, removing duplicates relative to primary.

    This is a convenience wrapper around `deduplicate_secondary_entries()` that
    only filters same-source entries (those matching primary's source identity).
    Different-source entries pass through unchanged.

    For comprehensive deduplication of all secondary entries (including
    different-source entries among themselves), use `deduplicate_secondary_entries()`
    directly.

    Args:
        primary: Primary source entry.
        secondary: List of secondary source entries.

    Returns:
        Filtered list of secondary sources.
    """
    # Separate same-source and different-source entries
    same_source = []
    different_source = []

    for entry in secondary:
        if _same_source(primary, entry):
            same_source.append(entry)
        else:
            different_source.append(entry)

    # Deduplicate same-source entries using canonical helper
    deduplicated_same = deduplicate_secondary_entries(same_source, primary=primary)

    # Return deduplicated same-source + all different-source
    return deduplicated_same + different_source


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
    entry.sender = metadata.get("sender", metadata.get("author", ""))

    # Content: prefer canonical "content", fall back to common alternatives used by some processors
    entry.content = metadata.get(
        "content",
        metadata.get("caption", metadata.get("text", metadata.get("message", ""))),
    )

    # Conversation: support both canonical "conversation" and processor-specific fields
    conversation = metadata.get("conversation") or metadata.get("chat", "")
    if not conversation:
        # Common raw fields emitted by messaging preprocessors
        conv_type = metadata.get("conversation_type") or metadata.get("channel_type")
        conv_title = (
            metadata.get("conversation_title")
            or metadata.get("channel_title")
            or metadata.get("chat_title")
        )
        conv_id = metadata.get("conversation_id") or metadata.get("channel_id")

        display = conv_title or conv_id or ""
        if conv_type == "dm":
            conversation = f"DM with {display}" if display else "DM"
        else:
            conversation = display

    entry.conversation = conversation

    # Parse timestamp
    ts = metadata.get("timestamp") or metadata.get("created")
    if isinstance(ts, datetime):
        entry.timestamp = ts
    elif isinstance(ts, str):
        try:
            # Try ISO format
            entry.timestamp = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

    return entry
