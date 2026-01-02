"""Media entry extraction utilities for metadata structures.

This module provides utilities to extract media entries from preprocessed
metadata.json structures, regardless of the content type (conversations,
media, or media_files).
"""

from typing import Any, Dict, Iterator

from common.metadata.schema import (
    ContentType,
    MetadataValidationError,
    PreprocessedMetadata,
    get_content_type,
    get_entry_timestamp,
)


def extract_all_media_entries(
    metadata: PreprocessedMetadata,
) -> Iterator[Dict[str, Any]]:
    """Extract all media entries from any metadata structure.

    Handles all three content types uniformly:
    - media_files: yields each entry directly
    - media: yields each post's media files with post context
    - conversations: yields each message's media files with context

    Also includes unmatched_media if present.

    This is useful for catalog building and cross-processor deduplication,
    providing a single interface to iterate over all media regardless of
    the underlying structure.

    Args:
        metadata: Parsed metadata.json as PreprocessedMetadata TypedDict.

    Yields:
        Media entry dicts with at minimum: filename, source_path
    """
    content_type = None
    try:
        content_type = get_content_type(metadata)
    except MetadataValidationError:
        # No valid content key found - still extract unmatched_media below
        pass

    # Get file_hashes lookup for content_hash (used by cross-processor dedup)
    file_hashes = metadata.get("file_hashes", {})

    if content_type == ContentType.MEDIA_FILES:
        # media_files entries should already have content_hash
        yield from metadata.get("media_files", [])

    elif content_type == ContentType.MEDIA:
        for post in metadata.get("media", []):
            source_paths = post.get("source_paths", {})
            for filename in post.get("media_files", []):
                yield {
                    "filename": filename,
                    "source_path": source_paths.get(filename, ""),
                    "content_hash": file_hashes.get(filename),
                    "timestamp": post.get("timestamp"),
                    "media_type": post.get("media_type"),
                    "caption": post.get("caption"),
                }

    elif content_type == ContentType.CONVERSATIONS:
        conversations = metadata.get("conversations", {})
        if isinstance(conversations, dict):
            conv_items = list(conversations.values())
        else:
            conv_items = conversations

        for conv in conv_items:
            conv_title = conv.get("conversation_title") or conv.get("conversation_name")
            for msg in conv.get("messages", []):
                source_paths = msg.get("source_paths", {})
                for filename in msg.get("media_files", []):
                    yield {
                        "filename": filename,
                        "source_path": source_paths.get(filename, ""),
                        "content_hash": file_hashes.get(filename),
                        "timestamp": get_entry_timestamp(msg),
                        "sender": msg.get("sender"),
                        "conversation": conv_title,
                    }

    # Include unmatched_media
    for entry in metadata.get("unmatched_media", []):
        yield entry
