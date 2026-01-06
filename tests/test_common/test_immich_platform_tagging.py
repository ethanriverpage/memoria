#!/usr/bin/env python3
"""
Tests for Immich platform tagging functionality.

Tests cover:
- ImmichAPIClient (with mocked HTTP)
- Platform tag derivation from dedup manifest
- Local file to Immich asset matching
"""

import json
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# ImmichAPIClient Tests
# =============================================================================


class TestImmichAPIClient:
    """Tests for ImmichAPIClient with mocked requests."""

    @pytest.fixture
    def mock_response(self):
        """Create a mock response factory."""

        def _make_response(status_code: int, json_data: Any = None):
            response = MagicMock()
            response.status_code = status_code
            response.ok = 200 <= status_code < 300
            response.json.return_value = json_data
            response.text = json.dumps(json_data) if json_data else ""
            return response

        return _make_response

    @pytest.fixture
    def client(self):
        """Create a client instance."""
        from common.upload.immich_api import ImmichAPIClient

        return ImmichAPIClient("https://immich.local", "test-api-key")

    def test_url_normalization_adds_api(self):
        """URL without /api gets it appended."""
        from common.upload.immich_api import ImmichAPIClient

        client = ImmichAPIClient("https://immich.local", "key")
        assert client.url == "https://immich.local/api"

    def test_url_normalization_preserves_api(self):
        """URL with /api is preserved."""
        from common.upload.immich_api import ImmichAPIClient

        client = ImmichAPIClient("https://immich.local/api", "key")
        assert client.url == "https://immich.local/api"

    def test_url_normalization_strips_trailing_slash(self):
        """Trailing slash is stripped before adding /api."""
        from common.upload.immich_api import ImmichAPIClient

        client = ImmichAPIClient("https://immich.local/", "key")
        assert client.url == "https://immich.local/api"

    @patch("common.upload.immich_api.requests.request")
    def test_list_tags(self, mock_request, client, mock_response):
        """list_tags returns tag list from API."""
        tags = [
            {"id": "tag1", "name": "Google", "color": "#34A853"},
            {"id": "tag2", "name": "Instagram", "color": "#C13584"},
        ]
        mock_request.return_value = mock_response(200, tags)

        result = client.list_tags()

        assert result == tags
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0] == ("GET", "https://immich.local/api/tags")

    @patch("common.upload.immich_api.requests.request")
    def test_create_tag(self, mock_request, client, mock_response):
        """create_tag sends POST and returns created tag."""
        created_tag = {"id": "new-tag", "name": "Discord", "color": "#5865F2"}
        mock_request.return_value = mock_response(201, created_tag)

        result = client.create_tag("Discord", "#5865F2")

        assert result == created_tag
        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0] == ("POST", "https://immich.local/api/tags")
        assert call_args[1]["json"] == {"name": "Discord", "color": "#5865F2"}

    @patch("common.upload.immich_api.requests.request")
    def test_find_tag_by_name_found(self, mock_request, client, mock_response):
        """find_tag_by_name returns tag when found."""
        tags = [
            {"id": "tag1", "name": "Google", "color": "#34A853"},
            {"id": "tag2", "name": "Instagram", "color": "#C13584"},
        ]
        mock_request.return_value = mock_response(200, tags)

        result = client.find_tag_by_name("Instagram")

        assert result is not None
        assert result["name"] == "Instagram"
        assert result["id"] == "tag2"

    @patch("common.upload.immich_api.requests.request")
    def test_find_tag_by_name_not_found(self, mock_request, client, mock_response):
        """find_tag_by_name returns None when not found."""
        tags = [{"id": "tag1", "name": "Google", "color": "#34A853"}]
        mock_request.return_value = mock_response(200, tags)

        result = client.find_tag_by_name("Discord")

        assert result is None

    @patch("common.upload.immich_api.requests.request")
    def test_find_tag_by_name_uses_value_field(self, mock_request, client, mock_response):
        """find_tag_by_name handles API version with 'value' instead of 'name'."""
        tags = [{"id": "tag1", "value": "Google", "color": "#34A853"}]
        mock_request.return_value = mock_response(200, tags)

        result = client.find_tag_by_name("Google")

        assert result is not None
        assert result["value"] == "Google"

    @patch("common.upload.immich_api.requests.request")
    def test_tag_assets(self, mock_request, client, mock_response):
        """tag_assets sends PUT with asset IDs."""
        mock_request.return_value = mock_response(200, [{"success": True}])

        result = client.tag_assets("tag1", ["asset1", "asset2"])

        mock_request.assert_called_once()
        call_args = mock_request.call_args
        assert call_args[0] == ("PUT", "https://immich.local/api/tags/tag1/assets")
        assert call_args[1]["json"] == {"ids": ["asset1", "asset2"]}

    @patch("common.upload.immich_api.requests.request")
    def test_tag_assets_empty_list(self, mock_request, client, mock_response):
        """tag_assets returns empty list for empty asset IDs."""
        result = client.tag_assets("tag1", [])

        assert result == []
        mock_request.assert_not_called()

    @patch("common.upload.immich_api.requests.request")
    def test_get_album_assets(self, mock_request, client, mock_response):
        """get_album_assets fetches album by name and returns assets."""
        albums = [
            {"id": "album1", "albumName": "Google/user/photos"},
            {"id": "album2", "albumName": "Instagram/user/posts"},
        ]
        album_detail = {
            "id": "album1",
            "albumName": "Google/user/photos",
            "assets": [
                {"id": "asset1", "originalFileName": "photo1.jpg"},
                {"id": "asset2", "originalFileName": "photo2.jpg"},
            ],
        }
        mock_request.side_effect = [
            mock_response(200, albums),
            mock_response(200, album_detail),
        ]

        result = client.get_album_assets("Google/user/photos")

        assert len(result) == 2
        assert result[0]["id"] == "asset1"

    @patch("common.upload.immich_api.requests.request")
    def test_get_album_assets_not_found(self, mock_request, client, mock_response):
        """get_album_assets returns empty list when album not found."""
        mock_request.return_value = mock_response(200, [])

        result = client.get_album_assets("NonExistent/album")

        assert result == []


