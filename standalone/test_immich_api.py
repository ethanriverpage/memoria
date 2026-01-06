#!/usr/bin/env python3
"""
Integration tests for Immich API.

Tests API endpoints needed for Memoria's Immich integration, including:
- Connection and authentication
- Albums (list, create, delete)
- Assets (list, search)
- Tags (list, create with colors, assign to assets, delete)

Also provides a command to set up production platform tags for Memoria.

Usage:
    # Run all tests
    python standalone/test_immich_api.py

    # Run specific test groups
    python standalone/test_immich_api.py --test connection
    python standalone/test_immich_api.py --test albums
    python standalone/test_immich_api.py --test tags

    # Create production platform tags (Google, Instagram, etc. with colors)
    python standalone/test_immich_api.py --test setup-platform-tags

    # Use custom credentials
    python standalone/test_immich_api.py --url https://immich.local/api --key YOUR_KEY

    # Verbose output
    python standalone/test_immich_api.py -v

    # Keep test resources (don't clean up tags/albums)
    python standalone/test_immich_api.py --no-cleanup

Environment Variables:
    IMMICH_INSTANCE_URL    Immich server URL
    IMMICH_API_KEY         Immich API key
"""

import argparse
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import requests
except ImportError:
    print("Error: 'requests' library is required. Install with: pip install requests")
    sys.exit(1)

# Load .env file if available
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass  # dotenv is optional


# Test tag prefix to identify test-created resources
TEST_TAG_PREFIX = "memoria-test-"
TEST_ALBUM_PREFIX = "memoria-test-"

# Platform tag definitions with colors
# These are the actual tags Memoria will create for uploaded assets
PLATFORM_TAGS = {
    "Google": "#34A853",
    "Instagram": "#C13584",
    "Snapchat": "#FFFC00",
    "Discord": "#5865F2",
    "iMessage": "#0A84FF",
}


@dataclass
class TestResult:
    """Result of a single test."""

    name: str
    passed: bool
    message: str
    duration_ms: float = 0.0


