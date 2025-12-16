#!/usr/bin/env python3
"""
Immich uploader utilities

Thin wrapper around the Immich CLI to perform auth verification and uploads
with controlled arguments. Avoids storing credentials on disk by passing
URL and API key per invocation.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional

from common.utils import parse_bool_env


def _base_env(url: Optional[str], key: Optional[str]) -> dict:
    """Prepare env for immich CLI with URL/key if provided."""
    env = os.environ.copy()
    if url:
        env["IMMICH_INSTANCE_URL"] = url
    if key:
        env["IMMICH_API_KEY"] = key
    return env


def verify_auth(url: Optional[str], key: Optional[str]) -> bool:
    """Verify Immich connectivity/credentials by calling server-info.

    Uses env vars to avoid leaking secrets in process args.
    """
    try:
        subprocess.run(
            ["immich", "server-info"],
            env=_base_env(url, key),
            capture_output=True,
            check=True,
            text=True,
            stdin=subprocess.DEVNULL,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def upload(
    path: str,
    album: str,
    url: Optional[str],
    key: Optional[str],
    skip_hash: bool = True,
    concurrency: int = 4,
    include_hidden: bool = False,
    recursive: bool = True,
    ignore_patterns: Optional[list[str]] = None,
) -> int:
    """Upload files/folders at path to Immich under a specific album.

    Args:
        path: Path to upload
        album: Album name in Immich
        url: Immich server URL
        key: Immich API key
        skip_hash: Skip hash checking for faster uploads
        concurrency: Number of concurrent uploads
        include_hidden: Include hidden files
        recursive: Recursively upload directories
        ignore_patterns: List of glob patterns to ignore (e.g., ["issues/**", "**/*matching/**"])

    Returns process returncode (0 for success).
    """
    # Check environment variable for skip_hash override
    env_skip_hash = os.environ.get("IMMICH_SKIP_HASH")
    if env_skip_hash is not None:
        skip_hash = parse_bool_env(env_skip_hash)
    
    # Default ignore patterns from environment or use provided patterns
    if ignore_patterns is None:
        env_ignore = os.environ.get("IMMICH_IGNORE_PATTERNS")
        if env_ignore:
            ignore_patterns = [p.strip() for p in env_ignore.split(",") if p.strip()]
        else:
            # Default patterns to exclude problematic folders
            # Note: needs_matching requires **/*matching/** pattern due to underscore handling
            ignore_patterns = ["issues/**", "**/*matching/**"]
    
    cmd = [
        "immich",
        "upload",
        "-A",
        album,
        "-c",
        str(concurrency),
    ]
    if skip_hash:
        cmd.append("--skip-hash")
    if include_hidden:
        cmd.append("--include-hidden")
    if recursive:
        cmd.append("--recursive")
    
    # Add ignore patterns
    for pattern in ignore_patterns:
        cmd.extend(["--ignore", pattern])
    
    cmd.append(path)

    # Ensure album string is not empty and path exists
    if not album.strip():
        raise ValueError("Album name must not be empty")
    if not Path(path).exists():
        raise FileNotFoundError(f"Upload path does not exist: {path}")

    proc = subprocess.run(
        cmd,
        env=_base_env(url, key),
        text=True,
        capture_output=False,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    return proc.returncode