# =============================================================================
# ensure_platform_tags Tests
# =============================================================================


class TestEnsurePlatformTags:
    """Tests for ensure_platform_tags idempotency and update behavior."""

    @pytest.fixture
    def mock_response(self):
        """Create a mock response factory."""

        def _make_response(status_code: int, json_data: Any = None):
            response = MagicMock()
            response.status_code = status_code
            response.ok = 200 <= status_code < 300
            response.json.return_value = json_data
            response.text = json.dumps(json_data) if json_data else ""
            return response

        return _make_response

    @pytest.fixture
    def client(self):
        """Create a client instance."""
        from common.upload.immich_api import ImmichAPIClient

        return ImmichAPIClient("https://immich.local", "test-api-key")

    @patch("common.upload.immich_api.requests.request")
    def test_creates_missing_tags(self, mock_request, client, mock_response):
        """ensure_platform_tags creates tags that don't exist."""
        # First call: list tags (empty)
        # Second call: create Google tag
        mock_request.side_effect = [
            mock_response(200, []),  # list_tags returns empty
            mock_response(201, {"id": "tag1", "name": "Google", "color": "#34A853"}),
        ]

        result = client.ensure_platform_tags({"Google": "#34A853"})

        assert "Google" in result
        assert result["Google"] == "tag1"
        # Should have called list_tags and create_tag
        assert mock_request.call_count == 2
        create_call = mock_request.call_args_list[1]
        assert create_call[0] == ("POST", "https://immich.local/api/tags")
        assert create_call[1]["json"] == {"name": "Google", "color": "#34A853"}

    @patch("common.upload.immich_api.requests.request")
    def test_reuses_existing_tags_with_correct_color(self, mock_request, client, mock_response):
        """ensure_platform_tags reuses existing tags if color matches."""
        existing_tag = {"id": "tag1", "name": "Google", "color": "#34A853"}
        mock_request.return_value = mock_response(200, [existing_tag])

        result = client.ensure_platform_tags({"Google": "#34A853"})

        assert result["Google"] == "tag1"
        # Should only call list_tags (no create or update)
        assert mock_request.call_count == 1

    @patch("common.upload.immich_api.requests.request")
    def test_updates_color_via_put(self, mock_request, client, mock_response):
        """ensure_platform_tags updates color using PUT when tag exists with different color."""
        existing_tag = {"id": "tag1", "name": "Google", "color": "#000000"}
        updated_tag = {"id": "tag1", "name": "Google", "color": "#34A853"}
        mock_request.side_effect = [
            mock_response(200, [existing_tag]),  # list_tags
            mock_response(200, updated_tag),  # PUT update succeeds
        ]

        result = client.ensure_platform_tags({"Google": "#34A853"})

        assert result["Google"] == "tag1"
        # Should call list_tags and PUT update
        assert mock_request.call_count == 2
        update_call = mock_request.call_args_list[1]
        assert update_call[0] == ("PUT", "https://immich.local/api/tags/tag1")
        assert update_call[1]["json"] == {"color": "#34A853"}

    @patch("common.upload.immich_api.requests.request")
    def test_falls_back_to_patch_when_put_returns_404(self, mock_request, client, mock_response):
        """ensure_platform_tags falls back to PATCH when PUT returns 404."""
        existing_tag = {"id": "tag1", "name": "Google", "color": "#000000"}
        updated_tag = {"id": "tag1", "name": "Google", "color": "#34A853"}
        mock_request.side_effect = [
            mock_response(200, [existing_tag]),  # list_tags
            mock_response(404, None),  # PUT returns 404
            mock_response(200, updated_tag),  # PATCH succeeds
        ]

        result = client.ensure_platform_tags({"Google": "#34A853"})

        assert result["Google"] == "tag1"
        # Should call list_tags, PUT (fails), PATCH (succeeds)
        assert mock_request.call_count == 3
        patch_call = mock_request.call_args_list[2]
        assert patch_call[0] == ("PATCH", "https://immich.local/api/tags/tag1")

    @patch("common.upload.immich_api.requests.request")
    def test_deletes_and_recreates_when_update_not_supported(self, mock_request, client, mock_response):
        """ensure_platform_tags deletes and recreates when both PUT and PATCH return 404."""
        existing_tag = {"id": "tag1", "name": "Google", "color": "#000000"}
        new_tag = {"id": "tag2", "name": "Google", "color": "#34A853"}
        mock_request.side_effect = [
            mock_response(200, [existing_tag]),  # list_tags
            mock_response(404, None),  # PUT returns 404
            mock_response(404, None),  # PATCH returns 404
            mock_response(204, None),  # DELETE succeeds
            mock_response(201, new_tag),  # CREATE succeeds
        ]

        result = client.ensure_platform_tags({"Google": "#34A853"})

        assert result["Google"] == "tag2"
        # Should call list_tags, PUT, PATCH, DELETE, CREATE
        assert mock_request.call_count == 5
        delete_call = mock_request.call_args_list[3]
        assert delete_call[0] == ("DELETE", "https://immich.local/api/tags/tag1")
        create_call = mock_request.call_args_list[4]
        assert create_call[0] == ("POST", "https://immich.local/api/tags")

    @patch("common.upload.immich_api.requests.request")
    def test_handles_multiple_platforms(self, mock_request, client, mock_response):
        """ensure_platform_tags handles multiple platforms correctly."""
        mock_request.side_effect = [
            mock_response(200, []),  # list_tags for Google (empty)
            mock_response(201, {"id": "g1", "name": "Google", "color": "#34A853"}),
            mock_response(200, [{"id": "g1", "name": "Google", "color": "#34A853"}]),  # list_tags for Instagram
            mock_response(201, {"id": "i1", "name": "Instagram", "color": "#C13584"}),
        ]

        result = client.ensure_platform_tags({
            "Google": "#34A853",
            "Instagram": "#C13584",
        })

        assert "Google" in result
        assert "Instagram" in result


