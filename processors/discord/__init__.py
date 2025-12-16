#!/usr/bin/env python3
"""
Discord Processor Module

Handles Discord data exports, downloading media attachments from CDN URLs
and embedding metadata.
"""

from processors.discord.processor import DiscordProcessor, get_processor

__all__ = ["DiscordProcessor", "get_processor"]

