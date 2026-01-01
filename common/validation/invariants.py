#!/usr/bin/env python3
"""
Shared invariant validators for failure handling.
"""

from __future__ import annotations

from pathlib import Path


class InvariantViolation(ValueError):
    """Raised when a required invariant is violated."""


def require_non_unknown_username(
    username: str, *, source: str, export_dir: Path, processor: str
) -> None:
    """
    Ensure we never proceed with an unknown export username.

    Raises:
        InvariantViolation if username is falsy or equals "unknown".
    """
    if not username or username == "unknown":
        raise InvariantViolation(
            f"[{processor}] Export username is unknown "
            f"(source={source}, export={export_dir})"
        )


def validate_gps(lat: float, lon: float, *, processor: str, file_path: str) -> None:
    """
    Validate GPS coordinates before embedding.

    Raises:
        InvariantViolation if coordinates are invalid.
    """
    if lat is None or lon is None:
        raise InvariantViolation(
            f"[{processor}] GPS coordinates missing for {file_path}"
        )

    if lat == 0.0 and lon == 0.0:
        raise InvariantViolation(
            f"[{processor}] GPS coordinates (0.0, 0.0) are invalid for {file_path}"
        )

    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        raise InvariantViolation(
            f"[{processor}] GPS coordinates out of range for {file_path}: "
            f"({lat}, {lon})"
        )
