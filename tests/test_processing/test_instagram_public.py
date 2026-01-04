"""
Processing tests for Instagram Public Media processor.

Tests cover edge cases including:
- Posts with JSON metadata
- Archived posts
- Multi-photo carousel posts
- YYYYMM folder organization
"""

import json


from tests.fixtures.generators import create_instagram_public_export
from tests.fixtures.media_samples import write_media_file


class TestInstagramPublicPosts:
    """Tests for regular post handling in Instagram Public Media."""

    def test_post_with_html_metadata(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should process post with HTML metadata."""
        create_instagram_public_export(
            temp_export_dir,
            posts=[
                {
                    "filename": "202101/photo.jpg",
                    "caption": "Test caption",
                    "timestamp": "2021-01-01T12:00:00",
                    "archived": False,
                }
            ],
            include_archived=False,
        )

        # Check media file exists
        posts_dir = temp_export_dir / "media" / "posts" / "202101"
        assert (posts_dir / "photo.jpg").exists()

        # Check HTML metadata exists
        html_dir = temp_export_dir / "your_instagram_activity" / "media"
        assert (html_dir / "posts_1.html").exists()

        # Verify HTML contains the caption
        html_content = (html_dir / "posts_1.html").read_text()
        assert "Test caption" in html_content

    def test_post_without_caption(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle post without caption."""
        posts_dir = temp_export_dir / "media" / "posts" / "202101"
        posts_dir.mkdir(parents=True)

        write_media_file(posts_dir / "no_caption.jpg", "jpeg")

        metadata = {"caption": "", "taken_at": "2021-01-01T12:00:00"}
        (posts_dir / "no_caption.jpg.json").write_text(json.dumps(metadata))

        assert (posts_dir / "no_caption.jpg").exists()
        loaded = json.loads((posts_dir / "no_caption.jpg.json").read_text())
        assert loaded["caption"] == ""


class TestInstagramPublicArchived:
    """Tests for archived post handling."""

    def test_archived_post(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should process archived posts."""
        create_instagram_public_export(
            temp_export_dir,
            posts=[
                {
                    "filename": "202101/archived_photo.jpg",
                    "caption": "Archived post",
                    "timestamp": "2021-01-01T12:00:00",
                    "archived": True,
                }
            ],
            include_archived=True,
        )

        # Check media file exists
        archived_dir = temp_export_dir / "media" / "archived_posts" / "202101"
        assert (archived_dir / "archived_photo.jpg").exists()

        # Check HTML metadata exists
        html_dir = temp_export_dir / "your_instagram_activity" / "media"
        assert (html_dir / "archived_posts.html").exists()

        # Verify HTML contains the caption
        html_content = (html_dir / "archived_posts.html").read_text()
        assert "Archived post" in html_content

    def test_mixed_posts_and_archived(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle both regular posts and archived posts."""
        create_instagram_public_export(
            temp_export_dir,
            posts=[
                {
                    "filename": "202101/regular.jpg",
                    "caption": "Regular post",
                    "timestamp": "2021-01-01T12:00:00",
                    "archived": False,
                },
                {
                    "filename": "202101/archived.jpg",
                    "caption": "Archived post",
                    "timestamp": "2021-01-15T12:00:00",
                    "archived": True,
                },
            ],
            include_archived=True,
        )

        assert (temp_export_dir / "media" / "posts" / "202101" / "regular.jpg").exists()
        assert (
            temp_export_dir / "media" / "archived_posts" / "202101" / "archived.jpg"
        ).exists()


class TestInstagramPublicCarousel:
    """Tests for carousel/multi-photo post handling."""

    def test_carousel_post(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle carousel posts with multiple photos."""
        posts_dir = temp_export_dir / "media" / "posts" / "202101"
        posts_dir.mkdir(parents=True)

        # Create carousel files
        write_media_file(posts_dir / "carousel_1.jpg", "jpeg")
        write_media_file(posts_dir / "carousel_2.jpg", "jpeg")
        write_media_file(posts_dir / "carousel_3.jpg", "jpeg")

        # Each image gets its own metadata
        for i in range(1, 4):
            metadata = {"caption": "Carousel post", "taken_at": "2021-01-01T12:00:00"}
            (posts_dir / f"carousel_{i}.jpg.json").write_text(json.dumps(metadata))

        assert (posts_dir / "carousel_1.jpg").exists()
        assert (posts_dir / "carousel_2.jpg").exists()
        assert (posts_dir / "carousel_3.jpg").exists()

    def test_mixed_media_carousel(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle carousel with mixed photos and videos."""
        posts_dir = temp_export_dir / "media" / "posts" / "202101"
        posts_dir.mkdir(parents=True)

        write_media_file(posts_dir / "carousel_1.jpg", "jpeg")
        write_media_file(posts_dir / "carousel_2.mp4", "mp4")
        write_media_file(posts_dir / "carousel_3.jpg", "jpeg")

        metadata = {"caption": "Mixed carousel", "taken_at": "2021-01-01T12:00:00"}
        for filename in ["carousel_1.jpg", "carousel_2.mp4", "carousel_3.jpg"]:
            (posts_dir / f"{filename}.json").write_text(json.dumps(metadata))

        assert (posts_dir / "carousel_1.jpg").exists()
        assert (posts_dir / "carousel_2.mp4").exists()
        assert (posts_dir / "carousel_3.jpg").exists()


class TestInstagramPublicFolderOrganization:
    """Tests for YYYYMM folder organization."""

    def test_yyyymm_folders(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should organize posts in YYYYMM folders."""
        create_instagram_public_export(
            temp_export_dir,
            posts=[
                {
                    "filename": "202101/jan.jpg",
                    "caption": "January",
                    "timestamp": "2021-01-15T12:00:00",
                    "archived": False,
                },
                {
                    "filename": "202102/feb.jpg",
                    "caption": "February",
                    "timestamp": "2021-02-15T12:00:00",
                    "archived": False,
                },
                {
                    "filename": "202103/mar.jpg",
                    "caption": "March",
                    "timestamp": "2021-03-15T12:00:00",
                    "archived": False,
                },
            ],
            include_archived=False,
        )

        posts_dir = temp_export_dir / "media" / "posts"
        assert (posts_dir / "202101" / "jan.jpg").exists()
        assert (posts_dir / "202102" / "feb.jpg").exists()
        assert (posts_dir / "202103" / "mar.jpg").exists()

    def test_multiple_posts_same_month(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle multiple posts in same month folder."""
        create_instagram_public_export(
            temp_export_dir,
            posts=[
                {
                    "filename": "202101/post1.jpg",
                    "caption": "Post 1",
                    "timestamp": "2021-01-01T12:00:00",
                    "archived": False,
                },
                {
                    "filename": "202101/post2.jpg",
                    "caption": "Post 2",
                    "timestamp": "2021-01-15T12:00:00",
                    "archived": False,
                },
                {
                    "filename": "202101/post3.jpg",
                    "caption": "Post 3",
                    "timestamp": "2021-01-30T12:00:00",
                    "archived": False,
                },
            ],
            include_archived=False,
        )

        posts_dir = temp_export_dir / "media" / "posts" / "202101"
        assert (posts_dir / "post1.jpg").exists()
        assert (posts_dir / "post2.jpg").exists()
        assert (posts_dir / "post3.jpg").exists()


class TestInstagramPublicFileTypes:
    """Tests for various file types in Instagram Public Media."""

    def test_jpg_post(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle JPG image posts."""
        create_instagram_public_export(
            temp_export_dir,
            posts=[
                {
                    "filename": "202101/image.jpg",
                    "caption": "JPG",
                    "timestamp": "2021-01-01T12:00:00",
                    "archived": False,
                }
            ],
            include_archived=False,
        )
        assert (temp_export_dir / "media" / "posts" / "202101" / "image.jpg").exists()

    def test_mp4_post(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle MP4 video posts."""
        posts_dir = temp_export_dir / "media" / "posts" / "202101"
        posts_dir.mkdir(parents=True)

        write_media_file(posts_dir / "video.mp4", "mp4")
        (posts_dir / "video.mp4.json").write_text(
            '{"caption": "Video", "taken_at": "2021-01-01T12:00:00"}'
        )

        assert (posts_dir / "video.mp4").exists()


class TestInstagramPublicRealisticFixtures:
    """Processing tests using committed schema-faithful Instagram Public fixtures."""

    def test_posts_and_archived_posts_structure(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly parse posts and archived_posts directories."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "instagram_public", "posts_and_archived_posts", temp_export_dir
        )

        media_dir = temp_export_dir / "media"

        # Verify posts structure
        assert (media_dir / "posts" / "202301").exists()
        assert (media_dir / "posts" / "202302").exists()

        # Verify archived_posts structure
        assert (media_dir / "archived_posts" / "202212").exists()

    def test_preprocessing_timestamp_timezone_behavior(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should treat official-export HTML timestamps as UTC-08:00 and convert to canonical UTC Z."""
        from tests.fixtures.export_tree import copy_export_fixture
        from processors.instagram_public_media.preprocess import InstagramPreprocessor

        copy_export_fixture(
            "instagram_public", "posts_and_archived_posts", temp_export_dir
        )

        preprocessor = InstagramPreprocessor(
            export_path=temp_export_dir,
            output_dir=temp_output_dir,
        )
        assert preprocessor.run() is True

        metadata_file = temp_output_dir / "metadata.json"
        assert metadata_file.exists()
        output_metadata = json.loads(metadata_file.read_text())
        posts = output_metadata.get("media", [])

        # Fixture HTML timestamps:
        #   "Jan 15, 2023 5:30 pm" -> 2023-01-16T01:30:00Z
        #   "Feb 20, 2023 2:15 pm" -> 2023-02-20T22:15:00Z
        captions_to_ts = {p.get("caption"): p.get("timestamp") for p in posts}

        assert (
            captions_to_ts.get("Beautiful sunset #photography #nature")
            == "2023-01-16T01:30:00Z"
        )
        assert captions_to_ts.get("Check out this video!") == "2023-02-20T22:15:00Z"

    def test_yyyymm_folder_organization(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should correctly organize posts by YYYYMM folders."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "instagram_public", "posts_and_archived_posts", temp_export_dir
        )

        media_dir = temp_export_dir / "media"

        # January 2023 post
        assert (media_dir / "posts" / "202301" / "photo_123.jpg").exists()
        # February 2023 post
        assert (media_dir / "posts" / "202302" / "video_456.mp4").exists()
        # December 2022 archived post
        assert (media_dir / "archived_posts" / "202212" / "archived_photo.jpg").exists()

    def test_json_sidecar_metadata(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should have JSON sidecar files with documented fields."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "instagram_public", "posts_and_archived_posts", temp_export_dir
        )

        # Check posts JSON
        posts_json = (
            temp_export_dir / "media" / "posts" / "202301" / "photo_123.jpg.json"
        )
        posts_data = json.loads(posts_json.read_text())
        assert "caption" in posts_data
        assert "taken_at" in posts_data
        assert "#photography" in posts_data["caption"]

        # Check archived JSON
        archived_json = (
            temp_export_dir
            / "media"
            / "archived_posts"
            / "202212"
            / "archived_photo.jpg.json"
        )
        archived_data = json.loads(archived_json.read_text())
        assert "caption" in archived_data
        assert "taken_at" in archived_data

    def test_mixed_media_types(
        self, instagram_public_processor, temp_export_dir, temp_output_dir
    ):
        """Should handle both photos and videos in posts."""
        from tests.fixtures.export_tree import copy_export_fixture

        copy_export_fixture(
            "instagram_public", "posts_and_archived_posts", temp_export_dir
        )

        posts_dir = temp_export_dir / "media" / "posts"

        # Photo
        photo = posts_dir / "202301" / "photo_123.jpg"
        assert photo.exists()
        assert photo.stat().st_size > 0

        # Video
        video = posts_dir / "202302" / "video_456.mp4"
        assert video.exists()
        assert video.stat().st_size > 0
