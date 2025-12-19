#!/usr/bin/env python3
"""Unit tests for common/failure_tracker.py FailureTracker class."""

import json

import pytest

from common.failure_tracker import FailureTracker


class TestFailureTrackerInit:
    """Tests for FailureTracker initialization."""

    def test_init_sets_processor_name(self):
        """Test that processor name is stored."""
        tracker = FailureTracker("Google Photos", "/path/to/export")
        assert tracker.processor_name == "Google Photos"

    def test_init_sets_export_directory(self):
        """Test that export directory is stored."""
        tracker = FailureTracker("Discord", "/path/to/export")
        assert tracker.export_directory == "/path/to/export"

    def test_init_creates_empty_lists(self):
        """Test that failure lists are initialized empty."""
        tracker = FailureTracker("Test", "/path")
        assert tracker.orphaned_media == []
        assert tracker.orphaned_metadata == []
        assert tracker.processing_failures == []


class TestAddOrphanedMedia:
    """Tests for add_orphaned_media method."""

    @pytest.fixture
    def tracker(self):
        """Create a tracker instance."""
        return FailureTracker("Test Processor", "/exports/test")

    @pytest.fixture
    def temp_file(self, tmp_path):
        """Create a temporary file."""
        test_file = tmp_path / "test_image.jpg"
        test_file.write_bytes(b"fake image content")
        return test_file

    def test_adds_orphaned_media_entry(self, tracker, temp_file):
        """Test that orphaned media entry is added."""
        tracker.add_orphaned_media(temp_file, "No matching metadata")

        assert len(tracker.orphaned_media) == 1
        assert tracker.orphaned_media[0]["file_path"] == str(temp_file)
        assert tracker.orphaned_media[0]["reason"] == "No matching metadata"

    def test_adds_file_size_automatically(self, tracker, temp_file):
        """Test that file size is added to context automatically."""
        tracker.add_orphaned_media(temp_file, "No metadata")

        assert "file_size" in tracker.orphaned_media[0]["context"]
        assert tracker.orphaned_media[0]["context"]["file_size"] > 0

    def test_respects_provided_context(self, tracker, temp_file):
        """Test that provided context is preserved."""
        tracker.add_orphaned_media(
            temp_file,
            "No metadata",
            context={"custom_field": "value"},
        )

        assert tracker.orphaned_media[0]["context"]["custom_field"] == "value"

    def test_handles_nonexistent_file(self, tracker, tmp_path):
        """Test handling of non-existent file paths."""
        nonexistent = tmp_path / "does_not_exist.jpg"
        tracker.add_orphaned_media(nonexistent, "Missing file")

        assert len(tracker.orphaned_media) == 1
        # Should not have file_size since file doesn't exist
        assert "file_size" not in tracker.orphaned_media[0]["context"]


class TestAddOrphanedMetadata:
    """Tests for add_orphaned_metadata method."""

    @pytest.fixture
    def tracker(self):
        """Create a tracker instance."""
        return FailureTracker("Test Processor", "/exports/test")

    def test_adds_orphaned_metadata_entry(self, tracker):
        """Test that orphaned metadata entry is added."""
        metadata = {"title": "Photo.jpg", "timestamp": "2024-01-15"}
        tracker.add_orphaned_metadata(metadata, "File not found")

        assert len(tracker.orphaned_metadata) == 1
        assert tracker.orphaned_metadata[0]["metadata_entry"] == metadata
        assert tracker.orphaned_metadata[0]["reason"] == "File not found"

    def test_preserves_context(self, tracker):
        """Test that context is preserved."""
        metadata = {"title": "Video.mp4"}
        context = {"expected_path": "/path/to/video.mp4"}

        tracker.add_orphaned_metadata(metadata, "Missing file", context=context)

        assert (
            tracker.orphaned_metadata[0]["context"]["expected_path"]
            == "/path/to/video.mp4"
        )


class TestAddProcessingFailure:
    """Tests for add_processing_failure method."""

    @pytest.fixture
    def tracker(self):
        """Create a tracker instance."""
        return FailureTracker("Test Processor", "/exports/test")

    def test_adds_processing_failure(self, tracker, tmp_path):
        """Test that processing failure is added."""
        test_file = tmp_path / "failed.jpg"
        test_file.touch()
        metadata = {"title": "Failed photo"}

        tracker.add_processing_failure(
            test_file,
            metadata,
            "EXIF write failed",
            "ValueError: Invalid date format",
        )

        assert len(tracker.processing_failures) == 1
        assert tracker.processing_failures[0]["file_path"] == str(test_file)
        assert tracker.processing_failures[0]["metadata"] == metadata
        assert tracker.processing_failures[0]["reason"] == "EXIF write failed"
        assert "ValueError" in tracker.processing_failures[0]["error_details"]


