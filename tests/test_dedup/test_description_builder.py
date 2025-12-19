#!/usr/bin/env python3
"""Unit tests for description builder."""

from datetime import datetime

from common.dedup.description import (
    # Constants
    PLATFORM_GOOGLE,
    PLATFORM_INSTAGRAM,
    PLATFORM_SNAPCHAT,
    PLATFORM_DISCORD,
    PLATFORM_IMESSAGE,
    SERVICE_PHOTOS,
    SERVICE_CHAT,
    SERVICE_MESSAGES,
    SERVICE_MEMORIES,
    SERVICE_STORIES,
    # Data structures
    SourceEntry,
    # Core building functions
    build_source_line,
    build_message_context,
    build_description,
    build_image_description,
    # Helper functions
    truncate_text,
    format_timestamp,
    format_primary_source,
    format_secondary_source,
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
    """Tests for primary source formatting using unified format."""

    def test_basic_format(self):
        """Test basic primary source formatting."""
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="johndoe",
            service=SERVICE_PHOTOS,
            is_primary=True,
        )
        result = format_primary_source(entry)
        assert result == "Source: Google/johndoe/photos"

    def test_without_service(self):
        """Test primary source without service."""
        entry = SourceEntry(
            processor=PLATFORM_DISCORD,
            username="johndoe",
        )
        result = format_primary_source(entry)
        assert result == "Source: Discord/johndoe"

    def test_processor_only(self):
        """Test primary source with processor only (minimal entry)."""
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
        )
        result = format_primary_source(entry)
        assert result == "Source: Google"


class TestFormatSecondarySource:
    """Tests for secondary source formatting using unified format."""

    def test_message_format_with_service(self):
        """Test message format with service path."""
        entry = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username="johndoe",
            service=SERVICE_MESSAGES,
            sender="jane_doe",
            conversation="Best Friends",
            content="Beach day with friends!",
            timestamp=datetime(2024, 1, 15),
        )
        result = format_secondary_source(entry)
        assert "+Instagram/johndoe/messages:" in result
        assert 'jane_doe in "Best Friends"' in result
        assert "Beach day" in result
        assert "(2024-01-15)" in result

    def test_message_format_without_service(self):
        """Test message format without service."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="mac",
            sender="John",
            conversation="Family",
            content="Check this photo!",
            timestamp=datetime(2024, 1, 16),
        )
        result = format_secondary_source(entry)
        assert "+iMessage/mac:" in result
        assert 'John in "Family"' in result
        assert "(2024-01-16)" in result

    def test_long_content_truncated(self):
        """Test that long content is truncated."""
        entry = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            content="A" * 500,  # Very long content
        )
        result = format_secondary_source(entry, max_chars=100)
        assert len(result) <= 100


class TestBuildImageDescription:
    """Tests for complete description building (multi-source dedup)."""

    def test_primary_only(self):
        """Test description with only primary source."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="johndoe",
            service=SERVICE_PHOTOS,
            is_primary=True,
        )
        result = build_image_description(primary, [])
        assert result == "Source: Google/johndoe/photos"

    def test_with_secondary_sources(self):
        """Test description with multiple secondary sources."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="johndoe",
            service=SERVICE_PHOTOS,
        )
        secondary = [
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="johndoe",
                service=SERVICE_MESSAGES,
                sender="jane",
                conversation="Best Friends",
                content="Beach day!",
            ),
            SourceEntry(
                processor=PLATFORM_IMESSAGE,
                username="mac",
                sender="John",
                conversation="Family",
                content="Look at this!",
            ),
        ]
        result = build_image_description(primary, secondary)

        assert "Source: Google/johndoe/photos" in result
        assert "+Instagram/johndoe/messages:" in result
        assert "+iMessage/mac:" in result

    def test_respects_max_total(self):
        """Test that total length is limited."""
        primary = SourceEntry(processor=PLATFORM_GOOGLE)
        secondary = [
            SourceEntry(processor=PLATFORM_INSTAGRAM, content="A" * 200),
            SourceEntry(processor=PLATFORM_IMESSAGE, content="B" * 200),
            SourceEntry(processor=PLATFORM_DISCORD, content="C" * 200),
        ]
        result = build_image_description(primary, secondary, max_total=300)
        # Should be limited to approximately max_total
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

    def test_with_service_parameter(self):
        """Test creation with service parameter."""
        metadata = {"username": "johndoe"}
        entry = create_source_entry(
            PLATFORM_GOOGLE, metadata, is_primary=True, service=SERVICE_PHOTOS
        )
        assert entry.processor == PLATFORM_GOOGLE
        assert entry.service == SERVICE_PHOTOS
        assert entry.is_primary is True


class TestBuildSourceLine:
    """Tests for build_source_line function."""

    def test_full_path(self):
        """Test source line with platform/username/service."""
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="john.doe@gmail.com",
            service=SERVICE_PHOTOS,
        )
        result = build_source_line(entry)
        assert result == "Source: Google/john.doe@gmail.com/photos"

    def test_without_service(self):
        """Test source line without service."""
        entry = SourceEntry(
            processor=PLATFORM_DISCORD,
            username="johndoe",
        )
        result = build_source_line(entry)
        assert result == "Source: Discord/johndoe"

    def test_imessage_format(self):
        """Test iMessage format with device identifier."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="mac",
        )
        result = build_source_line(entry)
        assert result == "Source: iMessage/mac"

    def test_snapchat_memories(self):
        """Test Snapchat memories format."""
        entry = SourceEntry(
            processor=PLATFORM_SNAPCHAT,
            username="johndoe",
            service=SERVICE_MEMORIES,
        )
        result = build_source_line(entry)
        assert result == "Source: Snapchat/johndoe/memories"

    def test_instagram_stories(self):
        """Test Instagram stories format."""
        entry = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username="johndoe",
            service=SERVICE_STORIES,
        )
        result = build_source_line(entry)
        assert result == "Source: Instagram/johndoe/stories"


