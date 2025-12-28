"""Metadata schema definitions for standardized metadata.json structure."""

from common.metadata.schema import (
    # Enums
    ContentType,
    # Export info
    ExportInfo,
    # Media entries
    MediaFileEntry,
    # Message/Conversation structures
    MessageEntry,
    ConversationEntry,
    # Post structures
    PostEntry,
    # Unmatched media
    UnmatchedMediaEntry,
    # Top-level
    PreprocessedMetadata,
    # Validation
    validate_metadata,
    get_content_type,
    MetadataValidationError,
)
from common.metadata.extraction import extract_all_media_entries

__all__ = [
    "ContentType",
    "ExportInfo",
    "MediaFileEntry",
    "MessageEntry",
    "ConversationEntry",
    "PostEntry",
    "UnmatchedMediaEntry",
    "PreprocessedMetadata",
    "validate_metadata",
    "get_content_type",
    "MetadataValidationError",
    "extract_all_media_entries",
]

