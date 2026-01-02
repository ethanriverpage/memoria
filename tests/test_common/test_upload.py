#!/usr/bin/env python3
"""Tests for common/upload modules (immich.py and targets.py).

Tests cover:
- Immich CLI wrapper functions (with mocked subprocess)
- Upload target building from processor OutputInfo
- Environment variable handling
"""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from common.upload.immich import verify_auth, upload, _base_env
from common.upload.targets import build_upload_targets, _ig_public_media_targets
from common.output.paths import OutputInfo


class TestBaseEnv:
    """Tests for _base_env helper function."""

    def test_copies_environ(self) -> None:
        """Should copy existing environment."""
        with patch.dict(os.environ, {"EXISTING_VAR": "value"}, clear=True):
            env = _base_env(None, None)
            assert "EXISTING_VAR" in env
            assert env["EXISTING_VAR"] == "value"

    def test_sets_immich_url(self) -> None:
        """Should set IMMICH_INSTANCE_URL when url provided."""
        env = _base_env("https://immich.example.com", None)
        assert env["IMMICH_INSTANCE_URL"] == "https://immich.example.com"

    def test_sets_immich_key(self) -> None:
        """Should set IMMICH_API_KEY when key provided."""
        env = _base_env(None, "test_api_key_123")
        assert env["IMMICH_API_KEY"] == "test_api_key_123"

    def test_sets_both_when_provided(self) -> None:
        """Should set both URL and key when both provided."""
        env = _base_env("https://immich.example.com", "test_key")
        assert env["IMMICH_INSTANCE_URL"] == "https://immich.example.com"
        assert env["IMMICH_API_KEY"] == "test_key"

    def test_no_modification_when_none(self) -> None:
        """Should not add keys when values are None."""
        with patch.dict(os.environ, {}, clear=True):
            env = _base_env(None, None)
            assert "IMMICH_INSTANCE_URL" not in env
            assert "IMMICH_API_KEY" not in env


