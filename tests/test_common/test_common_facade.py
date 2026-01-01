#!/usr/bin/env python3
"""Smoke tests for the `common` facade exports.

This ensures `common/__init__.py` continues to re-export the public API that
processors and docs rely on.
"""


def test_common_facade_exports_and_all():
    import common

    expected_names = [
        # Failures
        "FailureTracker",
        # Files / naming
        "FilenameGenerator",
        "sanitize_filename",
        # Processing
        "process_batches_parallel",
        "temp_processing_directory",
        # Output / summary
        "print_processing_summary",
        "print_preprocessing_stats",
        # Text
        "normalize_text",
        # Types / extensions
        "IMAGE_EXTENSIONS",
        "VIDEO_EXTENSIONS",
        "ALL_MEDIA_EXTENSIONS",
    ]

    for name in expected_names:
        assert hasattr(common, name), f"common is missing facade export: {name}"
        assert name in common.__all__, f"common.__all__ is missing: {name}"