class ImmichTestClient:
    """Immich API client for testing."""

    def __init__(self, url: str, api_key: str, verbose: bool = False):
        """Initialize client with server URL and API key.

        Args:
            url: Immich server URL (e.g., https://immich.local or https://immich.local/api)
            api_key: Immich API key
            verbose: Enable verbose output
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
        self.verbose = verbose

    def _log(self, message: str) -> None:
        """Log message if verbose mode is enabled."""
        if self.verbose:
            print(f"  [DEBUG] {message}")

    def _request(
        self,
        method: str,
        endpoint: str,
        json: Optional[dict] = None,
        timeout: int = 30,
    ) -> requests.Response:
        """Make an API request.

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, PATCH)
            endpoint: API endpoint (e.g., /albums)
            json: JSON body for POST/PUT/PATCH requests
            timeout: Request timeout in seconds

        Returns:
            Response object
        """
        url = f"{self.url}{endpoint}"
        self._log(f"{method} {url}")
        if json:
            self._log(f"  Body: {json}")

        response = requests.request(
            method,
            url,
            headers=self.headers,
            json=json,
            timeout=timeout,
        )
        self._log(f"  Status: {response.status_code}")
        return response

    # -------------------------------------------------------------------------
    # Connection / Auth
    # -------------------------------------------------------------------------

    def verify_connection(self) -> tuple[bool, Optional[str]]:
        """Verify connection to Immich server.

        Returns:
            Tuple of (success, error_message)
        """
        try:
            response = self._request("GET", "/server/version")
            if response.status_code == 200:
                return True, None
            elif response.status_code == 401:
                return False, "Authentication failed - check your API key"
            else:
                return False, f"Server returned status {response.status_code}"
        except requests.ConnectionError:
            return False, "Could not connect to server - check URL"
        except requests.Timeout:
            return False, "Connection timed out"
        except requests.RequestException as e:
            return False, str(e)

    def get_server_version(self) -> Optional[dict]:
        """Get server version information."""
        response = self._request("GET", "/server/version")
        if response.status_code == 200:
            return response.json()
        return None

    def get_server_config(self) -> Optional[dict]:
        """Get server configuration (public info)."""
        response = self._request("GET", "/server/config")
        if response.status_code == 200:
            return response.json()
        return None

    def get_my_user(self) -> Optional[dict]:
        """Get current user info (validates auth)."""
        response = self._request("GET", "/users/me")
        if response.status_code == 200:
            return response.json()
        return None

    # -------------------------------------------------------------------------
    # Albums
    # -------------------------------------------------------------------------

    def get_albums(self) -> list[dict]:
        """Get all albums."""
        response = self._request("GET", "/albums")
        response.raise_for_status()
        return response.json()

    def create_album(self, name: str, description: str = "") -> dict:
        """Create a new album."""
        response = self._request(
            "POST",
            "/albums",
            json={"albumName": name, "description": description},
        )
        response.raise_for_status()
        return response.json()

    def delete_album(self, album_id: str) -> bool:
        """Delete an album."""
        response = self._request("DELETE", f"/albums/{album_id}")
        return response.status_code in (200, 204)

    def get_album(self, album_id: str) -> Optional[dict]:
        """Get album details including assets."""
        response = self._request("GET", f"/albums/{album_id}")
        if response.status_code == 200:
            return response.json()
        return None

    # -------------------------------------------------------------------------
    # Assets
    # -------------------------------------------------------------------------

    def get_assets(self, page: int = 1, size: int = 100) -> dict:
        """Get assets via search/metadata endpoint."""
        response = self._request(
            "POST",
            "/search/metadata",
            json={"page": page, "size": size},
        )
        response.raise_for_status()
        return response.json()

    def get_asset(self, asset_id: str) -> Optional[dict]:
        """Get a single asset by ID."""
        response = self._request("GET", f"/assets/{asset_id}")
        if response.status_code == 200:
            return response.json()
        return None

    def get_random_assets(self, count: int = 5) -> list[dict]:
        """Get random assets for testing."""
        response = self._request("GET", f"/assets/random?count={count}")
        if response.status_code == 200:
            return response.json()
        return []

    # -------------------------------------------------------------------------
    # Tags
    # -------------------------------------------------------------------------

    def get_tags(self) -> list[dict]:
        """Get all tags."""
        response = self._request("GET", "/tags")
        if response.status_code == 200:
            return response.json()
        response.raise_for_status()
        return []

    def create_tag(self, name: str, color: Optional[str] = None) -> dict:
        """Create a new tag.

        Args:
            name: Tag name (can include / for hierarchy, e.g., "Platform/Discord")
            color: Optional hex color (e.g., "#4285F4")

        Returns:
            Created tag object with id, name, color, etc.
        """
        body: dict = {"name": name}
        if color:
            body["color"] = color
        response = self._request("POST", "/tags", json=body)
        response.raise_for_status()
        return response.json()

    def get_tag(self, tag_id: str) -> Optional[dict]:
        """Get a tag by ID."""
        response = self._request("GET", f"/tags/{tag_id}")
        if response.status_code == 200:
            return response.json()
        return None

    def find_tag_by_name(self, name: str, exact: bool = True) -> Optional[dict]:
        """Find a tag by its name.

        Args:
            name: Tag name to search for
            exact: If True, require exact match; if False, match if name is contained

        Returns:
            Tag object if found, None otherwise
        """
        tags = self.get_tags()
        for tag in tags:
            # Check both 'name' and 'value' fields depending on API version
            tag_name = tag.get("name") or tag.get("value", "")
            if exact:
                if tag_name == name:
                    return tag
            else:
                if name in tag_name:
                    return tag
        return None

    def update_tag(self, tag_id: str, color: Optional[str] = None) -> Optional[dict]:
        """Update a tag's properties.

        Args:
            tag_id: Tag UUID
            color: New hex color (e.g., "#4285F4")

        Returns:
            Updated tag object, or None if update not supported
        """
        body: dict = {}
        if color:
            body["color"] = color

        # Try PUT first (some API versions)
        response = self._request("PUT", f"/tags/{tag_id}", json=body)
        if response.status_code == 404:
            # Try PATCH as fallback
            response = self._request("PATCH", f"/tags/{tag_id}", json=body)

        if response.status_code == 404:
            # Update not supported - return None
            return None

        response.raise_for_status()
        return response.json()

    def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag."""
        response = self._request("DELETE", f"/tags/{tag_id}")
        return response.status_code in (200, 204)

    def tag_assets(self, tag_id: str, asset_ids: list[str]) -> dict:
        """Add assets to a tag (bulk tagging).

        Args:
            tag_id: Tag UUID
            asset_ids: List of asset UUIDs to tag

        Returns:
            Response data
        """
        response = self._request(
            "PUT",
            f"/tags/{tag_id}/assets",
            json={"ids": asset_ids},
        )
        response.raise_for_status()
        return response.json()

    def untag_assets(self, tag_id: str, asset_ids: list[str]) -> dict:
        """Remove assets from a tag.

        Args:
            tag_id: Tag UUID
            asset_ids: List of asset UUIDs to untag

        Returns:
            Response data
        """
        response = self._request(
            "DELETE",
            f"/tags/{tag_id}/assets",
            json={"ids": asset_ids},
        )
        response.raise_for_status()
        return response.json()

    def upsert_tags(self, asset_ids: list[str], tag_ids: list[str]) -> dict:
        """Bulk tag multiple assets with multiple tags.

        This endpoint allows tagging many assets with many tags in one call.

        Args:
            asset_ids: List of asset UUIDs
            tag_ids: List of tag UUIDs

        Returns:
            Response data
        """
        response = self._request(
            "PUT",
            "/tags/assets",
            json={"assetIds": asset_ids, "tagIds": tag_ids},
        )
        # This endpoint might not exist in all versions
        if response.status_code == 404:
            raise NotImplementedError("Bulk tag endpoint not available")
        response.raise_for_status()
        return response.json()


