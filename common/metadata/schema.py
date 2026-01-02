"""Standardized metadata.json schema for all processors.

This schema supports:
- Within-processor deduplication (via PreprocessorBase)
- Cross-processor deduplication (via catalog building)
- Failure tracking (FailureTracker + unmatched_media)

The schema defines three content structure types:
1. MEDIA_FILES - Flat list of media entries (Google Photos)
2. MEDIA - List of posts containing media (Instagram, Instaloader)
3. CONVERSATIONS - Nested messages containing media (iMessage, Discord, etc.)

All preprocessors MUST produce metadata.json with:
- export_info: Required metadata about the export
- file_hashes: Dict mapping filename -> content_hash
- Exactly ONE content key: conversations, media, or media_files
- All media entries MUST have source_path for cross-processor dedup
"""

from enum import Enum
from typing import Any, Dict, List, TypedDict, Union


# =============================================================================
# Exceptions
# =============================================================================


class MetadataValidationError(ValueError):
    """Raised when metadata.json validation fails."""


# =============================================================================
# Content Structure Type
# =============================================================================


class ContentType(str, Enum):
    """Identifies the structure type for validation and catalog building."""

    CONVERSATIONS = "conversations"  # Messaging platforms
    MEDIA = "media"  # Post-based platforms
    MEDIA_FILES = "media_files"  # Flat file list


# Valid content keys
CONTENT_KEYS = [
    ContentType.CONVERSATIONS.value,
    ContentType.MEDIA.value,
    ContentType.MEDIA_FILES.value,
]


# =============================================================================
# Export Info (Required)
# =============================================================================


class ExportInfo(TypedDict, total=False):
    """Export metadata - required fields plus optional processor-specific stats.

    Required fields:
        export_path: Path to the original export directory
        export_username: Username/identifier from the export
        processed_date: ISO format timestamp of when preprocessing ran
        platform: Platform identifier for unambiguous detection (e.g., "Snapchat")
        service: Service identifier within platform (e.g., "messages"), or None

    Optional fields:
        export_name: Name of the export directory
        export_paths: List of paths for multi-export processors (iMessage)
        username_source: Method used to detect username

    Processor-specific stats are allowed as additional keys.
    """

    # Required
    export_path: str
    export_username: str
    processed_date: str
    platform: str
    service: str  # Can be None in actual JSON

    # Standard optional
    export_name: str
    export_paths: List[str]
    username_source: str


# =============================================================================
# Media File Entries
# =============================================================================


class MediaFileEntry(TypedDict, total=False):
    """Media file entry for flat structures (Google Photos, Snapchat Memories).

    Required fields:
        filename: Output filename
        source_path: Absolute path to source file (required for cross-processor dedup)

    Optional fields:
        content_hash: xxHash64 of file content (avoids re-computation)
        timestamp: ISO format timestamp
        capture_timestamp: Preferred timestamp for photos (from EXIF)

    Platform-specific metadata fields are allowed.
    """

    # Required for cross-processor dedup
    filename: str
    source_path: str

    # Optional but recommended
    content_hash: str

    # Timestamps
    timestamp: str
    date: str  # Snapchat Memories uses "date" field
    capture_timestamp: str

    # Google Photos specific
    albums: List[str]
    gps: Dict[str, float]
    people: List[str]
    title: str
    description: str

    # Snapchat Memories specific
    media_type: str  # "PHOTO" or "VIDEO"
    media_filename: str  # Original filename in export
    overlay_filename: str  # Associated overlay file


# =============================================================================
# Message and Conversation Structures
# =============================================================================


class MessageEntry(TypedDict, total=False):
    """Message entry for messaging platforms.

    Media attachments use:
        media_files: List of filenames
        media_file: Single filename (some processors use singular)
        source_paths: Dict mapping filename -> absolute source path

    The source_paths dict is REQUIRED for any message with media.
    """

    # Timing
    timestamp: str
    message_id: str
    created: str  # Alternative timestamp field (Snapchat)

    # Participants
    sender: str
    is_from_me: bool  # iMessage: True if sent by device owner
    is_sender: bool  # Snapchat: True if you sent the message

    # Media attachments
    media_files: List[str]
    media_file: str
    source_paths: Dict[str, str]
    overlay_file: str  # Snapchat: associated overlay filename
    media_id: str  # Snapchat: Media ID reference

    # Content
    content: str
    text: str

    # iMessage-specific
    is_live_photo_video: bool  # True if this is a Live Photo video component
    source_export: str  # Which export this came from (cross-export dedup)
    is_duplicate: bool  # True if this is a duplicate occurrence
    live_photo_pair_keys: List[str]  # Stable keys for pairing Live Photo still + video

    # Platform-specific metadata
    metadata: Dict[str, Any]