class TestHasFailures:
    """Tests for has_failures method."""

    @pytest.fixture
    def tracker(self):
        """Create a tracker instance."""
        return FailureTracker("Test", "/path")

    def test_returns_false_when_no_failures(self, tracker):
        """Test that has_failures returns False with no failures."""
        assert tracker.has_failures() is False

    def test_returns_true_with_orphaned_media(self, tracker, tmp_path):
        """Test that has_failures returns True with orphaned media."""
        test_file = tmp_path / "test.jpg"
        test_file.touch()
        tracker.add_orphaned_media(test_file, "reason")

        assert tracker.has_failures() is True

    def test_returns_true_with_orphaned_metadata(self, tracker):
        """Test that has_failures returns True with orphaned metadata."""
        tracker.add_orphaned_metadata({"title": "test"}, "reason")

        assert tracker.has_failures() is True

    def test_returns_true_with_processing_failures(self, tracker, tmp_path):
        """Test that has_failures returns True with processing failures."""
        test_file = tmp_path / "test.jpg"
        test_file.touch()
        tracker.add_processing_failure(test_file, {}, "reason", "error")

        assert tracker.has_failures() is True


class TestGetSummary:
    """Tests for get_summary method."""

    @pytest.fixture
    def tracker(self):
        """Create a tracker instance."""
        return FailureTracker("Test", "/path")

    def test_empty_tracker_summary(self, tracker):
        """Test summary with no failures."""
        summary = tracker.get_summary()

        assert summary["total_failures"] == 0
        assert summary["failed_matching"] == 0
        assert summary["failed_processing"] == 0

    def test_summary_with_mixed_failures(self, tracker, tmp_path):
        """Test summary with various failure types."""
        # Add orphaned media
        f1 = tmp_path / "f1.jpg"
        f1.touch()
        tracker.add_orphaned_media(f1, "reason")

        # Add orphaned metadata
        tracker.add_orphaned_metadata({"title": "test"}, "reason")
        tracker.add_orphaned_metadata({"title": "test2"}, "reason")

        # Add processing failure
        f2 = tmp_path / "f2.jpg"
        f2.touch()
        tracker.add_processing_failure(f2, {}, "reason", "error")

        summary = tracker.get_summary()

        assert summary["total_failures"] == 4
        assert summary["failed_matching"] == 3  # 1 media + 2 metadata
        assert summary["failed_processing"] == 1


class TestGenerateReport:
    """Tests for generate_report method."""

    def test_report_structure(self, tmp_path):
        """Test that report has correct structure."""
        tracker = FailureTracker("Google Photos", "/exports/google")

        # Add some failures
        test_file = tmp_path / "test.jpg"
        test_file.touch()
        tracker.add_orphaned_media(test_file, "No metadata")
        tracker.add_orphaned_metadata({"title": "Missing"}, "File not found")

        report = tracker.generate_report()

        assert report["processor_name"] == "Google Photos"
        assert report["export_directory"] == "/exports/google"
        assert "timestamp" in report
        assert "summary" in report
        assert "failed_matching" in report
        assert "failed_processing" in report

    def test_report_contains_all_failures(self, tmp_path):
        """Test that report contains all tracked failures."""
        tracker = FailureTracker("Test", "/path")

        # Add various failures
        f1 = tmp_path / "orphan.jpg"
        f1.touch()
        tracker.add_orphaned_media(f1, "reason1")

        tracker.add_orphaned_metadata({"key": "value"}, "reason2")

        f2 = tmp_path / "failed.jpg"
        f2.touch()
        tracker.add_processing_failure(f2, {"meta": "data"}, "reason3", "error")

        report = tracker.generate_report()

        assert len(report["failed_matching"]["orphaned_media"]) == 1
        assert len(report["failed_matching"]["orphaned_metadata"]) == 1
        assert len(report["failed_processing"]) == 1