# =============================================================================
# Platform Tag Derivation Tests
# =============================================================================


class TestPlatformTagDerivation:
    """Tests for platform tag derivation from dedup manifest."""

    def test_platform_from_album_name_google(self):
        """platform_from_album_name extracts Google."""
        from common.upload.platform_tagging import platform_from_album_name

        assert platform_from_album_name("Google/user/photos") == "Google"

    def test_platform_from_album_name_instagram(self):
        """platform_from_album_name extracts Instagram."""
        from common.upload.platform_tagging import platform_from_album_name

        assert platform_from_album_name("Instagram/johndoe/messages") == "Instagram"

    def test_platform_from_album_name_imessage(self):
        """platform_from_album_name extracts iMessage."""
        from common.upload.platform_tagging import platform_from_album_name

        assert platform_from_album_name("iMessage/device") == "iMessage"

    def test_platform_from_album_name_unknown(self):
        """platform_from_album_name returns None for unknown."""
        from common.upload.platform_tagging import platform_from_album_name

        assert platform_from_album_name("Unknown/user") is None

    def test_platform_from_album_name_empty(self):
        """platform_from_album_name returns None for empty."""
        from common.upload.platform_tagging import platform_from_album_name

        assert platform_from_album_name("") is None

    def test_platforms_for_content_hash_no_manifest(self):
        """platforms_for_content_hash returns empty set with no manifest."""
        from common.upload.platform_tagging import platforms_for_content_hash

        result = platforms_for_content_hash("somehash", None)
        assert result == set()

    def test_platforms_for_content_hash_not_found(self):
        """platforms_for_content_hash returns empty set when hash not in manifest."""
        from common.upload.platform_tagging import platforms_for_content_hash

        manifest = {"duplicates": {}}
        result = platforms_for_content_hash("somehash", manifest)
        assert result == set()

    def test_platforms_for_content_hash_single_source(self):
        """platforms_for_content_hash returns single platform for single source."""
        from common.upload.platform_tagging import platforms_for_content_hash

        manifest = {
            "duplicates": {
                "abc123": {
                    "primary": {"processor": "Google Photos"},
                    "secondary": [],
                }
            }
        }
        result = platforms_for_content_hash("abc123", manifest)
        assert result == {"Google"}

    def test_platforms_for_content_hash_multiple_sources(self):
        """platforms_for_content_hash returns all platforms for dedup group."""
        from common.upload.platform_tagging import platforms_for_content_hash

        manifest = {
            "duplicates": {
                "abc123": {
                    "primary": {"processor": "Google Photos"},
                    "secondary": [
                        {"processor": "Instagram Messages"},
                        {"processor": "iMessage"},
                    ],
                }
            }
        }
        result = platforms_for_content_hash("abc123", manifest)
        assert result == {"Google", "Instagram", "iMessage"}

    def test_platforms_for_content_hash_perceptual_group(self):
        """platforms_for_content_hash handles perceptual hash groups."""
        from common.upload.platform_tagging import platforms_for_content_hash

        manifest = {
            "duplicates": {
                "perceptual_xyz": {
                    "best_quality": {"content_hash": "abc123"},
                    "primary": {"processor": "Instagram Public Media", "content_hash": "abc123"},
                    "secondary": [{"processor": "Snapchat Messages"}],
                }
            }
        }
        result = platforms_for_content_hash("abc123", manifest)
        assert result == {"Instagram", "Snapchat"}


