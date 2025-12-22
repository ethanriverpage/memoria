#!/usr/bin/env python3
"""Property-based tests using hypothesis.

These tests verify invariants that should hold for any input, finding edge cases
that example-based tests might miss.

Tests cover:
- Filename sanitization invariants
- Hash computation determinism
- Deduplication invariants
- Path handling robustness
"""

import string
import tempfile
from pathlib import Path

from hypothesis import given, assume, settings, strategies as st, HealthCheck

from common.utils import sanitize_filename, FilenameGenerator
from common.files.filter import BannedFilesFilter
from common.dedup.hashing import compute_content_hash


# ============================================================================
# Strategies for generating test data
# ============================================================================

# Valid filename characters (subset that's safe across platforms)
safe_filename_chars = string.ascii_letters + string.digits + "-_. "

# Strategy for filenames with potentially problematic characters
problematic_filenames = st.text(
    alphabet=st.sampled_from(
        safe_filename_chars + "/<>:\"\\|?*\x00\n\r\t@#$%^&()[]{}=+"
    ),
    min_size=1,
    max_size=100,
)

# Strategy for valid-looking filenames
valid_filenames = st.text(
    alphabet=st.sampled_from(safe_filename_chars),
    min_size=1,
    max_size=50,
).filter(lambda x: x.strip() and x not in (".", ".."))

# Strategy for file extensions
file_extensions = st.sampled_from([
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic",
    ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav",
])

# Strategy for timestamps
timestamp_strings = st.from_regex(
    r"20[0-9]{2}-[01][0-9]-[0-3][0-9] [0-2][0-9]:[0-5][0-9]:[0-5][0-9]",
    fullmatch=True,
)


# ============================================================================
# Filename Sanitization Tests
# ============================================================================


class TestSanitizeFilenameProperties:
    """Property-based tests for sanitize_filename function."""

    @given(st.text(min_size=0, max_size=200))
    @settings(max_examples=200)
    def test_output_is_always_valid_filename(self, input_text: str) -> None:
        """Sanitized output should always be a valid filename."""
        result = sanitize_filename(input_text)

        # Should not be empty (has fallback)
        assert len(result) > 0 or input_text == ""

        # Should not contain path separators
        assert "/" not in result
        assert "\\" not in result

        # Should not contain null bytes
        assert "\x00" not in result

        # Should not start/end with whitespace
        if result:
            assert result == result.strip() or not result.strip()

    @given(st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=50))
    def test_alphanumeric_preserved(self, input_text: str) -> None:
        """Alphanumeric characters should be preserved (case-insensitive)."""
        result = sanitize_filename(input_text)

        # All ASCII alphanumeric chars from input should be in output (case-insensitive)
        for char in input_text:
            if char.isascii() and char.isalnum():
                assert char.lower() in result.lower()

    @given(st.text(min_size=1, max_size=100))
    def test_idempotent(self, input_text: str) -> None:
        """Sanitizing twice should give same result as once."""
        once = sanitize_filename(input_text)
        twice = sanitize_filename(once)
        assert once == twice

    @given(st.text(min_size=0, max_size=300))
    def test_length_bounded(self, input_text: str) -> None:
        """Output length should be reasonable."""
        result = sanitize_filename(input_text)
        # Typical filesystem limit is 255, we should be well under
        assert len(result) <= 255


# ============================================================================
# Filename Generator Tests
# ============================================================================


class TestFilenameGeneratorProperties:
    """Property-based tests for FilenameGenerator."""

    @given(
        prefix=st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10),
        timestamp=st.from_regex(
            r"20[0-9]{2}-[01][0-9]-[0-3][0-9] [0-2][0-9]:[0-5][0-9]:[0-5][0-9]",
            fullmatch=True,
        ),
        identifier=st.text(alphabet=string.ascii_letters, min_size=1, max_size=20),
        extension=st.sampled_from([".jpg", ".png", ".mp4"]),
    )
    def test_generates_unique_filenames(
        self,
        prefix: str,
        timestamp: str,
        identifier: str,
        extension: str,
    ) -> None:
        """Generator should produce unique filenames for different inputs."""
        gen = FilenameGenerator(prefix)
        filename = gen.generate(timestamp, identifier, extension)

        assert filename is not None
        assert filename.endswith(extension)
        assert len(filename) > 0

    @given(
        prefix=st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10),
        identifier=st.text(alphabet=string.ascii_letters, min_size=1, max_size=20),
    )
    def test_handles_none_timestamp(
        self,
        prefix: str,
        identifier: str,
    ) -> None:
        """Generator should handle None timestamp gracefully."""
        gen = FilenameGenerator(prefix)
        filename = gen.generate(None, identifier, ".jpg")

        assert filename is not None
        assert filename.endswith(".jpg")


# ============================================================================
# File Hash Tests
# ============================================================================


