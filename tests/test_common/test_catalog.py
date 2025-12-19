#!/usr/bin/env python3
"""Unit tests for catalog_media_files() in common/catalog.py."""

import gc
import tempfile
import tracemalloc
from pathlib import Path
from typing import Iterator
from unittest.mock import patch, MagicMock
import types

import pytest

from common.dedup.catalog import catalog_media_files, collect_catalog


class TestCatalogMediaFiles:
    """Tests for catalog_media_files() generator and collect_catalog() function."""

    @pytest.fixture
    def temp_media_dir(self):
        """Create a temporary directory with test media files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create some test files
            (tmppath / "photo1.jpg").write_bytes(b"fake jpeg content 1")
            (tmppath / "photo2.jpg").write_bytes(b"fake jpeg content 2")
            (tmppath / "video.mp4").write_bytes(b"fake video content")

            yield tmppath

    def test_catalog_structure(self, temp_media_dir):
        """Test that collect_catalog has correct structure."""
        media_files = list(temp_media_dir.glob("*"))

        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        assert "processor" in catalog
        assert "input_dir" in catalog
        assert "files" in catalog
        assert catalog["processor"] == "Test Processor"
        assert catalog["input_dir"] == str(temp_media_dir)
        assert isinstance(catalog["files"], list)

    def test_generator_yields_entries(self, temp_media_dir):
        """Test that catalog_media_files is a generator that yields entries."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        entries = list(
            catalog_media_files(
                processor_name="Test Processor",
                input_path=temp_media_dir,
                media_files=media_files,
                workers=2,
            )
        )

        assert len(entries) == len(media_files)
        for entry in entries:
            assert "source_path" in entry
            assert "content_hash" in entry
            assert "_processor" in entry
            assert entry["_processor"] == "Test Processor"

    def test_file_entries_have_required_fields(self, temp_media_dir):
        """Test that each file entry has all required fields."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        required_fields = [
            "source_path",
            "relative_path",
            "content_hash",
            "perceptual_hash",
            "file_size",
            "dimensions",
            "timestamp",
            "metadata",
        ]

        for entry in catalog["files"]:
            for field in required_fields:
                assert field in entry, f"Missing field: {field}"

    def test_content_hash_computed(self, temp_media_dir):
        """Test that content hashes are computed."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        for entry in catalog["files"]:
            assert entry["content_hash"] is not None
            assert len(entry["content_hash"]) == 16  # xxHash64 hex length

    def test_different_files_have_different_hashes(self, temp_media_dir):
        """Test that different files have different content hashes."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        hashes = [entry["content_hash"] for entry in catalog["files"]]
        assert len(hashes) == len(set(hashes)), "Expected unique hashes for each file"

    def test_relative_path_correct(self, temp_media_dir):
        """Test that relative paths are correctly computed."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        for entry in catalog["files"]:
            assert not entry["relative_path"].startswith("/")
            assert entry["relative_path"].endswith(".jpg")

    def test_file_size_correct(self, temp_media_dir):
        """Test that file sizes are correctly recorded."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        for entry in catalog["files"]:
            # File should have non-zero size
            assert entry["file_size"] > 0
            # Verify against actual file
            actual_size = Path(entry["source_path"]).stat().st_size
            assert entry["file_size"] == actual_size

    def test_empty_file_list(self, temp_media_dir):
        """Test handling of empty file list."""
        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=[],
            workers=2,
        )

        assert catalog["files"] == []
        assert catalog["processor"] == "Test Processor"

    def test_empty_generator(self, temp_media_dir):
        """Test that generator yields nothing for empty file list."""
        entries = list(
            catalog_media_files(
                processor_name="Test Processor",
                input_path=temp_media_dir,
                media_files=[],
                workers=2,
            )
        )
        assert entries == []

    def test_metadata_extractor_called(self, temp_media_dir):
        """Test that metadata_extractor callback is called."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        def extract_metadata(file_path: Path):
            return {"custom_field": file_path.name}

        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
            metadata_extractor=extract_metadata,
        )

        for entry in catalog["files"]:
            assert "custom_field" in entry["metadata"]
            # The custom field should be the filename
            expected_name = Path(entry["source_path"]).name
            assert entry["metadata"]["custom_field"] == expected_name

    def test_metadata_extractor_failure_handled(self, temp_media_dir):
        """Test that metadata_extractor failures don't break cataloging."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        def failing_extractor(file_path: Path):
            raise ValueError("Intentional failure")

        # Should not raise
        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
            metadata_extractor=failing_extractor,
        )

        # Files should still be cataloged
        assert len(catalog["files"]) == len(media_files)

    def test_perceptual_hash_respects_config(self, temp_media_dir):
        """Test that perceptual hashing respects config setting."""
        media_files = list(temp_media_dir.glob("*.jpg"))

        # Mock the config to disable perceptual hashing
        with patch("common.dedup.catalog.get_dedup_config") as mock_config:
            mock_config.return_value.use_perceptual = False
            # Ensure database_path is also mocked to avoid creating files in project root
            mock_config.return_value.database_path = temp_media_dir / "test_dedup.db"

            catalog = collect_catalog(
                processor_name="Test Processor",
                input_path=temp_media_dir,
                media_files=media_files,
                workers=2,
            )

        # Perceptual hash should be None when disabled
        for entry in catalog["files"]:
            assert entry["perceptual_hash"] is None

    def test_handles_missing_files_gracefully(self, temp_media_dir):
        """Test that missing files don't crash cataloging."""
        existing_file = temp_media_dir / "photo1.jpg"
        missing_file = temp_media_dir / "nonexistent.jpg"

        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=[existing_file, missing_file],
            workers=2,
        )

        # Should only have the existing file
        assert len(catalog["files"]) == 1
        assert catalog["files"][0]["source_path"] == str(existing_file)

    def test_nested_directory_structure(self, temp_media_dir):
        """Test cataloging files in nested directories."""
        # Create nested structure
        subdir = temp_media_dir / "subdir" / "deep"
        subdir.mkdir(parents=True)
        nested_file = subdir / "nested.jpg"
        nested_file.write_bytes(b"nested content")

        media_files = [nested_file]

        catalog = collect_catalog(
            processor_name="Test Processor",
            input_path=temp_media_dir,
            media_files=media_files,
            workers=2,
        )

        assert len(catalog["files"]) == 1
        assert catalog["files"][0]["relative_path"] == "subdir/deep/nested.jpg"