class ImmichAPITester:
    """Test runner for Immich API integration tests."""

    def __init__(self, client: ImmichTestClient, cleanup: bool = True):
        self.client = client
        self.results: list[TestResult] = []
        self._cleanup_items: list[tuple[str, str]] = []  # (type, id)
        self._do_cleanup = cleanup

    def _run_test(self, name: str, test_func) -> TestResult:
        """Run a single test and record the result."""
        start = time.perf_counter()
        try:
            test_func()
            duration = (time.perf_counter() - start) * 1000
            result = TestResult(name, True, "PASSED", duration)
        except AssertionError as e:
            duration = (time.perf_counter() - start) * 1000
            result = TestResult(name, False, f"FAILED: {e}", duration)
        except NotImplementedError as e:
            duration = (time.perf_counter() - start) * 1000
            result = TestResult(name, True, f"SKIPPED: {e}", duration)
        except Exception as e:
            duration = (time.perf_counter() - start) * 1000
            result = TestResult(name, False, f"ERROR: {type(e).__name__}: {e}", duration)

        self.results.append(result)
        return result

    def _schedule_cleanup(self, resource_type: str, resource_id: str) -> None:
        """Schedule a resource for cleanup after tests."""
        self._cleanup_items.append((resource_type, resource_id))

    def cleanup(self) -> None:
        """Clean up test-created resources (if cleanup is enabled)."""
        if not self._do_cleanup:
            if self._cleanup_items:
                print(f"\n[INFO] Skipping cleanup - {len(self._cleanup_items)} resource(s) left in Immich")
                for resource_type, resource_id in self._cleanup_items:
                    print(f"  - {resource_type}: {resource_id}")
            self._cleanup_items.clear()
            return

        for resource_type, resource_id in reversed(self._cleanup_items):
            try:
                if resource_type == "tag":
                    self.client.delete_tag(resource_id)
                elif resource_type == "album":
                    self.client.delete_album(resource_id)
            except Exception:
                pass  # Best effort cleanup
        self._cleanup_items.clear()

    # -------------------------------------------------------------------------
    # Connection Tests
    # -------------------------------------------------------------------------

    def test_connection(self) -> TestResult:
        """Test basic server connectivity."""

        def test():
            connected, error = self.client.verify_connection()
            assert connected, error

        return self._run_test("Connection", test)

    def test_server_version(self) -> TestResult:
        """Test getting server version."""

        def test():
            version = self.client.get_server_version()
            assert version is not None, "Failed to get server version"
            assert "major" in version, f"Unexpected version format: {version}"

        return self._run_test("Server Version", test)

    def test_authentication(self) -> TestResult:
        """Test authentication by getting current user."""

        def test():
            user = self.client.get_my_user()
            assert user is not None, "Failed to get user info (authentication failed?)"
            assert "id" in user, f"Unexpected user format: {user}"
            assert "email" in user, f"User missing email field: {user}"

        return self._run_test("Authentication", test)

    # -------------------------------------------------------------------------
    # Album Tests
    # -------------------------------------------------------------------------

    def test_list_albums(self) -> TestResult:
        """Test listing albums."""

        def test():
            albums = self.client.get_albums()
            assert isinstance(albums, list), f"Expected list, got {type(albums)}"
            # Just verify we can list - may be empty

        return self._run_test("List Albums", test)

    def test_create_delete_album(self) -> TestResult:
        """Test creating and deleting an album."""

        def test():
            # Create
            test_name = f"{TEST_ALBUM_PREFIX}{uuid.uuid4().hex[:8]}"
            album = self.client.create_album(test_name, "Test album for API validation")
            assert "id" in album, f"Album missing id: {album}"
            assert album.get("albumName") == test_name

            album_id = album["id"]
            self._schedule_cleanup("album", album_id)

            # Verify it exists
            fetched = self.client.get_album(album_id)
            assert fetched is not None, "Failed to fetch created album"
            assert fetched.get("albumName") == test_name

            # Delete (only if cleanup enabled - this tests the delete endpoint)
            if self._do_cleanup:
                deleted = self.client.delete_album(album_id)
                assert deleted, "Failed to delete album"

                # Remove from cleanup since we deleted it
                self._cleanup_items = [
                    (t, i) for t, i in self._cleanup_items if i != album_id
                ]

        return self._run_test("Create/Delete Album", test)

    # -------------------------------------------------------------------------
    # Asset Tests
    # -------------------------------------------------------------------------

    def test_search_assets(self) -> TestResult:
        """Test searching assets via metadata endpoint."""

        def test():
            result = self.client.get_assets(page=1, size=10)
            # Response format varies by version
            assert isinstance(result, dict), f"Expected dict, got {type(result)}"
            # Should have either 'assets' key or 'items' key
            has_assets = "assets" in result or "items" in result
            assert has_assets, f"Unexpected response format: {list(result.keys())}"

        return self._run_test("Search Assets", test)

    def test_random_assets(self) -> TestResult:
        """Test getting random assets."""

        def test():
            assets = self.client.get_random_assets(count=3)
            assert isinstance(assets, list), f"Expected list, got {type(assets)}"
            # May be empty if no assets exist

        return self._run_test("Random Assets", test)

    # -------------------------------------------------------------------------
    # Tag Tests
    # -------------------------------------------------------------------------

    def test_list_tags(self) -> TestResult:
        """Test listing tags."""

        def test():
            tags = self.client.get_tags()
            assert isinstance(tags, list), f"Expected list, got {type(tags)}"
            # List may be empty

        return self._run_test("List Tags", test)

    def test_create_delete_tag(self) -> TestResult:
        """Test creating and deleting a tag."""

        def test():
            # Create a simple tag
            tag_name = f"{TEST_TAG_PREFIX}{uuid.uuid4().hex[:8]}"
            tag = self.client.create_tag(tag_name)
            assert "id" in tag, f"Tag missing id: {tag}"

            tag_id = tag["id"]
            self._schedule_cleanup("tag", tag_id)

            # Verify it exists
            fetched = self.client.get_tag(tag_id)
            assert fetched is not None, "Failed to fetch created tag"

            # Delete (only if cleanup enabled)
            if self._do_cleanup:
                deleted = self.client.delete_tag(tag_id)
                assert deleted, "Failed to delete tag"

                # Remove from cleanup
                self._cleanup_items = [
                    (t, i) for t, i in self._cleanup_items if i != tag_id
                ]

        return self._run_test("Create/Delete Tag", test)

    def test_hierarchical_tag(self) -> TestResult:
        """Test creating a hierarchical tag (e.g., Platform/Discord)."""

        def test():
            # Create a hierarchical tag with unique name to avoid conflicts
            tag_name = f"{TEST_TAG_PREFIX}Platform/Test-{uuid.uuid4().hex[:8]}"

            # Try to create tag
            tag = self.client.create_tag(tag_name)
            assert "id" in tag, f"Tag missing id: {tag}"

            tag_id = tag["id"]
            self._schedule_cleanup("tag", tag_id)

            # Verify the tag name is preserved
            fetched = self.client.get_tag(tag_id)
            assert fetched is not None, "Failed to fetch hierarchical tag"
            # Tag value might be the full path or just the leaf depending on version
            tag_value = fetched.get("name") or fetched.get("value", "")
            assert "Test-" in tag_value or "Platform" in tag_value, (
                f"Tag value unexpected: {tag_value}"
            )

            # Delete (only if cleanup enabled)
            if self._do_cleanup:
                self.client.delete_tag(tag_id)
                self._cleanup_items = [
                    (t, i) for t, i in self._cleanup_items if i != tag_id
                ]

        return self._run_test("Hierarchical Tag", test)

    def test_tag_assets(self) -> TestResult:
        """Test tagging assets."""

        def test():
            # Get some random assets to tag
            assets = self.client.get_random_assets(count=2)
            if not assets:
                raise NotImplementedError("No assets available for tagging test")

            asset_ids = [a["id"] for a in assets]

            # Create a tag
            tag_name = f"{TEST_TAG_PREFIX}tagging-test-{uuid.uuid4().hex[:8]}"
            tag = self.client.create_tag(tag_name)
            tag_id = tag["id"]
            self._schedule_cleanup("tag", tag_id)

            # Tag the assets
            result = self.client.tag_assets(tag_id, asset_ids)
            # Result should indicate success for each asset
            assert isinstance(result, list), f"Expected list result, got {type(result)}"

            # Untag and delete (only if cleanup enabled)
            if self._do_cleanup:
                self.client.untag_assets(tag_id, asset_ids)
                self.client.delete_tag(tag_id)
                self._cleanup_items = [
                    (t, i) for t, i in self._cleanup_items if i != tag_id
                ]

        return self._run_test("Tag Assets", test)

    def test_bulk_tag_endpoint(self) -> TestResult:
        """Test bulk tagging endpoint (may not exist in all versions)."""

        def test():
            # Get some random assets
            assets = self.client.get_random_assets(count=2)
            if not assets:
                raise NotImplementedError("No assets available for bulk tag test")

            asset_ids = [a["id"] for a in assets]

            # Create two tags
            tags = []
            for i in range(2):
                tag_name = f"{TEST_TAG_PREFIX}bulk-{i}-{uuid.uuid4().hex[:8]}"
                tag = self.client.create_tag(tag_name)
                tags.append(tag)
                self._schedule_cleanup("tag", tag["id"])

            tag_ids = [t["id"] for t in tags]

            # Try bulk tagging
            try:
                result = self.client.upsert_tags(asset_ids, tag_ids)
                assert result is not None
            except NotImplementedError:
                raise  # Re-raise to mark as skipped

            # Cleanup: untag and delete tags (only if cleanup enabled)
            if self._do_cleanup:
                for tag_id in tag_ids:
                    try:
                        self.client.untag_assets(tag_id, asset_ids)
                        self.client.delete_tag(tag_id)
                    except Exception:
                        pass
                self._cleanup_items = [
                    (t, i) for t, i in self._cleanup_items if i not in tag_ids
                ]

        return self._run_test("Bulk Tag Endpoint", test)

    # -------------------------------------------------------------------------
    # Platform Tagging Simulation
    # -------------------------------------------------------------------------

    def test_tag_with_color(self) -> TestResult:
        """Test creating a tag with a color."""

        def test():
            tag_name = f"{TEST_TAG_PREFIX}colored-tag-{uuid.uuid4().hex[:8]}"
            color = "#FF5733"
            tag = self.client.create_tag(tag_name, color=color)
            assert "id" in tag, f"Tag missing id: {tag}"

            tag_id = tag["id"]
            self._schedule_cleanup("tag", tag_id)

            # Verify the tag has the color
            fetched = self.client.get_tag(tag_id)
            assert fetched is not None, "Failed to fetch tag"
            # Color might be stored with or without #, uppercase or lowercase
            fetched_color = fetched.get("color", "")
            assert fetched_color, f"Tag missing color: {fetched}"

            # Delete (only if cleanup enabled)
            if self._do_cleanup:
                self.client.delete_tag(tag_id)
                self._cleanup_items = [
                    (t, i) for t, i in self._cleanup_items if i != tag_id
                ]

        return self._run_test("Tag with Color", test)

    def test_platform_tagging_workflow(self) -> TestResult:
        """Test the full platform tagging workflow as it would be used by Memoria."""

        def test():
            # Get some random assets to simulate uploaded media
            assets = self.client.get_random_assets(count=3)
            if not assets:
                raise NotImplementedError("No assets available for workflow test")

            asset_ids = [a["id"] for a in assets]

            # Simulate creating platform tags like Memoria would (with unique test prefix)
            run_id = uuid.uuid4().hex[:8]
            test_platforms = {
                f"{TEST_TAG_PREFIX}Google-{run_id}": "#4285F4",
                f"{TEST_TAG_PREFIX}Instagram-{run_id}": "#E1306C",
                f"{TEST_TAG_PREFIX}Discord-{run_id}": "#5865F2",
            }
            created_tags = []

            for platform, color in test_platforms.items():
                tag = self.client.create_tag(platform, color=color)
                created_tags.append(tag)
                self._schedule_cleanup("tag", tag["id"])

            # Simulate tagging assets with their source platform
            # In real usage, each asset would only get one platform tag
            discord_tag = created_tags[2]  # Discord
            result = self.client.tag_assets(discord_tag["id"], [asset_ids[0]])
            assert isinstance(result, list), "Tag assets should return list"

            # Untag and cleanup (only if cleanup enabled)
            if self._do_cleanup:
                for tag in created_tags:
                    try:
                        self.client.untag_assets(tag["id"], asset_ids)
                        self.client.delete_tag(tag["id"])
                    except Exception:
                        pass
                self._cleanup_items = [
                    (t, i)
                    for t, i in self._cleanup_items
                    if i not in [tag["id"] for tag in created_tags]
                ]

        return self._run_test("Platform Tagging Workflow", test)

    # -------------------------------------------------------------------------
    # Platform Tag Setup (Production)
    # -------------------------------------------------------------------------

    def setup_platform_tags(self) -> list[TestResult]:
        """Create or update the actual platform tags for Memoria.

        This is not a test - it creates/updates the production platform tags
        with the correct names and colors.
        """
        results = []

        for platform, color in PLATFORM_TAGS.items():
            start = time.perf_counter()
            try:
                # Check if tag already exists (exact match only)
                existing = self.client.find_tag_by_name(platform, exact=True)
                if existing:
                    # Check if color needs updating
                    existing_color = (existing.get("color") or "").upper()
                    if existing_color != color.upper():
                        # Try to update color
                        updated = self.client.update_tag(existing["id"], color=color)
                        if updated:
                            duration = (time.perf_counter() - start) * 1000
                            results.append(TestResult(
                                f"Platform Tag: {platform}",
                                True,
                                f"Updated color to {color}",
                                duration,
                            ))
                        else:
                            # Update not supported - delete and recreate
                            self.client.delete_tag(existing["id"])
                            self.client.create_tag(platform, color=color)
                            duration = (time.perf_counter() - start) * 1000
                            results.append(TestResult(
                                f"Platform Tag: {platform}",
                                True,
                                f"Recreated with color {color}",
                                duration,
                            ))
                    else:
                        duration = (time.perf_counter() - start) * 1000
                        results.append(TestResult(
                            f"Platform Tag: {platform}",
                            True,
                            "Already exists with correct color",
                            duration,
                        ))
                else:
                    # Create new tag
                    self.client.create_tag(platform, color=color)
                    duration = (time.perf_counter() - start) * 1000
                    results.append(TestResult(
                        f"Platform Tag: {platform}",
                        True,
                        f"Created with color {color}",
                        duration,
                    ))
            except Exception as e:
                duration = (time.perf_counter() - start) * 1000
                results.append(TestResult(
                    f"Platform Tag: {platform}",
                    False,
                    f"ERROR: {e}",
                    duration,
                ))

        self.results.extend(results)
        return results

    # -------------------------------------------------------------------------
    # Test Suites
    # -------------------------------------------------------------------------

    def run_connection_tests(self) -> list[TestResult]:
        """Run connection and auth tests."""
        return [
            self.test_connection(),
            self.test_server_version(),
            self.test_authentication(),
        ]

    def run_album_tests(self) -> list[TestResult]:
        """Run album-related tests."""
        return [
            self.test_list_albums(),
            self.test_create_delete_album(),
        ]

    def run_asset_tests(self) -> list[TestResult]:
        """Run asset-related tests."""
        return [
            self.test_search_assets(),
            self.test_random_assets(),
        ]

    def run_tag_tests(self) -> list[TestResult]:
        """Run tag-related tests."""
        return [
            self.test_list_tags(),
            self.test_create_delete_tag(),
            self.test_hierarchical_tag(),
            self.test_tag_with_color(),
            self.test_tag_assets(),
            self.test_bulk_tag_endpoint(),
            self.test_platform_tagging_workflow(),
        ]

    def run_all_tests(self) -> list[TestResult]:
        """Run all tests."""
        self.run_connection_tests()
        self.run_album_tests()
        self.run_asset_tests()
        self.run_tag_tests()
        return self.results