# =============================================================================
# Asset Matching Tests
# =============================================================================


class TestAssetMatching:
    """Tests for matching local files to Immich assets."""

    def test_match_by_filename_and_size(self, tmp_path):
        """match_local_files_to_assets matches by filename and size."""
        from common.upload.platform_tagging import match_local_files_to_assets

        # Create a test file
        test_file = tmp_path / "photo.jpg"
        test_file.write_bytes(b"x" * 1024)

        local_files = [test_file]
        immich_assets = [
            {
                "id": "asset123",
                "originalFileName": "photo.jpg",
                "exifInfo": {"fileSizeInBytes": 1024},
            }
        ]

        result = match_local_files_to_assets(local_files, immich_assets)

        assert test_file in result
        assert result[test_file] == "asset123"

    def test_match_empty_files(self, tmp_path):
        """match_local_files_to_assets handles empty file list."""
        from common.upload.platform_tagging import match_local_files_to_assets

        result = match_local_files_to_assets([], [])
        assert result == {}

    def test_match_no_matching_asset(self, tmp_path):
        """match_local_files_to_assets handles unmatched files."""
        from common.upload.platform_tagging import match_local_files_to_assets

        test_file = tmp_path / "photo.jpg"
        test_file.write_bytes(b"x" * 1024)

        local_files = [test_file]
        immich_assets = [
            {
                "id": "asset123",
                "originalFileName": "different.jpg",
                "exifInfo": {"fileSizeInBytes": 2048},
            }
        ]

        result = match_local_files_to_assets(local_files, immich_assets)

        assert test_file not in result

    def test_match_case_insensitive_filename(self, tmp_path):
        """match_local_files_to_assets matches case-insensitively."""
        from common.upload.platform_tagging import match_local_files_to_assets

        test_file = tmp_path / "Photo.JPG"
        test_file.write_bytes(b"x" * 1024)

        local_files = [test_file]
        immich_assets = [
            {
                "id": "asset123",
                "originalFileName": "photo.jpg",
                "exifInfo": {"fileSizeInBytes": 1024},
            }
        ]

        result = match_local_files_to_assets(local_files, immich_assets)

        assert test_file in result
        assert result[test_file] == "asset123"

    def test_match_uses_fallback_size_fields(self, tmp_path):
        """match_local_files_to_assets uses fallback size fields."""
        from common.upload.platform_tagging import match_local_files_to_assets

        test_file = tmp_path / "video.mp4"
        test_file.write_bytes(b"x" * 2048)

        local_files = [test_file]
        # Asset with fileSize instead of exifInfo.fileSizeInBytes
        immich_assets = [
            {
                "id": "asset456",
                "originalFileName": "video.mp4",
                "fileSize": 2048,
            }
        ]

        result = match_local_files_to_assets(local_files, immich_assets)

        assert test_file in result
        assert result[test_file] == "asset456"


