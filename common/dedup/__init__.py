"""
Deduplication subsystem for cross-processor media deduplication.

This package provides:
- Content and perceptual hash computation
- Hash database persistence
- Duplicate detection and grouping
- Metadata merging and description building
"""

from common.dedup.config import DedupConfig, get_dedup_config
from common.dedup.database import HashDatabase
from common.dedup.hashing import (
    compute_content_hash,
    compute_perceptual_hash,
    compute_perceptual_hash_with_dims,
    get_media_dimensions,
    hash_similarity,
    PERCEPTION_AVAILABLE,
    HEIC_AVAILABLE,
)
from common.dedup.catalog import catalog_media_files
from common.dedup.description import (
    SourceEntry,
    build_image_description,
    create_source_entry,
    truncate_text,
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
    # Database
    "HashDatabase",
    # Hashing
    "compute_content_hash",
    "compute_perceptual_hash",
    "compute_perceptual_hash_with_dims",
    "get_media_dimensions",
    "hash_similarity",
    "PERCEPTION_AVAILABLE",
    "HEIC_AVAILABLE",
    # Catalog
    "catalog_media_files",
    # Description
    "SourceEntry",
    "build_image_description",
    "create_source_entry",
    "truncate_text",
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

