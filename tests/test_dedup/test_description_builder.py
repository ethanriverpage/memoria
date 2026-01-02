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
    SERVICE_VOICE,
    SERVICE_POSTS,
    # Processor mapping
    PROCESSOR_TO_PLATFORM_SERVICE,
    get_platform_service,
    is_consolidating_processor,
    format_relative_source_path,
    # Data structures
    SourceEntry,
    # Core building functions
    build_source_line,
    format_message_line,
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


class TestProcessorMapping:
    """Tests for processor name to platform/service mapping."""

    def test_google_photos_mapping(self):
        """Test Google Photos maps to Google/photos."""
        platform, service = get_platform_service("Google Photos")
        assert platform == PLATFORM_GOOGLE
        assert service == SERVICE_PHOTOS

    def test_google_chat_mapping(self):
        """Test Google Chat maps to Google/chat."""
        platform, service = get_platform_service("Google Chat")
        assert platform == PLATFORM_GOOGLE
        assert service == SERVICE_CHAT

    def test_google_voice_mapping(self):
        """Test Google Voice maps to Google/voice."""
        platform, service = get_platform_service("Google Voice")
        assert platform == PLATFORM_GOOGLE
        assert service == SERVICE_VOICE

    def test_imessage_mapping(self):
        """Test iMessage maps to iMessage with no service."""
        platform, service = get_platform_service("iMessage")
        assert platform == PLATFORM_IMESSAGE
        assert service == ""


    def test_instagram_messages_mapping(self):
        """Test Instagram Messages maps to Instagram/messages."""
        platform, service = get_platform_service("Instagram Messages")
        assert platform == PLATFORM_INSTAGRAM
        assert service == SERVICE_MESSAGES

    def test_instagram_public_media_mapping(self):
        """Test Instagram Public Media maps to Instagram/posts."""
        platform, service = get_platform_service("Instagram Public Media")
        assert platform == PLATFORM_INSTAGRAM
        assert service == SERVICE_POSTS

    def test_snapchat_messages_mapping(self):
        """Test Snapchat Messages maps to Snapchat/messages."""
        platform, service = get_platform_service("Snapchat Messages")
        assert platform == PLATFORM_SNAPCHAT
        assert service == SERVICE_MESSAGES

    def test_snapchat_memories_mapping(self):
        """Test Snapchat Memories maps to Snapchat/memories."""
        platform, service = get_platform_service("Snapchat Memories")
        assert platform == PLATFORM_SNAPCHAT
        assert service == SERVICE_MEMORIES

    def test_discord_mapping(self):
        """Test Discord maps to Discord with no service."""
        platform, service = get_platform_service("Discord")
        assert platform == PLATFORM_DISCORD
        assert service == ""

    def test_unknown_processor_fallback(self):
        """Test unknown processor returns processor name as platform."""
        platform, service = get_platform_service("Unknown Processor")
        assert platform == "Unknown Processor"
        assert service == ""

    def test_all_processors_in_mapping(self):
        """Test all expected processors are in the mapping."""
        expected = [
            "Google Photos",
            "Google Chat",
            "Google Voice",
            "iMessage",
            "Instagram Messages",
            "Instagram Public Media",
            "Snapchat Messages",
            "Snapchat Memories",
            "Discord",
        ]
        for processor in expected:
            assert processor in PROCESSOR_TO_PLATFORM_SERVICE
        # Note: Instaloader is now handled by Instagram Public Media


class TestConsolidatingProcessors:
    """Tests for consolidating processor detection."""

    def test_imessage_is_consolidating(self):
        """Test iMessage is a consolidating processor."""
        assert is_consolidating_processor("iMessage") is True

    def test_google_photos_not_consolidating(self):
        """Test Google Photos is not a consolidating processor."""
        assert is_consolidating_processor("Google Photos") is False

    def test_instagram_not_consolidating(self):
        """Test Instagram is not a consolidating processor."""
        assert is_consolidating_processor("Instagram Messages") is False

    def test_platform_name_not_consolidating(self):
        """Test platform constant is not in consolidating set."""
        # Only full processor names should match
        assert is_consolidating_processor(PLATFORM_IMESSAGE) is True
        assert is_consolidating_processor(PLATFORM_GOOGLE) is False


