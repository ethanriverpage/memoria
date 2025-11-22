#!/usr/bin/env python3
"""
Processor Registry

Manages registration and discovery of all media processors.
Supports detection of multiple processors per input directory.
"""

from pathlib import Path
from typing import List
from processors.base import ProcessorBase


class ProcessorRegistry:
    """Registry for all media processors"""

    def __init__(self):
        """Initialize empty processor registry"""
        self.processors: List[ProcessorBase] = []

    def register(self, processor: ProcessorBase) -> None:
        """Register a processor

        Args:
            processor: A class (not instance) that inherits from ProcessorBase

        Raises:
            TypeError: If processor is not a class or doesn't inherit from ProcessorBase
        """
        # Validate that processor is a class
        if not isinstance(processor, type):
            raise TypeError(
                f"Processor must be a class (not an instance), got {type(processor).__name__}"
            )

        # Validate that processor inherits from ProcessorBase
        if not issubclass(processor, ProcessorBase):
            raise TypeError(
                f"Processor class must inherit from ProcessorBase, got {processor.__name__}"
            )

        self.processors.append(processor)

    def detect_all(self, input_path: Path) -> List[ProcessorBase]:
        """Detect ALL processors that can handle this input

        Unlike typical detection that finds one match, this finds all matches
        because a single export directory can contain multiple types of data
        (e.g., Google export with Photos + Chat + Voice).

        Args:
            input_path: Path to input directory

        Returns:
            List of matching processors, sorted by priority (highest first)
        """
        matches = []

        for processor in self.processors:
            try:
                if processor.detect(input_path):
                    matches.append(processor)
            except Exception as e:
                # Log but don't fail if one detector has issues
                print(f"Warning: Detector for {processor.get_name()} failed: {e}")
                continue

        # Sort by priority (highest first) for consistent ordering
        matches.sort(key=lambda p: p.get_priority(), reverse=True)

        return matches

    def get_all_processors(self) -> List[ProcessorBase]:
        """Get all registered processors sorted by priority

        Returns:
            List of all processors, sorted by priority (highest first)
        """
        return sorted(self.processors, key=lambda p: p.get_priority(), reverse=True)

    def get_processor_count(self) -> int:
        """Get count of registered processors

        Returns:
            Number of registered processors
        """
        return len(self.processors)
