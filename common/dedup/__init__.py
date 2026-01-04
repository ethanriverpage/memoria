"""
Deduplication subsystem for cross-processor media deduplication.

This package provides:
- Content and perceptual hash computation
- Hash database persistence
- Duplicate detection and grouping
- Metadata merging and description building
"""

from common.dedup.config import (
    DedupConfig,
    get_dedup_config,
    is_dedup_enabled,
)
from common.dedup.database import HashDatabase, HashDatabaseProtocol
from common.dedup.hashing import (
    compute_content_hash,
    compute_image_hash_with_dims,
    compute_video_hash_with_dims,
    compute_perceptual_hash_with_dims,
    get_media_dimensions,
    get_image_dimensions,
    get_video_dimensions,
    hash_similarity,
    # Live Photo utilities
    is_live_photo_pair,
    find_live_photo_video,
)
from common.dedup.description import (
    # Constants
    PLATFORM_GOOGLE,
    PLATFORM_INSTAGRAM,
    PLATFORM_SNAPCHAT,
    PLATFORM_DISCORD,
    PLATFORM_IMESSAGE,
    SERVICE_PHOTOS,
    SERVICE_CHAT,
    SERVICE_VOICE,
    SERVICE_MESSAGES,
    SERVICE_MEMORIES,
    SERVICE_STORIES,
    SERVICE_POSTS,
    SERVICE_REELS,
    # Processor mapping
    PROCESSOR_TO_PLATFORM_SERVICE,
    get_platform_service,
    # Data structures
    SourceEntry,
    # Core building functions
    build_source_line,
    format_message_line,
    build_description,
    build_image_description,
    # Helper functions
    create_source_entry,
    format_timestamp,
    format_primary_source,
    format_secondary_source,
    filter_secondary_sources,
    deduplicate_secondary_entries,
)
from common.dedup.service import (
    DeduplicationService,
    save_manifest,
    is_file_primary_by_hash,
    filter_primary_files,
    get_merged_description_for_file,
    get_merged_metadata_for_hash,
    get_primary_source_for_hash,
    get_secondary_reference,
    build_dedup_references,
    save_dedup_metadata,
    get_live_photo_base_for_hash,
)

__all__ = [
    # Config
    "DedupConfig",
    "get_dedup_config",
    "is_dedup_enabled",
    # Database
    "HashDatabase",
    "HashDatabaseProtocol",
    # Hashing
    "compute_content_hash",
    "compute_image_hash_with_dims",
    "compute_video_hash_with_dims",
    "compute_perceptual_hash_with_dims",
    "get_media_dimensions",
    "get_image_dimensions",
    "get_video_dimensions",
    "hash_similarity",
    "is_live_photo_pair",
    "find_live_photo_video",
    # Description - Constants
    "PLATFORM_GOOGLE",
    "PLATFORM_INSTAGRAM",
    "PLATFORM_SNAPCHAT",
    "PLATFORM_DISCORD",
    "PLATFORM_IMESSAGE",
    "SERVICE_PHOTOS",
    "SERVICE_CHAT",
    "SERVICE_VOICE",
    "SERVICE_MESSAGES",
    "SERVICE_MEMORIES",
    "SERVICE_STORIES",
    "SERVICE_POSTS",
    "SERVICE_REELS",
    # Description - Processor mapping
    "PROCESSOR_TO_PLATFORM_SERVICE",
    "get_platform_service",
    # Description - Data structures
    "SourceEntry",
    # Description - Core building functions
    "build_source_line",
    "format_message_line",
    "build_description",
    "build_image_description",
    # Description - Helper functions
    "create_source_entry",
    "format_timestamp",
    "format_primary_source",
    "format_secondary_source",
    "filter_secondary_sources",
    "deduplicate_secondary_entries",
    # Service
    "DeduplicationService",
    "save_manifest",
    "is_file_primary_by_hash",
    "filter_primary_files",
    "get_merged_description_for_file",
    "get_merged_metadata_for_hash",
    "get_primary_source_for_hash",
    "get_secondary_reference",
    "build_dedup_references",
    "save_dedup_metadata",
    "get_live_photo_base_for_hash",
]
