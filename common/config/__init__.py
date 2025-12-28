"""
Configuration utilities for media processors.

This package provides:
- Environment variable loading from .env files
"""

from common.config.env import load_dotenv_file

__all__ = [
    "load_dotenv_file",
]
