"""
Context merger functions for internal processor deduplication.

These functions define how metadata context should be merged when duplicates
are detected during preprocessing. Each processor may have different merging
requirements based on what metadata it tracks.

All merger functions follow the same signature:
    def merger(existing: Dict, new: Dict) -> None

The merger modifies `existing` in-place to incorporate data from `new`.
"""

from typing import Any, Dict


def merge_albums(existing: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Merge album lists for Google Photos.

    When the same photo appears in multiple albums, we want to preserve
    all album memberships. This merger accumulates album names from
    duplicate files.

    Args:
        existing: Context dict for the primary (first seen) file.
        new: Context dict for the duplicate file being merged.
    """
    if "albums" not in existing:
        existing["albums"] = []

    for album in new.get("albums", []):
        if album not in existing["albums"]:
            existing["albums"].append(album)


def merge_snapchat_context(existing: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Merge message context for Snapchat messages.

    When the same media is sent in multiple messages (or the same message
    references the media multiple times), we accumulate the message list.

    Args:
        existing: Context dict for the primary (first seen) file.
        new: Context dict for the duplicate file being merged.
    """
    if "messages" not in existing:
        existing["messages"] = []

    if "messages" in new:
        existing["messages"].extend(new["messages"])


def merge_imessage_context(existing: Dict[str, Any], new: Dict[str, Any]) -> None:
    """Merge message context for iMessage attachments.

    When the same media is attached to multiple messages (e.g., forwarded
    across conversations or cross-export duplicates), we accumulate all
    message occurrences.

    Args:
        existing: Context dict for the primary (first seen) file.
        new: Context dict for the duplicate file being merged.
    """
    if "messages" not in existing:
        existing["messages"] = []

    # New context contains message data to accumulate
    existing["messages"].append(new)