# =============================================================================
# Platform to Asset IDs Integration Tests
# =============================================================================


class TestBuildPlatformToAssetIds:
    """Tests for build_platform_to_asset_ids integration."""

    def test_build_platform_to_asset_ids_simple(self, tmp_path):
        """build_platform_to_asset_ids groups assets by platform from album."""
        from common.upload.platform_tagging import build_platform_to_asset_ids

        # Create test files
        (tmp_path / "photo1.jpg").write_bytes(b"a" * 1024)
        (tmp_path / "photo2.jpg").write_bytes(b"b" * 2048)

        immich_assets = [
            {
                "id": "asset1",
                "originalFileName": "photo1.jpg",
                "exifInfo": {"fileSizeInBytes": 1024},
            },
            {
                "id": "asset2",
                "originalFileName": "photo2.jpg",
                "exifInfo": {"fileSizeInBytes": 2048},
            },
        ]

        result = build_platform_to_asset_ids(
            upload_path=tmp_path,
            album_name="Instagram/user/posts",
            immich_assets=immich_assets,
            dedup_manifest=None,
        )

        assert "Instagram" in result
        assert set(result["Instagram"]) == {"asset1", "asset2"}

    def test_build_platform_to_asset_ids_empty_dir(self, tmp_path):
        """build_platform_to_asset_ids returns empty dict for empty directory."""
        from common.upload.platform_tagging import build_platform_to_asset_ids

        result = build_platform_to_asset_ids(
            upload_path=tmp_path,
            album_name="Google/user/photos",
            immich_assets=[],
            dedup_manifest=None,
        )

        assert result == {}


