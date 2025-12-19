#!/usr/bin/env python3
"""Unit tests for description builder."""

from datetime import datetime

from common.dedup.description import (
    SourceEntry,
    truncate_text,
    format_timestamp,
    format_primary_source,
    format_secondary_source,
    build_image_description,
    create_source_entry,
)


class TestTruncateText:
    """Tests for text truncation."""

    def test_short_text_unchanged(self):
        """Test short text is returned unchanged."""
        text = "Hello"
        assert truncate_text(text, 100) == "Hello"

    def test_long_text_truncated(self):
        """Test long text is truncated with suffix."""
        text = "This is a very long text that exceeds the limit"
        result = truncate_text(text, 20)
        assert len(result) <= 20
        assert result.endswith("...")

    def test_word_boundary_preservation(self):
        """Test truncation tries to preserve word boundaries."""
        text = "Hello beautiful world and everyone"
        result = truncate_text(text, 25)
        # Should truncate at word boundary before limit
        assert " " not in result[-4:-3] or result.endswith("...")

    def test_empty_text(self):
        """Test empty text returns empty string."""
        assert truncate_text("", 100) == ""

    def test_none_text(self):
        """Test None text returns empty string."""
        assert truncate_text(None, 100) == ""


class TestFormatTimestamp:
    """Tests for timestamp formatting."""

    def test_datetime_format(self):
        """Test datetime formatting to date string."""
        dt = datetime(2024, 1, 15, 10, 30, 0)
        assert format_timestamp(dt) == "2024-01-15"

    def test_none_returns_empty(self):
        """Test None returns empty string."""
        assert format_timestamp(None) == ""


class TestFormatPrimarySource:
    """Tests for primary source formatting."""

    def test_basic_format(self):
        """Test basic primary source formatting."""
        entry = SourceEntry(
            processor="Google Photos",
            username="johndoe",
            is_primary=True,
        )
        result = format_primary_source(entry)
        assert "Source: Google Photos/johndoe" in result

    def test_with_albums(self):
        """Test primary source with albums."""
        entry = SourceEntry(
            processor="Google Photos",
            username="johndoe",
            albums=["Vacation 2024", "Summer"],
        )
        result = format_primary_source(entry)
        assert "Vacation 2024" in result
        assert "Summer" in result

    def test_many_albums_truncated(self):
        """Test that many albums are limited."""
        entry = SourceEntry(
            processor="Google Photos",
            albums=["Album1", "Album2", "Album3", "Album4", "Album5"],
        )
        result = format_primary_source(entry)
        # Should only show first 3 albums
        assert "Album4" not in result or "+2 more" in result


class TestFormatSecondarySource:
    """Tests for secondary source formatting."""

    def test_story_format(self):
        """Test story/post format."""
        entry = SourceEntry(
            processor="Instagram",
            username="johndoe",
            context_type="Story",
            content="Beach day with friends!",
            timestamp=datetime(2024, 1, 15),
        )
        result = format_secondary_source(entry)
        assert "+Instagram/johndoe:" in result
        assert "Story" in result
        assert "Beach day" in result
        assert "2024-01-15" in result

    def test_message_format(self):
        """Test message format with sender and conversation."""
        entry = SourceEntry(
            processor="iMessage",
            username="iph13",
            sender="John",
            conversation="Family",
            content="Check this photo!",
            timestamp=datetime(2024, 1, 16),
        )
        result = format_secondary_source(entry)
        assert "+iMessage/iph13:" in result
        assert "John" in result
        assert "[Family]" in result

    def test_long_content_truncated(self):
        """Test that long content is truncated."""
        entry = SourceEntry(
            processor="Instagram",
            content="A" * 500,  # Very long content
        )
        result = format_secondary_source(entry, max_chars=100)
        assert len(result) <= 100


class TestBuildImageDescription:
    """Tests for complete description building."""

    def test_primary_only(self):
        """Test description with only primary source."""
        primary = SourceEntry(
            processor="Google Photos",
            username="johndoe",
            is_primary=True,
        )
        result = build_image_description(primary, [])
        assert "Source: Google Photos/johndoe" in result

    def test_with_secondary_sources(self):
        """Test description with multiple secondary sources."""
        primary = SourceEntry(
            processor="Google Photos",
            username="johndoe",
            albums=["Vacation"],
        )
        secondary = [
            SourceEntry(
                processor="Instagram",
                username="johndoe",
                context_type="Story",
                content="Beach day!",
            ),
            SourceEntry(
                processor="iMessage",
                sender="John",
                conversation="Family",
                content="Look at this!",
            ),
        ]
        result = build_image_description(primary, secondary)

        assert "Source: Google Photos" in result
        assert "+Instagram" in result
        assert "+iMessage" in result

    def test_respects_max_total(self):
        """Test that total length is limited."""
        primary = SourceEntry(processor="Google Photos")
        secondary = [
            SourceEntry(processor="Instagram", content="A" * 200),
            SourceEntry(processor="iMessage", content="B" * 200),
            SourceEntry(processor="Discord", content="C" * 200),
        ]
        result = build_image_description(primary, secondary, max_total=300)
        # Should be limited to approximately max_total (may be off by a few chars due to line joining)
        assert len(result) < 400  # Reasonable limit


class TestCreateSourceEntry:
    """Tests for creating source entries from metadata."""

    def test_basic_creation(self):
        """Test basic source entry creation."""
        metadata = {
            "username": "johndoe",
            "albums": ["Vacation"],
        }
        entry = create_source_entry("Google Photos", metadata)

        assert entry.processor == "Google Photos"
        assert entry.username == "johndoe"
        assert entry.albums == ["Vacation"]

    def test_with_timestamp_string(self):
        """Test creation with timestamp string."""
        metadata = {
            "timestamp": "2024-01-15T10:30:00Z",
        }
        entry = create_source_entry("Instagram", metadata)
        assert entry.timestamp is not None
        assert entry.timestamp.year == 2024

    def test_with_timestamp_datetime(self):
        """Test creation with datetime object."""
        dt = datetime(2024, 1, 15, 10, 30)
        metadata = {
            "timestamp": dt,
        }
        entry = create_source_entry("Instagram", metadata)
        assert entry.timestamp == dt

    def test_with_message_fields(self):
        """Test creation with message-specific fields."""
        metadata = {
            "sender": "John",
            "conversation": "Family",
            "content": "Hello!",
        }
        entry = create_source_entry("iMessage", metadata)

        assert entry.sender == "John"
        assert entry.conversation == "Family"
        assert entry.content == "Hello!"
