#!/usr/bin/env python3
"""
Tests for PreprocessorBase template enforcement, cached detection, and helpers.

Tests cover:
- Template enforcement via __init_subclass__ (abstract methods, _context_merger guard)
- Cached username detection (ensure_detection_result, build_export_info)
- Validation helpers (require_exists, require_any_files, require_any_dirs)
- Stats helpers (bump_stat, summarize_dedup_results)
"""

from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest

from common.detection import DetectionResult
from processors.preprocess_base import (
    HashResult,
    PreprocessorBase,
    SHARED_STATS_KEYS,
)


# =============================================================================
# Test Fixtures
# =============================================================================


class ValidPreprocessor(PreprocessorBase):
    """Valid preprocessor implementation for testing."""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path],
        workers: Optional[int] = None,
    ):
        super().__init__(
            processor_name="ValidTest",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
        )
        self.detect_count = 0

    def validate_export(self) -> bool:
        return True

    def detect_username(self) -> DetectionResult:
        self.detect_count += 1
        return DetectionResult(username="testuser", source="test")

    def process_export(self) -> bool:
        return True

    def print_statistics(self) -> None:
        pass

    def get_content_key(self) -> str:
        return "media_files"

    @staticmethod
    def get_platform() -> str:
        return "TestPlatform"

    @staticmethod
    def get_service() -> str:
        return "test-service"


@pytest.fixture
def preprocessor(tmp_path):
    """Create a valid preprocessor for testing."""
    export_dir = tmp_path / "export"
    output_dir = tmp_path / "output"
    export_dir.mkdir()
    return ValidPreprocessor(export_path=export_dir, output_dir=output_dir)


# =============================================================================
# Template Enforcement Tests
# =============================================================================


class TestTemplateEnforcement:
    """Tests for preprocessor template enforcement via __init_subclass__."""

    def test_all_preprocessors_importable(self):
        """All 10 preprocessors should import without error."""
        from processors.instagram_public_media.preprocess import InstagramPreprocessor
        from processors.instagram_messages.preprocess import (
            InstagramMessagesPreprocessor,
        )
        from processors.instaloader.preprocess import InstaloaderPreprocessor
        from processors.google_chat.preprocess import GoogleChatPreprocessor
        from processors.google_voice.preprocess import GoogleVoicePreprocessor
        from processors.discord.preprocess import DiscordPreprocessor
        from processors.google_photos.preprocess import GooglePhotosPreprocessor
        from processors.imessage.preprocess import IMessagePreprocessor
        from processors.imessage_imazing.preprocess import ImazingPreprocessor
        from processors.snapchat_messages.preprocess import SnapchatMessagesPreprocessor

        # All should be subclasses of PreprocessorBase
        assert issubclass(InstagramPreprocessor, PreprocessorBase)
        assert issubclass(InstagramMessagesPreprocessor, PreprocessorBase)
        assert issubclass(InstaloaderPreprocessor, PreprocessorBase)
        assert issubclass(GoogleChatPreprocessor, PreprocessorBase)
        assert issubclass(GoogleVoicePreprocessor, PreprocessorBase)
        assert issubclass(DiscordPreprocessor, PreprocessorBase)
        assert issubclass(GooglePhotosPreprocessor, PreprocessorBase)
        assert issubclass(IMessagePreprocessor, PreprocessorBase)
        assert issubclass(ImazingPreprocessor, PreprocessorBase)
        assert issubclass(SnapchatMessagesPreprocessor, PreprocessorBase)

    def test_missing_get_content_key_raises_error_at_class_definition(self):
        """Preprocessor missing get_content_key should fail at class definition time."""
        with pytest.raises(TypeError, match="must override get_content_key"):
            # This class lacks get_content_key - fails at class definition
            class MissingContentKey(PreprocessorBase):
                def validate_export(self):
                    return True

                def detect_username(self):
                    return DetectionResult("test", "test")

                def process_export(self):
                    return True

                def print_statistics(self):
                    pass

                @staticmethod
                def get_platform():
                    return "Test"

                @staticmethod
                def get_service():
                    return None

    def test_invalid_content_key_raises_error_at_instantiation(self, tmp_path):
        """Preprocessor with invalid get_content_key() return value should fail."""

        class InvalidContentKey(PreprocessorBase):
            def validate_export(self):
                return True

            def detect_username(self):
                return DetectionResult("test", "test")

            def process_export(self):
                return True

            def print_statistics(self):
                pass

            def get_content_key(self):
                return "invalid_key"  # Not in CONTENT_KEYS

            @staticmethod
            def get_platform():
                return "Test"

            @staticmethod
            def get_service():
                return None

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        with pytest.raises(TypeError, match="must return one of"):
            InvalidContentKey(
                processor_name="Test",
                export_path=export_dir,
                output_dir=output_dir,
            )

    def test_missing_required_hook_raises_at_class_definition(self):
        """Missing required hooks should fail at class definition time."""
        # Try to define a class missing validate_export
        with pytest.raises(TypeError, match="must override validate_export"):

            class MissingValidateExport(PreprocessorBase):
                def detect_username(self):
                    return DetectionResult("test", "test")

                def process_export(self):
                    return True

                def print_statistics(self):
                    pass

                def get_content_key(self):
                    return "media_files"

                @staticmethod
                def get_platform():
                    return "Test"

                @staticmethod
                def get_service():
                    return None

    def test_direct_context_merger_assignment_raises_error(self, preprocessor):
        """Direct assignment to _context_merger should raise TypeError."""
        with pytest.raises(TypeError, match="Direct assignment to _context_merger"):
            preprocessor._context_merger = lambda x, y: None

    def test_set_context_merger_works(self, preprocessor):
        """set_context_merger should work correctly."""

        def merger(existing, new):
            existing.update(new)

        # Should not raise
        preprocessor.set_context_merger(merger)
        # Verify it was set (we can't check directly due to guard, but we can test behavior)
        assert preprocessor._context_merger is not None

    def test_get_content_key_must_be_implemented(self, tmp_path):
        """get_content_key is abstract and must be implemented."""
        # ValidPreprocessor implements it, so this should work
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        p = ValidPreprocessor(export_path=export_dir, output_dir=output_dir)
        assert p.get_content_key() == "media_files"

    def test_valid_content_keys(self, preprocessor):
        """get_content_key should return a valid content key."""
        from common.metadata.schema import CONTENT_KEYS

        key = preprocessor.get_content_key()
        assert key in CONTENT_KEYS


