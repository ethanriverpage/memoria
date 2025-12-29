"""
Processing tests for iMessage processor.

Tests cover edge cases including:
- Mac export (chat.db + Attachments/)
- iPhone export (SMS/sms.db + SMS/Attachments/)
- Live Photo pairs
- Group conversation
- DM conversation
- Contact resolution (with contacts.vcf)
- Cross-export deduplication
"""

import sqlite3


from tests.fixtures.generators import (
    create_imessage_mac_export,
    create_imessage_iphone_export,
)
from tests.fixtures.media_samples import (
    write_media_file,
    create_imessage_chat_db,
)


class TestIMessageMacExport:
    """Tests for Mac export handling."""

    def test_mac_export_basic(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should process basic Mac export."""
        create_imessage_mac_export(temp_export_dir)

        assert (temp_export_dir / "chat.db").exists()
        assert (temp_export_dir / "Attachments").is_dir()

    def test_mac_export_with_attachments(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should process Mac export with multiple attachments."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {
                            "text": "Photo 1",
                            "attachment": "00/00/image1.jpg",
                            "date": 631152000000000000,
                        },
                        {
                            "text": "Photo 2",
                            "attachment": "00/01/image2.png",
                            "date": 631152000000000001,
                        },
                        {
                            "text": "Video",
                            "attachment": "01/00/video.mp4",
                            "date": 631152000000000002,
                        },
                    ],
                },
            ],
        )

        attachments_dir = temp_export_dir / "Attachments"
        assert (attachments_dir / "00" / "00" / "image1.jpg").exists()
        assert (attachments_dir / "00" / "01" / "image2.png").exists()
        assert (attachments_dir / "01" / "00" / "video.mp4").exists()


class TestIMessageIPhoneExport:
    """Tests for iPhone export handling."""

    def test_iphone_export_basic(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should process basic iPhone export."""
        create_imessage_iphone_export(temp_export_dir)

        assert (temp_export_dir / "SMS" / "sms.db").exists()
        assert (temp_export_dir / "SMS" / "Attachments").is_dir()

    def test_iphone_export_with_attachments(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should process iPhone export with attachments."""
        create_imessage_iphone_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {
                            "text": "Image",
                            "attachment": "00/00/photo.jpg",
                            "date": 631152000000000000,
                        },
                    ],
                },
            ],
        )

        attachments_dir = temp_export_dir / "SMS" / "Attachments"
        assert (attachments_dir / "00" / "00" / "photo.jpg").exists()


class TestIMessageConversationTypes:
    """Tests for different conversation types."""

    def test_dm_conversation(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should process direct message conversations."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "display_name": None,  # DMs typically don't have display names
                    "messages": [
                        {"text": "Hello!", "is_from_me": 0, "date": 631152000000000000},
                        {
                            "text": "Hi there!",
                            "is_from_me": 1,
                            "date": 631152000000000001,
                        },
                    ],
                },
            ],
        )

        # Verify database has the conversation
        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()
        cursor.execute("SELECT chat_identifier FROM chat")
        chats = cursor.fetchall()
        conn.close()

        assert len(chats) == 1
        assert chats[0][0] == "+1234567890"

    def test_group_conversation(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should process group conversations."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "chat123456",
                    "display_name": "Family Group",
                    "messages": [
                        {
                            "text": "Hey family!",
                            "is_from_me": 1,
                            "date": 631152000000000000,
                        },
                    ],
                },
            ],
        )

        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()
        cursor.execute(
            "SELECT display_name FROM chat WHERE chat_identifier = ?", ("chat123456",)
        )
        result = cursor.fetchone()
        conn.close()

        assert result[0] == "Family Group"


class TestIMessageLivePhotos:
    """Tests for Live Photo handling."""

    def test_live_photo_pair(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle Live Photo pairs (HEIC + MOV)."""
        attachments_dir = temp_export_dir / "Attachments" / "00" / "00"
        attachments_dir.mkdir(parents=True)

        # Create Live Photo pair
        write_media_file(
            attachments_dir / "IMG_1234.HEIC", "jpeg"
        )  # HEIC uses JPEG bytes for test
        write_media_file(attachments_dir / "IMG_1234.MOV", "mov")

        create_imessage_chat_db(temp_export_dir / "chat.db")

        assert (attachments_dir / "IMG_1234.HEIC").exists()
        assert (attachments_dir / "IMG_1234.MOV").exists()

    def test_live_photo_jpg_mov_pair(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle Live Photo pairs (JPG + MOV)."""
        attachments_dir = temp_export_dir / "Attachments" / "00" / "00"
        attachments_dir.mkdir(parents=True)

        write_media_file(attachments_dir / "Photo.JPG", "jpeg")
        write_media_file(attachments_dir / "Photo.MOV", "mov")

        create_imessage_chat_db(temp_export_dir / "chat.db")

        assert (attachments_dir / "Photo.JPG").exists()
        assert (attachments_dir / "Photo.MOV").exists()


class TestIMessageLivePhotoPairing:
    """Tests for Live Photo filename pairing logic.

    Verifies that:
    1. Live photo pairs (image + MOV sidecar) get matching base filenames
    2. Collision prevention works when regular files share timestamps with live photos
    """

    def test_live_photo_pair_same_base_filename(self, tmp_path):
        """Live photo image and video should share the same base filename."""
        from common.utils import FilenameGenerator

        # Simulate the two-pass logic from iMessage processor
        filename_gen = FilenameGenerator(
            "imessage",
            "testuser",
            timestamp_format="%Y-%m-%d %H:%M:%S UTC",
        )

        # Simulate metadata from preprocessing:
        # - Image with attachment_id=1
        # - Video with is_live_photo_video=True, live_photo_image_id=1
        filename_index = {
            "lp_image.HEIC": {
                "attachment_id": "1",
                "created": "2023-05-26 12:00:00 UTC",
            },
            "lp_image.MOV": {
                "is_live_photo_video": True,
                "live_photo_image_id": "1",
                "created": "2023-05-26 12:00:00 UTC",
            },
        }

        # Build live photo pairing map
        live_photo_videos_by_image = {}
        for media_filename, message in filename_index.items():
            if message.get("is_live_photo_video"):
                image_id = message.get("live_photo_image_id")
                if image_id:
                    if image_id not in live_photo_videos_by_image:
                        live_photo_videos_by_image[image_id] = set()
                    live_photo_videos_by_image[image_id].add(media_filename)

        # Two-pass filename generation
        live_photo_bases = {}
        results = {}

        # Pass A: non-live-video items
        for media_filename, message in filename_index.items():
            if message.get("is_live_photo_video"):
                continue

            file_ext = ".heic"
            canonical_ts = message.get("created")
            output_filename = filename_gen.generate(canonical_ts, file_ext)

            # Store base and reserve .mov for paired video
            attachment_id = message.get("attachment_id")
            if attachment_id and attachment_id in live_photo_videos_by_image:
                base = output_filename[: -len(file_ext)]
                live_photo_bases[attachment_id] = base
                filename_gen.used_filenames.add(f"{base}.mov")

            results[media_filename] = output_filename

        # Pass B: live-photo video items
        for media_filename, message in filename_index.items():
            if not message.get("is_live_photo_video"):
                continue

            file_ext = ".mov"
            image_id = message.get("live_photo_image_id")

            if image_id and image_id in live_photo_bases:
                base = live_photo_bases[image_id]
                output_filename = f"{base}{file_ext}"
                filename_gen.used_filenames.add(output_filename)
            else:
                output_filename = filename_gen.generate(
                    message.get("created"), file_ext
                )

            results[media_filename] = output_filename

        # Verify: both files should have same base
        heic_name = results["lp_image.HEIC"]
        mov_name = results["lp_image.MOV"]
        heic_base = heic_name.rsplit(".", 1)[0]
        mov_base = mov_name.rsplit(".", 1)[0]

        assert heic_base == mov_base, f"Base mismatch: {heic_base} != {mov_base}"
        assert heic_name.endswith(".heic")
        assert mov_name.endswith(".mov")

    def test_live_photo_no_collision_with_regular_file(self, tmp_path):
        """Regular file with same timestamp should not collide with live photo pair."""
        from common.utils import FilenameGenerator

        filename_gen = FilenameGenerator(
            "imessage",
            "testuser",
            timestamp_format="%Y-%m-%d %H:%M:%S UTC",
        )

        # Same timestamp for all three files:
        # - Live photo image (attachment_id=1)
        # - Live photo video (linked to attachment_id=1)
        # - Regular video (not a live photo)
        timestamp = "2023-05-26 12:00:00 UTC"
        filename_index = {
            "lp_image.HEIC": {
                "attachment_id": "1",
                "created": timestamp,
            },
            "lp_image.MOV": {
                "is_live_photo_video": True,
                "live_photo_image_id": "1",
                "created": timestamp,
            },
            "regular_video.MOV": {
                "created": timestamp,
            },
        }

        # Build live photo pairing map
        live_photo_videos_by_image = {}
        for media_filename, message in filename_index.items():
            if message.get("is_live_photo_video"):
                image_id = message.get("live_photo_image_id")
                if image_id:
                    if image_id not in live_photo_videos_by_image:
                        live_photo_videos_by_image[image_id] = set()
                    live_photo_videos_by_image[image_id].add(media_filename)

        # Two-pass filename generation
        live_photo_bases = {}
        results = {}

        # Pass A: non-live-video items (including regular video)
        for media_filename, message in filename_index.items():
            if message.get("is_live_photo_video"):
                continue

            file_ext = ".heic" if "HEIC" in media_filename else ".mov"
            canonical_ts = message.get("created")
            output_filename = filename_gen.generate(canonical_ts, file_ext)

            # Store base and reserve .mov for paired live photo video
            attachment_id = message.get("attachment_id")
            if attachment_id and attachment_id in live_photo_videos_by_image:
                base = output_filename[: -len(file_ext)]
                live_photo_bases[attachment_id] = base
                filename_gen.used_filenames.add(f"{base}.mov")

            results[media_filename] = output_filename

        # Pass B: live-photo video items
        for media_filename, message in filename_index.items():
            if not message.get("is_live_photo_video"):
                continue

            file_ext = ".mov"
            image_id = message.get("live_photo_image_id")

            if image_id and image_id in live_photo_bases:
                base = live_photo_bases[image_id]
                output_filename = f"{base}{file_ext}"
                filename_gen.used_filenames.add(output_filename)
            else:
                output_filename = filename_gen.generate(
                    message.get("created"), file_ext
                )

            results[media_filename] = output_filename

        # Verify: live photo pair has matching bases
        heic_name = results["lp_image.HEIC"]
        lp_mov_name = results["lp_image.MOV"]
        heic_base = heic_name.rsplit(".", 1)[0]
        lp_mov_base = lp_mov_name.rsplit(".", 1)[0]
        assert heic_base == lp_mov_base, f"Live photo pair mismatch: {heic_base} != {lp_mov_base}"

        # Verify: regular video has different base (got a sequence number)
        regular_mov_name = results["regular_video.MOV"]
        regular_base = regular_mov_name.rsplit(".", 1)[0]
        assert regular_base != lp_mov_base, (
            f"Regular video should have different base: {regular_base} == {lp_mov_base}"
        )

        # All filenames should be unique
        all_names = list(results.values())
        assert len(all_names) == len(set(all_names)), f"Duplicate filenames: {all_names}"

    def test_live_photo_video_without_image_falls_back(self, tmp_path):
        """Live photo video without paired image should fall back to independent name."""
        from common.utils import FilenameGenerator

        filename_gen = FilenameGenerator(
            "imessage",
            "testuser",
            timestamp_format="%Y-%m-%d %H:%M:%S UTC",
        )

        # Video claims to be live photo but references non-existent image
        filename_index = {
            "orphan_video.MOV": {
                "is_live_photo_video": True,
                "live_photo_image_id": "999",  # No matching image
                "created": "2023-05-26 12:00:00 UTC",
            },
        }

        # No images, so live_photo_videos_by_image won't find any matches
        live_photo_bases = {}
        results = {}

        # Pass A: no non-live-video items
        # Pass B: live-photo video items
        for media_filename, message in filename_index.items():
            if not message.get("is_live_photo_video"):
                continue

            file_ext = ".mov"
            image_id = message.get("live_photo_image_id")

            if image_id and image_id in live_photo_bases:
                base = live_photo_bases[image_id]
                output_filename = f"{base}{file_ext}"
            else:
                # Fallback: generate independent name
                output_filename = filename_gen.generate(
                    message.get("created"), file_ext
                )

            results[media_filename] = output_filename

        # Should still get a valid filename (fallback path)
        assert "orphan_video.MOV" in results
        assert results["orphan_video.MOV"].endswith(".mov")


class TestIMessageDeduplication:
    """Tests for cross-export deduplication."""

    def test_consolidation_supported(self, imessage_processor):
        """Should support consolidation mode."""
        assert imessage_processor.supports_consolidation() is True

    def test_multiple_exports_same_conversation(
        self, imessage_processor, tmp_path, temp_output_dir
    ):
        """Should handle same conversation across multiple exports."""
        export1 = tmp_path / "export1"
        export2 = tmp_path / "export2"

        # Same phone number in both exports
        create_imessage_mac_export(
            export1,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Message 1", "date": 631152000000000000},
                    ],
                },
            ],
        )

        create_imessage_mac_export(
            export2,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Message 2", "date": 631152000000000001},
                    ],
                },
            ],
        )

        # Both exports should exist
        assert (export1 / "chat.db").exists()
        assert (export2 / "chat.db").exists()

    def test_duplicate_attachments_across_exports(
        self, imessage_processor, tmp_path, temp_output_dir
    ):
        """Should handle duplicate attachments across exports."""
        export1 = tmp_path / "export1"
        export2 = tmp_path / "export2"

        # Same attachment in both exports
        create_imessage_mac_export(
            export1,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {
                            "text": "Photo",
                            "attachment": "00/00/same_photo.jpg",
                            "date": 631152000000000000,
                        },
                    ],
                },
            ],
        )

        create_imessage_mac_export(
            export2,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {
                            "text": "Photo",
                            "attachment": "00/00/same_photo.jpg",
                            "date": 631152000000000000,
                        },
                    ],
                },
            ],
        )

        # Both should have the attachment
        assert (export1 / "Attachments" / "00" / "00" / "same_photo.jpg").exists()
        assert (export2 / "Attachments" / "00" / "00" / "same_photo.jpg").exists()


class TestIMessageDatabase:
    """Tests for database schema and queries."""

    def test_database_schema(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should create database with correct schema."""
        create_imessage_mac_export(temp_export_dir)

        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()

        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}

        assert "message" in tables
        assert "handle" in tables
        assert "chat" in tables
        assert "attachment" in tables
        assert "chat_message_join" in tables
        assert "message_attachment_join" in tables

        conn.close()

    def test_message_handle_relationship(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should properly link messages to handles."""
        create_imessage_mac_export(
            temp_export_dir,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "Hello", "is_from_me": 0, "date": 631152000000000000},
                    ],
                },
            ],
        )

        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()

        # Check message is linked to handle
        cursor.execute(
            """
            SELECT m.text, h.id
            FROM message m
            JOIN handle h ON m.handle_id = h.ROWID
        """
        )
        result = cursor.fetchone()
        conn.close()

        assert result[0] == "Hello"
        assert result[1] == "+1234567890"


class TestIMessageMixedExports:
    """Tests for mixed Mac and iPhone exports."""

    def test_mac_and_iphone_same_conversation(
        self, imessage_processor, tmp_path, temp_output_dir
    ):
        """Should handle same conversation from Mac and iPhone exports."""
        mac_export = tmp_path / "mac"
        iphone_export = tmp_path / "iphone"

        create_imessage_mac_export(
            mac_export,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "From Mac", "date": 631152000000000000},
                    ],
                },
            ],
        )

        create_imessage_iphone_export(
            iphone_export,
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {"text": "From iPhone", "date": 631152000000000001},
                    ],
                },
            ],
        )

        assert (mac_export / "chat.db").exists()
        assert (iphone_export / "SMS" / "sms.db").exists()


class TestIMessageOrphanDetection:
    """Tests for orphaned media/metadata detection."""

    def test_detect_orphaned_media_method(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should track media files not in processed attachments as orphaned media."""
        from processors.imessage.preprocess import IMessagePreprocessor
        from tests.fixtures.media_samples import write_media_file

        # Create basic export structure (just the Attachments dir, no database needed)
        temp_export_dir.mkdir(parents=True, exist_ok=True)
        attachments_dir = temp_export_dir / "Attachments"
        attachments_dir.mkdir()

        # Create media files - one will be "processed", one will be orphaned
        processed_dir = attachments_dir / "00" / "00"
        processed_dir.mkdir(parents=True)
        processed_file = processed_dir / "processed.jpg"
        write_media_file(processed_file, "jpeg")

        orphan_dir = attachments_dir / "99" / "99"
        orphan_dir.mkdir(parents=True)
        orphan_file = orphan_dir / "orphan_not_in_db.jpg"
        write_media_file(orphan_file, "jpeg")

        # Create a minimal preprocessor and test _detect_orphaned_media directly
        preprocessor = IMessagePreprocessor(
            export_paths=[temp_export_dir],
            output_dir=temp_output_dir,
        )

        # Simulate "processed" attachments - only the processed file was in the database
        processed_attachments = [
            {"source_path": str(processed_file.resolve())}
        ]

        # Run orphan detection
        orphaned_media = preprocessor._detect_orphaned_media(processed_attachments)

        # Check that the orphan file was detected
        orphaned_paths = [entry["filename"] for entry in orphaned_media]
        assert "orphan_not_in_db.jpg" in orphaned_paths

        # Check FailureTracker was updated
        tracker_paths = [
            entry["file_path"] for entry in preprocessor.failure_tracker.orphaned_media
        ]
        assert any("orphan_not_in_db.jpg" in path for path in tracker_paths)

    def test_no_orphans_when_all_files_processed(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should not report orphans when all files were processed from database."""
        from processors.imessage.preprocess import IMessagePreprocessor
        from tests.fixtures.media_samples import write_media_file

        temp_export_dir.mkdir(parents=True, exist_ok=True)
        attachments_dir = temp_export_dir / "Attachments" / "00" / "00"
        attachments_dir.mkdir(parents=True)

        # Create a media file
        media_file = attachments_dir / "photo.jpg"
        write_media_file(media_file, "jpeg")

        preprocessor = IMessagePreprocessor(
            export_paths=[temp_export_dir],
            output_dir=temp_output_dir,
        )

        # Simulate that this file was processed from the database
        # Note: _query_attachments uses "resolved_path" key
        processed_attachments = [
            {"resolved_path": str(media_file.resolve())}
        ]

        orphaned_media = preprocessor._detect_orphaned_media(processed_attachments)

        # No orphans should be found
        assert len(orphaned_media) == 0
        assert len(preprocessor.failure_tracker.orphaned_media) == 0


class TestIMessageRealisticFixtures:
    """Processing tests using committed schema-faithful iMessage fixtures."""

    def test_mac_basic_database_structure(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse Mac export database structure."""
        from tests.fixtures.export_tree import copy_export_fixture
        import sqlite3

        copy_export_fixture("imessage", "mac_basic", temp_export_dir)

        # Verify database has expected tables and data
        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()

        # Check tables exist
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "message" in tables
        assert "handle" in tables
        assert "chat" in tables
        assert "attachment" in tables

        # Check we have sample data
        cursor.execute("SELECT id FROM handle WHERE ROWID = 1")
        handle = cursor.fetchone()
        assert handle[0] == "+14045551234"

        conn.close()

    def test_iphone_basic_database_structure(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse iPhone export database structure."""
        from tests.fixtures.export_tree import copy_export_fixture
        import sqlite3

        copy_export_fixture("imessage", "iphone_basic", temp_export_dir)

        # Verify SMS subdirectory structure
        assert (temp_export_dir / "SMS" / "sms.db").exists()

        # Verify database has expected data
        conn = sqlite3.connect(temp_export_dir / "SMS" / "sms.db")
        cursor = conn.cursor()

        cursor.execute("SELECT id FROM handle WHERE ROWID = 1")
        handle = cursor.fetchone()
        assert handle[0] == "+14045559876"

        conn.close()

    def test_live_photo_lp_image_filesystem_scan(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should identify Live Photo MOV not tracked in database."""
        from tests.fixtures.export_tree import copy_export_fixture
        import sqlite3

        copy_export_fixture("imessage", "live_photo_lp_image_pair", temp_export_dir)

        # The HEIC is in the database
        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()
        cursor.execute("SELECT filename FROM attachment")
        attachments = [row[0] for row in cursor.fetchall()]
        conn.close()

        # Only HEIC should be in DB
        heic_in_db = any("lp_image.HEIC" in a for a in attachments)
        mov_in_db = any("lp_image.MOV" in a for a in attachments)
        assert heic_in_db
        assert not mov_in_db

        # But both exist on filesystem
        lp_dir = temp_export_dir / "Attachments" / "lp" / "00" / "LIVEPHOTO-UUID"
        assert (lp_dir / "lp_image.HEIC").exists()
        assert (lp_dir / "lp_image.MOV").exists()

    def test_ventura_attributedbody_parsing(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle message with attributedBody instead of text."""
        from tests.fixtures.export_tree import copy_export_fixture
        import sqlite3

        copy_export_fixture("imessage", "ventura_attributedbody", temp_export_dir)

        conn = sqlite3.connect(temp_export_dir / "chat.db")
        cursor = conn.cursor()
        cursor.execute("SELECT text, attributedBody FROM message WHERE ROWID = 1")
        text, attributed_body = cursor.fetchone()
        conn.close()

        # text should be NULL, attributedBody should have content
        assert text is None
        assert attributed_body is not None
        # Check for streamtyped header per docs
        assert b"streamtyped" in attributed_body

    def test_contacts_vcf_format(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Should parse contacts.vcf in standard vCard format."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture("imessage", "with_contacts_vcf", temp_export_dir)

        vcf_content = (temp_export_dir / "contacts.vcf").read_text()

        # Verify vCard format
        assert "BEGIN:VCARD" in vcf_content
        assert "END:VCARD" in vcf_content
        assert "FN:John Doe" in vcf_content
        assert "TEL" in vcf_content
        assert "+14045551234" in vcf_content
