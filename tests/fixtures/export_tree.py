"""
Utilities for working with committed realistic export fixtures.

These fixtures live under tests/fixtures/exports/<platform>/<case>/...
and represent schema-faithful export structures matching the docs.
"""

import shutil
from pathlib import Path
from typing import List, Optional


# Path to the committed fixtures directory
FIXTURES_DIR = Path(__file__).parent / "exports"


def get_fixture_path(platform: str, case: str) -> Path:
    """Get the path to a specific fixture case.

    Args:
        platform: Platform name (e.g., 'discord', 'snapchat_messages')
        case: Case name (e.g., 'raw_minimal', 'raw_conversation_id_map')

    Returns:
        Path to the fixture directory

    Raises:
        FileNotFoundError: If the fixture does not exist
    """
    fixture_path = FIXTURES_DIR / platform / case
    if not fixture_path.exists():
        raise FileNotFoundError(
            f"Fixture not found: {platform}/{case} at {fixture_path}"
        )
    return fixture_path


def copy_export_fixture(platform: str, case: str, dest: Path) -> Path:
    """Copy a committed fixture tree to a destination directory for testing.

    Args:
        platform: Platform name (e.g., 'discord', 'snapchat_messages')
        case: Case name (e.g., 'raw_minimal', 'raw_conversation_id_map')
        dest: Destination directory (typically a pytest tmp_path)

    Returns:
        Path to the copied fixture (dest directory itself, populated)

    Raises:
        FileNotFoundError: If the fixture does not exist
    """
    fixture_path = get_fixture_path(platform, case)

    # Copy the entire fixture tree to dest
    if dest.exists():
        # Clear existing contents if any
        for item in dest.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    # Copy all contents
    for item in fixture_path.iterdir():
        if item.is_dir():
            shutil.copytree(item, dest / item.name)
        else:
            shutil.copy2(item, dest / item.name)

    return dest


def list_fixture_cases(platform: str) -> List[str]:
    """List all fixture cases for a platform.

    Args:
        platform: Platform name (e.g., 'discord', 'google_photos')

    Returns:
        List of case names available for the platform
    """
    platform_dir = FIXTURES_DIR / platform
    if not platform_dir.exists():
        return []
    return sorted([d.name for d in platform_dir.iterdir() if d.is_dir()])


def list_platforms() -> List[str]:
    """List all platforms with fixtures.

    Returns:
        List of platform names
    """
    if not FIXTURES_DIR.exists():
        return []
    return sorted([d.name for d in FIXTURES_DIR.iterdir() if d.is_dir()])


def fixture_exists(platform: str, case: str) -> bool:
    """Check if a fixture exists.

    Args:
        platform: Platform name
        case: Case name

    Returns:
        True if the fixture exists
    """
    return (FIXTURES_DIR / platform / case).exists()

