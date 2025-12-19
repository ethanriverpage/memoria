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
    reset_config,
)
from common.dedup.database import HashDatabase
from common.dedup.hashing import (
    compute_content_hash,
    compute_image_hash,
    compute_image_hash_with_dims,
    compute_video_hash_with_dims,
    compute_perceptual_hash_with_dims,
    get_media_dimensions,
    get_image_dimensions,
    get_video_dimensions,
    hash_similarity,
    hamming_distance,
    are_similar,
    is_perceptual_hash_available,
    # Live Photo utilities
    LivePhotoEntry,
    is_live_photo_pair,
    find_live_photo_video,
    find_live_photo_pairs,
    get_live_photo_content_hash,
    # Constants
    PERCEPTION_AVAILABLE,
    HEIC_AVAILABLE,
    DEFAULT_HASH_SIZE,
)
from common.dedup.catalog import catalog_media_files
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
    # Data structures
    SourceEntry,
    # Core building functions
    build_source_line,
    build_message_context,
    build_description,
    build_image_description,
    # Helper functions
    create_source_entry,
    truncate_text,
    format_timestamp,
    format_primary_source,
    format_secondary_source,
    merge_descriptions,
    parse_source_from_description,
)
from common.dedup.service import (
    DeduplicationService,
    save_manifest,
    load_manifest,
    is_file_primary,
    is_file_primary_by_hash,
    filter_primary_files,
    get_primary_for_file,
    get_merged_description_for_file,
    get_duplicate_group_for_file,
    get_secondary_reference,
    build_dedup_references,
    get_skipped_secondary_files,
    save_dedup_metadata,
)

__all__ = [
    # Config
    "DedupConfig",
    "get_dedup_config",
    "is_dedup_enabled",
    "reset_config",
    # Database
    "HashDatabase",
    # Hashing
    "compute_content_hash",
    "compute_image_hash",
    "compute_image_hash_with_dims",
    "compute_video_hash_with_dims",
    "compute_perceptual_hash_with_dims",
    "get_media_dimensions",
    "get_image_dimensions",
    "get_video_dimensions",
    "hash_similarity",
    "hamming_distance",
    "are_similar",
    "is_perceptual_hash_available",
    "LivePhotoEntry",
    "is_live_photo_pair",
    "find_live_photo_video",
    "find_live_photo_pairs",
    "get_live_photo_content_hash",
    "PERCEPTION_AVAILABLE",
    "HEIC_AVAILABLE",
    "DEFAULT_HASH_SIZE",
    # Catalog
    "catalog_media_files",
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
    # Description - Data structures
    "SourceEntry",
    # Description - Core building functions
    "build_source_line",
    "build_message_context",
    "build_description",
    "build_image_description",
    # Description - Helper functions
    "create_source_entry",
    "truncate_text",
    "format_timestamp",
    "format_primary_source",
    "format_secondary_source",
    "merge_descriptions",
    "parse_source_from_description",
    # Service
    "DeduplicationService",
    "save_manifest",
    "load_manifest",
    "is_file_primary",
    "is_file_primary_by_hash",
    "filter_primary_files",
    "get_primary_for_file",
    "get_merged_description_for_file",
    "get_duplicate_group_for_file",
    "get_secondary_reference",
    "build_dedup_references",
    "get_skipped_secondary_files",
    "save_dedup_metadata",
]