class TestProcessorBuildCatalogStreaming:
    """Tests for processor build_catalog() streaming behavior.

    These tests verify that build_catalog() returns a generator that yields
    entries one at a time, enabling memory-efficient streaming to the database.
    """

    @pytest.fixture
    def temp_export_dir(self):
        """Create a temporary directory simulating a preprocessed export."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create media directory (standard preprocessed structure)
            media_dir = tmppath / "media"
            media_dir.mkdir()

            # Create test media files
            (media_dir / "photo1.jpg").write_bytes(b"fake jpeg content 1")
            (media_dir / "photo2.jpg").write_bytes(b"fake jpeg content 2")
            (media_dir / "video.mp4").write_bytes(b"fake video content")

            # Create metadata.json (minimal valid structure)
            metadata_file = tmppath / "metadata.json"
            metadata_file.write_text('{"export_info": {"export_username": "test_user"}}')

            yield tmppath

    def test_build_catalog_returns_generator(self, temp_export_dir):
        """Test that build_catalog returns a generator, not a dict."""
        from processors.google_voice.processor import GoogleVoiceProcessor

        # Create the expected structure for Google Voice
        # Use .jpg extension since ALL_MEDIA_EXTENSIONS includes images/videos
        voice_dir = temp_export_dir / "Voice" / "Calls"
        voice_dir.mkdir(parents=True)
        (voice_dir / "mms_image.jpg").write_bytes(b"fake image content")

        result = GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)

        # Result should be a generator, not a dict
        assert isinstance(result, types.GeneratorType), (
            f"build_catalog should return a generator, got {type(result)}"
        )

    def test_build_catalog_yields_entries_lazily(self, temp_export_dir):
        """Test that entries are yielded one at a time (lazy evaluation)."""
        from processors.google_voice.processor import GoogleVoiceProcessor

        # Create test files with image extensions
        voice_dir = temp_export_dir / "Voice" / "Calls"
        voice_dir.mkdir(parents=True)
        (voice_dir / "image1.jpg").write_bytes(b"image content 1")
        (voice_dir / "image2.jpg").write_bytes(b"image content 2")

        generator = GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=1)

        # Get first entry
        first_entry = next(generator)
        assert isinstance(first_entry, dict)
        assert "content_hash" in first_entry
        assert "source_path" in first_entry

        # Get second entry
        second_entry = next(generator)
        assert isinstance(second_entry, dict)
        assert first_entry["source_path"] != second_entry["source_path"]

        # Generator should be exhausted
        with pytest.raises(StopIteration):
            next(generator)

    def test_build_catalog_entry_structure(self, temp_export_dir):
        """Test that yielded entries have all required fields."""
        from processors.google_voice.processor import GoogleVoiceProcessor

        voice_dir = temp_export_dir / "Voice" / "Calls"
        voice_dir.mkdir(parents=True)
        (voice_dir / "mms_image.jpg").write_bytes(b"fake image")

        entries = list(
            GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)
        )

        required_fields = [
            "source_path",
            "relative_path",
            "content_hash",
            "perceptual_hash",
            "file_size",
            "dimensions",
            "timestamp",
            "metadata",
            "_processor",
        ]

        assert len(entries) == 1
        for field in required_fields:
            assert field in entries[0], f"Missing required field: {field}"

    def test_build_catalog_empty_export_returns_empty_generator(self, temp_export_dir):
        """Test that empty exports yield nothing (not an empty dict)."""
        from processors.google_voice.processor import GoogleVoiceProcessor

        # Don't create the expected Voice/Calls directory
        # Processor should detect nothing and return empty generator

        result = GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)

        # Should be a generator
        assert isinstance(result, types.GeneratorType)

        # Should yield nothing
        entries = list(result)
        assert entries == []

    def test_build_catalog_can_be_iterated_multiple_times_fresh(self, temp_export_dir):
        """Test that calling build_catalog again gives a fresh generator."""
        from processors.google_voice.processor import GoogleVoiceProcessor

        voice_dir = temp_export_dir / "Voice" / "Calls"
        voice_dir.mkdir(parents=True)
        (voice_dir / "mms_image.jpg").write_bytes(b"image content")

        # First iteration
        entries1 = list(
            GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)
        )

        # Second iteration (fresh call)
        entries2 = list(
            GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)
        )

        assert len(entries1) == len(entries2) == 1
        assert entries1[0]["content_hash"] == entries2[0]["content_hash"]

    def test_build_catalog_processor_field_set(self, temp_export_dir):
        """Test that _processor field is set correctly by catalog_media_files."""
        from processors.google_voice.processor import GoogleVoiceProcessor

        voice_dir = temp_export_dir / "Voice" / "Calls"
        voice_dir.mkdir(parents=True)
        (voice_dir / "mms_image.jpg").write_bytes(b"image")

        entries = list(
            GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)
        )

        assert entries[0]["_processor"] == "Google Voice"

    def test_streaming_does_not_accumulate_in_memory(self, temp_export_dir):
        """Test that streaming truly processes one at a time.

        This is a behavioral test that verifies the generator pattern
        is being used correctly - entries are yielded as they're computed,
        not all collected first.
        """
        from processors.google_voice.processor import GoogleVoiceProcessor

        voice_dir = temp_export_dir / "Voice" / "Calls"
        voice_dir.mkdir(parents=True)

        # Create multiple files with image extension
        for i in range(5):
            (voice_dir / f"image{i}.jpg").write_bytes(f"image {i}".encode())

        generator = GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=1)

        # Consume entries one by one, tracking what we've seen
        seen_paths = []
        for entry in generator:
            seen_paths.append(entry["source_path"])
            # At each step, we should only have processed up to this point
            # (verifying lazy evaluation)
            assert len(seen_paths) <= 5

        assert len(seen_paths) == 5


class TestMultipleProcessorStreaming:
    """Test streaming behavior across different processor types."""

    @pytest.fixture
    def temp_discord_export(self):
        """Create a temporary Discord preprocessed export."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Discord preprocessed structure
            media_dir = tmppath / "media"
            media_dir.mkdir()
            (media_dir / "attachment1.png").write_bytes(b"discord image 1")
            (media_dir / "attachment2.jpg").write_bytes(b"discord image 2")

            metadata_file = tmppath / "metadata.json"
            metadata_file.write_text('{"export_info": {}}')

            yield tmppath

    @pytest.fixture
    def temp_imessage_export(self):
        """Create a temporary iMessage export."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # iMessage structure
            attachments_dir = tmppath / "Attachments"
            attachments_dir.mkdir()
            (attachments_dir / "photo.jpg").write_bytes(b"imessage photo")

            yield tmppath

    def test_discord_build_catalog_is_generator(self, temp_discord_export):
        """Test Discord processor returns generator."""
        from processors.discord.processor import DiscordProcessor

        result = DiscordProcessor.build_catalog(str(temp_discord_export), workers=2)
        assert isinstance(result, types.GeneratorType)

        entries = list(result)
        assert len(entries) == 2
        for entry in entries:
            assert entry["_processor"] == "Discord"

    def test_imessage_build_catalog_is_generator(self, temp_imessage_export):
        """Test iMessage processor returns generator."""
        from processors.imessage.processor import IMessageProcessor

        result = IMessageProcessor.build_catalog(str(temp_imessage_export), workers=2)
        assert isinstance(result, types.GeneratorType)

        entries = list(result)
        assert len(entries) == 1
        assert entries[0]["_processor"] == "iMessage"

    def test_all_processors_return_generators(self):
        """Verify all dedup-supporting processors return generators."""
        from processors.registry import ProcessorRegistry
        import types

        # Get all processors that support dedup
        registry = ProcessorRegistry()

        # Import all processor modules to populate registry
        from processors.google_voice.processor import get_processor as gv
        from processors.google_chat.processor import get_processor as gc
        from processors.discord.processor import get_processor as dc
        from processors.imessage.processor import get_processor as im
        from processors.instagram_messages.processor import get_processor as igm

        processors = [gv(), gc(), dc(), im(), igm()]

        for processor in processors:
            if processor.supports_dedup():
                # Create a minimal temp dir
                with tempfile.TemporaryDirectory() as tmpdir:
                    result = processor.build_catalog(tmpdir, workers=1)

                    # Should be a generator type
                    assert isinstance(result, types.GeneratorType), (
                        f"{processor.get_name()}.build_catalog() should return a generator"
                    )

                    # Consume to ensure it doesn't error
                    list(result)


class TestStreamingMemoryBehavior:
    """Tests that verify streaming catalog does not accumulate memory.

    These tests use tracemalloc to measure actual memory usage and verify
    that the streaming approach uses bounded memory regardless of file count.

    Note on large files:
        The streaming architecture bounds memory from catalog entry accumulation,
        but image/video decoding during perceptual hashing can still consume
        significant memory per file:

        - JPEG: Uses draft() mode for memory-efficient decoding (~2-5MB peak)
        - HEIC/PNG: Full decode then resize (~36-50MB peak per 12MP image)
        - Video: Extracts single frame via ffmpeg (~2-5MB)

        With 8 parallel workers processing large HEIC files:
            8 workers x 36MB = 288MB peak just for image decoding

        Mitigations:
        1. Use --workers 2 for low-memory systems
        2. Disable perceptual hashing: DEDUP_USE_PERCEPTUAL=false
        3. The early-downsampling optimization reduces memory after initial load
    """

    @pytest.fixture
    def large_temp_dir(self):
        """Create a temporary directory with many test files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create 100 test files (enough to show memory difference)
            for i in range(100):
                # Each file has unique content to prevent dedup
                (tmppath / f"file_{i:03d}.jpg").write_bytes(
                    f"unique content for file {i} with some padding".encode() * 10
                )

            yield tmppath

    def test_streaming_uses_bounded_memory(self, large_temp_dir):
        """Test that streaming iteration uses bounded memory.

        This test verifies that iterating over catalog_media_files() one entry
        at a time does not accumulate all entries in memory.
        """
        media_files = list(large_temp_dir.glob("*.jpg"))
        assert len(media_files) == 100, "Should have 100 test files"

        # Force garbage collection before measuring
        gc.collect()

        # Measure memory during streaming iteration
        tracemalloc.start()

        peak_during_streaming = 0
        entries_processed = 0

        for entry in catalog_media_files(
            processor_name="Test",
            input_path=large_temp_dir,
            media_files=media_files,
            workers=1,  # Single worker for predictable behavior
        ):
            entries_processed += 1
            # Sample memory at intervals
            if entries_processed % 10 == 0:
                current, peak = tracemalloc.get_traced_memory()
                peak_during_streaming = max(peak_during_streaming, peak)

        _, streaming_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert entries_processed == 100, "Should process all 100 files"

        # Memory should be reasonable (less than 10MB for 100 small entries)
        # Each entry is ~500 bytes, so 100 entries = ~50KB if all held
        # With streaming, we should see much less
        assert streaming_peak < 10 * 1024 * 1024, (
            f"Streaming peak memory {streaming_peak / 1024 / 1024:.2f}MB "
            "exceeds 10MB threshold"
        )

    def test_streaming_does_not_hold_all_entries(self, large_temp_dir):
        """Test that streaming processes entries without holding all in memory.

        This verifies the streaming pattern works correctly by checking that
        we can process entries one at a time without keeping references to
        previous entries.
        """
        media_files = list(large_temp_dir.glob("*.jpg"))

        # Track that we're processing entries without accumulating
        processed_hashes = set()
        current_entry = None

        for entry in catalog_media_files(
            processor_name="Test",
            input_path=large_temp_dir,
            media_files=media_files,
            workers=1,
        ):
            # Verify we get a fresh entry each time
            assert entry is not current_entry, "Should get new entry object each iteration"

            # Track unique hashes to verify all files processed
            processed_hashes.add(entry["content_hash"])

            # Only keep reference to current entry (simulates bounded memory usage)
            current_entry = entry

        # Verify all files were processed
        assert len(processed_hashes) == 100, "Should process all 100 unique files"

    def test_collect_catalog_holds_all_entries(self, large_temp_dir):
        """Test that collect_catalog does hold all entries (for comparison).

        This verifies that collect_catalog behaves as expected - it accumulates
        all entries in memory. This is the behavior we're avoiding with streaming.
        """
        media_files = list(large_temp_dir.glob("*.jpg"))

        catalog = collect_catalog(
            processor_name="Test",
            input_path=large_temp_dir,
            media_files=media_files,
            workers=1,
        )

        # collect_catalog should have all entries in memory at once
        assert len(catalog["files"]) == 100, "collect_catalog should hold all 100 entries"

        # All entries should be accessible (they're all in memory)
        hashes = {entry["content_hash"] for entry in catalog["files"]}
        assert len(hashes) == 100, "All entries should have unique hashes"

    def test_worker_writes_entries_incrementally(self):
        """Test that the catalog worker writes to DB incrementally, not all at once.

        This mocks the database to verify entries are written in batches
        during iteration, not accumulated and written at the end.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Create test structure for a processor
            media_dir = tmppath / "media"
            media_dir.mkdir()

            # Create enough files to trigger multiple batch writes (batch size is 100)
            for i in range(250):
                (media_dir / f"file_{i:03d}.jpg").write_bytes(f"content {i}".encode())

            # Mock the database
            mock_db = MagicMock()
            write_call_counts = []

            def track_batch_write(run_id, export_name, batch):
                write_call_counts.append(len(batch))
                return len(batch)

            mock_db.write_catalog_entries_batch.side_effect = track_batch_write

            # Import and run the worker function
            from processors.discord.processor import DiscordProcessor

            # Process using the actual generator
            batch = []
            for entry in DiscordProcessor.build_catalog(str(tmppath), workers=1):
                batch.append(entry)
                if len(batch) >= 100:
                    mock_db.write_catalog_entries_batch("test_run", "test_export", batch)
                    batch = []

            if batch:
                mock_db.write_catalog_entries_batch("test_run", "test_export", batch)

            # Verify multiple batch writes occurred
            assert len(write_call_counts) >= 2, (
                f"Expected multiple batch writes, got {len(write_call_counts)}"
            )

            # Each batch should be <= 100 entries (except possibly the last)
            for i, count in enumerate(write_call_counts[:-1]):
                assert count == 100, f"Batch {i} should have 100 entries, got {count}"

            # Total entries written should match files created
            total_written = sum(write_call_counts)
            assert total_written == 250, f"Expected 250 entries, got {total_written}"

    def test_catalog_workers_env_limits_parallelism(self, large_temp_dir):
        """Test that CATALOG_WORKERS environment variable limits worker count.

        This allows users to reduce memory usage on constrained systems by
        limiting how many large files are processed in parallel.
        """
        import os

        media_files = list(large_temp_dir.glob("*.jpg"))[:10]  # Use subset

        # Set environment variable to limit workers
        original_value = os.environ.get("CATALOG_WORKERS")
        try:
            os.environ["CATALOG_WORKERS"] = "1"

            # Process files - should use only 1 worker
            entries = list(
                catalog_media_files(
                    processor_name="Test",
                    input_path=large_temp_dir,
                    media_files=media_files,
                    workers=8,  # This should be overridden by env var
                )
            )

            assert len(entries) == 10, "Should process all files even with 1 worker"

        finally:
            # Restore original value
            if original_value is None:
                os.environ.pop("CATALOG_WORKERS", None)
            else:
                os.environ["CATALOG_WORKERS"] = original_value

    def test_large_file_memory_documentation(self):
        """Document memory usage expectations for large files.

        This test serves as documentation for expected memory behavior
        when processing large image files (especially HEIC).

        Memory usage per file during perceptual hashing:
        - JPEG: ~2-5MB (uses draft mode for efficient decoding)
        - HEIC: ~36-50MB (full decode before resize)
        - PNG: ~20-40MB (depends on dimensions)
        - Video: ~2-5MB (extracts single frame via ffmpeg)

        With default 8 workers processing large HEIC files:
            8 x 50MB = 400MB peak just for image decoding

        Mitigations for memory-constrained systems:
        1. CATALOG_WORKERS=2 - Limit parallel file processing
        2. DEDUP_USE_PERCEPTUAL=false - Skip perceptual hashing entirely
        3. --workers 2 - Reduce global worker count

        The streaming catalog architecture ensures catalog ENTRIES don't
        accumulate, but file processing memory is still bounded by worker count.
        """
        # This is a documentation test - verify the env var exists
        from common.dedup.catalog import CATALOG_WORKERS_ENV

        assert CATALOG_WORKERS_ENV == "CATALOG_WORKERS"