class ConversationEntry(TypedDict, total=False):
    """Conversation entry containing messages.

    Supports both dict and list formats for conversations at top level.
    """

    # Identity
    conversation_id: str
    conversation_title: str
    conversation_name: str
    conversation_type: str  # "dm", "group", "space", etc.
    chat_identifier: str  # iMessage: phone/email identifier

    # Discord-specific
    guild_name: str  # Discord server name
    channel_title: str  # Discord channel name
    channel_type: str  # Discord: "DM", "GROUP_DM", "GUILD_TEXT", etc.

    # Participants
    participants: List[str]

    # Messages
    messages: List[MessageEntry]

    # Metadata
    metadata: Dict[str, Any]


# =============================================================================
# Post Structures (Instagram, Instaloader)
# =============================================================================


class PostEntry(TypedDict, total=False):
    """Post entry for media-centric platforms (Instagram, Instaloader).

    Media attachments use:
        media_files: List of filenames
        source_paths: Dict mapping filename -> absolute source path

    The source_paths dict is REQUIRED for any post with media.
    """

    # Identity
    post_id: str

    # Timing
    timestamp: str

    # Type
    media_type: str  # "posts", "stories", "reels", "profile", "archived_posts"

    # Media
    media_files: List[str]
    source_paths: Dict[str, str]

    # Content
    caption: str

    # Location
    location: str

    # Instaloader-specific
    shortcode: str  # Instagram post shortcode

    # Metadata
    metadata: Dict[str, Any]


# =============================================================================
# Unmatched Media
# =============================================================================


class UnmatchedMediaEntry(TypedDict, total=False):
    """Media file processable but lacking full context.

    Distinct from FailureTracker orphaned media (a matching failure recorded via
    FailureTracker and copied to issues/failed-matching/).

    Unmatched media WILL be processed but with limited metadata.
    Common scenarios:
    - File exists in export but not referenced in any conversation
    - File matched to a message but sender/conversation unknown
    """

    # Required
    filename: str
    source_path: str

    # Optional
    content_hash: str
    timestamp: str
    media_id: str

    # What we could extract
    metadata: Dict[str, Any]


# =============================================================================
# Top-Level Metadata Structure
# =============================================================================


class PreprocessedMetadata(TypedDict, total=False):
    """Top-level metadata.json structure.

    Invariants:
    1. export_info is required with export_path, export_username, processed_date
    2. file_hashes is required (may be empty dict)
    3. Exactly ONE content key: conversations, media, or media_files
    4. All media entries must have source_path for cross-processor dedup

    The unmatched_media field is optional and contains files that can be
    processed but lack full context (e.g., no conversation association).
    """

    # Required
    export_info: ExportInfo
    file_hashes: Dict[str, str]

    # Content - exactly ONE of these
    conversations: Union[List[ConversationEntry], Dict[str, ConversationEntry]]
    media: List[PostEntry]
    media_files: List[MediaFileEntry]

    # Optional
    unmatched_media: List[UnmatchedMediaEntry]


# =============================================================================
# Timestamp Extraction Helpers
# =============================================================================

# Timestamp field priority order for extraction.
# Different processors use different field names for the same concept:
# - timestamp: Discord, Instagram, Google Chat, Google Voice, Instaloader
# - capture_timestamp: Google Photos (preferred photo timestamp from EXIF)
# - date: Snapchat Memories
# - created: Snapchat Messages, iMessage
# - primary_created: Merged message contexts (iMessage, Snapchat cross-message dedup)
#
# This priority list ensures consistent timestamp extraction across all processors.
_TIMESTAMP_FIELDS = (
    "canonical_timestamp",  # Normalized by processor (highest priority)
    "timestamp",  # Standard field (Discord, Instagram, etc.)
    "capture_timestamp",  # Google Photos
    "date",  # Snapchat Memories
    "primary_created",  # Merged message contexts
    "created",  # Snapchat Messages, iMessage
)


def get_entry_timestamp(entry: Dict[str, Any]) -> str | None:
    """Extract timestamp from a metadata entry using standard field priority.

    This function centralizes timestamp extraction logic to handle the various
    field names used by different processors. Use this instead of manually
    checking multiple field names.

    Args:
        entry: A media file entry, message entry, or post entry dict.

    Returns:
        The timestamp string if found, None otherwise.

    Example:
        >>> entry = {"filename": "photo.jpg", "capture_timestamp": "2023-01-15T10:30:00Z"}
        >>> get_entry_timestamp(entry)
        '2023-01-15T10:30:00Z'

        >>> msg = {"sender": "Alice", "created": "2023-01-15 10:30:00 UTC"}
        >>> get_entry_timestamp(msg)
        '2023-01-15 10:30:00 UTC'
    """
    for field in _TIMESTAMP_FIELDS:
        value = entry.get(field)
        if value:
            return value
    return None