# =============================================================================
# Cached Username Detection Tests
# =============================================================================


class TestCachedUsernameDetection:
    """Tests for cached username detection."""

    def test_detect_username_called_once(self, preprocessor):
        """detect_username should only be called once per lifecycle."""
        # Call ensure_detection_result multiple times
        result1 = preprocessor.ensure_detection_result()
        result2 = preprocessor.ensure_detection_result()
        result3 = preprocessor.ensure_detection_result()

        # detect_username should only have been called once
        assert preprocessor.detect_count == 1
        assert result1 is result2 is result3

    def test_build_export_info_uses_cached_result(self, preprocessor):
        """build_export_info should use cached detection result."""
        # Ensure detection is called
        preprocessor.ensure_detection_result()
        initial_count = preprocessor.detect_count

        # Call build_export_info multiple times
        info1 = preprocessor.build_export_info()
        info2 = preprocessor.build_export_info()

        # detect_username should not have been called again
        assert preprocessor.detect_count == initial_count
        assert info1["export_username"] == "testuser"
        assert info2["export_username"] == "testuser"

    def test_run_caches_detection_result(self, preprocessor):
        """run() should call ensure_detection_result and log."""
        # Mock logger to avoid actual log output
        with patch("processors.preprocess_base.logger"):
            preprocessor.run()

        # Detection should have been called once
        assert preprocessor.detect_count == 1

    def test_directory_username_overrides_content_detection(self, tmp_path):
        """Directory-derived username should be canonical when extractable."""
        export_dir = tmp_path / "testprefix-diruser-20250101"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ValidPreprocessor(export_path=export_dir, output_dir=output_dir)
        result = preprocessor.ensure_detection_result()

        assert result.username == "diruser"
        assert result.source == "directory"
        # detect_username still only called once (cached)
        assert preprocessor.detect_count == 1

    def test_directory_username_username_service_date_style(self, tmp_path):
        """Support username-service-date style for some iMessage-like exports."""
        export_dir = tmp_path / "iphxr-messages-20251121"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ValidPreprocessor(export_path=export_dir, output_dir=output_dir)
        result = preprocessor.ensure_detection_result()

        assert result.username == "iphxr"
        assert result.source == "directory"


