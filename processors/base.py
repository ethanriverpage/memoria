#!/usr/bin/env python3
"""Base class for all media processors."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from common.output.paths import OutputInfo


class ProcessorBase(ABC):
    """Base class for all media processors."""

    @staticmethod
    @abstractmethod
    def detect(input_path: Path) -> bool:
        """Check if this processor can handle the input directory."""
        pass

    @staticmethod
    @abstractmethod
    def get_name() -> str:
        """Return human-readable processor name."""
        pass

    @staticmethod
    @abstractmethod
    def get_priority() -> int:
        """Return execution priority (higher = run first, range 1-100)."""
        pass

    @staticmethod
    @abstractmethod
    def get_output_info() -> OutputInfo:
        """Return output configuration for Platform/User/Service structure.

        Returns:
            OutputInfo with platform, service, and username_prefix.

        Example:
            return OutputInfo(
                platform="Google",
                service="photos",
                username_prefix="google",
            )
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
        """Process the input directory."""
        pass

    @staticmethod
    def supports_consolidation() -> bool:
        """Return True if processor supports multi-export consolidation."""
        return False

    @staticmethod
    def process_consolidated(
        input_dirs: List[str], output_dir: Optional[str] = None, **kwargs
    ) -> bool:
        """Process multiple exports as a single consolidated unit."""
        raise NotImplementedError(
            "Processor declares supports_consolidation=True "
            "but does not implement process_consolidated()"
        )

    @staticmethod
    def supports_dedup() -> bool:
        """Return True if processor supports cross-processor deduplication."""
        return False

    @staticmethod
    def build_catalog(input_dir: str, **kwargs) -> Iterator[Dict[str, Any]]:
        """Build catalog by yielding entries one at a time (memory-efficient).

        Yields catalog entries as they are computed, allowing the caller to
        write them to a database incrementally without holding all entries
        in memory.

        Args:
            input_dir: Path to input directory.
            **kwargs: Additional arguments (verbose, workers).

        Yields:
            Catalog entry dictionaries with hashes and metadata.
        """
        raise NotImplementedError(
            "Processor declares supports_dedup=True "
            "but does not implement build_catalog()"
        )
