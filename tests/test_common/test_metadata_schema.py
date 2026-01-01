#!/usr/bin/env python3
"""Unit tests for metadata schema validation and extraction."""

import json
from pathlib import Path

import pytest

from common.metadata.schema import (
    ContentType,
    CONTENT_KEYS,
    MetadataValidationError,
    get_content_type,
    validate_metadata,
)
from processors.base import ProcessorBase


class TestContentTypeDetection:
    """Tests for get_content_type() function."""

    def test_detects_conversations_type(self):
        """Test detection of conversations content type."""
        metadata = {
            "export_info": {"export_username": "test"},
            "conversations": [{"messages": []}],
            "file_hashes": {},
        }
        assert get_content_type(metadata) == ContentType.CONVERSATIONS

    def test_detects_conversations_dict_format(self):
        """Test detection of conversations as dict (keyed by ID)."""
        metadata = {
            "export_info": {"export_username": "test"},
            "conversations": {"conv1": {"messages": []}, "conv2": {"messages": []}},
            "file_hashes": {},
        }
        assert get_content_type(metadata) == ContentType.CONVERSATIONS

    def test_detects_media_type(self):
        """Test detection of media content type (posts)."""
        metadata = {
            "export_info": {"export_username": "test"},
            "media": [{"media_files": [], "source_paths": {}}],
            "file_hashes": {},
        }
        assert get_content_type(metadata) == ContentType.MEDIA

    def test_detects_media_files_type(self):
        """Test detection of media_files content type (flat structure)."""
        metadata = {
            "export_info": {"export_username": "test"},
            "media_files": [{"filename": "test.jpg", "source_path": "/path/to/file"}],
            "file_hashes": {},
        }
        assert get_content_type(metadata) == ContentType.MEDIA_FILES

    def test_raises_on_missing_content_key(self):
        """Test that missing content key raises error."""
        metadata = {"export_info": {"export_username": "test"}, "file_hashes": {}}
        with pytest.raises(MetadataValidationError) as exc_info:
            get_content_type(metadata)
        assert "No valid content key found" in str(exc_info.value)

    def test_content_keys_constant(self):
        """Test that CONTENT_KEYS contains expected values."""
        assert "conversations" in CONTENT_KEYS
        assert "media" in CONTENT_KEYS
        assert "media_files" in CONTENT_KEYS
        assert len(CONTENT_KEYS) == 3


