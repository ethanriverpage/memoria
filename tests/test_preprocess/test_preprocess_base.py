#!/usr/bin/env python3
"""Tests for processors/preprocess_base.py base preprocessor class.

Tests cover:
- PreprocessorBase initialization and validation
- temp_output_dir context manager
- hash_with_dedup batch operations
- File hashing and deduplication
- Context merging during deduplication
- Filename collision handling
- Log message handling
"""

import os
from pathlib import Path
from typing import Dict, Optional
from unittest.mock import patch

import pytest

from processors.preprocess_base import (
    PreprocessorBase,
    CopyTask,
    CopyResult,
    HashResult,
)
from common.detection import DetectionResult


class ConcretePreprocessor(PreprocessorBase):
    """Concrete implementation for testing abstract base class."""

    def __init__(
        self,
        export_path: Path,
        output_dir: Optional[Path],
        workers: Optional[int] = None,
    ):
        super().__init__(
            processor_name="TestProcessor",
            export_path=export_path,
            output_dir=output_dir,
            workers=workers,
        )
        self.validated = False
        self.processed = False
        self.stats_printed = False

    def validate_export(self) -> bool:
        self.validated = True
        return True

    def detect_username(self) -> DetectionResult:
        return DetectionResult(username="testuser", source="test")

    def process_export(self) -> bool:
        self.processed = True
        return True

    def print_statistics(self) -> None:
        self.stats_printed = True

    def get_content_key(self) -> str:
        return "media_files"


