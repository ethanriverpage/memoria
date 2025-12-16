"""
Multi-processor detection tests.

Tests cover scenarios where a single export directory contains
data for multiple processors (e.g., Google export with Photos + Chat + Voice).
"""



from tests.fixtures.generators import (
    create_google_photos_export,
    create_google_chat_export,
    create_google_voice_export,
    create_snapchat_memories_export,
    create_snapchat_messages_export,
    create_instagram_messages_export,
    create_instagram_public_export,
)


class TestGoogleMultiProcessor:
    """Tests for Google multi-processor detection."""

    def test_detect_photos_chat_voice(self, processor_registry, temp_export_dir):
        """Should detect all three Google processors in combined export."""
        create_google_photos_export(temp_export_dir)
        create_google_chat_export(temp_export_dir)
        create_google_voice_export(temp_export_dir)

        matches = processor_registry.detect_all(temp_export_dir)
        names = [p.get_name() for p in matches]

        assert "Google Photos" in names
        assert "Google Chat" in names
        assert "Google Voice" in names

    def test_detect_photos_and_chat(self, processor_registry, temp_export_dir):
        """Should detect Photos and Chat without Voice."""
        create_google_photos_export(temp_export_dir)
        create_google_chat_export(temp_export_dir)

        matches = processor_registry.detect_all(temp_export_dir)
        names = [p.get_name() for p in matches]

        assert "Google Photos" in names
        assert "Google Chat" in names
        assert "Google Voice" not in names

    def test_detect_chat_and_voice(self, processor_registry, temp_export_dir):
        """Should detect Chat and Voice without Photos."""
        create_google_chat_export(temp_export_dir)
        create_google_voice_export(temp_export_dir)

        matches = processor_registry.detect_all(temp_export_dir)
        names = [p.get_name() for p in matches]

        assert "Google Photos" not in names
        assert "Google Chat" in names
        assert "Google Voice" in names


class TestSnapchatMultiProcessor:
    """Tests for Snapchat multi-processor detection."""

    def test_detect_memories_and_messages_consolidated(self, processor_registry, temp_export_dir):
        """Should detect both Memories and Messages in consolidated export."""
        # Create consolidated structure with memories/ and messages/ subdirs
        memories_dir = temp_export_dir / "memories"
        messages_dir = temp_export_dir / "messages"

        create_snapchat_memories_export(memories_dir)
        create_snapchat_messages_export(messages_dir, raw_format=True)

        matches = processor_registry.detect_all(temp_export_dir)
        names = [p.get_name() for p in matches]

        assert "Snapchat Memories" in names
        assert "Snapchat Messages" in names

    def test_detect_memories_only(self, processor_registry, temp_export_dir):
        """Should detect only Memories when Messages not present."""
        create_snapchat_memories_export(temp_export_dir)

        matches = processor_registry.detect_all(temp_export_dir)
        names = [p.get_name() for p in matches]

        assert "Snapchat Memories" in names
        assert "Snapchat Messages" not in names

    def test_detect_messages_only(self, processor_registry, temp_export_dir):
        """Should detect only Messages when Memories not present."""
        create_snapchat_messages_export(temp_export_dir, raw_format=True)

        matches = processor_registry.detect_all(temp_export_dir)
        names = [p.get_name() for p in matches]

        assert "Snapchat Memories" not in names
        assert "Snapchat Messages" in names


class TestInstagramMultiProcessor:
    """Tests for Instagram multi-processor detection."""

    def test_detect_messages_and_public(self, processor_registry, temp_export_dir):
        """Should detect both Messages and Public Media."""
        create_instagram_messages_export(temp_export_dir)
        create_instagram_public_export(temp_export_dir)

        matches = processor_registry.detect_all(temp_export_dir)
        names = [p.get_name() for p in matches]

        assert "Instagram Messages" in names
        assert "Instagram Public Media" in names

    def test_detect_messages_only(self, processor_registry, temp_export_dir):
        """Should detect only Messages when Public Media not present."""
        create_instagram_messages_export(temp_export_dir)

        matches = processor_registry.detect_all(temp_export_dir)
        names = [p.get_name() for p in matches]

        assert "Instagram Messages" in names
        assert "Instagram Public Media" not in names


class TestProcessorPriority:
    """Tests for processor priority ordering."""

    def test_processors_sorted_by_priority(self, processor_registry, temp_export_dir):
        """Should return processors sorted by priority (highest first)."""
        # Create export that matches multiple processors
        create_google_photos_export(temp_export_dir)
        create_google_chat_export(temp_export_dir)

        matches = processor_registry.detect_all(temp_export_dir)

        # Verify sorted by priority (descending)
        priorities = [p.get_priority() for p in matches]
        assert priorities == sorted(priorities, reverse=True)

    def test_high_priority_first(self, processor_registry):
        """Should have higher priority processors first in registry."""
        all_processors = processor_registry.get_all_processors()

        # Verify sorted by priority
        priorities = [p.get_priority() for p in all_processors]
        assert priorities == sorted(priorities, reverse=True)


class TestNoMatch:
    """Tests for no-match scenarios."""

    def test_empty_directory(self, processor_registry, temp_export_dir):
        """Should return empty list for empty directory."""
        matches = processor_registry.detect_all(temp_export_dir)
        assert len(matches) == 0

    def test_unrecognized_structure(self, processor_registry, temp_export_dir):
        """Should return empty list for unrecognized structure."""
        # Create some random files that don't match any processor
        (temp_export_dir / "random_file.txt").write_text("test")
        (temp_export_dir / "another_file.json").write_text("{}")
        random_dir = temp_export_dir / "random_dir"
        random_dir.mkdir()
        (random_dir / "nested.txt").write_text("nested")

        matches = processor_registry.detect_all(temp_export_dir)
        assert len(matches) == 0


class TestProcessorCount:
    """Tests for processor count validation."""

    def test_at_least_10_processors(self, processor_registry):
        """Should have at least 10 registered processors."""
        count = processor_registry.get_processor_count()
        assert count >= 10

    def test_all_processors_have_required_methods(self, processor_registry):
        """All processors should implement required methods."""
        for processor in processor_registry.get_all_processors():
            assert hasattr(processor, "detect")
            assert hasattr(processor, "get_name")
            assert hasattr(processor, "get_priority")
            assert hasattr(processor, "process")

            # Verify methods are callable
            assert callable(processor.detect)
            assert callable(processor.get_name)
            assert callable(processor.get_priority)
            assert callable(processor.process)

            # Verify get_name returns string
            name = processor.get_name()
            assert isinstance(name, str)
            assert len(name) > 0

            # Verify get_priority returns int
            priority = processor.get_priority()
            assert isinstance(priority, int)
            assert 1 <= priority <= 100