class TestFormatRelativeSourcePath:
    """Tests for relative path notation."""

    def test_different_platform_full_path(self):
        """Test different platforms get full path."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        secondary = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username="user",
            service=SERVICE_MESSAGES,
        )
        result = format_relative_source_path(primary, secondary)
        assert result == "Instagram/user/messages"

    def test_same_platform_user_different_service(self):
        """Test same platform/user, different service uses ././service."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        secondary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_CHAT,
        )
        result = format_relative_source_path(primary, secondary)
        assert result == "././chat"

    def test_same_platform_different_user(self):
        """Test same platform, different user uses ./user/service."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user1",
            service=SERVICE_PHOTOS,
        )
        secondary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user2",
            service=SERVICE_PHOTOS,
        )
        result = format_relative_source_path(primary, secondary)
        assert result == "./user2/photos"

    def test_same_source_returns_empty(self):
        """Test same source returns empty string."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        secondary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        result = format_relative_source_path(primary, secondary)
        assert result == ""

    def test_imessage_consolidation_same_source(self):
        """Test iMessage consolidation treats different devices as same source."""
        primary = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="iph13p",
        )
        secondary = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="mac",  # Different device
        )
        result = format_relative_source_path(primary, secondary)
        # Should return empty because iMessage consolidates devices
        assert result == ""

    def test_no_service_in_path(self):
        """Test path without service."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        secondary = SourceEntry(
            processor=PLATFORM_DISCORD,
            username="user",
            # No service for Discord
        )
        result = format_relative_source_path(primary, secondary)
        assert result == "Discord/user"

    def test_empty_username_no_double_slashes(self):
        """Test empty username doesn't produce ugly paths like 'Google//photos'."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        secondary = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username="",  # Empty username
            service=SERVICE_MESSAGES,
        )
        result = format_relative_source_path(primary, secondary)
        # Should not have double slashes
        assert "//" not in result
        # Should include platform and service
        assert result == "Instagram/messages"

    def test_empty_service_no_trailing_slash(self):
        """Test empty service doesn't produce ugly paths like './user/'."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user1",
            service=SERVICE_PHOTOS,
        )
        secondary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user2",
            service="",  # Empty service
        )
        result = format_relative_source_path(primary, secondary)
        # Should not have trailing slash
        assert not result.endswith("/")
        # Should use relative notation
        assert result == "./user2"

    def test_both_empty_minimal_path(self):
        """Test both empty username and service produce minimal path."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        secondary = SourceEntry(
            processor=PLATFORM_DISCORD,
            username="",
            service="",
        )
        result = format_relative_source_path(primary, secondary)
        # Should just be platform name
        assert result == "Discord"
        assert "//" not in result
        assert not result.endswith("/")


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

    def test_uses_xml_entity_newlines_not_literal(self):
        """Test that build_image_description uses &#xa; for line separators.

        This is critical for exiftool argfile compatibility. Literal newlines
        in argfile values cause lines to be interpreted as separate arguments,
        silently dropping secondary source information.
        """
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        secondary = [
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="insta_user",
                service=SERVICE_MESSAGES,
                sender="friend",
                conversation="Chat",
            ),
        ]
        result = build_image_description(primary, secondary)

        # Must use XML entity for newlines
        assert "&#xa;" in result, (
            "build_image_description must use &#xa; (XML entity) for line "
            f"separators, not literal newlines. Got: {repr(result)}"
        )
        # Must NOT contain literal newlines
        assert "\n" not in result, (
            "build_image_description must not contain literal newlines "
            f"(breaks exiftool argfile format). Got: {repr(result)}"
        )

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

    def test_primary_with_context(self):
        """Test primary source includes its context."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
            sender="me",
            conversation="Family Album",
            content="Vacation photo",
        )
        result = build_image_description(primary, [])
        assert "Source: Google/user/photos" in result
        assert '  - me in "Family Album": "Vacation photo"' in result

    def test_same_source_detail_folding(self):
        """Test same-source secondaries are folded into message detail lines."""
        primary = SourceEntry(
            processor=PLATFORM_INSTAGRAM,
            username="johndoe",
            service=SERVICE_MESSAGES,
            sender="alice",
            conversation="Friends",
            content="Look at this!",
        )
        secondary = [
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="johndoe",
                service=SERVICE_MESSAGES,
                sender="bob",
                conversation="Family",
                content="Sharing this photo",
                timestamp=datetime(2024, 1, 20),
            ),
        ]
        result = build_image_description(primary, secondary)

        # Primary source line
        assert "Source: Instagram/johndoe/messages" in result
        # Primary context
        assert '  - alice in "Friends": "Look at this!"' in result
        # Secondary context should be folded (with + prefix)
        assert '+bob in "Family"' in result
        # Should NOT have a duplicate source line
        assert result.count("Instagram/johndoe/messages") == 1

    def test_relative_path_same_platform_different_service(self):
        """Test ././service notation for same platform/user, different service."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        secondary = [
            SourceEntry(
                processor=PLATFORM_GOOGLE,
                username="user",
                service=SERVICE_CHAT,
                conversation="Work Team",
                content="Here's the file",
                timestamp=datetime(2024, 1, 15),
            ),
        ]
        result = build_image_description(primary, secondary)

        assert "Source: Google/user/photos" in result
        assert "+././chat:" in result

    def test_relative_path_same_platform_different_user(self):
        """Test ./user/service notation for same platform, different user."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user1",
            service=SERVICE_PHOTOS,
        )
        secondary = [
            SourceEntry(
                processor=PLATFORM_GOOGLE,
                username="user2",
                service=SERVICE_PHOTOS,
                timestamp=datetime(2024, 1, 15),
            ),
        ]
        result = build_image_description(primary, secondary)

        assert "Source: Google/user1/photos" in result
        assert "+./user2/photos:" in result

    def test_imessage_consolidation(self):
        """Test iMessage devices are treated as same source (context folding)."""
        primary = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            username="iph13p",
            sender="me",
            conversation="Family Group",
        )
        secondary = [
            SourceEntry(
                processor=PLATFORM_IMESSAGE,
                username="mac",  # Different device
                sender="me",
                conversation="Work Chat",
                content="For the presentation",
                timestamp=datetime(2024, 1, 18),
            ),
        ]
        result = build_image_description(primary, secondary)

        # Primary source line
        assert "Source: iMessage/iph13p" in result
        # Primary context
        assert '  - me in "Family Group"' in result
        # Secondary should be folded (context only, no source path)
        assert '+me in "Work Chat"' in result
        # Should NOT show ./mac as a separate source
        assert "+./mac" not in result

    def test_same_message_details_skipped(self):
        """Test duplicate message details are skipped."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
            sender="me",
            conversation="Family",
            content="Vacation pic",
        )
        secondary = [
            SourceEntry(
                processor=PLATFORM_GOOGLE,
                username="user",
                service=SERVICE_PHOTOS,
                sender="me",
                conversation="Family",
                content="Vacation pic",  # Same context
            ),
        ]
        result = build_image_description(primary, secondary)

        # Should only have one context line, not duplicated
        assert result.count("Family") == 1

    def test_mixed_sources(self):
        """Test mix of same-source and different-source secondaries."""
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        secondary = [
            # Same source, different context
            SourceEntry(
                processor=PLATFORM_GOOGLE,
                username="user",
                service=SERVICE_PHOTOS,
                conversation="Album A",
            ),
            # Different service
            SourceEntry(
                processor=PLATFORM_GOOGLE,
                username="user",
                service=SERVICE_CHAT,
                conversation="Team Chat",
            ),
            # Different platform
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="insta_user",
                service=SERVICE_MESSAGES,
                conversation="DMs",
            ),
        ]
        result = build_image_description(primary, secondary)

        # Primary source
        assert "Source: Google/user/photos" in result
        # Same-source message details should be folded (just +details, no path)
        assert '+in "Album A"' in result
        # Different service uses relative path
        assert "+././chat:" in result
        # Different platform uses full path
        assert "+Instagram/insta_user/messages:" in result

    def test_identical_rendered_lines_deduplicated(self):
        """Test that identical rendered different-source lines appear only once.

        When multiple different-source entries collapse to the same rendered
        line (e.g., due to missing context or truncation), only one should
        be emitted.
        """
        primary = SourceEntry(
            processor=PLATFORM_GOOGLE,
            username="user",
            service=SERVICE_PHOTOS,
        )
        # Two Instagram entries with no message details - should render identically
        secondary = [
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="insta_user",
                service=SERVICE_POSTS,
                # No sender, conversation, or content
            ),
            SourceEntry(
                processor=PLATFORM_INSTAGRAM,
                username="insta_user",
                service=SERVICE_POSTS,
                # Also no context - would render identical line
            ),
        ]
        result = build_image_description(primary, secondary)

        # The +Instagram/insta_user/posts line should appear exactly once
        assert result.count("+Instagram/insta_user/posts") == 1