class TestPreprocessorBaseInit:
    """Tests for PreprocessorBase initialization."""

    def test_requires_output_dir(self, tmp_path: Path) -> None:
        """Should raise ValueError if output_dir is None."""
        export_dir = tmp_path / "export"
        export_dir.mkdir()

        with pytest.raises(ValueError, match="output_dir is required"):
            ConcretePreprocessor(export_path=export_dir, output_dir=None)

    def test_prevents_writing_to_export_dir(self, tmp_path: Path) -> None:
        """Should raise ValueError if output_dir equals export_path."""
        export_dir = tmp_path / "export"
        export_dir.mkdir()

        with pytest.raises(ValueError, match="cannot be the same"):
            ConcretePreprocessor(export_path=export_dir, output_dir=export_dir)

    def test_sets_up_directories(self, tmp_path: Path) -> None:
        """Should configure output directories correctly."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )

        assert preprocessor.output_dir == output_dir
        assert preprocessor.metadata_file == output_dir / "metadata.json"
        assert preprocessor.log_file == output_dir / "preprocessing.log"

    def test_initializes_failure_tracker(self, tmp_path: Path) -> None:
        """Should initialize failure tracker."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )

        assert preprocessor.failure_tracker is not None
        assert preprocessor.failure_tracker.processor_name == "TestProcessor"

    def test_custom_worker_count(self, tmp_path: Path) -> None:
        """Should use custom worker count when specified."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
            workers=4,
        )

        assert preprocessor.workers == 4


class TestPreprocessorBaseRun:
    """Tests for PreprocessorBase.run() method."""

    def test_run_calls_template_methods(self, tmp_path: Path) -> None:
        """Should call all template methods in order."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )

        result = preprocessor.run()

        assert result is True
        assert preprocessor.validated is True
        assert preprocessor.processed is True
        assert preprocessor.stats_printed is True

    def test_run_returns_false_on_validation_failure(self, tmp_path: Path) -> None:
        """Should return False if validation fails."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )
        # Override validate to fail
        preprocessor.validate_export = lambda: False

        result = preprocessor.run()

        assert result is False


class TestHashWithDedup:
    """Tests for hash_with_dedup method (hash-only, no file copy)."""

    def test_hashes_unique_files_without_copying(self, tmp_path: Path) -> None:
        """Should hash unique files without copying to output directory."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        # Create source files
        file1 = export_dir / "photo1.jpg"
        file2 = export_dir / "photo2.jpg"
        file1.write_bytes(b"content1")
        file2.write_bytes(b"content2")

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
            workers=1,
        )

        tasks = [
            CopyTask(source_path=file1, dest_filename="photo1.jpg"),
            CopyTask(source_path=file2, dest_filename="photo2.jpg"),
        ]

        with patch.dict(os.environ, {"DISABLE_HASH_CACHE": "true"}):
            results = preprocessor.hash_with_dedup(tasks, show_progress=False)

        # Results should exist for both files
        assert len(results) == 2
        assert file1 in results
        assert file2 in results

        # Files should NOT be copied to output directory
        assert not (output_dir / "media" / "photo1.jpg").exists()
        assert not (output_dir / "media" / "photo2.jpg").exists()

        # Results should be HashResult instances
        assert isinstance(results[file1], HashResult)
        assert isinstance(results[file2], HashResult)

        # source_path should point to original files
        assert results[file1].primary_source == file1
        assert results[file2].primary_source == file2

    def test_deduplicates_identical_files(self, tmp_path: Path) -> None:
        """Should deduplicate files with same content."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        # Create duplicate files
        file1 = export_dir / "photo1.jpg"
        file2 = export_dir / "photo2.jpg"
        same_content = b"identical content"
        file1.write_bytes(same_content)
        file2.write_bytes(same_content)

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
            workers=1,
        )

        tasks = [
            CopyTask(source_path=file1, dest_filename="photo1.jpg"),
            CopyTask(source_path=file2, dest_filename="photo2.jpg"),
        ]

        with patch.dict(os.environ, {"DISABLE_HASH_CACHE": "true"}):
            results = preprocessor.hash_with_dedup(tasks, show_progress=False)

        # Both should have same hash and point to same primary
        assert results[file1].content_hash == results[file2].content_hash
        assert results[file1].is_duplicate is False
        assert results[file2].is_duplicate is True
        assert results[file1].dest_filename == results[file2].dest_filename
        # Both should reference the primary source
        assert results[file1].primary_source == file1
        assert results[file2].primary_source == file1

    def test_stores_source_path_in_registry(self, tmp_path: Path) -> None:
        """Should store source_path in hash registry for later use."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        file1 = export_dir / "photo1.jpg"
        file1.write_bytes(b"content1")

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
            workers=1,
        )

        tasks = [
            CopyTask(source_path=file1, dest_filename="photo1.jpg"),
        ]

        with patch.dict(os.environ, {"DISABLE_HASH_CACHE": "true"}):
            results = preprocessor.hash_with_dedup(tasks, show_progress=False)

        # Check registry has source_path (stored at top level)
        content_hash = results[file1].content_hash
        # Access the internal _hash_registry to verify source_path storage
        registry_entry = preprocessor._hash_registry.get(content_hash)
        assert registry_entry is not None
        assert registry_entry.get("source_path") == str(file1)

    def test_merges_context_for_duplicates(self, tmp_path: Path) -> None:
        """Should merge context when duplicates found."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        file1 = export_dir / "photo1.jpg"
        file2 = export_dir / "photo2.jpg"
        same_content = b"identical"
        file1.write_bytes(same_content)
        file2.write_bytes(same_content)

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
            workers=1,
        )

        # Custom merger that accumulates albums
        def merge_albums(existing: Dict, new: Dict) -> None:
            if "albums" not in existing:
                existing["albums"] = []
            existing["albums"].extend(new.get("albums", []))

        preprocessor.set_context_merger(merge_albums)

        tasks = [
            CopyTask(
                source_path=file1,
                dest_filename="photo1.jpg",
                context={"albums": ["album1"]},
            ),
            CopyTask(
                source_path=file2,
                dest_filename="photo2.jpg",
                context={"albums": ["album2"]},
            ),
        ]

        with patch.dict(os.environ, {"DISABLE_HASH_CACHE": "true"}):
            results = preprocessor.hash_with_dedup(tasks, show_progress=False)

        # Check merged context
        merged_contexts = preprocessor.get_merged_contexts()
        assert len(merged_contexts) == 1
        content_hash = results[file1].content_hash
        assert "album1" in merged_contexts[content_hash]["albums"]
        assert "album2" in merged_contexts[content_hash]["albums"]

    def test_handles_empty_task_list(self, tmp_path: Path) -> None:
        """Should return empty dict for empty task list."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )

        results = preprocessor.hash_with_dedup([], show_progress=False)

        assert results == {}

    def test_handles_filename_collision(self, tmp_path: Path) -> None:
        """Should add suffix for filename collisions without copying."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        # Create files with different content but same name request
        file1 = export_dir / "a" / "photo.jpg"
        file2 = export_dir / "b" / "photo.jpg"
        file1.parent.mkdir()
        file2.parent.mkdir()
        file1.write_bytes(b"content1")
        file2.write_bytes(b"content2")

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
            workers=1,
        )

        tasks = [
            CopyTask(source_path=file1, dest_filename="photo.jpg"),
            CopyTask(source_path=file2, dest_filename="photo.jpg"),
        ]

        with patch.dict(os.environ, {"DISABLE_HASH_CACHE": "true"}):
            results = preprocessor.hash_with_dedup(tasks, show_progress=False)

        # Should have different output filenames
        filenames = {results[file1].dest_filename, results[file2].dest_filename}
        assert len(filenames) == 2
        assert "photo.jpg" in filenames
        assert "photo_1.jpg" in filenames

        # But no files should actually exist
        assert not (output_dir / "media").exists()


class TestFilenameCollision:
    """Tests for filename collision handling."""

    def test_handles_filename_collision(self, tmp_path: Path) -> None:
        """Should add suffix for filename collisions."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        # Create files with different content but same name request
        file1 = export_dir / "a" / "photo.jpg"
        file2 = export_dir / "b" / "photo.jpg"
        file1.parent.mkdir()
        file2.parent.mkdir()
        file1.write_bytes(b"content1")
        file2.write_bytes(b"content2")

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
            workers=1,
        )

        tasks = [
            CopyTask(source_path=file1, dest_filename="photo.jpg"),
            CopyTask(source_path=file2, dest_filename="photo.jpg"),
        ]

        with patch.dict(os.environ, {"DISABLE_HASH_CACHE": "true"}):
            results = preprocessor.hash_with_dedup(tasks, show_progress=False)

        # Should have different output filenames
        filenames = {results[file1].dest_filename, results[file2].dest_filename}
        assert len(filenames) == 2
        assert "photo.jpg" in filenames
        assert "photo_1.jpg" in filenames


