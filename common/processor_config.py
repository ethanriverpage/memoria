#!/usr/bin/env python3
"""
Processor Configuration Module

Centralized configuration for processor-specific settings including
output directory mappings and default directories.
"""

from pathlib import Path
from typing import Optional


# Processor-specific subdirectory mapping
# Maps processor names to their subdirectory within a base output directory
PROCESSOR_SUBDIRS = {
    "Instagram Messages": "messages",
    "Instagram Public Media": "public-media",
    "Instagram Old Public Media": "public-media",
    "Google Photos": "Google Photos",
    "Google Chat": "Google Chat",
    "Google Voice": "Google Voice",
    # Snapchat processors use base directory directly (no subdirectory)
    "Snapchat Messages": None,
    "Snapchat Memories": None,
}

# Default output directories when no output_dir is specified
PROCESSOR_DEFAULTS = {
    "Instagram Messages": "final_instamsgs",
    "Instagram Public Media": "final_instagram",
    "Instagram Old Public Media": "final_media",
    "Google Photos": "final_googlephotos",
    "Google Chat": "final_googlechat",
    "Google Voice": "final_googlevoice",
    "Snapchat Messages": "final_snapmsgs",
    "Snapchat Memories": "final_snapmemories",
}


def get_effective_output_dir(
    processor_name: str, base_output_dir: Optional[str]
) -> str:
    """Get the effective output directory for a processor
    
    Args:
        processor_name: Name of the processor
        base_output_dir: Base output directory (from args.output), or None
        
    Returns:
        Full effective output directory path as string
        
    Examples:
        >>> get_effective_output_dir("Google Photos", "/out")
        '/out/Google Photos'
        >>> get_effective_output_dir("Google Photos", None)
        'final_googlephotos'
        >>> get_effective_output_dir("Snapchat Messages", "/out")
        '/out'
    """
    if base_output_dir:
        # Get the subdirectory for this processor (may be None)
        subdir = PROCESSOR_SUBDIRS.get(processor_name)
        
        if subdir:
            return str(Path(base_output_dir) / subdir)
        else:
            # Use base directory directly
            return base_output_dir
    else:
        # Use processor-specific default
        return PROCESSOR_DEFAULTS.get(processor_name, "")