# =============================================================================
# Validation Helper Tests
# =============================================================================


class TestValidationHelpers:
    """Tests for validation helper methods."""

    def test_require_exists_returns_true_when_exists(self, preprocessor, tmp_path):
        """Should return True when path exists."""
        existing = tmp_path / "existing"
        existing.mkdir()

        assert preprocessor.require_exists(existing, label="Test") is True

    def test_require_exists_returns_false_when_missing(self, preprocessor, tmp_path):
        """Should return False when path doesn't exist."""
        missing = tmp_path / "missing"

        assert preprocessor.require_exists(missing, label="Test") is False

    def test_require_any_files_returns_true_when_files_exist(
        self, preprocessor, tmp_path
    ):
        """Should return True when matching files exist."""
        dir_path = tmp_path / "test_dir"
        dir_path.mkdir()
        (dir_path / "file1.json").touch()
        (dir_path / "file2.json").touch()

        assert preprocessor.require_any_files(dir_path, "*.json", label="JSON") is True

    def test_require_any_files_returns_false_when_no_files(
        self, preprocessor, tmp_path
    ):
        """Should return False when no matching files exist."""
        dir_path = tmp_path / "empty_dir"
        dir_path.mkdir()

        assert preprocessor.require_any_files(dir_path, "*.json", label="JSON") is False

    def test_require_any_dirs_returns_true_when_subdirs_exist(
        self, preprocessor, tmp_path
    ):
        """Should return True when subdirectories exist."""
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / "child1").mkdir()
        (parent / "child2").mkdir()

        assert preprocessor.require_any_dirs(parent, label="Children") is True

    def test_require_any_dirs_returns_false_when_no_subdirs(
        self, preprocessor, tmp_path
    ):
        """Should return False when no subdirectories exist."""
        empty = tmp_path / "empty"
        empty.mkdir()

        assert preprocessor.require_any_dirs(empty, label="Children") is False

    def test_require_any_dirs_with_filter(self, preprocessor, tmp_path):
        """Should apply filter function to subdirectories."""
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / "good_dir").mkdir()
        (parent / "bad_dir").mkdir()

        # Filter that only matches "good" dirs
        def is_good(path):
            return "good" in path.name

        assert (
            preprocessor.require_any_dirs(parent, label="Good", filter_fn=is_good)
            is True
        )

        # Filter that matches nothing
        def is_nothing(path):
            return False

        assert (
            preprocessor.require_any_dirs(parent, label="Nothing", filter_fn=is_nothing)
            is False
        )


# =============================================================================
# Stats Helper Tests
# =============================================================================


class TestStatsHelpers:
    """Tests for stats helper methods."""

    def test_shared_stats_keys_initialized(self, preprocessor):
        """Shared stats keys should be initialized to 0."""
        for key in SHARED_STATS_KEYS:
            assert key in preprocessor.stats
            assert preprocessor.stats[key] == 0

    def test_bump_stat_increments(self, preprocessor):
        """bump_stat should increment stat values."""
        preprocessor.bump_stat("files_total", 5)
        assert preprocessor.stats["files_total"] == 5

        preprocessor.bump_stat("files_total", 3)
        assert preprocessor.stats["files_total"] == 8

    def test_bump_stat_creates_new_key(self, preprocessor):
        """bump_stat should create new keys if they don't exist."""
        preprocessor.bump_stat("custom_stat", 10)
        assert preprocessor.stats["custom_stat"] == 10

    def test_summarize_dedup_results(self, preprocessor, tmp_path):
        """summarize_dedup_results should correctly count unique and duplicates."""
        # Create mock results
        results = {
            Path("/path/file1.jpg"): HashResult(
                source_path=Path("/path/file1.jpg"),
                dest_filename="file1.jpg",
                content_hash="hash1",
                is_duplicate=False,
                primary_source=Path("/path/file1.jpg"),
            ),
            Path("/path/file2.jpg"): HashResult(
                source_path=Path("/path/file2.jpg"),
                dest_filename="file2.jpg",
                content_hash="hash2",
                is_duplicate=False,
                primary_source=Path("/path/file2.jpg"),
            ),
            Path("/path/file3.jpg"): HashResult(
                source_path=Path("/path/file3.jpg"),
                dest_filename="file1.jpg",
                content_hash="hash1",
                is_duplicate=True,
                primary_source=Path("/path/file1.jpg"),
            ),
        }

        unique, duplicates = preprocessor.summarize_dedup_results(results)

        assert unique == 2
        assert duplicates == 1
        assert preprocessor.stats["files_unique"] == 2
        assert preprocessor.stats["files_duplicates"] == 1


