#!/usr/bin/env python3
"""Tests for processors/base.py shared utility methods.

Focuses on:
- Parallel EXIF validation and metadata reading
- Re-reading metadata for corrupted files after rebuild
"""

from typing import List, Tuple
from unittest.mock import MagicMock, patch

from processors.base import ProcessorBase


class TestBatchProcessExif:
    """Test batch_process_exif parallel execution and corruption handling."""

    def test_parallel_execution_both_functions_called(self) -> None:
        """Verify both validation and metadata reading are called."""
        file_paths = ["/path/to/file1.jpg", "/path/to/file2.jpg"]
        file_info: List[Tuple] = [
            ("/path/to/file1.jpg", "2024:01:15 14:30:00"),
            ("/path/to/file2.jpg", "2024:01:16 10:00:00"),
        ]

        mock_validate = MagicMock(return_value=set())  # No corrupted files
        mock_read_metadata = MagicMock(return_value={})
        mock_rebuild = MagicMock()
        mock_write = MagicMock()
        mock_request_builder = MagicMock(return_value=[])

        with patch("common.media.exiftool.batch_validate_exif", mock_validate), patch(
            "common.media.exiftool.batch_read_existing_metadata", mock_read_metadata
        ), patch("common.media.exiftool.batch_rebuild_exif", mock_rebuild), patch(
            "common.media.exiftool.batch_write_metadata_requests", mock_write
        ):
            result = ProcessorBase.batch_process_exif(
                file_paths, file_info, mock_request_builder
            )

        # Both functions should be called with the full file list
        mock_validate.assert_called_once_with(file_paths)
        mock_read_metadata.assert_called_once_with(file_paths)
        # No corruption, so rebuild should not be called
        mock_rebuild.assert_not_called()
        # Result should be 0 (no rebuilt files)
        assert result == 0

    def test_corrupted_files_trigger_rebuild_and_reread(self) -> None:
        """Verify corrupted files are rebuilt and their metadata is re-read."""
        file_paths = ["/path/to/good.jpg", "/path/to/corrupted.jpg"]
        file_info: List[Tuple] = [
            ("/path/to/good.jpg", "2024:01:15 14:30:00"),
            ("/path/to/corrupted.jpg", "2024:01:16 10:00:00"),
        ]
        corrupted = {"/path/to/corrupted.jpg"}

        # Initial metadata read returns incomplete data for corrupted file
        initial_metadata = {
            "/path/to/good.jpg": {"DateTimeOriginal": True},
            "/path/to/corrupted.jpg": {},  # Corrupted, no metadata readable
        }
        # After rebuild, metadata is now readable
        rebuilt_metadata = {
            "/path/to/corrupted.jpg": {"DateTimeOriginal": True, "GPSLatitude": True},
        }

        mock_validate = MagicMock(return_value=corrupted)
        mock_read_metadata = MagicMock(side_effect=[initial_metadata, rebuilt_metadata])
        mock_rebuild = MagicMock()
        mock_write = MagicMock()
        mock_request_builder = MagicMock(return_value=[])

        with patch("common.media.exiftool.batch_validate_exif", mock_validate), patch(
            "common.media.exiftool.batch_read_existing_metadata", mock_read_metadata
        ), patch("common.media.exiftool.batch_rebuild_exif", mock_rebuild), patch(
            "common.media.exiftool.batch_write_metadata_requests", mock_write
        ):
            result = ProcessorBase.batch_process_exif(
                file_paths, file_info, mock_request_builder
            )

        # Validation called once
        mock_validate.assert_called_once_with(file_paths)

        # Metadata read called twice: once for all files, once for corrupted
        assert mock_read_metadata.call_count == 2
        mock_read_metadata.assert_any_call(file_paths)
        mock_read_metadata.assert_any_call(list(corrupted))

        # Rebuild called with corrupted files
        mock_rebuild.assert_called_once_with(list(corrupted))

        # Result should be 1 (one rebuilt file)
        assert result == 1

    def test_metadata_map_updated_with_rebuilt_data(self) -> None:
        """Verify the final metadata map contains updated data for rebuilt files."""
        file_paths = ["/path/to/corrupted.jpg"]
        file_info: List[Tuple] = [("/path/to/corrupted.jpg", "2024:01:15 14:30:00")]
        corrupted = {"/path/to/corrupted.jpg"}

        # Initial read returns empty (corruption prevented reading)
        initial_metadata = {"/path/to/corrupted.jpg": {}}
        # After rebuild, metadata is readable
        rebuilt_metadata = {
            "/path/to/corrupted.jpg": {
                "DateTimeOriginal": True,
                "CreateDate": True,
            }
        }

        mock_validate = MagicMock(return_value=corrupted)
        mock_read_metadata = MagicMock(side_effect=[initial_metadata, rebuilt_metadata])
        mock_rebuild = MagicMock()
        mock_write = MagicMock()
        mock_request_builder = MagicMock(return_value=[])

        # Capture the metadata map passed to write
        captured_metadata_map = None

        def capture_write(requests, metadata_map, dedup_manifest=None):
            nonlocal captured_metadata_map
            captured_metadata_map = metadata_map

        mock_write.side_effect = capture_write

        with patch("common.media.exiftool.batch_validate_exif", mock_validate), patch(
            "common.media.exiftool.batch_read_existing_metadata", mock_read_metadata
        ), patch("common.media.exiftool.batch_rebuild_exif", mock_rebuild), patch(
            "common.media.exiftool.batch_write_metadata_requests", mock_write
        ):
            ProcessorBase.batch_process_exif(
                file_paths, file_info, mock_request_builder
            )

        # The metadata map should have the updated data from rebuilt_metadata
        assert captured_metadata_map is not None
        assert (
            captured_metadata_map["/path/to/corrupted.jpg"]["DateTimeOriginal"] is True
        )
        assert captured_metadata_map["/path/to/corrupted.jpg"]["CreateDate"] is True

    def test_empty_file_paths_returns_zero(self) -> None:
        """Verify empty file list returns 0 without calling any functions."""
        mock_validate = MagicMock()
        mock_read_metadata = MagicMock()
        mock_request_builder = MagicMock()

        with patch("common.media.exiftool.batch_validate_exif", mock_validate), patch(
            "common.media.exiftool.batch_read_existing_metadata", mock_read_metadata
        ):
            result = ProcessorBase.batch_process_exif([], [], mock_request_builder)

        assert result == 0
        mock_validate.assert_not_called()
        mock_read_metadata.assert_not_called()

    def test_multiple_corrupted_files_all_rebuilt(self) -> None:
        """Verify multiple corrupted files are all rebuilt and re-read."""
        file_paths = [
            "/path/to/file1.jpg",
            "/path/to/file2.jpg",
            "/path/to/file3.jpg",
        ]
        file_info: List[Tuple] = [
            ("/path/to/file1.jpg", "2024:01:15 14:30:00"),
            ("/path/to/file2.jpg", "2024:01:16 10:00:00"),
            ("/path/to/file3.jpg", "2024:01:17 12:00:00"),
        ]
        # All files are corrupted
        corrupted = {
            "/path/to/file1.jpg",
            "/path/to/file2.jpg",
            "/path/to/file3.jpg",
        }

        mock_validate = MagicMock(return_value=corrupted)
        mock_read_metadata = MagicMock(return_value={})
        mock_rebuild = MagicMock()
        mock_write = MagicMock()
        mock_request_builder = MagicMock(return_value=[])

        with patch("common.media.exiftool.batch_validate_exif", mock_validate), patch(
            "common.media.exiftool.batch_read_existing_metadata", mock_read_metadata
        ), patch("common.media.exiftool.batch_rebuild_exif", mock_rebuild), patch(
            "common.media.exiftool.batch_write_metadata_requests", mock_write
        ):
            result = ProcessorBase.batch_process_exif(
                file_paths, file_info, mock_request_builder
            )

        # All 3 files were rebuilt
        assert result == 3
        mock_rebuild.assert_called_once()
        # Metadata was read twice (initial + re-read for corrupted)
        assert mock_read_metadata.call_count == 2
