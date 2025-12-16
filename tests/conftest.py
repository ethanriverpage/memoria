"""
Pytest configuration and shared fixtures for Memoria tests.

This module provides:
- Session-scoped fixtures for test export directories
- Per-test temporary output directories
- Processor import helpers
- Common assertion utilities
"""

import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Type

import pytest

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from processors.base import ProcessorBase  # noqa: E402
from processors.registry import ProcessorRegistry  # noqa: E402


# ============================================================================
# Session-scoped fixtures - created once per test session
# ============================================================================


@pytest.fixture(scope="session")
def project_root() -> Path:
    """Return the project root directory."""
    return PROJECT_ROOT


@pytest.fixture(scope="session")
def test_exports_dir(tmp_path_factory) -> Path:
    """Create a session-scoped directory for test exports.

    This directory persists for the entire test session and is
    automatically cleaned up after all tests complete.
    """
    return tmp_path_factory.mktemp("test_exports")


@pytest.fixture(scope="session")
def processor_registry() -> ProcessorRegistry:
    """Create and populate a processor registry with all available processors."""
    from importlib import import_module

    registry = ProcessorRegistry()
    processors_dir = PROJECT_ROOT / "processors"

    for pkg_dir in processors_dir.iterdir():
        if pkg_dir.is_dir() and (pkg_dir / "processor.py").exists():
            pkg = pkg_dir.name
            module_name = f"processors.{pkg}.processor"
            try:
                module = import_module(module_name)
                if hasattr(module, "get_processor"):
                    processor = module.get_processor()
                    registry.register(processor)
            except ImportError:
                pass  # Skip processors that fail to import

    return registry


# ============================================================================
# Function-scoped fixtures - created fresh for each test
# ============================================================================


@pytest.fixture
def temp_output_dir(tmp_path) -> Path:
    """Create a temporary output directory for a single test.

    This directory is automatically cleaned up after each test.
    """
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return output_dir


@pytest.fixture
def temp_export_dir(tmp_path) -> Path:
    """Create a temporary export directory for a single test.

    This directory is automatically cleaned up after each test.
    """
    export_dir = tmp_path / "export"
    export_dir.mkdir()
    return export_dir


# ============================================================================
# Export generator fixtures
# ============================================================================


@pytest.fixture
def google_photos_export(temp_export_dir) -> Path:
    """Create a minimal Google Photos export."""
    from tests.fixtures.generators import create_google_photos_export
    return create_google_photos_export(temp_export_dir)


@pytest.fixture
def google_chat_export(temp_export_dir) -> Path:
    """Create a minimal Google Chat export."""
    from tests.fixtures.generators import create_google_chat_export
    return create_google_chat_export(temp_export_dir)


@pytest.fixture
def google_voice_export(temp_export_dir) -> Path:
    """Create a minimal Google Voice export."""
    from tests.fixtures.generators import create_google_voice_export
    return create_google_voice_export(temp_export_dir)


@pytest.fixture
def snapchat_memories_export(temp_export_dir) -> Path:
    """Create a minimal Snapchat Memories export."""
    from tests.fixtures.generators import create_snapchat_memories_export
    return create_snapchat_memories_export(temp_export_dir)


@pytest.fixture
def snapchat_messages_export(temp_export_dir) -> Path:
    """Create a minimal Snapchat Messages export."""
    from tests.fixtures.generators import create_snapchat_messages_export
    return create_snapchat_messages_export(temp_export_dir)


@pytest.fixture
def instagram_messages_export(temp_export_dir) -> Path:
    """Create a minimal Instagram Messages export."""
    from tests.fixtures.generators import create_instagram_messages_export
    return create_instagram_messages_export(temp_export_dir)


@pytest.fixture
def instagram_public_export(temp_export_dir) -> Path:
    """Create a minimal Instagram Public Media export."""
    from tests.fixtures.generators import create_instagram_public_export
    return create_instagram_public_export(temp_export_dir)


@pytest.fixture
def instagram_old_export(temp_export_dir) -> Path:
    """Create a minimal Instagram Old Format export."""
    from tests.fixtures.generators import create_instagram_old_export
    return create_instagram_old_export(temp_export_dir)


@pytest.fixture
def discord_export(temp_export_dir) -> Path:
    """Create a minimal Discord export."""
    from tests.fixtures.generators import create_discord_export
    return create_discord_export(temp_export_dir)


@pytest.fixture
def imessage_mac_export(temp_export_dir) -> Path:
    """Create a minimal iMessage Mac export."""
    from tests.fixtures.generators import create_imessage_mac_export
    return create_imessage_mac_export(temp_export_dir)


