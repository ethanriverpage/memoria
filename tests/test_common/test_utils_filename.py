#!/usr/bin/env python3
"""Unit tests for `common.files.naming.FilenameGenerator`."""

import pytest

from common.files.naming import FilenameGenerator
from common.validation.invariants import InvariantViolation


class TestFilenameGenerator:
    """Tests for FilenameGenerator class."""

    def test_single_service_filename(self):
        gen = FilenameGenerator("discord", "ethriverpage")
        result = gen.generate("2024-01-15 10:30:00", ".jpg")
        assert result == "discord-ethriverpage-20240115.jpg"

    def test_multi_service_filename(self):
        gen = FilenameGenerator("google", "ethriverpage", service="photos")
        result = gen.generate("2024-01-15 10:30:00", ".jpg")
        assert result == "google-ethriverpage-photos-20240115.jpg"

    def test_none_timestamp_raises_invariant_violation(self):
        """Missing timestamp is a bug in calling code - must raise."""
        gen = FilenameGenerator("google", "ethriverpage", service="chat")
        with pytest.raises(InvariantViolation, match="missing timestamp"):
            gen.generate(None, ".jpg")

    def test_empty_timestamp_raises_invariant_violation(self):
        """Empty timestamp is a bug in calling code - must raise."""
        gen = FilenameGenerator("google", "ethriverpage", service="chat")
        with pytest.raises(InvariantViolation, match="missing timestamp"):
            gen.generate("", ".jpg")

    def test_invalid_timestamp_raises_invariant_violation(self):
        """Unparseable timestamp is a bug in calling code - must raise."""
        gen = FilenameGenerator("google", "ethriverpage", service="chat")
        with pytest.raises(InvariantViolation, match="unparseable timestamp"):
            gen.generate("not-a-date", ".jpg")

    def test_custom_timestamp_format(self):
        gen = FilenameGenerator("imessage", "m3mbp", timestamp_format="%Y/%m/%d")
        result = gen.generate("2024/01/15", ".jpg")
        assert result == "imessage-m3mbp-20240115.jpg"

    def test_uniqueness_adds_sequence_number(self):
        gen = FilenameGenerator("snap", "ethriverpage", service="msgs")

        r1 = gen.generate("2024-01-15 10:30:00", ".jpg")
        r2 = gen.generate("2024-01-15 11:00:00", ".jpg")
        r3 = gen.generate("2024-01-15 12:00:00", ".jpg")

        assert r1 == "snap-ethriverpage-msgs-20240115.jpg"
        assert r2 == "snap-ethriverpage-msgs-20240115_2.jpg"
        assert r3 == "snap-ethriverpage-msgs-20240115_3.jpg"

    def test_sequence_start_configuration(self):
        gen = FilenameGenerator(
            "snap", "ethriverpage", service="msgs", sequence_start=10
        )

        r1 = gen.generate("2024-01-15 10:30:00", ".jpg")
        r2 = gen.generate("2024-01-15 10:30:00", ".jpg")

        assert r1 == "snap-ethriverpage-msgs-20240115.jpg"
        assert r2 == "snap-ethriverpage-msgs-20240115_10.jpg"

    def test_different_extensions_are_unique(self):
        gen = FilenameGenerator("google", "ethriverpage", service="photos")

        r1 = gen.generate("2024-01-15 10:30:00", ".jpg")
        r2 = gen.generate("2024-01-15 10:30:00", ".png")

        assert r1 == "google-ethriverpage-photos-20240115.jpg"
        assert r2 == "google-ethriverpage-photos-20240115.png"

    def test_different_dates_are_unique(self):
        gen = FilenameGenerator("google", "ethriverpage", service="photos")

        r1 = gen.generate("2024-01-15 10:30:00", ".jpg")
        r2 = gen.generate("2024-01-16 10:30:00", ".jpg")

        assert r1 == "google-ethriverpage-photos-20240115.jpg"
        assert r2 == "google-ethriverpage-photos-20240116.jpg"

    def test_used_filenames_tracking(self):
        gen = FilenameGenerator("google", "ethriverpage", service="photos")

        gen.generate("2024-01-15 10:30:00", ".jpg")
        gen.generate("2024-01-15 10:30:00", ".jpg")

        assert "google-ethriverpage-photos-20240115.jpg" in gen.used_filenames
        assert "google-ethriverpage-photos-20240115_2.jpg" in gen.used_filenames
        assert len(gen.used_filenames) == 2

    def test_video_extensions(self):
        gen = FilenameGenerator("google", "ethriverpage", service="photos")

        result = gen.generate("2024-01-15 10:30:00", ".mp4")

        assert result == "google-ethriverpage-photos-20240115.mp4"
        assert result.endswith(".mp4")

    def test_many_duplicates(self):
        gen = FilenameGenerator("insta", "ethriverpage", service="posts")

        results = [gen.generate("2024-01-15 10:30:00", ".jpg") for _ in range(100)]

        assert len(set(results)) == 100
        assert results[0] == "insta-ethriverpage-posts-20240115.jpg"
        assert results[1] == "insta-ethriverpage-posts-20240115_2.jpg"
        assert results[99] == "insta-ethriverpage-posts-20240115_100.jpg"

    def test_empty_extension_no_error(self):
        gen = FilenameGenerator("insta", "ethriverpage", service="posts")

        r1 = gen.generate("2024-01-15 10:30:00", "")
        r2 = gen.generate("2024-01-15 10:30:00", "")
        r3 = gen.generate("2024-01-15 10:30:00", "")

        assert r1 == "insta-ethriverpage-posts-20240115"
        assert r2 == "insta-ethriverpage-posts-20240115_2"
        assert r3 == "insta-ethriverpage-posts-20240115_3"

    def test_empty_extension_uniqueness(self):
        gen = FilenameGenerator("insta", "ethriverpage", service="posts")

        r1 = gen.generate("2024-01-15 10:30:00", ".jpg")
        r2 = gen.generate("2024-01-15 10:30:00", "")

        assert r1 == "insta-ethriverpage-posts-20240115.jpg"
        assert r2 == "insta-ethriverpage-posts-20240115"
        assert r1 != r2

    # ========================================================================
    # Filename Sanitization Tests
    # ========================================================================

    def test_sanitizes_unicode_apostrophe_in_username(self):
        """Unicode curly apostrophe should be stripped from username."""
        # This was the real-world case: "Ethan's iPhone" with Unicode apostrophe
        gen = FilenameGenerator("imessage", "Ethan\u2019s iPhone")
        result = gen.generate("2020-08-27 10:30:00", ".png")
        assert result == "imessage-ethans_iphone-20200827.png"

    def test_sanitizes_whitespace_in_username(self):
        """Whitespace in username should be converted to underscores."""
        gen = FilenameGenerator("imessage", "My Device Name")
        result = gen.generate("2024-01-15 10:30:00", ".jpg")
        assert result == "imessage-my_device_name-20240115.jpg"

    def test_sanitizes_uppercase_to_lowercase(self):
        """Uppercase characters should be converted to lowercase."""
        gen = FilenameGenerator("Discord", "UserName", service="Photos")
        result = gen.generate("2024-01-15 10:30:00", ".jpg")
        assert result == "discord-username-photos-20240115.jpg"

    def test_sanitizes_special_characters(self):
        """Special characters should be removed from components."""
        gen = FilenameGenerator("plat@form!", "user<name>", service="serv:ice")
        result = gen.generate("2024-01-15 10:30:00", ".jpg")
        assert result == "platform-username-service-20240115.jpg"

    def test_sanitizes_path_separators(self):
        """Path separators should be removed from components."""
        gen = FilenameGenerator("plat/form", "user\\name")
        result = gen.generate("2024-01-15 10:30:00", ".jpg")
        assert result == "platform-username-20240115.jpg"

    def test_sanitizes_collapses_multiple_underscores(self):
        """Multiple consecutive underscores should be collapsed."""
        gen = FilenameGenerator("test", "user   name")  # multiple spaces
        result = gen.generate("2024-01-15 10:30:00", ".jpg")
        assert result == "test-user_name-20240115.jpg"

    def test_sanitized_components_accessible(self):
        """Sanitized values should be stored in instance attributes."""
        gen = FilenameGenerator("Discord", "Ethan\u2019s iPhone", service="Messages")
        assert gen.platform == "discord"
        assert gen.username == "ethans_iphone"
        assert gen.service == "messages"
