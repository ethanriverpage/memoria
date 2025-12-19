"""
Configuration utilities for media processors.

This package provides:
- Processor-specific output directory configuration
- Environment variable loading from .env files
"""

from common.config.processor import (
    PROCESSOR_SUBDIRS,
    PROCESSOR_DEFAULTS,
    get_effective_output_dir,
)
from common.config.env import load_dotenv_file

__all__ = [
    # Processor config
    "PROCESSOR_SUBDIRS",
    "PROCESSOR_DEFAULTS",
    "get_effective_output_dir",
    # Env loader
    "load_dotenv_file",
]
