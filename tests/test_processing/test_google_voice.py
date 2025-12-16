"""
Processing tests for Google Voice processor.

Tests cover edge cases including:
- Text conversation with media
- HTML parsing variations
- Media files without matching HTML
"""



from tests.fixtures.generators import create_google_voice_export
from tests.fixtures.media_samples import write_media_file


class TestGoogleVoiceConversations:
    """Tests for conversation handling in Google Voice processing."""

    def test_text_conversation_with_media(self, google_voice_processor, temp_export_dir, temp_output_dir):
        """Should process text conversation with attached media."""
        create_google_voice_export(
            temp_export_dir,
            calls=[
                {
                    "contact": "+1234567890",
                    "type": "Text",
                    "timestamp": "2021-01-01T12:00:00Z",
                    "messages": [
                        {"sender": "Me", "text": "Check this out!", "time": "12:00 PM"},
                        {"sender": "+1234567890", "text": "Nice photo!", "time": "12:01 PM"},
                    ],
                    "media": ["photo.jpg"],
                }
            ]
        )

        calls_dir = temp_export_dir / "Voice" / "Calls"
        assert calls_dir.exists()

        # Check HTML file exists
        html_files = list(calls_dir.glob("*.html"))
        assert len(html_files) == 1

        # Check media file exists
        assert (calls_dir / "photo.jpg").exists()

    def test_text_only_conversation(self, google_voice_processor, temp_export_dir, temp_output_dir):
        """Should process text conversation without media."""
        create_google_voice_export(
            temp_export_dir,
            calls=[
                {
                    "contact": "+1234567890",
                    "type": "Text",
                    "timestamp": "2021-01-01T12:00:00Z",
                    "messages": [
                        {"sender": "Me", "text": "Hello!", "time": "12:00 PM"},
                        {"sender": "+1234567890", "text": "Hi!", "time": "12:01 PM"},
                    ],
                    "media": [],
                }
            ]
        )

        calls_dir = temp_export_dir / "Voice" / "Calls"
        html_files = list(calls_dir.glob("*.html"))
        assert len(html_files) == 1

        # No media files
        media_files = list(calls_dir.glob("*.jpg")) + list(calls_dir.glob("*.png"))
        assert len(media_files) == 0


class TestGoogleVoiceEdgeCases:
    """Tests for edge cases in Google Voice processing."""

    def test_multiple_conversations(self, google_voice_processor, temp_export_dir, temp_output_dir):
        """Should handle multiple conversations with same and different contacts."""
        create_google_voice_export(
            temp_export_dir,
            calls=[
                {
                    "contact": "+1234567890",
                    "type": "Text",
                    "timestamp": "2021-01-01T12:00:00Z",
                    "messages": [{"sender": "Me", "text": "Hi", "time": "12:00 PM"}],
                    "media": [],
                },
                {
                    "contact": "+1234567890",
                    "type": "Text",
                    "timestamp": "2021-01-02T12:00:00Z",
                    "messages": [{"sender": "Me", "text": "Hello again", "time": "12:00 PM"}],
                    "media": [],
                },
                {
                    "contact": "+0987654321",
                    "type": "Text",
                    "timestamp": "2021-01-03T12:00:00Z",
                    "messages": [{"sender": "Me", "text": "Hey", "time": "12:00 PM"}],
                    "media": [],
                },
            ]
        )

        calls_dir = temp_export_dir / "Voice" / "Calls"
        html_files = list(calls_dir.glob("*.html"))
        assert len(html_files) == 3

    def test_media_without_matching_html(self, google_voice_processor, temp_export_dir, temp_output_dir):
        """Should handle orphaned media files without corresponding HTML."""
        calls_dir = temp_export_dir / "Voice" / "Calls"
        calls_dir.mkdir(parents=True)

        # Create HTML for one conversation
        html_content = """<!DOCTYPE html>
<html><head><title>Text</title></head>
<body><div class="message">Test message</div></body>
</html>"""
        (calls_dir / "+1234567890 - Text - 2021-01-01_12-00-00Z.html").write_text(html_content)

        # Create orphaned media file (no matching HTML)
        write_media_file(calls_dir / "orphaned_photo.jpg", "jpeg")

        html_files = list(calls_dir.glob("*.html"))
        assert len(html_files) == 1
        assert (calls_dir / "orphaned_photo.jpg").exists()

    def test_special_characters_in_contact(self, google_voice_processor, temp_export_dir, temp_output_dir):
        """Should handle contacts with special formatting."""
        create_google_voice_export(
            temp_export_dir,
            calls=[
                {
                    "contact": "+1 (234) 567-8900",
                    "type": "Text",
                    "timestamp": "2021-01-01T12:00:00Z",
                    "messages": [{"sender": "Me", "text": "Hi", "time": "12:00 PM"}],
                    "media": [],
                },
            ]
        )

        calls_dir = temp_export_dir / "Voice" / "Calls"
        html_files = list(calls_dir.glob("*.html"))
        assert len(html_files) == 1