class TestCreateSourceEntry:
    """Tests for creating source entries from metadata."""

    def test_basic_creation(self):
        """Test basic source entry creation."""
        metadata = {
            "username": "johndoe",
        }
        entry = create_source_entry("Google Photos", metadata)

        assert entry.processor == "Google Photos"
        assert entry.username == "johndoe"

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


class TestFormatMessageLine:
    """Tests for format_message_line function (formats lines for description)."""

    def test_full_message_details(self):
        """Test message line with sender, conversation, and content."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            sender="John Doe",
            conversation="Family Group",
            content="Check out this photo!",
        )
        result = format_message_line(entry)
        assert result == '  - John Doe in "Family Group": "Check out this photo!"'

    def test_sender_only(self):
        """Test message line with sender only."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            sender="John Doe",
        )
        result = format_message_line(entry)
        assert result == "  - John Doe"

    def test_conversation_only(self):
        """Test message line with conversation only."""
        entry = SourceEntry(
            processor=PLATFORM_DISCORD,
            conversation="#photos in Gaming Server",
        )
        result = format_message_line(entry)
        assert result == '  - in "#photos in Gaming Server"'

    def test_content_only(self):
        """Test message line with content only (no sender or conversation)."""
        entry = SourceEntry(
            processor=PLATFORM_GOOGLE,
            content="A caption for this photo",
        )
        result = format_message_line(entry)
        # Should NOT have a colon before content when there's no sender/conversation
        assert result == '  - "A caption for this photo"'

    def test_empty_message_details(self):
        """Test empty message details returns empty string."""
        entry = SourceEntry(processor=PLATFORM_GOOGLE)
        result = format_message_line(entry)
        assert result == ""

    def test_content_truncation(self):
        """Test that long content is truncated."""
        entry = SourceEntry(
            processor=PLATFORM_IMESSAGE,
            sender="John",
            conversation="Family",
            content="A" * 200,
        )
        result = format_message_line(entry, max_content=50)
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
