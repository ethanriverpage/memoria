#!/usr/bin/env python3
"""
Platform tagging logic for Immich uploads.

Derives per-file platform tag sets from:
1. Dedup manifest (primary + secondary processors) for cross-processor dedup
2. Album name prefix as fallback for non-dedup mode

Also provides file iteration matching the upload-only flow so tagging sees
exactly what files were uploaded.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, TYPE_CHECKING

from common.dedup.description import get_platform_service
from common.dedup.hashing import compute_content_hash

if TYPE_CHECKING:
    from common.upload.immich_api import ImmichAPIClient

logger = logging.getLogger(__name__)

# Known platform prefixes in album names (Platform/username/service structure)
KNOWN_PLATFORMS = {"Google", "Instagram", "Snapchat", "Discord", "iMessage"}


def platform_from_album_name(album_name: str) -> Optional[str]:
    """Extract platform from album name.

    Album names follow Platform/username[/service] structure.

    Args:
        album_name: Album name (e.g., "Google/username/photos")

    Returns:
        Platform name if recognized, None otherwise
    """
    if not album_name:
        return None

    # First component is the platform
    first_component = album_name.split("/")[0]
    if first_component in KNOWN_PLATFORMS:
        return first_component

    return None


def platforms_for_content_hash(
    content_hash: str,
    dedup_manifest: Optional[Dict[str, Any]],
) -> Set[str]:
    """Get all platforms that contributed to a file by its content hash.

    For files in a dedup group, returns the union of:
    - Primary processor's platform
    - All secondary processors' platforms

    This enables tagging a deduplicated output file with all platforms
    where the original media appeared.

    Args:
        content_hash: xxHash64 content hash of the file
        dedup_manifest: Dedup manifest from analyze_from_db(), or None

    Returns:
        Set of platform names. Empty set if hash not in manifest.
    """
    if not dedup_manifest or not content_hash:
        return set()

    duplicates = dedup_manifest.get("duplicates", {})

    # Direct lookup for exact hash matches
    group = duplicates.get(content_hash)

    # Also check perceptual groups if not found
    if group is None:
        for key, grp in duplicates.items():
            if key.startswith("perceptual_"):
                # Check best_quality, primary, and secondaries
                best = grp.get("best_quality", {})
                primary = grp.get("primary", {})
                if (
                    best.get("content_hash") == content_hash
                    or primary.get("content_hash") == content_hash
                ):
                    group = grp
                    break
                for sec in grp.get("secondary", []):
                    if sec.get("content_hash") == content_hash:
                        group = grp
                        break
                if group:
                    break

    if group is None:
        return set()

    platforms: Set[str] = set()

    # Add primary processor's platform
    primary = group.get("primary", {})
    primary_processor = primary.get("processor", "")
    if primary_processor:
        platform, _service = get_platform_service(primary_processor)
        platforms.add(platform)

    # Add all secondary processors' platforms
    for secondary in group.get("secondary", []):
        sec_processor = secondary.get("processor", "")
        if sec_processor:
            platform, _service = get_platform_service(sec_processor)
            platforms.add(platform)

    return platforms


def platforms_for_file(
    file_path: Path,
    dedup_manifest: Optional[Dict[str, Any]],
    album_name: str,
) -> Set[str]:
    """Get platforms for a file, with album fallback.

    Tries dedup manifest first (if available), falls back to album name.

    Args:
        file_path: Path to the file
        dedup_manifest: Dedup manifest or None
        album_name: Album name for fallback

    Returns:
        Set of platform names (never empty if album is valid)
    """
    # Try dedup manifest first
    if dedup_manifest:
        content_hash = compute_content_hash(file_path)
        platforms = platforms_for_content_hash(content_hash, dedup_manifest)
        if platforms:
            return platforms

    # Fallback to album name
    platform = platform_from_album_name(album_name)
    if platform:
        return {platform}

    return set()


def iter_upload_files(
    path: Path,
    ignore_patterns: Optional[List[str]] = None,
) -> Iterator[Path]:
    """Iterate files that would be uploaded from a directory.

    Uses the same scanning logic as memoria's upload-only mode so
    tagging sees exactly what files were uploaded.

    Args:
        path: Path to scan (file or directory)
        ignore_patterns: Glob patterns to ignore (defaults to standard ignores)

    Yields:
        Path objects for each file that would be uploaded
    """
    from common.files.scanner import iter_all_files

    if ignore_patterns is None:
        # Default patterns match immich.py defaults
        ignore_patterns = ["issues/**", "**/*matching/**"]

    if not path.exists():
        return

    if path.is_file():
        yield path
        return

    yield from iter_all_files(path, ignore_patterns=ignore_patterns, skip_hidden=True)


def build_platform_to_assets_map(
    upload_path: Path,
    album_name: str,
    dedup_manifest: Optional[Dict[str, Any]],
    ignore_patterns: Optional[List[str]] = None,
) -> Dict[str, List[Path]]:
    """Build mapping from platforms to local files.

    Groups files by their platform tag(s). A file may appear under
    multiple platforms if it's in a dedup group with multiple sources.

    Args:
        upload_path: Path containing uploaded files
        album_name: Album name (for fallback platform detection)
        dedup_manifest: Dedup manifest or None
        ignore_patterns: Glob patterns to ignore

    Returns:
        Dict mapping platform name to list of file paths
    """
    platform_to_files: Dict[str, List[Path]] = {}

    for file_path in iter_upload_files(upload_path, ignore_patterns):
        platforms = platforms_for_file(file_path, dedup_manifest, album_name)

        for platform in platforms:
            if platform not in platform_to_files:
                platform_to_files[platform] = []
            platform_to_files[platform].append(file_path)

    return platform_to_files


# =============================================================================
# Asset Matching (local files -> Immich asset IDs)
# =============================================================================


def _build_local_file_key(file_path: Path) -> tuple:
    """Build lookup key for a local file.

    Args:
        file_path: Path to local file

    Returns:
        Tuple of (basename, file_size) for matching
    """
    try:
        stat = file_path.stat()
        return (file_path.name.lower(), stat.st_size)
    except OSError:
        return (file_path.name.lower(), 0)


def _build_asset_key(asset: Dict[str, Any]) -> tuple:
    """Build lookup key for an Immich asset.

    Handles API version differences in field names.

    Args:
        asset: Immich asset dict from API

    Returns:
        Tuple of (filename, file_size) for matching
    """
    # Get filename - try various field names
    filename = asset.get("originalFileName") or asset.get("originalPath", "")
    if "/" in filename:
        filename = filename.rsplit("/", 1)[-1]
    filename = filename.lower()

    # Get file size - try various field names
    size = (
        asset.get("exifInfo", {}).get("fileSizeInBytes")
        or asset.get("fileSizeInBytes")
        or asset.get("fileSize")
        or asset.get("size")
        or 0
    )

    return (filename, size)


def match_local_files_to_assets(
    local_files: List[Path],
    immich_assets: List[Dict[str, Any]],
) -> Dict[Path, str]:
    """Match local files to Immich assets by filename and size.

    Uses (filename, size) as the primary matching key. For ambiguous
    matches (same name+size), logs a warning and uses the first match.

    Args:
        local_files: List of local file paths
        immich_assets: List of Immich asset dicts from API

    Returns:
        Dict mapping local file path to Immich asset ID
    """
    # Build asset lookup by (filename, size)
    asset_lookup: Dict[tuple, List[Dict[str, Any]]] = {}
    for asset in immich_assets:
        key = _build_asset_key(asset)
        if key not in asset_lookup:
            asset_lookup[key] = []
        asset_lookup[key].append(asset)

    # Match local files
    matched: Dict[Path, str] = {}
    unmatched: List[Path] = []
    ambiguous: List[Path] = []

    for file_path in local_files:
        key = _build_local_file_key(file_path)
        candidates = asset_lookup.get(key, [])

        if len(candidates) == 1:
            matched[file_path] = candidates[0]["id"]
        elif len(candidates) > 1:
            # Ambiguous - use first match and log warning
            matched[file_path] = candidates[0]["id"]
            ambiguous.append(file_path)
        else:
            unmatched.append(file_path)

    if ambiguous:
        logger.warning(
            "Ambiguous asset matches for %d files (used first match)",
            len(ambiguous),
        )
        for path in ambiguous[:5]:  # Log first 5
            logger.debug("  Ambiguous: %s", path.name)

    if unmatched:
        logger.warning(
            "Could not match %d local files to Immich assets",
            len(unmatched),
        )
        for path in unmatched[:5]:  # Log first 5
            logger.debug("  Unmatched: %s", path.name)

    return matched


def build_platform_to_asset_ids(
    upload_path: Path,
    album_name: str,
    immich_assets: List[Dict[str, Any]],
    dedup_manifest: Optional[Dict[str, Any]],
    ignore_patterns: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """Build mapping from platforms to Immich asset IDs.

    Complete workflow: scan local files, match to Immich assets,
    group by platform.

    Args:
        upload_path: Path containing uploaded files
        album_name: Album name (for fallback platform detection)
        immich_assets: Immich assets from the album (via API)
        dedup_manifest: Dedup manifest or None
        ignore_patterns: Glob patterns to ignore

    Returns:
        Dict mapping platform name to list of asset IDs
    """
    # Get local files
    local_files = list(iter_upload_files(upload_path, ignore_patterns))
    if not local_files:
        return {}

    # Match local files to Immich assets
    file_to_asset_id = match_local_files_to_assets(local_files, immich_assets)

    # Group by platform
    platform_to_asset_ids: Dict[str, List[str]] = {}

    for file_path, asset_id in file_to_asset_id.items():
        platforms = platforms_for_file(file_path, dedup_manifest, album_name)

        for platform in platforms:
            if platform not in platform_to_asset_ids:
                platform_to_asset_ids[platform] = []
            if asset_id not in platform_to_asset_ids[platform]:
                platform_to_asset_ids[platform].append(asset_id)

    return platform_to_asset_ids


# =============================================================================
# Main Tagging Entry Point
# =============================================================================


def apply_platform_tags(
    client: "ImmichAPIClient",
    upload_path: Path,
    album_name: str,
    dedup_manifest: Optional[Dict[str, Any]] = None,
    ignore_patterns: Optional[List[str]] = None,
    platform_colors: Optional[Dict[str, str]] = None,
) -> Dict[str, int]:
    """Apply platform tags to uploaded assets in Immich.

    Main entry point for post-upload tagging. This function:
    1. Ensures platform tags exist (creates if missing)
    2. Fetches album assets from Immich
    3. Matches local files to Immich asset IDs
    4. Applies platform tags based on dedup manifest or album name

    Args:
        client: ImmichAPIClient instance
        upload_path: Path to uploaded files
        album_name: Immich album name (e.g., "Google/username/photos")
        dedup_manifest: Dedup manifest dict, or None
        ignore_patterns: Glob patterns to ignore
        platform_colors: Platform to color mapping (uses defaults if None)

    Returns:
        Dict mapping platform name to count of assets tagged
    """
    from common.upload.immich_api import DEFAULT_PLATFORM_COLORS

    if platform_colors is None:
        platform_colors = DEFAULT_PLATFORM_COLORS

    # Ensure platform tags exist
    platform_to_tag_id = client.ensure_platform_tags(platform_colors)

    # Get album assets from Immich
    immich_assets = client.get_album_assets(album_name)
    if not immich_assets:
        logger.warning("No assets found in album %s for tagging", album_name)
        return {}

    # Build platform -> asset IDs mapping
    platform_to_asset_ids = build_platform_to_asset_ids(
        upload_path=upload_path,
        album_name=album_name,
        immich_assets=immich_assets,
        dedup_manifest=dedup_manifest,
        ignore_patterns=ignore_patterns,
    )

    if not platform_to_asset_ids:
        logger.info("No assets to tag for %s", album_name)
        return {}

    # Apply tags
    results: Dict[str, int] = {}

    for platform, asset_ids in platform_to_asset_ids.items():
        if not asset_ids:
            continue

        tag_id = platform_to_tag_id.get(platform)
        if not tag_id:
            logger.warning("No tag ID for platform %s, skipping", platform)
            continue

        try:
            client.tag_assets(tag_id, asset_ids)
            results[platform] = len(asset_ids)
            logger.info("Tagged %d assets with %s", len(asset_ids), platform)
        except Exception as e:
            logger.error("Failed to tag assets with %s: %s", platform, e)
            results[platform] = 0

    return results


def apply_platform_tags_for_upload_target(
    upload_path: str,
    album_name: str,
    dedup_manifest_path: Optional[str],
    immich_url: str,
    immich_key: str,
    ignore_patterns: Optional[List[str]] = None,
) -> Dict[str, int]:
    """Apply platform tags for a single upload target.

    Convenience wrapper that creates the API client and loads the manifest.
    Designed to be called from memoria.py after each successful upload.

    Args:
        upload_path: Path to uploaded files
        album_name: Immich album name
        dedup_manifest_path: Path to dedup_manifest.json, or None
        immich_url: Immich server URL
        immich_key: Immich API key
        ignore_patterns: Glob patterns to ignore

    Returns:
        Dict mapping platform name to count of assets tagged
    """
    from common.upload.immich_api import ImmichAPIClient

    # Load dedup manifest if provided
    dedup_manifest = None
    if dedup_manifest_path:
        manifest_path = Path(dedup_manifest_path)
        if manifest_path.exists():
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    dedup_manifest = json.load(f)
            except Exception as e:
                logger.warning("Failed to load dedup manifest: %s", e)

    # Create API client
    client = ImmichAPIClient(immich_url, immich_key)

    # Verify connection
    connected, error = client.verify_connection()
    if not connected:
        logger.error("Failed to connect to Immich for tagging: %s", error)
        return {}

    # Apply tags
    return apply_platform_tags(
        client=client,
        upload_path=Path(upload_path),
        album_name=album_name,
        dedup_manifest=dedup_manifest,
        ignore_patterns=ignore_patterns,
    )

