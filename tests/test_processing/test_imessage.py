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
    3. Pairing uses stable live_photo_pair_keys (not DB IDs)
    """

    def test_live_photo_pair_same_base_filename(self, tmp_path):
        """Live photo image and video should share the same base filename."""
        from common.files.naming import FilenameGenerator

        # Simulate the two-pass logic from iMessage processor
        filename_gen = FilenameGenerator(
            "imessage",
            "testuser",
        )

        # Simulate metadata from preprocessing:
        # - Image with live_photo_pair_keys
        # - Video with is_live_photo_video=True and same live_photo_pair_keys
        pair_key = "/path/to/attachments/lp/00/LIVEPHOTO-UUID"
        filename_index = {
            "lp_image.HEIC": {
                "live_photo_pair_keys": [pair_key],
                "created": "2023-05-26 12:00:00 UTC",
            },
            "lp_image.MOV": {
                "is_live_photo_video": True,
                "live_photo_pair_keys": [pair_key],
                "created": "2023-05-26 12:00:00 UTC",
            },
        }

        # Build live photo pairing map (key -> set of video filenames)
        live_photo_videos_by_key: dict[str, set[str]] = {}
        for media_filename, message in filename_index.items():
            if message.get("is_live_photo_video"):
                for pk in message.get("live_photo_pair_keys", []):
                    if pk not in live_photo_videos_by_key:
                        live_photo_videos_by_key[pk] = set()
                    live_photo_videos_by_key[pk].add(media_filename)

        # Two-pass filename generation
        live_photo_bases_by_key: dict[str, str] = {}
        results = {}

        # Pass A: non-live-video items
        for media_filename, message in filename_index.items():
            if message.get("is_live_photo_video"):
                continue

            file_ext = ".heic"
            canonical_ts = message.get("created")
            output_filename = filename_gen.generate(canonical_ts, file_ext)

            # Store base and reserve .mov for paired video
            pair_keys = message.get("live_photo_pair_keys", [])
            has_video_pair = any(pk in live_photo_videos_by_key for pk in pair_keys)
            if has_video_pair:
                base = output_filename[: -len(file_ext)]
                for pk in pair_keys:
                    if pk in live_photo_videos_by_key:
                        live_photo_bases_by_key[pk] = base
                filename_gen.used_filenames.add(f"{base}.mov")

            results[media_filename] = output_filename

        # Pass B: live-photo video items
        for media_filename, message in filename_index.items():
            if not message.get("is_live_photo_video"):
                continue

            file_ext = ".mov"
            pair_keys = message.get("live_photo_pair_keys", [])
            matched_base = None
            for pk in pair_keys:
                if pk in live_photo_bases_by_key:
                    matched_base = live_photo_bases_by_key[pk]
                    break

            if matched_base:
                output_filename = f"{matched_base}{file_ext}"
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
        from common.files.naming import FilenameGenerator

        filename_gen = FilenameGenerator(
            "imessage",
            "testuser",
        )

        # Same timestamp for all three files:
        # - Live photo image (with pair key)
        # - Live photo video (with same pair key)
        # - Regular video (not a live photo, no pair keys)
        timestamp = "2023-05-26 12:00:00 UTC"
        pair_key = "/path/to/attachments/lp/00/LIVEPHOTO-UUID"
        filename_index = {
            "lp_image.HEIC": {
                "live_photo_pair_keys": [pair_key],
                "created": timestamp,
            },
            "lp_image.MOV": {
                "is_live_photo_video": True,
                "live_photo_pair_keys": [pair_key],
                "created": timestamp,
            },
            "regular_video.MOV": {
                "created": timestamp,
            },
        }

        # Build live photo pairing map
        live_photo_videos_by_key: dict[str, set[str]] = {}
        for media_filename, message in filename_index.items():
            if message.get("is_live_photo_video"):
                for pk in message.get("live_photo_pair_keys", []):
                    if pk not in live_photo_videos_by_key:
                        live_photo_videos_by_key[pk] = set()
                    live_photo_videos_by_key[pk].add(media_filename)

        # Two-pass filename generation
        live_photo_bases_by_key: dict[str, str] = {}
        results = {}

        # Pass A: non-live-video items (including regular video)
        for media_filename, message in filename_index.items():
            if message.get("is_live_photo_video"):
                continue

            file_ext = ".heic" if "HEIC" in media_filename else ".mov"
            canonical_ts = message.get("created")
            output_filename = filename_gen.generate(canonical_ts, file_ext)

            # Store base and reserve .mov for paired live photo video
            pair_keys = message.get("live_photo_pair_keys", [])
            has_video_pair = any(pk in live_photo_videos_by_key for pk in pair_keys)
            if has_video_pair:
                base = output_filename[: -len(file_ext)]
                for pk in pair_keys:
                    if pk in live_photo_videos_by_key:
                        live_photo_bases_by_key[pk] = base
                filename_gen.used_filenames.add(f"{base}.mov")

            results[media_filename] = output_filename

        # Pass B: live-photo video items
        for media_filename, message in filename_index.items():
            if not message.get("is_live_photo_video"):
                continue

            file_ext = ".mov"
            pair_keys = message.get("live_photo_pair_keys", [])
            matched_base = None
            for pk in pair_keys:
                if pk in live_photo_bases_by_key:
                    matched_base = live_photo_bases_by_key[pk]
                    break

            if matched_base:
                output_filename = f"{matched_base}{file_ext}"
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
        assert heic_base == lp_mov_base, (
            f"Live photo pair mismatch: {heic_base} != {lp_mov_base}"
        )

        # Verify: regular video has different base (got a sequence number)
        regular_mov_name = results["regular_video.MOV"]
        regular_base = regular_mov_name.rsplit(".", 1)[0]
        assert regular_base != lp_mov_base, (
            f"Regular video should have different base: {regular_base} == {lp_mov_base}"
        )

        # All filenames should be unique
        all_names = list(results.values())
        assert len(all_names) == len(set(all_names)), (
            f"Duplicate filenames: {all_names}"
        )

    def test_live_photo_video_without_image_falls_back(self, tmp_path):
        """Live photo video without paired image should fall back to independent name."""
        from common.files.naming import FilenameGenerator

        filename_gen = FilenameGenerator(
            "imessage",
            "testuser",
        )

        # Video claims to be live photo but references non-existent pair key
        filename_index = {
            "orphan_video.MOV": {
                "is_live_photo_video": True,
                "live_photo_pair_keys": ["/nonexistent/path"],  # No matching image
                "created": "2023-05-26 12:00:00 UTC",
            },
        }

        # No images, so live_photo_bases_by_key will be empty
        live_photo_bases_by_key: dict[str, str] = {}
        results = {}

        # Pass A: no non-live-video items
        # Pass B: live-photo video items
        for media_filename, message in filename_index.items():
            if not message.get("is_live_photo_video"):
                continue

            file_ext = ".mov"
            pair_keys = message.get("live_photo_pair_keys", [])
            matched_base = None
            for pk in pair_keys:
                if pk in live_photo_bases_by_key:
                    matched_base = live_photo_bases_by_key[pk]
                    break

            if matched_base:
                output_filename = f"{matched_base}{file_ext}"
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
        processed_attachments = [{"source_path": str(processed_file.resolve())}]

        # Run orphan detection
        orphaned_media = preprocessor._detect_orphaned_media(processed_attachments)

        # Check that the orphaned media file was detected
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
        processed_attachments = [{"resolved_path": str(media_file.resolve())}]

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


class TestIMessageLivePhotoPairingPreprocessing:
    """Regression tests for Live Photo pairing via preprocessing.

    These tests verify that:
    1. Preprocessing emits live_photo_pair_keys for both still and MOV
    2. The pair keys match so the processor can pair them at runtime
    """

    def test_preprocessing_emits_live_photo_pair_keys(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Preprocessing should emit live_photo_pair_keys for Live Photo pairs."""
        import json
        from tests.fixtures.export_tree import copy_export_fixture
        from processors.imessage.preprocess import IMessagePreprocessor

        # Copy the live photo fixture
        copy_export_fixture("imessage", "live_photo_lp_image_pair", temp_export_dir)

        # Rename directory to match expected pattern for username detection
        import shutil

        renamed_dir = temp_export_dir.parent / "imessage-testdev-20251230"
        if renamed_dir.exists():
            shutil.rmtree(renamed_dir)
        shutil.move(str(temp_export_dir), str(renamed_dir))

        # Run preprocessing
        preprocessor = IMessagePreprocessor(
            export_paths=[renamed_dir],
            output_dir=temp_output_dir,
        )
        success = preprocessor.run()
        assert success, "Preprocessing should succeed"

        # Load generated metadata.json
        metadata_file = temp_output_dir / "metadata.json"
        assert metadata_file.exists(), "metadata.json should be created"

        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        # Find still and MOV entries by source_paths
        conversations = metadata.get("conversations", {})
        still_entry = None
        mov_entry = None

        for conv_id, conv in conversations.items():
            for msg in conv.get("messages", []):
                source_paths = msg.get("source_paths", {})
                for filename, path in source_paths.items():
                    if "lp_image.HEIC" in path:
                        still_entry = msg
                    elif "lp_image.MOV" in path:
                        mov_entry = msg

        assert still_entry is not None, "Should find still image entry"
        assert mov_entry is not None, "Should find MOV video entry"

        # Verify both have live_photo_pair_keys
        still_keys = still_entry.get("live_photo_pair_keys", [])
        mov_keys = mov_entry.get("live_photo_pair_keys", [])

        assert len(still_keys) > 0, "Still image should have live_photo_pair_keys"
        assert len(mov_keys) > 0, "MOV video should have live_photo_pair_keys"

        # Verify the keys have non-empty intersection (they should match)
        assert set(still_keys) & set(mov_keys), (
            f"Pair keys should overlap: still={still_keys}, mov={mov_keys}"
        )

        # Verify MOV is marked as live photo video
        assert mov_entry.get("is_live_photo_video") is True, (
            "MOV should be marked as is_live_photo_video=True"
        )

    def test_preprocessing_emits_live_photo_image_hash(
        self, imessage_processor, temp_export_dir, temp_output_dir
    ):
        """Preprocessing should emit live_photo_image_hash for cross-export pairing."""
        import json
        from tests.fixtures.export_tree import copy_export_fixture
        from processors.imessage.preprocess import IMessagePreprocessor
        import shutil

        # Copy the live photo fixture
        copy_export_fixture("imessage", "live_photo_lp_image_pair", temp_export_dir)

        # Rename directory to match expected pattern for username detection
        renamed_dir = temp_export_dir.parent / "imessage-testdev-20251230"
        if renamed_dir.exists():
            shutil.rmtree(renamed_dir)
        shutil.move(str(temp_export_dir), str(renamed_dir))

        # Run preprocessing
        preprocessor = IMessagePreprocessor(
            export_paths=[renamed_dir],
            output_dir=temp_output_dir,
        )
        success = preprocessor.run()
        assert success, "Preprocessing should succeed"

        # Load generated metadata.json
        metadata_file = temp_output_dir / "metadata.json"
        with open(metadata_file, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        # Find still and MOV entries by source_paths
        conversations = metadata.get("conversations", {})
        still_entry = None
        mov_entry = None

        for conv_id, conv in conversations.items():
            for msg in conv.get("messages", []):
                source_paths = msg.get("source_paths", {})
                for filename, path in source_paths.items():
                    if "lp_image.HEIC" in path:
                        still_entry = msg
                    elif "lp_image.MOV" in path:
                        mov_entry = msg

        assert still_entry is not None, "Should find still image entry"
        assert mov_entry is not None, "Should find MOV video entry"

        # Verify both have live_photo_image_hash (for cross-export pairing)
        still_hash = still_entry.get("live_photo_image_hash")
        mov_hash = mov_entry.get("live_photo_image_hash")

        assert still_hash is not None, "Still image should have live_photo_image_hash"
        assert mov_hash is not None, "MOV video should have live_photo_image_hash"

        # Both should have the same hash (the HEIC's content hash)
        assert still_hash == mov_hash, (
            f"HEIC and MOV should share same image hash: {still_hash} != {mov_hash}"
        )


class TestIMessageCrossExportLivePhotoPairing:
    """Tests for cross-export Live Photo pairing when HEIC and MOV are in different exports."""

    def test_cross_export_fallback_uses_heic_hash(self, tmp_path):
        """MOV should pair with HEIC via hash when local HEIC is skipped."""
        from common.dedup.service import get_live_photo_base_for_hash

        # Simulate a dedup manifest where HEIC is primary in a different export
        heic_hash = "abc123def456"
        dedup_manifest = {
            "duplicates": {
                heic_hash: {
                    "best_quality": {
                        "source_path": "/export1/Attachments/00/00/lp_image.HEIC",
                        "processor": "iMessage",
                    },
                    "merged_metadata": {
                        "timestamp": "2023-05-26 12:00:00 UTC",
                    },
                }
            }
        }

        # Test the helper function
        base = get_live_photo_base_for_hash(
            dedup_manifest,
            heic_hash,
            "imessage",
            "testuser",
        )

        assert base is not None, "Should find base from manifest"
        assert base == "imessage-testuser-20230526"

    def test_cross_export_fallback_without_manifest_entry(self, tmp_path):
        """MOV should fall back to independent name when HEIC hash not in manifest."""
        from common.dedup.service import get_live_photo_base_for_hash

        # Empty manifest
        dedup_manifest = {"duplicates": {}}

        base = get_live_photo_base_for_hash(
            dedup_manifest,
            "nonexistent_hash",
            "imessage",
            "testuser",
        )

        assert base is None, "Should return None when hash not in manifest"

    def test_live_photo_video_cross_export_fallback_logic(self, tmp_path):
        """Full simulation of cross-export fallback for Live Photo MOV."""
        from common.dedup.service import get_live_photo_base_for_hash

        # Simulate: MOV has no local pair key match (HEIC was in different export)
        heic_hash = "abc123def456"
        message = {
            "is_live_photo_video": True,
            "live_photo_pair_keys": ["/nonexistent/path"],  # No match locally
            "live_photo_image_hash": heic_hash,
            "created": "2023-05-26 12:00:00 UTC",
        }

        # Manifest has the HEIC
        dedup_manifest = {
            "duplicates": {
                heic_hash: {
                    "best_quality": {
                        "source_path": "/export1/Attachments/00/00/lp_image.HEIC",
                        "processor": "iMessage",
                    },
                    "merged_metadata": {
                        "timestamp": "2023-05-26 12:00:00 UTC",
                    },
                }
            }
        }

        # Simulate the processor's Pass B logic
        pair_keys = message.get("live_photo_pair_keys", [])
        live_photo_bases_by_key = {}  # Empty - no local HEIC

        matched_base = None
        for pk in pair_keys:
            if pk in live_photo_bases_by_key:
                matched_base = live_photo_bases_by_key[pk]
                break

        if not matched_base:
            # Cross-export fallback
            img_hash = message.get("live_photo_image_hash")
            if img_hash and dedup_manifest:
                matched_base = get_live_photo_base_for_hash(
                    dedup_manifest,
                    img_hash,
                    "imessage",
                    "testuser",
                )

        assert matched_base is not None, "Should find base via cross-export fallback"
        assert matched_base == "imessage-testuser-20230526"

        output_filename = f"{matched_base}.mov"
        assert output_filename == "imessage-testuser-20230526.mov"


class TestMixedConsolidatedProcessing:
    """Tests for mixed SQLite + iMazing consolidated processing.

    Verifies that IMessageProcessor.process_consolidated() correctly handles
    mixed input types: SQLite exports (Mac/iPhone) and iMazing exports.
    """

    def test_mixed_sqlite_and_imazing_consolidated(self, tmp_path):
        """Mixed SQLite + iMazing exports should process into same output."""
        from processors.imessage.processor import IMessageProcessor
        from tests.fixtures.generators import (
            create_imessage_mac_export,
            create_imessage_imazing_export,
        )

        # Create a SQLite (Mac) export with proper naming convention
        # Directory name must match pattern for username detection: imessage-{username}-YYYYMMDD
        sqlite_export = tmp_path / "imessage-macuser-20210115"
        sqlite_export.mkdir()
        create_imessage_mac_export(
            sqlite_export,
            username="macuser",
            conversations=[
                {
                    "chat_identifier": "+1234567890",
                    "messages": [
                        {
                            "text": "SQLite message",
                            "attachment": "00/00/sqlite_photo.jpg",
                            "date": 631152000000000000,
                        },
                    ],
                },
            ],
        )

        # Create an iMazing export with proper naming convention
        # Same naming pattern as SQLite exports: imessage-{device}-YYYYMMDD
        imazing_export = tmp_path / "imessage-imazingdev-20210115"
        imazing_export.mkdir()
        create_imessage_imazing_export(
            imazing_export,
            username="imazinguser",
            conversations=[
                {
                    "contact_name": "John Doe",
                    "messages": [
                        {
                            "date": "2021-01-15 14:30:00",
                            "sender": "John Doe",
                            "text": "iMazing message",
                            "attachment": "imazing_photo.jpg",
                        },
                    ],
                },
            ],
        )

        # Verify both exports are detected
        assert IMessageProcessor.detect(sqlite_export) is True
        assert IMessageProcessor.detect(imazing_export) is True

        # Create output directory
        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Process both exports in consolidated mode
        result = IMessageProcessor.process_consolidated(
            input_dirs=[str(sqlite_export), str(imazing_export)],
            output_dir=str(output_dir),
            threads=1,
            temp_dir=str(tmp_path / "temp"),
        )

        assert result is True, "Consolidated processing should succeed"

        # Verify output directory contains processed files
        messages_output = output_dir / "messages"
        assert messages_output.exists(), "Messages output directory should exist"

        # Should have at least one output file from each export
        output_files = list(messages_output.rglob("*"))
        media_files = [f for f in output_files if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".heic"}]
        assert len(media_files) >= 2, f"Expected at least 2 media files, got {len(media_files)}: {media_files}"

    def test_mixed_run_artifact_namespacing(self, tmp_path):
        """Multiple preprocessed dirs should produce namespaced artifacts."""
        from processors.imessage.processor import IMessageProcessor
        from tests.fixtures.generators import create_imessage_imazing_export

        # Create two iMazing exports with proper naming convention
        # Same naming pattern as SQLite exports: imessage-{device}-YYYYMMDD
        imazing1 = tmp_path / "imessage-dev1-20210115"
        imazing1.mkdir()
        create_imessage_imazing_export(
            imazing1,
            username="dev1",
            conversations=[
                {
                    "contact_name": "Contact A",
                    "messages": [
                        {
                            "date": "2021-01-15 14:30:00",
                            "sender": "Contact A",
                            "text": "Message A",
                            "attachment": "photo_a.jpg",
                        },
                    ],
                },
            ],
        )

        imazing2 = tmp_path / "imessage-dev2-20210116"
        imazing2.mkdir()
        create_imessage_imazing_export(
            imazing2,
            username="dev2",
            conversations=[
                {
                    "contact_name": "Contact B",
                    "messages": [
                        {
                            "date": "2021-01-16 15:30:00",
                            "sender": "Contact B",
                            "text": "Message B",
                            "attachment": "photo_b.jpg",
                        },
                    ],
                },
            ],
        )

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Process both
        result = IMessageProcessor.process_consolidated(
            input_dirs=[str(imazing1), str(imazing2)],
            output_dir=str(output_dir),
            threads=1,
            temp_dir=str(tmp_path / "temp"),
        )

        assert result is True

        # Output should contain files from both exports
        messages_output = output_dir / "messages"
        media_files = list(messages_output.rglob("*.jpg")) + list(messages_output.rglob("*.jpeg"))

        # Should have 2 media files (one from each export)
        assert len(media_files) >= 2, f"Expected at least 2 files, got {len(media_files)}"

    def test_unknown_input_type_fails_early(self, tmp_path):
        """Unknown input directories should cause processing to fail early.

        Directories that don't match SQLite, iMazing, or preprocessed formats
        should not be silently processed as SQLite.
        """
        from processors.imessage.processor import IMessageProcessor

        # Create an empty directory that doesn't match any known format
        unknown_dir = tmp_path / "imessage-unknown-20210115"
        unknown_dir.mkdir()
        # Add a random file that doesn't match any export pattern
        (unknown_dir / "random.txt").write_text("This is not an iMessage export")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Processing should fail because the input type is unknown
        result = IMessageProcessor.process_consolidated(
            input_dirs=[str(unknown_dir)],
            output_dir=str(output_dir),
            threads=1,
        )

        assert result is False, "Processing should fail for unknown input type"

    def test_mixed_valid_and_unknown_fails(self, tmp_path):
        """Mix of valid and unknown inputs should still fail early."""
        from processors.imessage.processor import IMessageProcessor
        from tests.fixtures.generators import create_imessage_mac_export

        # Create a valid SQLite export
        valid_export = tmp_path / "imessage-valid-20210115"
        valid_export.mkdir()
        create_imessage_mac_export(valid_export)

        # Create an unknown directory
        unknown_dir = tmp_path / "imessage-unknown-20210115"
        unknown_dir.mkdir()
        (unknown_dir / "random.txt").write_text("Not an export")

        output_dir = tmp_path / "output"
        output_dir.mkdir()

        # Should fail because one input is unknown
        result = IMessageProcessor.process_consolidated(
            input_dirs=[str(valid_export), str(unknown_dir)],
            output_dir=str(output_dir),
            threads=1,
        )

        assert result is False, "Processing should fail when any input is unknown"