class TestMetadataValidation:
    """Tests for validate_metadata() function."""

    def test_valid_conversations_metadata(self):
        """Test validation of valid conversations metadata."""
        metadata = {
            "export_info": {
                "export_path": "/path/to/export",
                "export_name": "test-export",
                "export_username": "testuser",
                "username_source": "directory",
                "processed_date": "2024-01-01T00:00:00",
            },
            "conversations": [
                {
                    "conversation_id": "conv1",
                    "messages": [
                        {
                            "media_files": ["image.jpg"],
                            "source_paths": {"image.jpg": "/path/to/image.jpg"},
                        }
                    ],
                }
            ],
            "file_hashes": {"image.jpg": "abc123"},
        }
        # Should not raise
        validate_metadata(metadata, require_source_paths=True)

    def test_valid_media_files_metadata(self):
        """Test validation of valid media_files metadata."""
        metadata = {
            "export_info": {
                "export_path": "/path/to/export",
                "export_name": "test-export",
                "export_username": "testuser",
                "username_source": "directory",
                "processed_date": "2024-01-01T00:00:00",
            },
            "media_files": [
                {"filename": "image.jpg", "source_path": "/path/to/image.jpg"}
            ],
            "file_hashes": {"image.jpg": "abc123"},
        }
        validate_metadata(metadata, require_source_paths=True)

    def test_valid_media_metadata(self):
        """Test validation of valid media (posts) metadata."""
        metadata = {
            "export_info": {
                "export_path": "/path/to/export",
                "export_name": "test-export",
                "export_username": "testuser",
                "username_source": "directory",
                "processed_date": "2024-01-01T00:00:00",
            },
            "media": [
                {
                    "media_files": ["image.jpg"],
                    "source_paths": {"image.jpg": "/path/to/image.jpg"},
                }
            ],
            "file_hashes": {"image.jpg": "abc123"},
        }
        validate_metadata(metadata, require_source_paths=True)

    def test_raises_on_missing_export_info(self):
        """Test that missing export_info raises error."""
        metadata = {"conversations": [], "file_hashes": {}}
        with pytest.raises(MetadataValidationError) as exc_info:
            validate_metadata(metadata)
        assert "export_info" in str(exc_info.value)

    def test_raises_on_missing_export_info_fields(self):
        """Test that missing export_info fields raise error."""
        metadata = {
            "export_info": {"export_username": "test"},  # Missing other required fields
            "conversations": [],
            "file_hashes": {},
        }
        with pytest.raises(MetadataValidationError) as exc_info:
            validate_metadata(metadata)
        assert "export_info missing required field" in str(exc_info.value)

    def test_raises_on_missing_file_hashes(self):
        """Test that missing file_hashes raises error."""
        metadata = {
            "export_info": {
                "export_path": "/path",
                "export_name": "test",
                "export_username": "user",
                "username_source": "dir",
                "processed_date": "2024-01-01",
            },
            "conversations": [],
        }
        with pytest.raises(MetadataValidationError) as exc_info:
            validate_metadata(metadata)
        assert "file_hashes" in str(exc_info.value)

    def test_raises_on_missing_source_path_in_media_files(self):
        """Test that missing source_path in media_files raises error."""
        metadata = {
            "export_info": {
                "export_path": "/path",
                "export_name": "test",
                "export_username": "user",
                "username_source": "dir",
                "processed_date": "2024-01-01",
            },
            "media_files": [
                {"filename": "image.jpg"}  # Missing source_path
            ],
            "file_hashes": {},
        }
        with pytest.raises(MetadataValidationError) as exc_info:
            validate_metadata(metadata, require_source_paths=True)
        assert "source_path" in str(exc_info.value)

    def test_skips_source_path_validation_when_disabled(self):
        """Test that source_path validation can be disabled."""
        metadata = {
            "export_info": {
                "export_path": "/path",
                "export_name": "test",
                "export_username": "user",
                "username_source": "dir",
                "processed_date": "2024-01-01",
            },
            "media_files": [{"filename": "image.jpg"}],  # Missing source_path
            "file_hashes": {},
        }
        # Should not raise when require_source_paths=False
        validate_metadata(metadata, require_source_paths=False)


class TestExtractAllMediaEntries:
    """Tests for ProcessorBase.extract_all_media_entries()."""

    def test_extracts_from_media_files_format(self):
        """Test extraction from flat media_files format."""
        metadata = {
            "export_info": {"export_username": "test"},
            "media_files": [
                {"filename": "image1.jpg", "source_path": "/path/image1.jpg"},
                {"filename": "image2.jpg", "source_path": "/path/image2.jpg"},
            ],
            "file_hashes": {},
        }
        entries = list(ProcessorBase.extract_all_media_entries(metadata))
        assert len(entries) == 2
        assert entries[0]["filename"] == "image1.jpg"
        assert entries[0]["source_path"] == "/path/image1.jpg"

    def test_extracts_from_media_format(self):
        """Test extraction from media (posts) format."""
        metadata = {
            "export_info": {"export_username": "test"},
            "media": [
                {
                    "media_files": ["image1.jpg", "image2.jpg"],
                    "source_paths": {
                        "image1.jpg": "/path/image1.jpg",
                        "image2.jpg": "/path/image2.jpg",
                    },
                    "timestamp": "2024-01-01",
                    "caption": "Test post",
                }
            ],
            "file_hashes": {},
        }
        entries = list(ProcessorBase.extract_all_media_entries(metadata))
        assert len(entries) == 2
        assert entries[0]["source_path"] == "/path/image1.jpg"
        assert entries[0]["timestamp"] == "2024-01-01"

    def test_extracts_from_conversations_list_format(self):
        """Test extraction from conversations as list format."""
        metadata = {
            "export_info": {"export_username": "test"},
            "conversations": [
                {
                    "conversation_title": "Test Chat",
                    "messages": [
                        {
                            "media_files": ["image1.jpg"],
                            "source_paths": {"image1.jpg": "/path/image1.jpg"},
                            "timestamp": "2024-01-01",
                            "sender": "user1",
                        }
                    ],
                }
            ],
            "file_hashes": {},
        }
        entries = list(ProcessorBase.extract_all_media_entries(metadata))
        assert len(entries) == 1
        assert entries[0]["source_path"] == "/path/image1.jpg"
        assert entries[0]["timestamp"] == "2024-01-01"
        assert entries[0]["conversation"] == "Test Chat"

    def test_extracts_from_conversations_dict_format(self):
        """Test extraction from conversations as dict format."""
        metadata = {
            "export_info": {"export_username": "test"},
            "conversations": {
                "conv1": {
                    "conversation_title": "Chat 1",
                    "messages": [
                        {
                            "media_files": ["image1.jpg"],
                            "source_paths": {"image1.jpg": "/path/image1.jpg"},
                        }
                    ],
                },
                "conv2": {
                    "conversation_title": "Chat 2",
                    "messages": [
                        {
                            "media_files": ["image2.jpg"],
                            "source_paths": {"image2.jpg": "/path/image2.jpg"},
                        }
                    ],
                },
            },
            "file_hashes": {},
        }
        entries = list(ProcessorBase.extract_all_media_entries(metadata))
        assert len(entries) == 2

    def test_includes_unmatched_media(self):
        """Test that unmatched_media entries are included."""
        metadata = {
            "export_info": {"export_username": "test"},
            "conversations": [],
            "unmatched_media": [
                {"filename": "orphan.jpg", "source_path": "/path/orphan.jpg"}
            ],
            "file_hashes": {},
        }
        entries = list(ProcessorBase.extract_all_media_entries(metadata))
        assert len(entries) == 1
        assert entries[0]["filename"] == "orphan.jpg"

    def test_empty_metadata_yields_nothing(self):
        """Test that empty metadata yields no entries."""
        metadata = {
            "export_info": {"export_username": "test"},
            "conversations": [],
            "file_hashes": {},
        }
        entries = list(ProcessorBase.extract_all_media_entries(metadata))
        assert len(entries) == 0

    def test_invalid_metadata_yields_nothing(self):
        """Test that invalid metadata (no content key) yields nothing."""
        metadata = {"export_info": {"export_username": "test"}}
        entries = list(ProcessorBase.extract_all_media_entries(metadata))
        assert len(entries) == 0