# =============================================================================
# Validation Functions
# =============================================================================


def get_content_type(metadata: "PreprocessedMetadata") -> ContentType:
    """Determine content type from metadata.

    Args:
        metadata: Parsed metadata.json as PreprocessedMetadata TypedDict.

    Returns:
        ContentType enum value

    Raises:
        MetadataValidationError: If no valid content key found
    """
    if "conversations" in metadata:
        return ContentType.CONVERSATIONS
    elif "media" in metadata:
        return ContentType.MEDIA
    elif "media_files" in metadata:
        return ContentType.MEDIA_FILES
    raise MetadataValidationError(
        "No valid content key found (expected one of: conversations, media, media_files)"
    )


def validate_metadata(
    metadata: "PreprocessedMetadata", require_source_paths: bool = True
) -> None:
    """Validate metadata.json structure.

    Checks:
    1. export_info exists with required fields
    2. file_hashes exists
    3. Exactly one content key present
    4. All media entries have source_path (if require_source_paths=True)

    Args:
        metadata: Parsed metadata.json as PreprocessedMetadata TypedDict.
        require_source_paths: Whether to validate source_path in media entries

    Raises:
        MetadataValidationError: If structure is invalid
    """
    # Required: export_info
    if "export_info" not in metadata:
        raise MetadataValidationError("metadata missing required 'export_info'")

    export_info = metadata["export_info"]
    required_export_fields = ["export_path", "export_username", "processed_date"]
    for field in required_export_fields:
        if field not in export_info:
            raise MetadataValidationError(
                f"export_info missing required field: {field}"
            )

    # Required: file_hashes
    if "file_hashes" not in metadata:
        raise MetadataValidationError("metadata missing required 'file_hashes'")

    # Exactly one content key
    present = [k for k in CONTENT_KEYS if k in metadata]
    if len(present) == 0:
        raise MetadataValidationError(
            f"metadata must have exactly one content key (one of: {CONTENT_KEYS}), found none"
        )
    if len(present) > 1:
        raise MetadataValidationError(
            f"metadata must have exactly one content key (one of: {CONTENT_KEYS}), found multiple: {present}"
        )

    # Validate source_paths if required
    if require_source_paths:
        content_key = present[0]
        _validate_source_paths(metadata, content_key)


def _validate_source_paths(metadata: Dict[str, Any], content_key: str) -> None:
    """Validate all media entries have source_path.

    Args:
        metadata: Parsed metadata.json dict
        content_key: The content key to validate

    Raises:
        MetadataValidationError: If any media entry is missing source_path
    """
    content = metadata[content_key]

    if content_key == "media_files":
        _validate_flat_source_paths(content)

    elif content_key == "media":
        _validate_post_source_paths(content)

    elif content_key == "conversations":
        _validate_conversation_source_paths(content)

    # Also validate unmatched_media if present
    unmatched = metadata.get("unmatched_media", [])
    for entry in unmatched:
        if "source_path" not in entry:
            raise MetadataValidationError(
                f"unmatched_media entry missing source_path: {entry.get('filename', 'unknown')}"
            )


def _validate_flat_source_paths(media_files: List[Dict[str, Any]]) -> None:
    """Validate source_paths in flat media_files list."""
    for entry in media_files:
        if "source_path" not in entry:
            raise MetadataValidationError(
                f"media_files entry missing source_path: {entry.get('filename', 'unknown')}"
            )


def _validate_post_source_paths(posts: List[Dict[str, Any]]) -> None:
    """Validate source_paths in post-based structure."""
    for post in posts:
        source_paths = post.get("source_paths", {})
        media_files = post.get("media_files", [])

        for media_file in media_files:
            if media_file not in source_paths:
                raise MetadataValidationError(
                    f"post missing source_path for media file: {media_file}"
                )


def _validate_conversation_source_paths(
    conversations: Union[List[Dict[str, Any]], Dict[str, Dict[str, Any]]],
) -> None:
    """Validate source_paths in conversation-based structure."""
    # Handle both list and dict formats
    if isinstance(conversations, dict):
        conv_items = list(conversations.values())
    else:
        conv_items = conversations

    for conv in conv_items:
        messages = conv.get("messages", [])
        for msg in messages:
            source_paths = msg.get("source_paths", {})

            # Collect all media files (both plural and singular forms)
            media_files = list(msg.get("media_files", []))
            if msg.get("media_file"):
                media_files.append(msg["media_file"])

            # Skip messages with no media
            if not media_files:
                continue

            for media_file in media_files:
                if media_file not in source_paths:
                    conv_id = conv.get("conversation_id") or conv.get(
                        "conversation_title", "unknown"
                    )
                    raise MetadataValidationError(
                        f"message in conversation '{conv_id}' missing source_path for: {media_file}"
                    )