class TestVerifyAuth:
    """Tests for verify_auth function."""

    def test_returns_true_on_success(self) -> None:
        """Should return True when immich server-info succeeds."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("common.upload.immich.subprocess.run", return_value=mock_result):
            result = verify_auth("https://immich.example.com", "api_key")

        assert result is True

    def test_returns_false_on_failure(self) -> None:
        """Should return False when immich server-info fails."""
        import subprocess

        with patch(
            "common.upload.immich.subprocess.run",
            side_effect=subprocess.CalledProcessError(1, "immich"),
        ):
            result = verify_auth("https://immich.example.com", "api_key")

        assert result is False

    def test_returns_false_when_immich_not_found(self) -> None:
        """Should return False when immich CLI not installed."""
        with patch(
            "common.upload.immich.subprocess.run",
            side_effect=FileNotFoundError("immich not found"),
        ):
            result = verify_auth("https://immich.example.com", "api_key")

        assert result is False

    def test_calls_server_info_command(self) -> None:
        """Should call 'immich server-info' command."""
        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch(
            "common.upload.immich.subprocess.run", return_value=mock_result
        ) as mock_run:
            verify_auth("https://immich.example.com", "api_key")

        # Check that the command was called correctly
        call_args = mock_run.call_args
        assert call_args[0][0] == ["immich", "server-info"]


class TestUpload:
    """Tests for upload function."""

    def test_raises_on_empty_album(self, tmp_path: Path) -> None:
        """Should raise ValueError when album name is empty."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        with pytest.raises(ValueError, match="Album name must not be empty"):
            upload(str(test_path), "", None, None)

    def test_raises_on_whitespace_album(self, tmp_path: Path) -> None:
        """Should raise ValueError when album name is only whitespace."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        with pytest.raises(ValueError, match="Album name must not be empty"):
            upload(str(test_path), "   ", None, None)

    def test_raises_on_nonexistent_path(self, tmp_path: Path) -> None:
        """Should raise FileNotFoundError when path doesn't exist."""
        nonexistent = tmp_path / "nonexistent"

        with pytest.raises(FileNotFoundError, match="does not exist"):
            upload(str(nonexistent), "Test Album", None, None)

    def test_builds_correct_command(self, tmp_path: Path) -> None:
        """Should build correct immich upload command."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch(
            "common.upload.immich.subprocess.run", return_value=mock_result
        ) as mock_run:
            upload(
                str(test_path),
                "Test Album",
                "https://immich.example.com",
                "api_key",
            )

        call_args = mock_run.call_args
        cmd = call_args[0][0]

        assert cmd[0] == "immich"
        assert cmd[1] == "upload"
        assert "-A" in cmd
        assert "Test Album" in cmd
        assert str(test_path) in cmd

    def test_includes_skip_hash_flag(self, tmp_path: Path) -> None:
        """Should include --skip-hash when skip_hash=True."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch(
            "common.upload.immich.subprocess.run", return_value=mock_result
        ) as mock_run:
            upload(str(test_path), "Album", None, None, skip_hash=True)

        cmd = mock_run.call_args[0][0]
        assert "--skip-hash" in cmd

    def test_excludes_skip_hash_when_false(self, tmp_path: Path) -> None:
        """Should not include --skip-hash when skip_hash=False."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch(
                "common.upload.immich.subprocess.run", return_value=mock_result
            ) as mock_run,
            patch.dict(os.environ, {}, clear=False),
        ):
            # Clear any env override
            os.environ.pop("IMMICH_SKIP_HASH", None)
            upload(str(test_path), "Album", None, None, skip_hash=False)

        cmd = mock_run.call_args[0][0]
        assert "--skip-hash" not in cmd

    def test_includes_recursive_flag(self, tmp_path: Path) -> None:
        """Should include --recursive when recursive=True."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch(
            "common.upload.immich.subprocess.run", return_value=mock_result
        ) as mock_run:
            upload(str(test_path), "Album", None, None, recursive=True)

        cmd = mock_run.call_args[0][0]
        assert "--recursive" in cmd

    def test_includes_concurrency(self, tmp_path: Path) -> None:
        """Should include concurrency setting."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch(
            "common.upload.immich.subprocess.run", return_value=mock_result
        ) as mock_run:
            upload(str(test_path), "Album", None, None, concurrency=8)

        cmd = mock_run.call_args[0][0]
        assert "-c" in cmd
        idx = cmd.index("-c")
        assert cmd[idx + 1] == "8"

    def test_includes_ignore_patterns(self, tmp_path: Path) -> None:
        """Should include ignore patterns."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch(
            "common.upload.immich.subprocess.run", return_value=mock_result
        ) as mock_run:
            upload(
                str(test_path),
                "Album",
                None,
                None,
                ignore_patterns=["issues/**", "*.tmp"],
            )

        cmd = mock_run.call_args[0][0]
        assert "--ignore" in cmd
        assert "issues/**" in cmd
        assert "*.tmp" in cmd

    def test_returns_process_returncode(self, tmp_path: Path) -> None:
        """Should return subprocess return code."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 42

        with patch("common.upload.immich.subprocess.run", return_value=mock_result):
            result = upload(str(test_path), "Album", None, None)

        assert result == 42

    def test_env_override_skip_hash(self, tmp_path: Path) -> None:
        """Should respect IMMICH_SKIP_HASH environment variable."""
        test_path = tmp_path / "test"
        test_path.mkdir()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch(
                "common.upload.immich.subprocess.run", return_value=mock_result
            ) as mock_run,
            patch.dict(os.environ, {"IMMICH_SKIP_HASH": "false"}),
        ):
            upload(str(test_path), "Album", None, None, skip_hash=True)

        cmd = mock_run.call_args[0][0]
        # Env should override the parameter
        assert "--skip-hash" not in cmd


class TestBuildUploadTargets:
    """Tests for build_upload_targets function."""

    def test_with_output_info(self, tmp_path: Path) -> None:
        """Should build targets from OutputInfo."""
        output_info = OutputInfo(
            platform="Google",
            service="photos",
            username_prefix="google",
        )

        targets = build_upload_targets(
            output_info,
            input_dir="google-testuser-20240101",
            output_base=str(tmp_path),
        )

        assert len(targets) == 1
        path, album = targets[0]
        assert path == str(tmp_path)
        assert album == "Google/testuser/photos"

    def test_without_service(self, tmp_path: Path) -> None:
        """Should build album name without service when None."""
        output_info = OutputInfo(
            platform="iMessage",
            service=None,
            username_prefix="imessage",
        )

        targets = build_upload_targets(
            output_info,
            input_dir="imessage-testuser-20240101",
            output_base=str(tmp_path),
        )

        assert len(targets) == 1
        path, album = targets[0]
        assert album == "iMessage/testuser"

    def test_invalid_processor_type_raises_error(self, tmp_path: Path) -> None:
        """Should raise TypeError for invalid processor argument types."""
        with pytest.raises(TypeError, match="processor must be ProcessorBase"):
            build_upload_targets(
                object(),
                input_dir="discord-user-20240101",
                output_base=str(tmp_path),
            )


class TestInstagramPublicMediaTargets:
    """Tests for Instagram public media subfolder handling."""

    def test_creates_targets_for_existing_subfolders(self, tmp_path: Path) -> None:
        """Should create targets for each existing subfolder."""
        # Create some subfolders
        (tmp_path / "posts").mkdir()
        (tmp_path / "stories").mkdir()

        targets = _ig_public_media_targets(tmp_path, "testuser")

        assert len(targets) == 2
        albums = {album for _, album in targets}
        assert "Instagram/testuser/posts" in albums
        assert "Instagram/testuser/stories" in albums

    def test_skips_nonexistent_subfolders(self, tmp_path: Path) -> None:
        """Should skip subfolders that don't exist."""
        # Only create one subfolder
        (tmp_path / "posts").mkdir()

        targets = _ig_public_media_targets(tmp_path, "testuser")

        assert len(targets) == 1
        assert targets[0][1] == "Instagram/testuser/posts"

    def test_skips_files(self, tmp_path: Path) -> None:
        """Should not include files as targets."""
        (tmp_path / "posts").mkdir()
        (tmp_path / "readme.txt").write_text("readme")

        targets = _ig_public_media_targets(tmp_path, "testuser")

        # Should only have the posts directory
        assert len(targets) == 1

    def test_all_expected_subfolders(self, tmp_path: Path) -> None:
        """Should check all expected Instagram subfolder types."""
        expected = ["posts", "archived_posts", "profile", "stories", "reels", "other"]
        for folder in expected:
            (tmp_path / folder).mkdir()

        targets = _ig_public_media_targets(tmp_path, "testuser")

        assert len(targets) == len(expected)
        albums = {album for _, album in targets}
        for folder in expected:
            assert f"Instagram/testuser/{folder}" in albums


