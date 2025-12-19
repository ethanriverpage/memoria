#!/usr/bin/env python3
"""Unit tests for FilenameGenerator class in common/utils.py."""


from common.utils import FilenameGenerator


class TestFilenameGenerator:
    """Tests for FilenameGenerator class."""

    def test_basic_filename_generation(self):
        """Test basic filename generation with valid inputs."""
        gen = FilenameGenerator("gchat")
        result = gen.generate("2024-01-15 10:30:00", "user-general", ".jpg")

        assert result == "gchat-user-general-20240115.jpg"

    def test_different_prefixes(self):
        """Test that different prefixes work correctly."""
        gen1 = FilenameGenerator("insta")
        gen2 = FilenameGenerator("snpmem")

        assert gen1.generate("2024-01-15 10:30:00", "user", ".jpg").startswith("insta-")
        assert gen2.generate("2024-01-15 10:30:00", "user", ".jpg").startswith(
            "snpmem-"
        )

    def test_none_timestamp_uses_fallback(self):
        """Test that None timestamp produces 00000000 date."""
        gen = FilenameGenerator("gchat")
        result = gen.generate(None, "user-general", ".jpg")

        assert result == "gchat-user-general-00000000.jpg"

    def test_empty_timestamp_uses_fallback(self):
        """Test that empty string timestamp produces 00000000 date."""
        gen = FilenameGenerator("gchat")
        result = gen.generate("", "user-general", ".jpg")

        assert result == "gchat-user-general-00000000.jpg"

    def test_invalid_timestamp_uses_fallback(self):
        """Test that unparseable timestamp produces 00000000 date."""
        gen = FilenameGenerator("gchat")
        result = gen.generate("not-a-date", "user-general", ".jpg")

        assert result == "gchat-user-general-00000000.jpg"

    def test_custom_timestamp_format(self):
        """Test parsing with custom timestamp format."""
        gen = FilenameGenerator("test", timestamp_format="%Y/%m/%d")
        result = gen.generate("2024/01/15", "user", ".jpg")

        assert result == "test-user-20240115.jpg"

    def test_uniqueness_adds_sequence_number(self):
        """Test that duplicate base filenames get sequence numbers."""
        gen = FilenameGenerator("gchat")

        result1 = gen.generate("2024-01-15 10:30:00", "user-general", ".jpg")
        result2 = gen.generate("2024-01-15 11:00:00", "user-general", ".jpg")
        result3 = gen.generate("2024-01-15 12:00:00", "user-general", ".jpg")

        assert result1 == "gchat-user-general-20240115.jpg"
        assert result2 == "gchat-user-general-20240115_2.jpg"
        assert result3 == "gchat-user-general-20240115_3.jpg"

    def test_sequence_start_configuration(self):
        """Test that sequence_start is respected."""
        gen = FilenameGenerator("gchat", sequence_start=10)

        result1 = gen.generate("2024-01-15 10:30:00", "user", ".jpg")
        result2 = gen.generate("2024-01-15 10:30:00", "user", ".jpg")

        assert result1 == "gchat-user-20240115.jpg"
        assert result2 == "gchat-user-20240115_10.jpg"

    def test_different_extensions_are_unique(self):
        """Test that different extensions produce unique filenames."""
        gen = FilenameGenerator("gchat")

        result1 = gen.generate("2024-01-15 10:30:00", "user", ".jpg")
        result2 = gen.generate("2024-01-15 10:30:00", "user", ".png")

        assert result1 == "gchat-user-20240115.jpg"
        assert result2 == "gchat-user-20240115.png"

    def test_different_identifiers_are_unique(self):
        """Test that different identifiers produce unique filenames."""
        gen = FilenameGenerator("gchat")

        result1 = gen.generate("2024-01-15 10:30:00", "user-general", ".jpg")
        result2 = gen.generate("2024-01-15 10:30:00", "user-random", ".jpg")

        assert result1 == "gchat-user-general-20240115.jpg"
        assert result2 == "gchat-user-random-20240115.jpg"

    def test_different_dates_are_unique(self):
        """Test that different dates produce unique filenames."""
        gen = FilenameGenerator("gchat")

        result1 = gen.generate("2024-01-15 10:30:00", "user", ".jpg")
        result2 = gen.generate("2024-01-16 10:30:00", "user", ".jpg")

        assert result1 == "gchat-user-20240115.jpg"
        assert result2 == "gchat-user-20240116.jpg"

    def test_used_filenames_tracking(self):
        """Test that used_filenames set is properly maintained."""
        gen = FilenameGenerator("gchat")

        gen.generate("2024-01-15 10:30:00", "user", ".jpg")
        gen.generate("2024-01-15 10:30:00", "user", ".jpg")

        assert "gchat-user-20240115.jpg" in gen.used_filenames
        assert "gchat-user-20240115_2.jpg" in gen.used_filenames
        assert len(gen.used_filenames) == 2

    def test_video_extensions(self):
        """Test that video extensions work correctly."""
        gen = FilenameGenerator("gchat")

        result = gen.generate("2024-01-15 10:30:00", "user", ".mp4")

        assert result == "gchat-user-20240115.mp4"
        assert result.endswith(".mp4")

    def test_many_duplicates(self):
        """Test handling of many duplicate filenames."""
        gen = FilenameGenerator("gchat")

        results = []
        for _ in range(100):
            results.append(gen.generate("2024-01-15 10:30:00", "user", ".jpg"))

        # All results should be unique
        assert len(set(results)) == 100

        # First should have no suffix, rest should have sequential suffixes
        assert results[0] == "gchat-user-20240115.jpg"
        assert results[1] == "gchat-user-20240115_2.jpg"
        assert results[99] == "gchat-user-20240115_100.jpg"

    def test_complex_identifier(self):
        """Test with complex identifier containing hyphens."""
        gen = FilenameGenerator("instagram-messages")
        result = gen.generate("2024-01-15 10:30:00", "user-john-doe-chat", ".jpg")

        assert result == "instagram-messages-user-john-doe-chat-20240115.jpg"

    def test_empty_extension_no_error(self):
        """Test that empty extension does not cause ValueError (empty separator bug)."""
        gen = FilenameGenerator("insta")

        # First file with no extension
        result1 = gen.generate("2024-01-15 10:30:00", "user", "")
        assert result1 == "insta-user-20240115"

        # Second file with no extension - should get sequence number, not crash
        result2 = gen.generate("2024-01-15 10:30:00", "user", "")
        assert result2 == "insta-user-20240115_2"

        # Third file with no extension
        result3 = gen.generate("2024-01-15 10:30:00", "user", "")
        assert result3 == "insta-user-20240115_3"

    def test_empty_extension_uniqueness(self):
        """Test that files with and without extensions are tracked separately."""
        gen = FilenameGenerator("insta")

        result1 = gen.generate("2024-01-15 10:30:00", "user", ".jpg")
        result2 = gen.generate("2024-01-15 10:30:00", "user", "")

        assert result1 == "insta-user-20240115.jpg"
        assert result2 == "insta-user-20240115"
        # Both should be unique (different because one has extension, one doesn't)