# =============================================================================
# apply_platform_tags Integration Tests
# =============================================================================


class TestApplyPlatformTags:
    """Integration tests for apply_platform_tags workflow."""

    @pytest.fixture
    def mock_response(self):
        """Create a mock response factory."""

        def _make_response(status_code: int, json_data: Any = None):
            response = MagicMock()
            response.status_code = status_code
            response.ok = 200 <= status_code < 300
            response.json.return_value = json_data
            response.text = json.dumps(json_data) if json_data else ""
            return response

        return _make_response

    @patch("common.upload.immich_api.requests.request")
    def test_apply_platform_tags_full_workflow(self, mock_request, tmp_path, mock_response):
        """apply_platform_tags ensures tags, fetches album, matches files, applies tags."""
        from common.upload.immich_api import ImmichAPIClient
        from common.upload.platform_tagging import apply_platform_tags

        # Create test file
        test_file = tmp_path / "photo.jpg"
        test_file.write_bytes(b"x" * 1024)

        # Mock responses
        existing_tags = [{"id": "tag1", "name": "Instagram", "color": "#C13584"}]
        albums = [{"id": "album1", "albumName": "Instagram/user/posts"}]
        album_detail = {
            "id": "album1",
            "albumName": "Instagram/user/posts",
            "assets": [
                {"id": "asset1", "originalFileName": "photo.jpg", "exifInfo": {"fileSizeInBytes": 1024}},
            ],
        }
        tag_result = [{"id": "asset1", "success": True}]

        mock_request.side_effect = [
            mock_response(200, existing_tags),  # list_tags for ensure_platform_tags
            mock_response(200, albums),  # list_albums for get_album_assets
            mock_response(200, album_detail),  # get_album for get_album_assets
            mock_response(200, tag_result),  # tag_assets
        ]

        client = ImmichAPIClient("https://immich.local", "test-key")
        result = apply_platform_tags(
            client=client,
            upload_path=tmp_path,
            album_name="Instagram/user/posts",
            dedup_manifest=None,
            platform_colors={"Instagram": "#C13584"},
        )

        assert "Instagram" in result
        assert result["Instagram"] == 1
        # Verify tag_assets was called
        tag_call = mock_request.call_args_list[-1]
        assert tag_call[0] == ("PUT", "https://immich.local/api/tags/tag1/assets")
        assert tag_call[1]["json"] == {"ids": ["asset1"]}

    @patch("common.upload.immich_api.requests.request")
    def test_apply_platform_tags_with_dedup_manifest_multiple_platforms(
        self, mock_request, tmp_path, mock_response
    ):
        """apply_platform_tags applies multiple platform tags from dedup manifest."""
        from common.upload.immich_api import ImmichAPIClient
        from common.upload.platform_tagging import apply_platform_tags
        from common.dedup.hashing import compute_content_hash

        # Create test file and compute its hash
        test_file = tmp_path / "photo.jpg"
        test_content = b"test content for dedup"
        test_file.write_bytes(test_content)
        content_hash = compute_content_hash(test_file)

        # Create dedup manifest with multiple platforms for this hash
        dedup_manifest = {
            "duplicates": {
                content_hash: {
                    "primary": {"processor": "Google Photos"},
                    "secondary": [
                        {"processor": "Instagram Messages"},
                    ],
                }
            }
        }

        # Mock responses
        existing_tags = [
            {"id": "gtag", "name": "Google", "color": "#34A853"},
            {"id": "itag", "name": "Instagram", "color": "#C13584"},
        ]
        albums = [{"id": "album1", "albumName": "Google/user/photos"}]
        album_detail = {
            "id": "album1",
            "albumName": "Google/user/photos",
            "assets": [
                {
                    "id": "asset1",
                    "originalFileName": "photo.jpg",
                    "exifInfo": {"fileSizeInBytes": len(test_content)},
                },
            ],
        }
        tag_result = [{"id": "asset1", "success": True}]

        mock_request.side_effect = [
            mock_response(200, existing_tags),  # list_tags for Google
            mock_response(200, existing_tags),  # list_tags for Instagram
            mock_response(200, albums),  # list_albums
            mock_response(200, album_detail),  # get_album
            mock_response(200, tag_result),  # tag_assets for Google
            mock_response(200, tag_result),  # tag_assets for Instagram
        ]

        client = ImmichAPIClient("https://immich.local", "test-key")
        result = apply_platform_tags(
            client=client,
            upload_path=tmp_path,
            album_name="Google/user/photos",
            dedup_manifest=dedup_manifest,
            platform_colors={"Google": "#34A853", "Instagram": "#C13584"},
        )

        # Both platforms should be tagged
        assert "Google" in result
        assert "Instagram" in result
        assert result["Google"] == 1
        assert result["Instagram"] == 1

    @patch("common.upload.immich_api.requests.request")
    def test_apply_platform_tags_falls_back_to_album_when_no_manifest(
        self, mock_request, tmp_path, mock_response
    ):
        """apply_platform_tags falls back to album-derived platform when no manifest."""
        from common.upload.immich_api import ImmichAPIClient
        from common.upload.platform_tagging import apply_platform_tags

        # Create test file
        test_file = tmp_path / "video.mp4"
        test_file.write_bytes(b"video" * 100)

        # Mock responses
        existing_tags = [{"id": "stag", "name": "Snapchat", "color": "#FFFC00"}]
        albums = [{"id": "album1", "albumName": "Snapchat/user/memories"}]
        album_detail = {
            "id": "album1",
            "albumName": "Snapchat/user/memories",
            "assets": [
                {
                    "id": "asset1",
                    "originalFileName": "video.mp4",
                    "exifInfo": {"fileSizeInBytes": 500},
                },
            ],
        }
        tag_result = [{"id": "asset1", "success": True}]

        mock_request.side_effect = [
            mock_response(200, existing_tags),  # list_tags
            mock_response(200, albums),  # list_albums
            mock_response(200, album_detail),  # get_album
            mock_response(200, tag_result),  # tag_assets
        ]

        client = ImmichAPIClient("https://immich.local", "test-key")
        result = apply_platform_tags(
            client=client,
            upload_path=tmp_path,
            album_name="Snapchat/user/memories",
            dedup_manifest=None,  # No manifest
            platform_colors={"Snapchat": "#FFFC00"},
        )

        # Should fall back to album-derived platform
        assert "Snapchat" in result
        assert result["Snapchat"] == 1

    @patch("common.upload.immich_api.requests.request")
    def test_apply_platform_tags_no_assets_in_album(
        self, mock_request, tmp_path, mock_response
    ):
        """apply_platform_tags returns empty dict when no assets in album."""
        from common.upload.immich_api import ImmichAPIClient
        from common.upload.platform_tagging import apply_platform_tags

        # Create test file
        (tmp_path / "photo.jpg").write_bytes(b"x" * 1024)

        # Mock responses - album exists but has no assets
        mock_request.side_effect = [
            mock_response(200, [{"id": "tag1", "name": "Google", "color": "#34A853"}]),  # list_tags
            mock_response(200, [{"id": "album1", "albumName": "Google/user/photos"}]),  # list_albums
            mock_response(200, {"id": "album1", "assets": []}),  # get_album - empty
        ]

        client = ImmichAPIClient("https://immich.local", "test-key")
        result = apply_platform_tags(
            client=client,
            upload_path=tmp_path,
            album_name="Google/user/photos",
            dedup_manifest=None,
            platform_colors={"Google": "#34A853"},
        )

        assert result == {}

