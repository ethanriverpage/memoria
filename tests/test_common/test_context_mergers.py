#!/usr/bin/env python3
"""Tests for common/preprocess/context_mergers.py merger functions.

Tests cover:
- merge_albums for Google Photos album accumulation
- merge_snapchat_context for Snapchat message accumulation
- merge_imessage_context for iMessage message accumulation
"""

from typing import Any, Dict


from common.preprocess.context_mergers import (
    merge_albums,
    merge_snapchat_context,
    merge_imessage_context,
)


class TestMergeAlbums:
    """Tests for merge_albums function."""

    def test_creates_albums_list_if_missing(self) -> None:
        """Should create albums list if not present in existing."""
        existing: Dict[str, Any] = {}
        new = {"albums": ["vacation"]}

        merge_albums(existing, new)

        assert "albums" in existing
        assert existing["albums"] == ["vacation"]

    def test_adds_new_albums(self) -> None:
        """Should add new albums to existing list."""
        existing: Dict[str, Any] = {"albums": ["album1"]}
        new = {"albums": ["album2", "album3"]}

        merge_albums(existing, new)

        assert set(existing["albums"]) == {"album1", "album2", "album3"}

    def test_prevents_duplicates(self) -> None:
        """Should not add duplicate album names."""
        existing: Dict[str, Any] = {"albums": ["vacation", "family"]}
        new = {"albums": ["vacation", "wedding"]}

        merge_albums(existing, new)

        # Count of "vacation" should be 1
        assert existing["albums"].count("vacation") == 1
        assert "wedding" in existing["albums"]

    def test_handles_empty_new_albums(self) -> None:
        """Should handle empty albums in new context."""
        existing: Dict[str, Any] = {"albums": ["album1"]}
        new: Dict[str, Any] = {"albums": []}

        merge_albums(existing, new)

        assert existing["albums"] == ["album1"]

    def test_handles_missing_albums_in_new(self) -> None:
        """Should handle missing albums key in new context."""
        existing: Dict[str, Any] = {"albums": ["album1"]}
        new: Dict[str, Any] = {}

        merge_albums(existing, new)

        assert existing["albums"] == ["album1"]

    def test_preserves_order(self) -> None:
        """Should preserve order of album additions."""
        existing: Dict[str, Any] = {"albums": ["first"]}
        new = {"albums": ["second", "third"]}

        merge_albums(existing, new)

        assert existing["albums"] == ["first", "second", "third"]


class TestMergeSnapchatContext:
    """Tests for merge_snapchat_context function."""

    def test_creates_messages_list_if_missing(self) -> None:
        """Should create messages list if not present in existing."""
        existing: Dict[str, Any] = {}
        new = {"messages": [{"sender": "friend", "text": "hey"}]}

        merge_snapchat_context(existing, new)

        assert "messages" in existing
        assert len(existing["messages"]) == 1

    def test_extends_messages_list(self) -> None:
        """Should extend existing messages with new messages."""
        existing: Dict[str, Any] = {
            "messages": [{"sender": "me", "text": "hello"}]
        }
        new = {
            "messages": [
                {"sender": "friend", "text": "hey"},
                {"sender": "friend", "text": "how are you"},
            ]
        }

        merge_snapchat_context(existing, new)

        assert len(existing["messages"]) == 3

    def test_handles_empty_new_messages(self) -> None:
        """Should handle empty messages in new context."""
        existing: Dict[str, Any] = {
            "messages": [{"sender": "me", "text": "hello"}]
        }
        new: Dict[str, Any] = {"messages": []}

        merge_snapchat_context(existing, new)

        assert len(existing["messages"]) == 1

    def test_handles_missing_messages_in_new(self) -> None:
        """Should handle missing messages key in new context."""
        existing: Dict[str, Any] = {
            "messages": [{"sender": "me", "text": "hello"}]
        }
        new: Dict[str, Any] = {}

        merge_snapchat_context(existing, new)

        assert len(existing["messages"]) == 1

    def test_preserves_message_structure(self) -> None:
        """Should preserve full message structure during merge."""
        existing: Dict[str, Any] = {"messages": []}
        new = {
            "messages": [
                {
                    "sender": "friend",
                    "text": "check this out",
                    "timestamp": "2024-01-15T10:30:00Z",
                    "media_type": "snap",
                }
            ]
        }

        merge_snapchat_context(existing, new)

        assert existing["messages"][0]["sender"] == "friend"
        assert existing["messages"][0]["timestamp"] == "2024-01-15T10:30:00Z"
        assert existing["messages"][0]["media_type"] == "snap"


class TestMergeImessageContext:
    """Tests for merge_imessage_context function."""

    def test_creates_messages_list_if_missing(self) -> None:
        """Should create messages list if not present in existing."""
        existing: Dict[str, Any] = {}
        new = {"sender": "John", "content": "Hello"}

        merge_imessage_context(existing, new)

        assert "messages" in existing
        assert len(existing["messages"]) == 1
        assert existing["messages"][0] == new

    def test_appends_new_context_directly(self) -> None:
        """Should append new context dict to messages list."""
        existing: Dict[str, Any] = {
            "messages": [{"sender": "John", "content": "Hello"}]
        }
        new = {"sender": "Jane", "content": "Hi there"}

        merge_imessage_context(existing, new)

        assert len(existing["messages"]) == 2
        assert existing["messages"][1]["sender"] == "Jane"

    def test_preserves_existing_messages(self) -> None:
        """Should preserve existing messages."""
        first_msg = {"sender": "John", "content": "First"}
        existing: Dict[str, Any] = {"messages": [first_msg]}
        new = {"sender": "Jane", "content": "Second"}

        merge_imessage_context(existing, new)

        assert existing["messages"][0] == first_msg
        assert len(existing["messages"]) == 2

    def test_handles_complex_context(self) -> None:
        """Should handle complex context with multiple fields."""
        existing: Dict[str, Any] = {"messages": []}
        new = {
            "sender": "John",
            "content": "Check this photo",
            "conversation_type": "dm",
            "conversation_id": "+1234567890",
            "source_export": "imessage-iph13p-20220426",
        }

        merge_imessage_context(existing, new)

        assert existing["messages"][0] == new


class TestMergerIntegration:
    """Integration tests for merger functions."""

    def test_multiple_album_merges(self) -> None:
        """Should handle multiple sequential album merges."""
        existing: Dict[str, Any] = {}

        # Simulate multiple duplicates being merged
        merge_albums(existing, {"albums": ["album1", "album2"]})
        merge_albums(existing, {"albums": ["album2", "album3"]})
        merge_albums(existing, {"albums": ["album3", "album4"]})

        assert set(existing["albums"]) == {"album1", "album2", "album3", "album4"}

    def test_multiple_snapchat_merges(self) -> None:
        """Should accumulate messages from multiple merges."""
        existing: Dict[str, Any] = {}

        merge_snapchat_context(existing, {"messages": [{"id": 1}]})
        merge_snapchat_context(existing, {"messages": [{"id": 2}]})
        merge_snapchat_context(existing, {"messages": [{"id": 3}, {"id": 4}]})

        assert len(existing["messages"]) == 4

    def test_multiple_imessage_merges(self) -> None:
        """Should accumulate contexts from multiple merges."""
        existing: Dict[str, Any] = {}

        merge_imessage_context(existing, {"sender": "A"})
        merge_imessage_context(existing, {"sender": "B"})
        merge_imessage_context(existing, {"sender": "C"})

        assert len(existing["messages"]) == 3
        senders = [m["sender"] for m in existing["messages"]]
        assert senders == ["A", "B", "C"]