@pytest.fixture
def imessage_iphone_export(temp_export_dir) -> Path:
    """Create a minimal iMessage iPhone export."""
    from tests.fixtures.generators import create_imessage_iphone_export
    return create_imessage_iphone_export(temp_export_dir)


# ============================================================================
# Processor class fixtures
# ============================================================================


@pytest.fixture
def google_photos_processor() -> Type[ProcessorBase]:
    """Return the Google Photos processor class."""
    from processors.google_photos.processor import GooglePhotosProcessor
    return GooglePhotosProcessor


@pytest.fixture
def google_chat_processor() -> Type[ProcessorBase]:
    """Return the Google Chat processor class."""
    from processors.google_chat.processor import GoogleChatProcessor
    return GoogleChatProcessor


@pytest.fixture
def google_voice_processor() -> Type[ProcessorBase]:
    """Return the Google Voice processor class."""
    from processors.google_voice.processor import GoogleVoiceProcessor
    return GoogleVoiceProcessor


@pytest.fixture
def snapchat_memories_processor() -> Type[ProcessorBase]:
    """Return the Snapchat Memories processor class."""
    from processors.snapchat_memories.processor import SnapchatMemoriesProcessor
    return SnapchatMemoriesProcessor


@pytest.fixture
def snapchat_messages_processor() -> Type[ProcessorBase]:
    """Return the Snapchat Messages processor class."""
    from processors.snapchat_messages.processor import SnapchatMessagesProcessor
    return SnapchatMessagesProcessor


@pytest.fixture
def instagram_messages_processor() -> Type[ProcessorBase]:
    """Return the Instagram Messages processor class."""
    from processors.instagram_messages.processor import InstagramMessagesProcessor
    return InstagramMessagesProcessor


@pytest.fixture
def instagram_public_processor() -> Type[ProcessorBase]:
    """Return the Instagram Public Media processor class."""
    from processors.instagram_public_media.processor import InstagramPublicMediaProcessor
    return InstagramPublicMediaProcessor


@pytest.fixture
def instagram_old_processor() -> Type[ProcessorBase]:
    """Return the Instagram Old Format processor class."""
    from processors.instagram_old_public_media.processor import OldInstagramPublicMediaProcessor
    return OldInstagramPublicMediaProcessor


@pytest.fixture
def discord_processor() -> Type[ProcessorBase]:
    """Return the Discord processor class."""
    from processors.discord.processor import DiscordProcessor
    return DiscordProcessor


@pytest.fixture
def imessage_processor() -> Type[ProcessorBase]:
    """Return the iMessage processor class."""
    from processors.imessage.processor import IMessageProcessor
    return IMessageProcessor


# ============================================================================
# Utility functions and helpers
# ============================================================================


def assert_files_exist(directory: Path, expected_files: List[str]) -> None:
    """Assert that all expected files exist in the directory.

    Args:
        directory: Directory to check
        expected_files: List of relative file paths that should exist
    """
    for file_path in expected_files:
        full_path = directory / file_path
        assert full_path.exists(), f"Expected file does not exist: {full_path}"


def assert_files_not_exist(directory: Path, unexpected_files: List[str]) -> None:
    """Assert that files do NOT exist in the directory.

    Args:
        directory: Directory to check
        unexpected_files: List of relative file paths that should NOT exist
    """
    for file_path in unexpected_files:
        full_path = directory / file_path
        assert not full_path.exists(), f"Unexpected file exists: {full_path}"


def count_files_by_extension(directory: Path, extension: str) -> int:
    """Count files with a given extension in a directory (recursive).

    Args:
        directory: Directory to search
        extension: File extension (without dot, e.g., "jpg")

    Returns:
        Number of files with the given extension
    """
    return len(list(directory.rglob(f"*.{extension}")))


def get_all_files(directory: Path, recursive: bool = True) -> List[Path]:
    """Get all files in a directory.

    Args:
        directory: Directory to search
        recursive: Whether to search recursively

    Returns:
        List of file paths
    """
    if recursive:
        return [p for p in directory.rglob("*") if p.is_file()]
    else:
        return [p for p in directory.iterdir() if p.is_file()]


# ============================================================================
# Markers and skip conditions
# ============================================================================


def requires_exiftool():
    """Skip test if exiftool is not available."""
    try:
        import subprocess
        result = subprocess.run(
            ["exiftool", "-ver"],
            capture_output=True,
            text=True
        )
        return pytest.mark.skipif(
            result.returncode != 0,
            reason="exiftool not available"
        )
    except FileNotFoundError:
        return pytest.mark.skip(reason="exiftool not installed")