class TestBuildMessageContext:
    """Tests for build_message_context function."""

    def test_full_context(self):
        """Test message context with sender, conversation, and content."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            sender="John Doe",
            conversation="Family Group",
            content="Check out this photo!",
        )
        result = build_message_context(entry)
        assert result == '  - John Doe in "Family Group": "Check out this photo!"'

    def test_sender_only(self):
        """Test message context with sender only."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            sender="John Doe",
        )
        result = build_message_context(entry)
        assert result == "  - John Doe"

    def test_conversation_only(self):
        """Test message context with conversation only."""
        entry = SourceEntry(
            processor=PLATFORM_DISCORD,
            conversation="#photos in Gaming Server",
        )
        result = build_message_context(entry)
        assert result == '  - in "#photos in Gaming Server"'

    def test_content_only(self):
        """Test message context with content only (no sender or conversation)."""
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            content="A caption for this photo",
        )
        result = build_message_context(entry)
        # Should NOT have a colon before content when there's no sender/conversation
        assert result == '  - "A caption for this photo"'

    def test_empty_context(self):
        """Test empty context returns empty string."""
        entry = SourceEntry(processor=PLATFORM_GOOGLE)
        result = build_message_context(entry)
        assert result == ""

    def test_content_truncation(self):
        """Test that long content is truncated."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            sender="John",
            conversation="Family",
            content="A" * 200,
        )
        result = build_message_context(entry, max_content=50)
        assert len(result) < 200


class TestBuildDescription:
    """Tests for build_description function (single-source processor use)."""

    def test_photos_description(self):
        """Test Google Photos description."""
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="john.doe@gmail.com",
            service=SERVICE_PHOTOS,
        )
        result = build_description(entry)
        assert result == "Source: Google/john.doe@gmail.com/photos"

    def test_message_description(self):
        """Test message description with context."""
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="john.doe@gmail.com",
            service=SERVICE_CHAT,
            sender="John Doe",
            conversation="Family Group",
            content="Check out this photo!",
        )
        result = build_description(entry)
        assert "Source: Google/john.doe@gmail.com/chat" in result
        assert '  - John Doe in "Family Group": "Check out this photo!"' in result
        assert "&#xa;" in result  # Line separator

    def test_discord_description(self):
        """Test Discord description."""
        entry = SourceEntry(
            processor=PLATFORM_DISCORD,
            username="johndoe",
            conversation="#photos in Gaming Server",
            content="New screenshot",
        )
        result = build_description(entry)
        assert "Source: Discord/johndoe" in result
        assert '  - in "#photos in Gaming Server": "New screenshot"' in result

    def test_imessage_description(self):
        """Test iMessage description."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="mac",
            sender="John Doe",
            conversation="DM with Jane",
            content="Sending you that pic",
        )
        result = build_description(entry)
        assert "Source: iMessage/mac" in result
        assert '  - John Doe in "DM with Jane": "Sending you that pic"' in result
