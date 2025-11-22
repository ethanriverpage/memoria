#!/usr/bin/env python3
"""
Lightweight .env loader with graceful fallback if python-dotenv is unavailable.

Loads key=value pairs into os.environ without overriding existing env vars.
"""

import os
from pathlib import Path
from typing import Optional


def load_dotenv_file(path: Optional[str]) -> None:
    """Load a .env file if present, without overwriting existing env vars.

    Args:
        path: Path to .env; if None, tries project root `.env` (cwd).
    """
    # Prefer python-dotenv if available
    try:
        from dotenv import load_dotenv  # type: ignore

        env_path = Path(path) if path else Path.cwd() / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path, override=False)
        return
    except Exception:
        # Fall back to simple parser
        pass

    env_path = Path(path) if path else Path.cwd() / ".env"
    if not env_path.exists():
        return

    try:
        with env_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception:
        # Silently ignore malformed files
        return


