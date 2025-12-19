#!/usr/bin/env python3
"""
Configuration for Cross-Processor Deduplication

Handles environment variable loading and configuration management
for the deduplication system.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from common.utils import parse_bool_env


def _parse_float(value: Optional[str], default: float) -> float:
    """Parse a float from environment variable string.

    Args:
        value: String value from environment variable.
        default: Default value if not set or invalid.

    Returns:
        Parsed float value.
    """
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _parse_int(value: Optional[str], default: int) -> int:
    """Parse an integer from environment variable string.

    Args:
        value: String value from environment variable.
        default: Default value if not set or invalid.

    Returns:
        Parsed integer value.
    """
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass
class DedupConfig:
    """Configuration for cross-processor deduplication.

    Attributes:
        enabled: Whether cross-processor deduplication is enabled.
        use_perceptual: Whether to use perceptual hashing for fuzzy matching.
        perceptual_threshold: Similarity threshold for perceptual matching (0.0-1.0).
        database_path: Path to SQLite database for hash persistence.
        desc_max_per_source: Max characters per source in ImageDescription.
        desc_max_total: Max total characters for ImageDescription field.
        msg_truncate: Max characters for message content truncation.
        validate_gps_distance: Max GPS distance (meters) for duplicate validation.
        validate_aspect_ratio: Max aspect ratio difference for validation.
        preprocessing_cache_dir: Directory for preprocessing cache during catalog phase.
    """

    # Feature flags
    enabled: bool = False
    use_perceptual: bool = True

    # Perceptual matching
    perceptual_threshold: float = 0.92

    # Storage
    database_path: Path = field(
        default_factory=lambda: Path.home() / ".memoria" / "dedup.db"
    )

    # ImageDescription formatting
    desc_max_per_source: int = 250
    desc_max_total: int = 1500
    msg_truncate: int = 200

    # Duplicate validation thresholds
    validate_gps_distance: float = 500.0  # meters
    validate_aspect_ratio: float = 0.10  # 10% tolerance

    # Preprocessing cache for catalog phase
    preprocessing_cache_dir: Path = field(
        default_factory=lambda: Path("../pre/preprocessing_cache")
    )

    @classmethod
    def from_env(cls) -> "DedupConfig":
        """Create configuration from environment variables.

        Environment Variables:
            CROSS_PROCESSOR_DEDUP: Enable cross-processor deduplication (true/false)
            DEDUP_USE_PERCEPTUAL: Enable perceptual hashing (true/false)
            DEDUP_PERCEPTUAL_THRESHOLD: Similarity threshold (0.0-1.0)
            DEDUP_DATABASE_PATH: Path to SQLite database
            DEDUP_DESC_MAX_PER_SOURCE: Max chars per source in description
            DEDUP_DESC_MAX_TOTAL: Max total chars in description
            DEDUP_MSG_TRUNCATE: Message content truncation length
            DEDUP_GPS_DISTANCE: Max GPS distance for validation (meters)
            DEDUP_ASPECT_RATIO: Max aspect ratio difference (0.0-1.0)
            DEDUP_PREPROCESSING_CACHE_DIR: Directory for preprocessing cache

        Performance Environment Variables (not part of DedupConfig):
            PARALLEL_CATALOG: Enable parallel catalog building (default: true)
            PARALLEL_DEDUP: Enable parallel perceptual hash comparison (default: true)

        Returns:
            DedupConfig instance populated from environment.
        """
        # Parse database path
        db_path_str = os.environ.get("DEDUP_DATABASE_PATH")
        if db_path_str:
            database_path = Path(db_path_str).expanduser()
        else:
            database_path = Path.home() / ".memoria" / "dedup.db"

        # Parse preprocessing cache directory
        cache_dir_str = os.environ.get("DEDUP_PREPROCESSING_CACHE_DIR")
        if cache_dir_str:
            preprocessing_cache_dir = Path(cache_dir_str).expanduser()
        else:
            preprocessing_cache_dir = Path("../pre/preprocessing_cache")

        return cls(
            enabled=parse_bool_env(os.environ.get("CROSS_PROCESSOR_DEDUP"), False),
            use_perceptual=parse_bool_env(os.environ.get("DEDUP_USE_PERCEPTUAL"), True),
            perceptual_threshold=_parse_float(
                os.environ.get("DEDUP_PERCEPTUAL_THRESHOLD"), 0.92
            ),
            database_path=database_path,
            desc_max_per_source=_parse_int(
                os.environ.get("DEDUP_DESC_MAX_PER_SOURCE"), 250
            ),
            desc_max_total=_parse_int(os.environ.get("DEDUP_DESC_MAX_TOTAL"), 1500),
            msg_truncate=_parse_int(os.environ.get("DEDUP_MSG_TRUNCATE"), 200),
            validate_gps_distance=_parse_float(
                os.environ.get("DEDUP_GPS_DISTANCE"), 500.0
            ),
            validate_aspect_ratio=_parse_float(
                os.environ.get("DEDUP_ASPECT_RATIO"), 0.10
            ),
            preprocessing_cache_dir=preprocessing_cache_dir,
        )

    def to_dict(self) -> dict:
        """Convert configuration to dictionary.

        Returns:
            Dictionary representation of configuration.
        """
        return {
            "enabled": self.enabled,
            "use_perceptual": self.use_perceptual,
            "perceptual_threshold": self.perceptual_threshold,
            "database_path": str(self.database_path),
            "desc_max_per_source": self.desc_max_per_source,
            "desc_max_total": self.desc_max_total,
            "msg_truncate": self.msg_truncate,
            "validate_gps_distance": self.validate_gps_distance,
            "validate_aspect_ratio": self.validate_aspect_ratio,
            "preprocessing_cache_dir": str(self.preprocessing_cache_dir),
        }


# Global configuration instance (lazy-loaded)
_config: Optional[DedupConfig] = None


def get_dedup_config() -> DedupConfig:
    """Get the global deduplication configuration.

    Loads from environment on first call, caches for subsequent calls.

    Returns:
        DedupConfig instance.
    """
    global _config
    if _config is None:
        _config = DedupConfig.from_env()
    return _config


def is_dedup_enabled() -> bool:
    """Check if cross-processor deduplication is enabled.

    Returns:
        True if CROSS_PROCESSOR_DEDUP is set to true.
    """
    return get_dedup_config().enabled


def reset_config() -> None:
    """Reset the cached configuration.

    Forces re-loading from environment on next access.
    Useful for testing.
    """
    global _config
    _config = None