class TestCopyOrphanedMedia:
    """Tests for copy_orphaned_media method."""

    @pytest.fixture
    def tracker_with_orphans(self, tmp_path):
        """Create a tracker with orphaned media files."""
        export_dir = tmp_path / "export"
        export_dir.mkdir()

        tracker = FailureTracker("Test", str(export_dir))

        # Create orphaned media files
        for i in range(3):
            f = export_dir / f"orphan{i}.jpg"
            f.write_bytes(f"content{i}".encode())
            tracker.add_orphaned_media(f, "No metadata")

        return tracker, export_dir

    def test_creates_destination_directory(self, tracker_with_orphans, tmp_path):
        """Test that destination directory is created."""
        tracker, _ = tracker_with_orphans
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        tracker.copy_orphaned_media(output_dir)

        dest_dir = output_dir / "issues" / "failed-matching" / "media"
        assert dest_dir.exists()

    def test_copies_files(self, tracker_with_orphans, tmp_path):
        """Test that files are copied to destination."""
        tracker, _ = tracker_with_orphans
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        tracker.copy_orphaned_media(output_dir)

        dest_dir = output_dir / "issues" / "failed-matching" / "media"
        copied_files = list(dest_dir.iterdir())
        assert len(copied_files) == 3

    def test_handles_filename_collisions(self, tmp_path):
        """Test that filename collisions are handled."""
        export_dir = tmp_path / "export"
        export_dir.mkdir()

        tracker = FailureTracker("Test", str(export_dir))

        # Create two files with different content but same would-be destination name
        # (simulate by creating source files, then manually creating conflict)
        f1 = export_dir / "photo.jpg"
        f1.write_bytes(b"content1")
        tracker.add_orphaned_media(f1, "reason")

        f2 = export_dir / "subdir"
        f2.mkdir()
        f2 = f2 / "photo.jpg"
        f2.write_bytes(b"content2")
        tracker.add_orphaned_media(f2, "reason")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        tracker.copy_orphaned_media(output_dir)

        dest_dir = output_dir / "issues" / "failed-matching" / "media"
        copied_files = list(dest_dir.iterdir())
        # Both should be copied (one with modified name)
        assert len(copied_files) == 2


class TestSaveOrphanedMetadata:
    """Tests for save_orphaned_metadata method."""

    def test_saves_metadata_as_json(self, tmp_path):
        """Test that metadata is saved as JSON files."""
        tracker = FailureTracker("Test", str(tmp_path))
        tracker.add_orphaned_metadata(
            {"title": "Test Photo", "date": "2024-01-15"},
            "File missing",
        )

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        tracker.save_orphaned_metadata(output_dir)

        dest_dir = output_dir / "issues" / "failed-matching" / "metadata"
        json_files = list(dest_dir.glob("*.json"))
        assert len(json_files) == 1

        # Verify content
        with open(json_files[0]) as f:
            saved = json.load(f)
        assert saved["title"] == "Test Photo"

    def test_uses_title_for_filename(self, tmp_path):
        """Test that title field is used for filename."""
        tracker = FailureTracker("Test", str(tmp_path))
        tracker.add_orphaned_metadata({"title": "VacationPhoto.jpg"}, "reason")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        tracker.save_orphaned_metadata(output_dir)

        dest_dir = output_dir / "issues" / "failed-matching" / "metadata"
        # Should contain sanitized title
        assert any("VacationPhoto" in f.name for f in dest_dir.iterdir())


class TestSaveReport:
    """Tests for save_report method."""

    def test_saves_report_json(self, tmp_path):
        """Test that failure report is saved as JSON."""
        tracker = FailureTracker("Test Processor", str(tmp_path))

        # Add a failure so report is generated
        tracker.add_orphaned_metadata({"key": "value"}, "reason")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        tracker.save_report(output_dir)

        report_path = output_dir / "issues" / "failure-report.json"
        assert report_path.exists()

        with open(report_path) as f:
            report = json.load(f)

        assert report["processor_name"] == "Test Processor"

    def test_no_report_when_no_failures(self, tmp_path):
        """Test that no report is saved when no failures."""
        tracker = FailureTracker("Test", str(tmp_path))

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        tracker.save_report(output_dir)

        # Should not create issues directory
        issues_dir = output_dir / "issues"
        assert not issues_dir.exists()


class TestHandleFailures:
    """Tests for handle_failures convenience method."""

    def test_does_nothing_when_no_failures(self, tmp_path):
        """Test that nothing is done when no failures."""
        tracker = FailureTracker("Test", str(tmp_path))
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        tracker.handle_failures(output_dir)

        # No issues directory should be created
        assert not (output_dir / "issues").exists()

    def test_processes_all_failure_types(self, tmp_path):
        """Test that all failure types are processed."""
        export_dir = tmp_path / "export"
        export_dir.mkdir()

        tracker = FailureTracker("Test", str(export_dir))

        # Add orphaned media
        media_file = export_dir / "orphan.jpg"
        media_file.write_bytes(b"content")
        tracker.add_orphaned_media(media_file, "No metadata")

        # Add orphaned metadata
        tracker.add_orphaned_metadata({"title": "Missing"}, "File not found")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        tracker.handle_failures(output_dir)

        issues_dir = output_dir / "issues"
        assert issues_dir.exists()
        assert (issues_dir / "failure-report.json").exists()
        assert (issues_dir / "failed-matching" / "media").exists()
        assert (issues_dir / "failed-matching" / "metadata").exists()