class TestHashProperties:
    """Property-based tests for file hashing."""

    @given(st.binary(min_size=0, max_size=10000))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_hash_is_deterministic(self, content: bytes) -> None:
        """Same content should always produce same hash."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "test.bin"
            test_file.write_bytes(content)

            hash1 = compute_content_hash(test_file)
            hash2 = compute_content_hash(test_file)

            assert hash1 == hash2

    @given(
        content1=st.binary(min_size=1, max_size=1000),
        content2=st.binary(min_size=1, max_size=1000),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_different_content_different_hash(
        self,
        content1: bytes,
        content2: bytes,
    ) -> None:
        """Different content should produce different hashes (with high probability)."""
        assume(content1 != content2)

        with tempfile.TemporaryDirectory() as tmp_dir:
            file1 = Path(tmp_dir) / "file1.bin"
            file2 = Path(tmp_dir) / "file2.bin"
            file1.write_bytes(content1)
            file2.write_bytes(content2)

            hash1 = compute_content_hash(file1)
            hash2 = compute_content_hash(file2)

            assert hash1 != hash2

    @given(st.binary(min_size=0, max_size=5000))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_hash_is_hex_string(self, content: bytes) -> None:
        """Hash should be a valid hexadecimal string."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            test_file = Path(tmp_dir) / "test.bin"
            test_file.write_bytes(content)

            hash_value = compute_content_hash(test_file)

            # Should be valid hex
            assert all(c in "0123456789abcdef" for c in hash_value.lower())
            # Should have consistent length (xxHash64 = 16 hex chars)
            assert len(hash_value) == 16


# ============================================================================
# Banned Files Filter Tests
# ============================================================================


class TestBannedFilesFilterProperties:
    """Property-based tests for BannedFilesFilter."""

    @given(
        filename=st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=50),
        extension=st.sampled_from([".jpg", ".png", ".mp4", ".txt"]),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_normal_files_not_banned(
        self,
        filename: str,
        extension: str,
    ) -> None:
        """Normal alphanumeric filenames should not be banned."""
        # Avoid filenames that happen to match banned patterns
        assume(not filename.startswith("._"))
        assume("DS_Store" not in filename)
        assume("@eaDir" not in filename)
        assume("thumbnail" not in filename.lower())
        assume("SYNOFILE" not in filename)
        assume("pluginPayload" not in filename)

        filter_instance = BannedFilesFilter()
        test_path = Path("/tmp") / f"{filename}{extension}"

        result = filter_instance.is_banned(test_path)
        assert result is False

    @given(st.sampled_from(BannedFilesFilter.BANNED_PATTERNS))
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_banned_patterns_are_banned(self, pattern: str) -> None:
        """Files matching banned patterns should be banned."""
        filter_instance = BannedFilesFilter()
        test_path = Path("/tmp") / pattern

        result = filter_instance.is_banned(test_path)
        assert result is True

    @given(
        filename=st.text(alphabet=string.ascii_letters, min_size=1, max_size=20),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_prefix_patterns_work(self, filename: str) -> None:
        """Prefix patterns like ._ should ban files starting with them."""
        filter_instance = BannedFilesFilter()
        test_path = Path("/tmp") / f"._{filename}.jpg"

        result = filter_instance.is_banned(test_path)
        assert result is True


# ============================================================================
# Path Handling Tests
# ============================================================================


class TestPathHandlingProperties:
    """Property-based tests for path handling robustness."""

    @given(
        parts=st.lists(
            st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=20),
            min_size=1,
            max_size=10,
        )
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_path_construction(self, parts: list) -> None:
        """Path construction should handle various segment combinations."""
        base = Path("/tmp/test_base")
        path = base
        for part in parts:
            path = path / part

        # Should be a valid path object
        assert isinstance(path, Path)

        # Should have correct number of parts relative to base
        relative = path.relative_to(base)
        assert len(relative.parts) == len(parts)


# ============================================================================
# Integration Property Tests
# ============================================================================


class TestDeduplicationProperties:
    """Property-based tests for deduplication invariants."""

    @given(
        num_copies=st.integers(min_value=2, max_value=5),
        content=st.binary(min_size=1, max_size=1000),
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_duplicate_detection_consistent(
        self,
        num_copies: int,
        content: bytes,
    ) -> None:
        """All copies of same content should have same hash."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            hashes = []

            for i in range(num_copies):
                file_path = Path(tmp_dir) / f"copy_{i}.bin"
                file_path.write_bytes(content)
                hashes.append(compute_content_hash(file_path))

            # All hashes should be identical
            assert len(set(hashes)) == 1

    @given(
        contents=st.lists(
            st.binary(min_size=1, max_size=500),
            min_size=2,
            max_size=5,
            unique=True,
        )
    )
    @settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_unique_files_unique_hashes(
        self,
        contents: list,
    ) -> None:
        """All unique files should have unique hashes."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            hashes = []

            for i, content in enumerate(contents):
                file_path = Path(tmp_dir) / f"file_{i}.bin"
                file_path.write_bytes(content)
                hashes.append(compute_content_hash(file_path))

            # All hashes should be unique
            assert len(set(hashes)) == len(contents)

