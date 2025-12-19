#!/usr/bin/env python3
"""Base class for all media processors."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class ProcessorBase(ABC):
    """Base class for all media processors

    All media processors must inherit from this class and implement
    the required abstract methods: detect(), get_name(), get_priority(),
    and process().

    Optional methods for cross-processor deduplication:
    - build_catalog(): Build hash catalog without copying files
    - supports_dedup(): Return True to participate in cross-processor dedup
    """

    @staticmethod
    @abstractmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory

        Args:
            input_path: Path to the input directory

        Returns:
            True if this processor can handle the input, False otherwise
        """
        pass

    @staticmethod
    @abstractmethod
    def get_name() -> str:
        """Return human-readable processor name

        Returns:
            Name of the processor (e.g., "Google Photos", "Snapchat Messages")
        """
        pass

    @staticmethod
    @abstractmethod
    def get_priority() -> int:
        """Return execution priority for ordering (higher = run first).

        Note: Priority only affects ORDER, not which processors run.
        All matching processors will run regardless of priority.

        Priority Guidelines:
        --------------------
        - 80-100: High priority (very specific detection patterns)
          Use when detection is highly specific with multiple required elements.
          Examples:
            * Snapchat Memories: Requires media/, overlays/, AND metadata.json
            * Processors checking for specific JSON structure/schema

        - 50-79: Medium priority (moderately specific patterns)
          Use when detection relies on directory structure or multiple files.
          Examples:
            * Google Photos: Requires Google Photos/ directory with album folders
            * Instagram Messages: Requires specific directory path structure

        - 1-49: Low priority (broad/generic patterns)
          Use when detection uses only filename patterns or single indicators.
          Examples:
            * Instagram Old Format: Only checks filename patterns in root
            * Processors that match common file extensions or simple patterns

        Best Practices:
        ---------------
        - Higher specificity = Higher priority
        - More required elements = Higher priority
        - Avoid priority ties when possible (use different values)
        - Document why you chose a specific priority value

        Bad Examples:
        -------------
        - Setting high priority (90+) for filename-only detection
        - Setting low priority (30-) for multi-requirement validation
        - Using same priority as existing processor without justification

        Returns:
            Priority value as integer (1-100)
        """
        pass

    @staticmethod
    @abstractmethod
    def process(
        input_dir: str,
        output_dir: Optional[str] = None,
        dedup_manifest: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> bool:
        """Process the input directory

        Args:
            input_dir: Path to input directory (as string)
            output_dir: Optional base output directory. If provided, processor
                       should create a subdirectory within it. If None, use
                       processor's default output location.
            dedup_manifest: Optional deduplication manifest from cross-processor
                           analysis. If provided, processor should respect
                           primary/secondary designations and skip copying
                           files marked as secondary.
            **kwargs: Additional arguments (verbose, workers, etc.)

        Returns:
            True if processing succeeded, False otherwise
        """
        pass

    @staticmethod
    def supports_consolidation() -> bool:
        """Return True if processor supports multi-export consolidation.

        Override this method to enable consolidation mode for a processor.
        When True, memoria.py will group matching exports and call
        process_consolidated() instead of process() for each.

        Returns:
            True if consolidation is supported, False otherwise
        """
        return False

    @staticmethod
    def process_consolidated(
        input_dirs: List[str], output_dir: Optional[str] = None, **kwargs
    ) -> bool:
        """Process multiple exports as a single consolidated unit.

        Only called when supports_consolidation() returns True and
        multiple matching exports are found.

        Args:
            input_dirs: List of paths to input directories
            output_dir: Base output directory
            **kwargs: Additional arguments (verbose, workers, etc.)

        Returns:
            True if processing succeeded, False otherwise

        Raises:
            NotImplementedError: If processor declares supports_consolidation=True
                but does not implement this method
        """
        raise NotImplementedError(
            "Processor declares supports_consolidation=True "
            "but does not implement process_consolidated()"
        )

    @staticmethod
    def supports_dedup() -> bool:
        """Return True if processor supports cross-processor deduplication.

        Override this method to enable participation in cross-processor
        deduplication. When True, memoria.py will call build_catalog()
        during the catalog phase.

        Processors that handle media files (photos, videos) should return True.
        Processors that handle non-media content (text chats, voice calls)
        may return False.

        Returns:
            True if deduplication is supported, False otherwise
        """
        return False

    @staticmethod
    def build_catalog(input_dir: str, **kwargs) -> Dict[str, Any]:
        """Build catalog of files with hashes and metadata.

        This method is called during the catalog phase of cross-processor
        deduplication. It should scan all media files, compute hashes,
        and return a catalog structure without copying any files.

        Args:
            input_dir: Path to input directory (as string)
            **kwargs: Additional arguments (verbose, workers, etc.)

        Returns:
            Catalog dictionary with structure:
            {
                "processor": str,           # Processor name
                "input_dir": str,           # Input directory path
                "files": [                  # List of file entries
                    {
                        "source_path": str,           # Full path to source file
                        "relative_path": str,         # Path relative to input_dir
                        "content_hash": str,          # xxHash64 hex digest
                        "perceptual_hash": str|None,  # dhash hex or None
                        "file_size": int,             # Size in bytes
                        "dimensions": tuple|None,     # (width, height) or None
                        "timestamp": str|None,        # ISO timestamp or None
                        "metadata": dict,             # Processor-specific metadata
                    },
                    ...
                ]
            }

        Raises:
            NotImplementedError: If processor declares supports_dedup=True
                but does not implement this method
        """
        raise NotImplementedError(
            "Processor declares supports_dedup=True "
            "but does not implement build_catalog()"
        )