# =============================================================================
# Save Metadata Tests
# =============================================================================


class TestSaveMetadata:
    """Tests for save_metadata wrapper."""

    def test_save_metadata_creates_file(self, preprocessor, tmp_path):
        """save_metadata should create metadata.json file."""
        # Ensure detection is cached first
        preprocessor.ensure_detection_result()

        content_data = [{"filename": "test.jpg", "source_path": "/path/test.jpg"}]
        preprocessor.save_metadata(content_data)

        assert preprocessor.metadata_file.exists()

    def test_save_metadata_includes_extra_export_info(self, preprocessor):
        """save_metadata should include extra_export_info in export_info."""
        preprocessor.ensure_detection_result()

        content_data = []
        extra = {"custom_field": "custom_value"}
        preprocessor.save_metadata(content_data, extra_export_info=extra)

        import json

        with open(preprocessor.metadata_file) as f:
            metadata = json.load(f)

        assert metadata["export_info"]["custom_field"] == "custom_value"


# =============================================================================
# Production Preprocessor Stats Tests
# =============================================================================


class TestProductionPreprocessorStats:
    """Tests that production preprocessors preserve shared stats keys."""

    @pytest.fixture
    def preprocessor_classes(self):
        """Import all 10 production preprocessor classes."""
        from processors.instagram_public_media.preprocess import InstagramPreprocessor
        from processors.instagram_messages.preprocess import (
            InstagramMessagesPreprocessor,
        )
        from processors.instaloader.preprocess import InstaloaderPreprocessor
        from processors.google_chat.preprocess import GoogleChatPreprocessor
        from processors.google_voice.preprocess import GoogleVoicePreprocessor
        from processors.discord.preprocess import DiscordPreprocessor
        from processors.google_photos.preprocess import GooglePhotosPreprocessor
        from processors.imessage.preprocess import IMessagePreprocessor
        from processors.imessage_imazing.preprocess import ImazingPreprocessor
        from processors.snapchat_messages.preprocess import SnapchatMessagesPreprocessor

        return {
            "instagram_public_media": InstagramPreprocessor,
            "instagram_messages": InstagramMessagesPreprocessor,
            "instaloader": InstaloaderPreprocessor,
            "google_chat": GoogleChatPreprocessor,
            "google_voice": GoogleVoicePreprocessor,
            "discord": DiscordPreprocessor,
            "google_photos": GooglePhotosPreprocessor,
            "imessage": IMessagePreprocessor,
            "imessage_imazing": ImazingPreprocessor,
            "snapchat_messages": SnapchatMessagesPreprocessor,
        }

    def test_all_preprocessors_preserve_shared_stats_keys(
        self, preprocessor_classes, tmp_path
    ):
        """All production preprocessors should have shared stats keys after init."""
        for name, cls in preprocessor_classes.items():
            export_dir = tmp_path / f"export_{name}"
            output_dir = tmp_path / f"output_{name}"
            export_dir.mkdir()

            # Instantiate with minimal args - some need special handling
            if name == "imessage":
                # iMessage needs export_paths list
                instance = cls(
                    export_paths=[export_dir],
                    output_dir=output_dir,
                )
            else:
                instance = cls(
                    export_path=export_dir,
                    output_dir=output_dir,
                )

            # Check all shared stats keys exist
            for key in SHARED_STATS_KEYS:
                assert key in instance.stats, (
                    f"{name} preprocessor missing shared stats key: {key}"
                )