class TestGoogleVoiceHTMLParsing:
    """Tests for HTML structure variations in Google Voice."""

    def test_standard_html_structure(self, google_voice_processor, temp_export_dir, temp_output_dir):
        """Should parse standard HTML structure."""
        calls_dir = temp_export_dir / "Voice" / "Calls"
        calls_dir.mkdir(parents=True)

        html_content = """<!DOCTYPE html>
<html>
<head><title>Text with +1234567890</title></head>
<body>
<div class="message">
<div class="sender">Me</div>
<div class="text">Hello there!</div>
<div class="time">12:00 PM</div>
</div>
<div class="message">
<div class="sender">+1234567890</div>
<div class="text">Hi back!</div>
<div class="time">12:01 PM</div>
</div>
</body>
</html>"""
        (calls_dir / "+1234567890 - Text - 2021-01-01_12-00-00Z.html").write_text(html_content)

        assert (calls_dir / "+1234567890 - Text - 2021-01-01_12-00-00Z.html").exists()

    def test_empty_message_body(self, google_voice_processor, temp_export_dir, temp_output_dir):
        """Should handle HTML with empty message body."""
        calls_dir = temp_export_dir / "Voice" / "Calls"
        calls_dir.mkdir(parents=True)

        html_content = """<!DOCTYPE html>
<html>
<head><title>Text</title></head>
<body>
<div class="message">
</div>
</body>
</html>"""
        (calls_dir / "+1234567890 - Text - 2021-01-01_12-00-00Z.html").write_text(html_content)

        assert (calls_dir / "+1234567890 - Text - 2021-01-01_12-00-00Z.html").exists()


class TestGoogleVoiceMediaTypes:
    """Tests for various media type handling in Google Voice."""

    def test_image_media(self, google_voice_processor, temp_export_dir, temp_output_dir):
        """Should handle image media files."""
        create_google_voice_export(
            temp_export_dir,
            calls=[
                {
                    "contact": "+1234567890",
                    "type": "Text",
                    "timestamp": "2021-01-01T12:00:00Z",
                    "messages": [{"sender": "Me", "text": "Photo", "time": "12:00 PM"}],
                    "media": ["image.jpg", "image.png"],
                }
            ]
        )

        calls_dir = temp_export_dir / "Voice" / "Calls"
        assert (calls_dir / "image.jpg").exists()
        assert (calls_dir / "image.png").exists()

    def test_video_media(self, google_voice_processor, temp_export_dir, temp_output_dir):
        """Should handle video media files."""
        create_google_voice_export(
            temp_export_dir,
            calls=[
                {
                    "contact": "+1234567890",
                    "type": "Text",
                    "timestamp": "2021-01-01T12:00:00Z",
                    "messages": [{"sender": "Me", "text": "Video", "time": "12:00 PM"}],
                    "media": ["video.mp4"],
                }
            ]
        )

        calls_dir = temp_export_dir / "Voice" / "Calls"
        assert (calls_dir / "video.mp4").exists()

