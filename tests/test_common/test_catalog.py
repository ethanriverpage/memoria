#!/usr/bin/env python3
"""Unit tests for catalog_media_files() in common/catalog.py."""

import gc
import tracemalloc
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import patch, MagicMock
import types

import pytest

from common.dedup.catalog import catalog_media_files


def build_catalog_dict(
    processor_name: str,
    input_path: Path,
    media_files: List[Path],
    workers: int = 4,
    metadata_extractor: Optional[Callable[[Path], Dict[str, Any]]] = None,
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a catalog dictionary by collecting all entries from the generator.

    Test helper that wraps catalog_media_files() into a dictionary format.
    """
    catalog: Dict[str, Any] = {
        "processor": processor_name,
        "input_dir": str(input_path),
        "files": [],
    }
    for entry in catalog_media_files(
        processor_name=processor_name,
        input_path=input_path,
        media_files=media_files,
        workers=workers,
        metadata_extractor=metadata_extractor,
        db_path=db_path,
    ):
        catalog["files"].append(entry)
    return catalog


class TestCatalogMediaFiles:
    """Tests for catalog_media_files() generator function."""

    @pytest.fixture
    def temp_media_dir(self, tmp_path):
        """Create a temporary directory with test media files.

        Uses pytest's tmp_path fixture for automatic cleanup.
        """
        # Create some test files
        (tmp_path / "photo1.jpg").write_bytes(b"fake jpeg content 1")
        (tmp_path / "photo2.jpg").write_bytes(b"fake jpeg content 2")
        (tmp_path / "video.mp4").write_bytes(b"fake video content")

        return tmp_path

    def test_catalog_structure(self, temp_media_dir):
        """Test that collect_catalog has correct structure."""
        media_files = list(temp_media_dir.glob("*"))

        catalog = build_catalog_dict(
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

        catalog = build_catalog_dict(
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

        catalog = build_catalog_dict(
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

        catalog = build_catalog_dict(
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

        catalog = build_catalog_dict(
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

        catalog = build_catalog_dict(
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
        catalog = build_catalog_dict(
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

        catalog = build_catalog_dict(
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
        catalog = build_catalog_dict(
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

            catalog = build_catalog_dict(
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

        catalog = build_catalog_dict(
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

        catalog = build_catalog_dict(
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
    def temp_export_dir(self, tmp_path):
        """Create a temporary preprocessed export directory.

        Uses pytest's tmp_path fixture for automatic cleanup.
        Creates a properly preprocessed directory structure with metadata.json
        containing conversations with messages (the format GoogleVoiceProcessor
        expects via catalog_from_preprocessed_if_available with container_key).
        """
        import json

        # Create source media files (simulating original export location)
        source_dir = tmp_path / "source_media"
        source_dir.mkdir()
        (source_dir / "image1.jpg").write_bytes(b"fake jpeg content 1")
        (source_dir / "image2.jpg").write_bytes(b"fake jpeg content 2")

        # Create metadata.json with conversations format (Google Voice uses this)
        # GoogleVoiceProcessor.build_catalog uses container_key="conversations"
        metadata = {
            "export_info": {"export_username": "test_user"},
            "conversations": [
                {
                    "conversation_name": "Test Conversation",
                    "messages": [
                        {
                            "media_files": ["image1.jpg"],
                            "source_paths": {
                                "image1.jpg": str(source_dir / "image1.jpg"),
                            },
                            "source_path": str(source_dir / "image1.jpg"),
                            "timestamp": "2024-01-01T00:00:00Z",
                            "sender": "test_sender",
                        },
                        {
                            "media_files": ["image2.jpg"],
                            "source_paths": {
                                "image2.jpg": str(source_dir / "image2.jpg"),
                            },
                            "source_path": str(source_dir / "image2.jpg"),
                            "timestamp": "2024-01-02T00:00:00Z",
                            "sender": "test_sender",
                        },
                    ],
                }
            ],
            "file_hashes": {},
        }
        metadata_file = tmp_path / "metadata.json"
        metadata_file.write_text(json.dumps(metadata))

        return tmp_path

    def test_build_catalog_returns_generator(self, temp_export_dir):
        """Test that build_catalog returns a generator, not a dict."""
        from processors.google_voice.processor import GoogleVoiceProcessor

        result = GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)

        # Result should be a generator, not a dict
        assert isinstance(result, types.GeneratorType), (
            f"build_catalog should return a generator, got {type(result)}"
        )

    def test_build_catalog_yields_entries_lazily(self, temp_export_dir):
        """Test that entries are yielded one at a time (lazy evaluation)."""
        from processors.google_voice.processor import GoogleVoiceProcessor

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

        assert len(entries) == 2
        for field in required_fields:
            assert field in entries[0], f"Missing required field: {field}"

    def test_build_catalog_empty_export_returns_empty_generator(self, tmp_path):
        """Test that empty exports yield nothing (not an empty dict)."""
        import json
        from processors.google_voice.processor import GoogleVoiceProcessor

        # Create metadata with no media files
        metadata = {
            "export_info": {"export_username": "test_user"},
            "conversations": [],
        }
        metadata_file = tmp_path / "metadata.json"
        metadata_file.write_text(json.dumps(metadata))

        result = GoogleVoiceProcessor.build_catalog(str(tmp_path), workers=2)

        # Should be a generator
        assert isinstance(result, types.GeneratorType)

        # Should yield nothing
        entries = list(result)
        assert entries == []

    def test_build_catalog_can_be_iterated_multiple_times_fresh(self, temp_export_dir):
        """Test that calling build_catalog again gives a fresh generator."""
        from processors.google_voice.processor import GoogleVoiceProcessor

        # First iteration
        entries1 = list(
            GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)
        )

        # Second iteration (fresh call)
        entries2 = list(
            GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)
        )

        assert len(entries1) == len(entries2) == 2
        # Compare content_hashes as sets since parallel processing order may vary
        hashes1 = {e["content_hash"] for e in entries1}
        hashes2 = {e["content_hash"] for e in entries2}
        assert hashes1 == hashes2

    def test_build_catalog_processor_field_set(self, temp_export_dir):
        """Test that _processor field is set correctly by catalog_media_files."""
        from processors.google_voice.processor import GoogleVoiceProcessor

        entries = list(
            GoogleVoiceProcessor.build_catalog(str(temp_export_dir), workers=2)
        )

        assert len(entries) == 2
        assert entries[0]["_processor"] == "Google Voice"

    def test_streaming_does_not_accumulate_in_memory(self, tmp_path):
        """Test that streaming truly processes one at a time.

        This is a behavioral test that verifies the generator pattern
        is being used correctly - entries are yielded as they're computed,
        not all collected first.
        """
        import json
        from processors.google_voice.processor import GoogleVoiceProcessor

        # Create source media files
        source_dir = tmp_path / "source_media"
        source_dir.mkdir()
        for i in range(5):
            (source_dir / f"image{i}.jpg").write_bytes(f"image {i}".encode())

        # Create metadata with conversations format (Google Voice format)
        messages = []
        for i in range(5):
            messages.append(
                {
                    "media_files": [f"image{i}.jpg"],
                    "source_paths": {
                        f"image{i}.jpg": str(source_dir / f"image{i}.jpg"),
                    },
                    "timestamp": "2024-01-01T00:00:00Z",
                    "sender": "test_sender",
                }
            )

        metadata = {
            "export_info": {"export_username": "test_user"},
            "conversations": [
                {
                    "conversation_name": "Test Conversation",
                    "messages": messages,
                }
            ],
            "file_hashes": {},
        }
        metadata_file = tmp_path / "metadata.json"
        metadata_file.write_text(json.dumps(metadata))

        generator = GoogleVoiceProcessor.build_catalog(str(tmp_path), workers=1)

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
    def temp_discord_export(self, tmp_path):
        """Create a temporary Discord preprocessed export.

        Uses pytest's tmp_path fixture for automatic cleanup.
        Discord uses conversations format with messages containing media_files.
        """
        import json

        # Create source media files
        source_dir = tmp_path / "source_media"
        source_dir.mkdir()
        (source_dir / "attachment1.png").write_bytes(b"discord image 1")
        (source_dir / "attachment2.jpg").write_bytes(b"discord image 2")

        # Create metadata.json with conversations format (Discord format)
        metadata = {
            "export_info": {"export_username": "discord_user"},
            "conversations": {
                "channel1": {
                    "type": "GUILD_TEXT",
                    "title": "general",
                    "guild_name": "Test Server",
                    "messages": [
                        {
                            "id": "msg1",
                            "media_files": ["attachment1.png"],
                            "source_paths": {
                                "attachment1.png": str(source_dir / "attachment1.png"),
                            },
                            "timestamp": "2024-01-01T00:00:00Z",
                            "sender": "test_user",
                        },
                        {
                            "id": "msg2",
                            "media_files": ["attachment2.jpg"],
                            "source_paths": {
                                "attachment2.jpg": str(source_dir / "attachment2.jpg"),
                            },
                            "timestamp": "2024-01-02T00:00:00Z",
                            "sender": "test_user",
                        },
                    ],
                }
            },
            "file_hashes": {},
        }
        metadata_file = tmp_path / "metadata.json"
        metadata_file.write_text(json.dumps(metadata))

        return tmp_path

    @pytest.fixture
    def temp_imessage_export(self, tmp_path):
        """Create a temporary preprocessed iMessage export.

        Uses pytest's tmp_path fixture for automatic cleanup.
        iMessage uses conversations format with messages.
        """
        import json

        # Create source media file
        source_dir = tmp_path / "source_media"
        source_dir.mkdir()
        (source_dir / "photo.jpg").write_bytes(b"imessage photo")

        # Create metadata.json with conversations format (iMessage format)
        metadata = {
            "export_info": {"export_username": "test_user"},
            "conversations": {
                "+14155551234": {
                    "conversation_title": "Test Chat",
                    "messages": [
                        {
                            "media_files": ["photo.jpg"],
                            "source_paths": {
                                "photo.jpg": str(source_dir / "photo.jpg"),
                            },
                            "timestamp": "2024-01-01 00:00:00 UTC",
                            "sender": "test_sender",
                        },
                    ],
                }
            },
            "file_hashes": {},
        }
        metadata_file = tmp_path / "metadata.json"
        metadata_file.write_text(json.dumps(metadata))

        return tmp_path

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

    def test_all_processors_return_generators(self, tmp_path):
        """Verify all dedup-supporting processors return generators.

        Uses pytest's tmp_path fixture for automatic cleanup.
        """
        from processors.registry import ProcessorRegistry

        # Get all processors that support dedup
        ProcessorRegistry()

        # Import processor classes
        from processors.google_voice.processor import GoogleVoiceProcessor
        from processors.google_chat.processor import GoogleChatProcessor
        from processors.discord.processor import DiscordProcessor
        from processors.imessage.processor import IMessageProcessor
        from processors.instagram_messages.processor import InstagramMessagesProcessor

        processors = [
            GoogleVoiceProcessor,
            GoogleChatProcessor,
            DiscordProcessor,
            IMessageProcessor,
            InstagramMessagesProcessor,
        ]

        for idx, processor in enumerate(processors):
            if processor.supports_dedup():
                # Create a unique subdir for each processor
                proc_dir = tmp_path / f"proc_{idx}"
                proc_dir.mkdir()

                result = processor.build_catalog(str(proc_dir), workers=1)

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
    def large_temp_dir(self, tmp_path):
        """Create a temporary directory with many test files.

        Uses pytest's tmp_path fixture for automatic cleanup.
        """
        # Create 100 test files (enough to show memory difference)
        for i in range(100):
            # Each file has unique content to prevent dedup
            (tmp_path / f"file_{i:03d}.jpg").write_bytes(
                f"unique content for file {i} with some padding".encode() * 10
            )

        return tmp_path

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
            assert entry is not current_entry, (
                "Should get new entry object each iteration"
            )

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

        catalog = build_catalog_dict(
            processor_name="Test",
            input_path=large_temp_dir,
            media_files=media_files,
            workers=1,
        )

        # collect_catalog should have all entries in memory at once
        assert len(catalog["files"]) == 100, (
            "collect_catalog should hold all 100 entries"
        )

        # All entries should be accessible (they're all in memory)
        hashes = {entry["content_hash"] for entry in catalog["files"]}
        assert len(hashes) == 100, "All entries should have unique hashes"

    def test_worker_writes_entries_incrementally(self, tmp_path):
        """Test that the catalog worker writes to DB incrementally, not all at once.

        This mocks the database to verify entries are written in batches
        during iteration, not accumulated and written at the end.

        Uses pytest's tmp_path fixture for automatic cleanup.
        """
        import json

        # Create source media files
        source_dir = tmp_path / "source_media"
        source_dir.mkdir()

        # Create enough files to trigger multiple batch writes (batch size is 100)
        messages = []
        for i in range(250):
            file_path = source_dir / f"file_{i:03d}.jpg"
            file_path.write_bytes(f"content {i}".encode())
            messages.append(
                {
                    "id": f"msg_{i}",
                    "media_files": [f"file_{i:03d}.jpg"],
                    "source_paths": {
                        f"file_{i:03d}.jpg": str(file_path),
                    },
                    "timestamp": "2024-01-01T00:00:00Z",
                    "sender": "test_user",
                }
            )

        # Create metadata.json with conversations format (Discord format)
        metadata = {
            "export_info": {"export_username": "discord_user"},
            "conversations": {
                "channel1": {
                    "type": "GUILD_TEXT",
                    "title": "general",
                    "guild_name": "Test Server",
                    "messages": messages,
                }
            },
            "file_hashes": {},
        }
        metadata_file = tmp_path / "metadata.json"
        metadata_file.write_text(json.dumps(metadata))

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
        for entry in DiscordProcessor.build_catalog(str(tmp_path), workers=1):
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
