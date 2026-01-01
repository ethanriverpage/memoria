"""Test helper utilities for running processors and making assertions."""

from tests.helpers.processing import (
    run_preprocessor,
    run_processor,
    ProcessingResult,
    load_failure_report,
    assert_no_failures,
    assert_has_media_files,
    assert_metadata_json_valid,
    assert_no_mkv_on_overlay_fail,
    count_output_files,
)

__all__ = [
    "run_preprocessor",
    "run_processor",
    "ProcessingResult",
    "load_failure_report",
    "assert_no_failures",
    "assert_has_media_files",
    "assert_metadata_json_valid",
    "assert_no_mkv_on_overlay_fail",
    "count_output_files",
]