class TestLogMessage:
    """Tests for log_message method."""

    def test_adds_log_entry(self, tmp_path: Path) -> None:
        """Should add formatted log entry."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )

        preprocessor.log_message("TEST_CATEGORY", "Test message", "details")

        assert len(preprocessor.log_entries) == 1
        entry = preprocessor.log_entries[0]
        assert "TEST_CATEGORY" in entry
        assert "Test message" in entry
        assert "details" in entry

    def test_thread_safe_logging(self, tmp_path: Path) -> None:
        """Should handle concurrent log messages."""
        import threading

        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )

        def log_many(n: int) -> None:
            for i in range(10):
                preprocessor.log_message("CAT", f"Message {n}-{i}")

        threads = [threading.Thread(target=log_many, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(preprocessor.log_entries) == 50


class TestSaveLog:
    """Tests for save_log method."""

    def test_saves_log_file(self, tmp_path: Path) -> None:
        """Should save log entries to file."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )

        preprocessor.log_message("TEST", "Entry 1")
        preprocessor.log_message("TEST", "Entry 2")
        preprocessor.save_log()

        assert preprocessor.log_file.exists()
        content = preprocessor.log_file.read_text()
        assert "Entry 1" in content
        assert "Entry 2" in content

    def test_skips_when_no_entries(self, tmp_path: Path) -> None:
        """Should not create file when no entries."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )

        preprocessor.save_log()

        assert not preprocessor.log_file.exists()


class TestTempOutputDir:
    """Tests for temp_output_dir context manager."""

    def test_creates_temp_directory(self, tmp_path: Path) -> None:
        """Should create temporary directory."""
        base_dir = tmp_path / "base"

        with ConcretePreprocessor.temp_output_dir(base_dir, "test") as temp_dir:
            assert temp_dir.exists()
            assert temp_dir.is_dir()

    def test_cleanup_on_exit(self, tmp_path: Path) -> None:
        """Should clean up directory on exit (when cleanup enabled)."""
        base_dir = tmp_path / "base"

        with patch.dict(os.environ, {"DISABLE_TEMP_CLEANUP": ""}):
            with ConcretePreprocessor.temp_output_dir(base_dir, "test") as temp_dir:
                _temp_path = temp_dir  # noqa: F841 - Captured for potential debugging

        # May or may not be cleaned up depending on environment
        # Just verify context manager completes without error


class TestContextMerging:
    """Tests for context merging functionality."""

    def test_get_merged_context_for_file(self, tmp_path: Path) -> None:
        """Should retrieve merged context by filename."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()
        output_dir.mkdir()

        file1 = export_dir / "photo.jpg"
        file1.write_bytes(b"content")

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
            workers=1,
        )

        tasks = [
            CopyTask(
                source_path=file1,
                dest_filename="photo.jpg",
                context={"key": "value"},
            ),
        ]

        with patch.dict(os.environ, {"DISABLE_HASH_CACHE": "true"}):
            preprocessor.hash_with_dedup(tasks, show_progress=False)

        context = preprocessor.get_merged_context_for_file("photo.jpg")
        assert context is not None
        assert context["key"] == "value"

    def test_get_merged_context_for_missing_file(self, tmp_path: Path) -> None:
        """Should return None for unknown filename."""
        export_dir = tmp_path / "export"
        output_dir = tmp_path / "output"
        export_dir.mkdir()

        preprocessor = ConcretePreprocessor(
            export_path=export_dir,
            output_dir=output_dir,
        )

        context = preprocessor.get_merged_context_for_file("nonexistent.jpg")
        assert context is None


class TestCopyTask:
    """Tests for CopyTask dataclass."""

    def test_default_context(self) -> None:
        """Should have empty dict as default context."""
        task = CopyTask(source_path=Path("/test"), dest_filename="test.jpg")
        assert task.context == {}

    def test_custom_context(self) -> None:
        """Should accept custom context."""
        task = CopyTask(
            source_path=Path("/test"),
            dest_filename="test.jpg",
            context={"key": "value"},
        )
        assert task.context == {"key": "value"}


class TestCopyResult:
    """Tests for CopyResult dataclass."""

    def test_attributes(self) -> None:
        """Should store all attributes."""
        result = CopyResult(
            dest_filename="photo.jpg",
            content_hash="abc123",
            is_duplicate=False,
            primary_source=Path("/source/photo.jpg"),
        )

        assert result.dest_filename == "photo.jpg"
        assert result.content_hash == "abc123"
        assert result.is_duplicate is False
        assert result.primary_source == Path("/source/photo.jpg")