class TestInstagramPublicMediaUploadTargets:
    """Tests for Instagram Public Media upload target building."""

    def test_instagram_public_media_uses_subfolders(self, tmp_path: Path) -> None:
        """Instagram Public Media should use per-subfolder albums."""
        from unittest.mock import MagicMock

        # Create some subfolders
        (tmp_path / "posts").mkdir()
        (tmp_path / "stories").mkdir()

        # Mock an Instagram Public Media processor
        mock_processor = MagicMock()
        mock_processor.get_name.return_value = "Instagram Public Media"
        mock_processor.get_output_info.return_value = OutputInfo(
            platform="Instagram",
            service="posts",
            username_prefix="instagram",
        )

        targets = build_upload_targets(
            mock_processor,
            input_dir="instagram-testuser-20240101",
            output_base=str(tmp_path),
        )

        # Should have targets for each subfolder
        assert len(targets) == 2
        albums = {album for _, album in targets}
        assert "Instagram/testuser/posts" in albums
        assert "Instagram/testuser/stories" in albums

    def test_output_info_alone_does_not_trigger_subfolders(
        self, tmp_path: Path
    ) -> None:
        """OutputInfo alone (without processor name) should not trigger subfolder logic."""
        # Create subfolders that would match Instagram Public Media pattern
        (tmp_path / "posts").mkdir()
        (tmp_path / "stories").mkdir()

        # Use OutputInfo directly (no processor name available)
        output_info = OutputInfo(
            platform="Instagram",
            service="posts",
            username_prefix="instagram",
        )

        targets = build_upload_targets(
            output_info,
            input_dir="instagram-testuser-20240101",
            output_base=str(tmp_path),
        )

        # Should get single album (can't distinguish without processor name)
        assert len(targets) == 1
        assert targets[0][1] == "Instagram/testuser/posts"

    def test_instaloader_format_only_produces_posts_subfolder(
        self, tmp_path: Path
    ) -> None:
        """Instaloader-format exports should only create posts/ subfolder."""
        from unittest.mock import MagicMock

        # Only posts subfolder exists (Instaloader format = posts-only)
        (tmp_path / "posts").mkdir()

        mock_processor = MagicMock()
        mock_processor.get_name.return_value = "Instagram Public Media"
        mock_processor.get_output_info.return_value = OutputInfo(
            platform="Instagram",
            service="public-media",
            username_prefix="instagram",
        )

        targets = build_upload_targets(
            mock_processor,
            input_dir="instagram-testuser-20240101",
            output_base=str(tmp_path),
        )

        # Only posts album should exist
        assert len(targets) == 1
        assert targets[0][1] == "Instagram/testuser/posts"


class TestIntegration:
    """Integration tests combining multiple upload functions."""

    def test_processor_to_upload_workflow(self, tmp_path: Path) -> None:
        """Should support full processor -> upload target workflow."""
        # Simulate a processor providing OutputInfo
        output_info = OutputInfo(
            platform="Snapchat",
            service="memories",
            username_prefix="snapchat",
        )

        output_base = tmp_path / "output"
        output_base.mkdir()

        targets = build_upload_targets(
            output_info,
            input_dir="snapchat-myuser-20240101",
            output_base=str(output_base),
        )

        assert len(targets) == 1
        path, album = targets[0]
        assert Path(path) == output_base
        assert album == "Snapchat/myuser/memories"