def print_results(results: list[TestResult]) -> tuple[int, int, int]:
    """Print test results and return (passed, failed, skipped) counts."""
    passed = 0
    failed = 0
    skipped = 0

    print("\n" + "=" * 60)
    print("TEST RESULTS")
    print("=" * 60)

    for result in results:
        if result.passed:
            if "SKIPPED" in result.message:
                status = "SKIP"
                skipped += 1
            else:
                status = "PASS"
                passed += 1
        else:
            status = "FAIL"
            failed += 1

        print(f"[{status}] {result.name} ({result.duration_ms:.1f}ms)")
        if not result.passed or "SKIPPED" in result.message:
            print(f"       {result.message}")

    print()
    print("-" * 60)
    print(f"Total: {len(results)} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
    print("-" * 60)

    return passed, failed, skipped


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Integration tests for Immich API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Test Groups:
    connection          Test server connectivity and authentication
    albums              Test album CRUD operations
    assets              Test asset listing and search
    tags                Test tag CRUD and asset tagging
    all                 Run all tests (default)
    setup-platform-tags Create/update production platform tags with colors

Platform Tags (created by setup-platform-tags):
    Google      #34A853 (green)
    Instagram   #C13584 (purple)
    Snapchat    #FFFC00 (yellow)
    Discord     #5865F2 (blurple)
    iMessage    #0A84FF (blue)

Examples:
    python standalone/test_immich_api.py
    python standalone/test_immich_api.py --test tags
    python standalone/test_immich_api.py --test setup-platform-tags
    python standalone/test_immich_api.py --url http://immich.local:2283 --key YOUR_KEY -v
        """,
    )

    parser.add_argument(
        "--url",
        dest="immich_url",
        default=os.environ.get("IMMICH_INSTANCE_URL", ""),
        help="Immich server URL (default: from IMMICH_INSTANCE_URL env var)",
    )
    parser.add_argument(
        "--key",
        dest="immich_key",
        default=os.environ.get("IMMICH_API_KEY", ""),
        help="Immich API key (default: from IMMICH_API_KEY env var)",
    )
    parser.add_argument(
        "--test",
        choices=["connection", "albums", "assets", "tags", "all", "setup-platform-tags"],
        default="all",
        help="Test group to run (default: all). Use 'setup-platform-tags' to create production tags.",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output (show API calls)",
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="Don't clean up test resources (leave tags/albums in Immich)",
    )

    args = parser.parse_args()

    # Validate credentials
    if not args.immich_url:
        print("Error: Immich URL not provided.")
        print("Set IMMICH_INSTANCE_URL environment variable or use --url")
        sys.exit(1)

    if not args.immich_key:
        print("Error: Immich API key not provided.")
        print("Set IMMICH_API_KEY environment variable or use --key")
        sys.exit(1)

    # Create client
    client = ImmichTestClient(args.immich_url, args.immich_key, verbose=args.verbose)

    print("Immich API Integration Tests")
    print(f"Server: {client.url}")
    print(f"Test group: {args.test}")

    # Quick connection check before running tests
    connected, error = client.verify_connection()
    if not connected:
        print(f"\nError: Cannot connect to Immich server: {error}")
        sys.exit(1)

    print("Connection: OK")

    # Create tester and run tests
    do_cleanup = not args.no_cleanup
    tester = ImmichAPITester(client, cleanup=do_cleanup)

    try:
        if args.test == "connection":
            tester.run_connection_tests()
        elif args.test == "albums":
            tester.run_album_tests()
        elif args.test == "assets":
            tester.run_asset_tests()
        elif args.test == "tags":
            tester.run_tag_tests()
        elif args.test == "setup-platform-tags":
            tester.setup_platform_tags()
        else:
            tester.run_all_tests()
    finally:
        # Always cleanup
        tester.cleanup()

    # Print results
    _passed, failed, _skipped = print_results(tester.results)

    # Exit code based on failures
    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()