class TestCatalogFromPreprocessed:
    """Tests for catalog_from_preprocessed with unified extraction."""

    @pytest.fixture
    def temp_media_files_export(self, tmp_path):
        """Create a temp export with media_files format."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "image1.jpg").write_bytes(b"content1")
        (source_dir / "image2.jpg").write_bytes(b"content2")

        metadata = {
            "export_info": {
                "export_path": str(tmp_path),
                "export_name": "test-export",
                "export_username": "testuser",
                "username_source": "directory",
                "processed_date": "2024-01-01",
            },
            "media_files": [
                {
                    "filename": "image1.jpg",
                    "source_path": str(source_dir / "image1.jpg"),
                },
                {
                    "filename": "image2.jpg",
                    "source_path": str(source_dir / "image2.jpg"),
                },
            ],
            "file_hashes": {},
        }
        (tmp_path / "metadata.json").write_text(json.dumps(metadata))
        return tmp_path

    @pytest.fixture
    def temp_conversations_export(self, tmp_path):
        """Create a temp export with conversations format."""
        source_dir = tmp_path / "source"
        source_dir.mkdir()
        (source_dir / "image1.jpg").write_bytes(b"content1")
        (source_dir / "image2.jpg").write_bytes(b"content2")

        metadata = {
            "export_info": {
                "export_path": str(tmp_path),
                "export_name": "test-export",
                "export_username": "testuser",
                "username_source": "directory",
                "processed_date": "2024-01-01",
            },
            "conversations": [
                {
                    "conversation_title": "Test Chat",
                    "messages": [
                        {
                            "media_files": ["image1.jpg"],
                            "source_paths": {
                                "image1.jpg": str(source_dir / "image1.jpg"),
                            },
                            "timestamp": "2024-01-01",
                        },
                        {
                            "media_files": ["image2.jpg"],
                            "source_paths": {
                                "image2.jpg": str(source_dir / "image2.jpg"),
                            },
                            "timestamp": "2024-01-02",
                        },
                    ],
                }
            ],
            "file_hashes": {},
        }
        (tmp_path / "metadata.json").write_text(json.dumps(metadata))
        return tmp_path

    def test_catalogs_media_files_format(self, temp_media_files_export):
        """Test cataloging from media_files format."""
        from common.dedup.catalog import catalog_from_preprocessed

        entries = list(
            catalog_from_preprocessed(
                processor_name="Test",
                input_path=temp_media_files_export,
                workers=1,
            )
        )
        assert len(entries) == 2
        assert all("content_hash" in e for e in entries)
        assert all("source_path" in e for e in entries)

    def test_catalogs_conversations_format(self, temp_conversations_export):
        """Test cataloging from conversations format."""
        from common.dedup.catalog import catalog_from_preprocessed

        entries = list(
            catalog_from_preprocessed(
                processor_name="Test",
                input_path=temp_conversations_export,
                workers=1,
            )
        )
        assert len(entries) == 2
        assert all("content_hash" in e for e in entries)

    def test_returns_generator(self, temp_media_files_export):
        """Test that catalog_from_preprocessed returns a generator."""
        import types
        from common.dedup.catalog import catalog_from_preprocessed

        result = catalog_from_preprocessed(
            processor_name="Test",
            input_path=temp_media_files_export,
            workers=1,
        )
        assert isinstance(result, types.GeneratorType)

    def test_empty_export_yields_nothing(self, tmp_path):
        """Test that empty exports yield no entries."""
        from common.dedup.catalog import catalog_from_preprocessed

        metadata = {
            "export_info": {
                "export_path": str(tmp_path),
                "export_name": "test",
                "export_username": "user",
                "username_source": "dir",
                "processed_date": "2024-01-01",
            },
            "media_files": [],
            "file_hashes": {},
        }
        (tmp_path / "metadata.json").write_text(json.dumps(metadata))

        entries = list(
            catalog_from_preprocessed(
                processor_name="Test", input_path=tmp_path, workers=1
            )
        )
        assert len(entries) == 0


class TestPreprocessorBaseMetadataMethods:
    """Tests for PreprocessorBase standardized metadata methods."""

    def test_build_file_hashes(self, tmp_path):
        """Test build_file_hashes returns correct mapping."""
        from processors.preprocess_base import PreprocessorBase

        class MockPreprocessor(PreprocessorBase):
            def validate_export(self):
                return True

            def detect_username(self):
                from common.detection import DetectionResult

                return DetectionResult("testuser", "mock")

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

        # Create a mock preprocessor
        preprocessor = MockPreprocessor(
            processor_name="Test",
            export_path=tmp_path,
            output_dir=tmp_path / "output",
        )

        # Manually populate hash registry
        preprocessor._hash_registry = {
            "hash1": {"filename": "file1.jpg", "source_path": "/path/file1.jpg"},
            "hash2": {"filename": "file2.jpg", "source_path": "/path/file2.jpg"},
        }

        file_hashes = preprocessor.build_file_hashes()
        assert file_hashes == {"file1.jpg": "hash1", "file2.jpg": "hash2"}

    def test_add_unmatched_media(self, tmp_path):
        """Test add_unmatched_media tracks entries correctly."""
        from processors.preprocess_base import PreprocessorBase

        class MockPreprocessor(PreprocessorBase):
            def validate_export(self):
                return True

            def detect_username(self):
                from common.detection import DetectionResult

                return DetectionResult("testuser", "mock")

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

        preprocessor = MockPreprocessor(
            processor_name="Test",
            export_path=tmp_path,
            output_dir=tmp_path / "output",
        )

        preprocessor.add_unmatched_media(
            filename="orphan.jpg",
            source_path=Path("/path/orphan.jpg"),
            content_hash="abc123",
            timestamp="2024-01-01",
            extra_field="value",
        )

        assert len(preprocessor._unmatched_media) == 1
        entry = preprocessor._unmatched_media[0]
        assert entry["filename"] == "orphan.jpg"
        assert entry["source_path"] == "/path/orphan.jpg"
        assert entry["content_hash"] == "abc123"
        assert entry["timestamp"] == "2024-01-01"
        assert entry["metadata"]["extra_field"] == "value"


class TestProcessorSideValidation:
    """Tests for processor-side metadata validation via load_metadata."""

    @staticmethod
    def _valid_export_info():
        """Return a valid export_info dict with all required fields."""
        return {
            "export_path": "/path/to/export",
            "export_username": "test",
            "processed_date": "2024-01-01T00:00:00",
        }

    def test_load_metadata_with_validation_raises_on_missing_export_info(
        self, tmp_path: Path
    ):
        """Processor load_metadata should raise when export_info is missing."""
        metadata_file = tmp_path / "metadata.json"
        invalid_metadata = {
            "conversations": [],
            "file_hashes": {},
        }
        metadata_file.write_text(json.dumps(invalid_metadata))

        with pytest.raises(MetadataValidationError) as exc_info:
            ProcessorBase.load_metadata(
                metadata_file, validate=True, require_source_paths=True
            )
        assert "export_info" in str(exc_info.value)

    def test_load_metadata_with_validation_raises_on_missing_file_hashes(
        self, tmp_path: Path
    ):
        """Processor load_metadata should raise when file_hashes is missing."""
        metadata_file = tmp_path / "metadata.json"
        invalid_metadata = {
            "export_info": self._valid_export_info(),
            "conversations": [],
        }
        metadata_file.write_text(json.dumps(invalid_metadata))

        with pytest.raises(MetadataValidationError) as exc_info:
            ProcessorBase.load_metadata(
                metadata_file, validate=True, require_source_paths=True
            )
        assert "file_hashes" in str(exc_info.value)

    def test_load_metadata_with_validation_raises_on_missing_source_paths(
        self, tmp_path: Path
    ):
        """Processor load_metadata should raise when source_paths is missing."""
        metadata_file = tmp_path / "metadata.json"
        invalid_metadata = {
            "export_info": self._valid_export_info(),
            "conversations": [
                {
                    "messages": [
                        {
                            "media_files": ["image.jpg"],
                            # Missing source_paths
                        }
                    ]
                }
            ],
            "file_hashes": {},
        }
        metadata_file.write_text(json.dumps(invalid_metadata))

        with pytest.raises(MetadataValidationError) as exc_info:
            ProcessorBase.load_metadata(
                metadata_file, validate=True, require_source_paths=True
            )
        assert "source_path" in str(exc_info.value)

    def test_load_metadata_with_validation_succeeds_on_valid_metadata(
        self, tmp_path: Path
    ):
        """Processor load_metadata should succeed with valid metadata."""
        metadata_file = tmp_path / "metadata.json"
        valid_metadata = {
            "export_info": self._valid_export_info(),
            "conversations": [
                {
                    "messages": [
                        {
                            "media_files": ["image.jpg"],
                            "source_paths": {"image.jpg": "/path/image.jpg"},
                        }
                    ]
                }
            ],
            "file_hashes": {"image.jpg": "abc123"},
        }
        metadata_file.write_text(json.dumps(valid_metadata))

        result = ProcessorBase.load_metadata(
            metadata_file, validate=True, require_source_paths=True
        )
        assert result["export_info"]["export_username"] == "test"
        assert len(result["conversations"]) == 1

    def test_load_metadata_without_validation_succeeds_on_invalid_metadata(
        self, tmp_path: Path
    ):
        """Processor load_metadata should succeed without validation even with invalid metadata."""
        metadata_file = tmp_path / "metadata.json"
        invalid_metadata = {
            "conversations": [],
            # Missing export_info and file_hashes
        }
        metadata_file.write_text(json.dumps(invalid_metadata))

        # Should NOT raise when validate=False
        result = ProcessorBase.load_metadata(metadata_file, validate=False)
        assert result["conversations"] == []

    def test_load_metadata_validation_accepts_media_files_content_type(
        self, tmp_path: Path
    ):
        """Processor load_metadata should validate media_files content type correctly."""
        metadata_file = tmp_path / "metadata.json"
        valid_metadata = {
            "export_info": self._valid_export_info(),
            "media_files": [
                {
                    "filename": "photo.jpg",
                    "source_path": "/path/photo.jpg",
                    "timestamp": "2024-01-01",
                }
            ],
            "file_hashes": {"photo.jpg": "abc123"},
        }
        metadata_file.write_text(json.dumps(valid_metadata))

        result = ProcessorBase.load_metadata(
            metadata_file, validate=True, require_source_paths=True
        )
        assert result["export_info"]["export_username"] == "test"
        assert len(result["media_files"]) == 1

    def test_load_metadata_validation_accepts_media_content_type(self, tmp_path: Path):
        """Processor load_metadata should validate media content type correctly."""
        metadata_file = tmp_path / "metadata.json"
        valid_metadata = {
            "export_info": self._valid_export_info(),
            "media": [
                {
                    "media_files": ["post.jpg"],
                    "source_paths": {"post.jpg": "/path/post.jpg"},
                    "timestamp": "2024-01-01",
                }
            ],
            "file_hashes": {"post.jpg": "abc123"},
        }
        metadata_file.write_text(json.dumps(valid_metadata))

        result = ProcessorBase.load_metadata(
            metadata_file, validate=True, require_source_paths=True
        )
        assert result["export_info"]["export_username"] == "test"
        assert len(result["media"]) == 1
