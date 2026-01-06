#!/usr/bin/env python3
"""
Immich REST API client for platform tagging.

Provides tag management and album/asset discovery needed for post-upload
platform tagging. API contract is validated by standalone/test_immich_api.py.

This is a production client extracted from the test client patterns.
"""

import logging
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# Default platform tag colors (match standalone/test_immich_api.py PLATFORM_TAGS)
DEFAULT_PLATFORM_COLORS: Dict[str, str] = {
    "Google": "#34A853",
    "Instagram": "#C13584",
    "Snapchat": "#FFFC00",
    "Discord": "#5865F2",
    "iMessage": "#0A84FF",
}


class ImmichAPIError(Exception):
    """Exception raised for Immich API errors."""

    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ImmichAPIClient:
    """Immich REST API client for tag and album operations.

    Provides the subset of Immich API needed for platform tagging:
    - Tag CRUD (list, create, update, find by name)
    - Bulk tag assignment to assets
    - Album lookup and asset discovery

    API contract validated by standalone/test_immich_api.py.
    """

    def __init__(
        self,
        url: str,
        api_key: str,
        timeout: int = 30,
    ):
        """Initialize client with server URL and API key.

        Args:
            url: Immich server URL (e.g., https://immich.local or https://immich.local/api)
            api_key: Immich API key
            timeout: Default request timeout in seconds
        """
        # Normalize URL - ensure it ends with /api
        url = url.rstrip("/")
        if not url.endswith("/api"):
            url = f"{url}/api"
        self.url = url
        self.headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json",
        }
        self.timeout = timeout

    def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[dict] = None,
        timeout: Optional[int] = None,
        raise_for_status: bool = True,
    ) -> requests.Response:
        """Make an API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            endpoint: API endpoint (e.g., /albums)
            json: JSON body for POST/PUT/PATCH requests
            timeout: Request timeout in seconds (uses default if None)
            raise_for_status: If True, raise exception on non-2xx status

        Returns:
            Response object

        Raises:
            ImmichAPIError: On request failure if raise_for_status is True
        """
        url = f"{self.url}{endpoint}"
        timeout = timeout or self.timeout

        try:
            response = requests.request(
                method,
                url,
                headers=self.headers,
                json=json,
                timeout=timeout,
            )
            if raise_for_status and not response.ok:
                raise ImmichAPIError(
                    f"{method} {endpoint} failed: {response.status_code} {response.text}",
                    status_code=response.status_code,
                )
            return response
        except requests.ConnectionError as e:
            raise ImmichAPIError(f"Connection error: {e}") from e
        except requests.Timeout as e:
            raise ImmichAPIError(f"Request timeout: {e}") from e
        except requests.RequestException as e:
            raise ImmichAPIError(f"Request error: {e}") from e

    # -------------------------------------------------------------------------
    # Connection / Auth
    # -------------------------------------------------------------------------

    def verify_connection(self) -> tuple[bool, Optional[str]]:
        """Verify connection to Immich server.

        Returns:
            Tuple of (success, error_message)
        """
        try:
            response = self._request(
                "GET", "/server/version", raise_for_status=False
            )
            if response.status_code == 200:
                return True, None
            elif response.status_code == 401:
                return False, "Authentication failed - check your API key"
            else:
                return False, f"Server returned status {response.status_code}"
        except ImmichAPIError as e:
            return False, str(e)

    # -------------------------------------------------------------------------
    # Tags
    # -------------------------------------------------------------------------

    def list_tags(self) -> List[Dict[str, Any]]:
        """Get all tags.

        Returns:
            List of tag objects with id, name/value, color, etc.
        """
        response = self._request("GET", "/tags")
        return response.json()

    def create_tag(self, name: str, color: Optional[str] = None) -> Dict[str, Any]:
        """Create a new tag.

        Args:
            name: Tag name (can include / for hierarchy, e.g., "Platform/Discord")
            color: Optional hex color (e.g., "#4285F4")

        Returns:
            Created tag object with id, name, color, etc.
        """
        body: Dict[str, Any] = {"name": name}
        if color:
            body["color"] = color
        response = self._request("POST", "/tags", json=body)
        return response.json()

    def get_tag(self, tag_id: str) -> Optional[Dict[str, Any]]:
        """Get a tag by ID.

        Args:
            tag_id: Tag UUID

        Returns:
            Tag object or None if not found
        """
        response = self._request(
            "GET", f"/tags/{tag_id}", raise_for_status=False
        )
        if response.status_code == 200:
            return response.json()
        return None

    def find_tag_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Find a tag by its exact name.

        Handles API version differences where tag name may be in
        'name' or 'value' field.

        Args:
            name: Tag name to search for (exact match)

        Returns:
            Tag object if found, None otherwise
        """
        tags = self.list_tags()
        for tag in tags:
            # Check both 'name' and 'value' fields depending on API version
            tag_name = tag.get("name") or tag.get("value", "")
            if tag_name == name:
                return tag
        return None

    def update_tag(self, tag_id: str, color: str) -> Optional[Dict[str, Any]]:
        """Update a tag's color.

        Tries PUT first, then PATCH as fallback (API version compatibility).

        Args:
            tag_id: Tag UUID
            color: New hex color (e.g., "#4285F4")

        Returns:
            Updated tag object, or None if update not supported
        """
        body = {"color": color}

        # Try PUT first (some API versions)
        response = self._request(
            "PUT", f"/tags/{tag_id}", json=body, raise_for_status=False
        )
        if response.status_code == 404:
            # Try PATCH as fallback
            response = self._request(
                "PATCH", f"/tags/{tag_id}", json=body, raise_for_status=False
            )

        if response.status_code == 404:
            # Update not supported
            return None

        if not response.ok:
            raise ImmichAPIError(
                f"Failed to update tag: {response.status_code} {response.text}",
                status_code=response.status_code,
            )
        return response.json()

    def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag.

        Args:
            tag_id: Tag UUID

        Returns:
            True if deleted successfully
        """
        response = self._request(
            "DELETE", f"/tags/{tag_id}", raise_for_status=False
        )
        return response.status_code in (200, 204)

    def tag_assets(self, tag_id: str, asset_ids: List[str]) -> List[Dict[str, Any]]:
        """Add assets to a tag (bulk tagging).

        Args:
            tag_id: Tag UUID
            asset_ids: List of asset UUIDs to tag

        Returns:
            Response data (list of results per asset)
        """
        if not asset_ids:
            return []
        response = self._request(
            "PUT",
            f"/tags/{tag_id}/assets",
            json={"ids": asset_ids},
        )
        return response.json()

    def upsert_tags(
        self, asset_ids: List[str], tag_ids: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Bulk tag multiple assets with multiple tags.

        This endpoint allows tagging many assets with many tags in one call.
        May not exist in all Immich versions.

        Args:
            asset_ids: List of asset UUIDs
            tag_ids: List of tag UUIDs

        Returns:
            Response data, or None if endpoint not available
        """
        if not asset_ids or not tag_ids:
            return None
        response = self._request(
            "PUT",
            "/tags/assets",
            json={"assetIds": asset_ids, "tagIds": tag_ids},
            raise_for_status=False,
        )
        if response.status_code == 404:
            # Endpoint not available in this version
            return None
        if not response.ok:
            raise ImmichAPIError(
                f"Bulk tag failed: {response.status_code} {response.text}",
                status_code=response.status_code,
            )
        return response.json()

    def ensure_platform_tags(
        self,
        platform_colors: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """Ensure platform tags exist with correct colors.

        Creates missing tags and updates colors if they differ.
        Matches behavior of standalone/test_immich_api.py setup_platform_tags().

        Args:
            platform_colors: Dict mapping platform name to hex color.
                Defaults to DEFAULT_PLATFORM_COLORS.

        Returns:
            Dict mapping platform name to tag ID
        """
        if platform_colors is None:
            platform_colors = DEFAULT_PLATFORM_COLORS

        platform_to_tag_id: Dict[str, str] = {}

        for platform, color in platform_colors.items():
            existing = self.find_tag_by_name(platform)

            if existing:
                tag_id = existing["id"]
                existing_color = (existing.get("color") or "").upper()
                if existing_color != color.upper():
                    # Try to update color
                    updated = self.update_tag(tag_id, color)
                    if updated:
                        logger.debug("Updated %s tag color to %s", platform, color)
                    else:
                        # Update not supported - delete and recreate
                        self.delete_tag(tag_id)
                        new_tag = self.create_tag(platform, color)
                        tag_id = new_tag["id"]
                        logger.debug("Recreated %s tag with color %s", platform, color)
                else:
                    logger.debug("%s tag already exists with correct color", platform)
                platform_to_tag_id[platform] = tag_id
            else:
                # Create new tag
                tag = self.create_tag(platform, color)
                platform_to_tag_id[platform] = tag["id"]
                logger.debug("Created %s tag with color %s", platform, color)

        return platform_to_tag_id

    # -------------------------------------------------------------------------
    # Albums
    # -------------------------------------------------------------------------

    def list_albums(self) -> List[Dict[str, Any]]:
        """Get all albums.

        Returns:
            List of album objects with id, albumName, etc.
        """
        response = self._request("GET", "/albums")
        return response.json()

    def find_album_by_name(self, album_name: str) -> Optional[Dict[str, Any]]:
        """Find an album by its exact name.

        Args:
            album_name: Album name to search for (exact match)

        Returns:
            Album object if found (basic info only), None otherwise
        """
        albums = self.list_albums()
        for album in albums:
            if album.get("albumName") == album_name:
                return album
        return None

    def get_album(self, album_id: str) -> Optional[Dict[str, Any]]:
        """Get album details including assets.

        Args:
            album_id: Album UUID

        Returns:
            Album object with assets list, or None if not found
        """
        response = self._request(
            "GET", f"/albums/{album_id}", raise_for_status=False
        )
        if response.status_code == 200:
            return response.json()
        return None

    def get_album_assets(self, album_name: str) -> List[Dict[str, Any]]:
        """Get assets in an album by album name.

        Convenience method that finds album and returns its assets.

        Args:
            album_name: Album name (e.g., "Google/username/photos")

        Returns:
            List of asset objects, empty if album not found
        """
        album = self.find_album_by_name(album_name)
        if not album:
            logger.warning("Album not found: %s", album_name)
            return []

        album_detail = self.get_album(album["id"])
        if not album_detail:
            return []

        return album_detail.get("assets", [])

    # -------------------------------------------------------------------------
    # Asset Search (fallback)
    # -------------------------------------------------------------------------

    def search_assets(
        self, page: int = 1, size: int = 100
    ) -> Dict[str, Any]:
        """Search assets via metadata endpoint.

        Fallback method if album details don't include assets.

        Args:
            page: Page number (1-indexed)
            size: Page size

        Returns:
            Search results with 'assets' or 'items' key
        """
        response = self._request(
            "POST",
            "/search/metadata",
            json={"page": page, "size": size},
        )
        return response.json()