def requires_ffmpeg():
    """Skip test if ffmpeg is not available."""
    try:
        import subprocess
        result = subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            text=True
        )
        return pytest.mark.skipif(
            result.returncode != 0,
            reason="ffmpeg not available"
        )
    except FileNotFoundError:
        return pytest.mark.skip(reason="ffmpeg not installed")


# ============================================================================
# Tool availability fixtures
# ============================================================================


@pytest.fixture(scope="session")
def exiftool_available() -> bool:
    """Check if exiftool is available on the system."""
    return shutil.which("exiftool") is not None


@pytest.fixture(scope="session")
def ffmpeg_available() -> bool:
    """Check if ffmpeg is available on the system."""
    return shutil.which("ffmpeg") is not None


@pytest.fixture(scope="session")
def external_tools_available(exiftool_available, ffmpeg_available) -> Dict[str, bool]:
    """Return availability status of all external tools."""
    return {
        "exiftool": exiftool_available,
        "ffmpeg": ffmpeg_available,
    }


# ============================================================================
# Directory comparison helpers
# ============================================================================


def assert_directories_equal(
    dir1: Path,
    dir2: Path,
    compare_contents: bool = False,
    ignore_patterns: Optional[List[str]] = None
) -> None:
    """Assert that two directories have the same structure and optionally content.

    Args:
        dir1: First directory to compare
        dir2: Second directory to compare
        compare_contents: If True, also compare file contents (not just names)
        ignore_patterns: List of glob patterns to ignore (e.g., ["*.log", "*.tmp"])

    Raises:
        AssertionError: If directories differ
    """
    if ignore_patterns is None:
        ignore_patterns = []

    def should_ignore(path: Path) -> bool:
        for pattern in ignore_patterns:
            if path.match(pattern):
                return True
        return False

    def get_relative_files(directory: Path) -> Dict[str, Path]:
        files = {}
        for f in directory.rglob("*"):
            if f.is_file() and not should_ignore(f):
                rel_path = str(f.relative_to(directory))
                files[rel_path] = f
        return files

    files1 = get_relative_files(dir1)
    files2 = get_relative_files(dir2)

    # Compare file sets
    set1 = set(files1.keys())
    set2 = set(files2.keys())

    missing_in_2 = set1 - set2
    missing_in_1 = set2 - set1

    assert not missing_in_2, f"Files in {dir1} but not in {dir2}: {missing_in_2}"
    assert not missing_in_1, f"Files in {dir2} but not in {dir1}: {missing_in_1}"

    # Optionally compare contents
    if compare_contents:
        import hashlib

        for rel_path in files1:
            path1 = files1[rel_path]
            path2 = files2[rel_path]

            hash1 = hashlib.md5(path1.read_bytes()).hexdigest()
            hash2 = hashlib.md5(path2.read_bytes()).hexdigest()

            assert hash1 == hash2, f"Content differs for {rel_path}"


def count_files_recursive(directory: Path, extensions: Optional[List[str]] = None) -> int:
    """Count files in directory recursively, optionally filtering by extension.

    Args:
        directory: Directory to count files in
        extensions: Optional list of extensions to filter (e.g., [".jpg", ".png"])

    Returns:
        Number of matching files
    """
    count = 0
    for f in directory.rglob("*"):
        if f.is_file():
            if extensions is None or f.suffix.lower() in extensions:
                count += 1
    return count


def get_file_sizes(directory: Path) -> Dict[str, int]:
    """Get sizes of all files in directory.

    Args:
        directory: Directory to scan

    Returns:
        Dict mapping relative paths to file sizes in bytes
    """
    sizes = {}
    for f in directory.rglob("*"):
        if f.is_file():
            rel_path = str(f.relative_to(directory))
            sizes[rel_path] = f.stat().st_size
    return sizes


# ============================================================================
# Test configuration
# ============================================================================


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests requiring external tools (exiftool, ffmpeg)"
    )


def pytest_sessionfinish(session, exitstatus):
    """Clean up test output directories after test session completes.
    
    Removes final_* directories that are created as default output locations
    during processor tests.
    """
    # Clean up final_* directories in project root
    for pattern in ["final_*", "pre"]:
        for path in PROJECT_ROOT.glob(pattern):
            if path.is_dir():
                try:
                    shutil.rmtree(path)
                except Exception:
                    pass  # Ignore errors during cleanup

