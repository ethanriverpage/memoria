"""Helper utilities for running processors and making assertions on output.

This module provides:
- ProcessingResult: A dataclass holding output paths and loaded artifacts
- run_preprocessor(): Run a preprocessor and collect results
- run_processor(): Run a processor and collect results
- Assertion primitives for validating outputs
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Type


@dataclass
class ProcessingResult:
    """Result of running a preprocessor or processor."""

    success: bool
    output_dir: Path
    metadata: Optional[Dict[str, Any]] = None
    failure_report: Optional[Dict[str, Any]] = None
    output_files: List[Path] = field(default_factory=list)
    issues_dir: Optional[Path] = None

    def has_failures(self) -> bool:
        """Check if there are any failures in the failure report."""
        if not self.failure_report:
            return False
        for key in [
            "failed_processing",
            "orphaned_media",
            "orphaned_metadata",
            "export_failures",
        ]:
            if self.failure_report.get(key):
                return True
        return False

    def get_failures_by_reason(self, reason: str) -> List[Dict[str, Any]]:
        """Get all failures with a specific reason."""
        if not self.failure_report:
            return []
        results = []
        for key in ["failed_processing", "orphaned_media", "orphaned_metadata"]:
            for entry in self.failure_report.get(key, []):
                if entry.get("reason") == reason:
                    results.append(entry)
        return results

    def count_output_media(self, extensions: Optional[List[str]] = None) -> int:
        """Count output media files, optionally filtered by extension."""
        if extensions is None:
            extensions = [".jpg", ".jpeg", ".png", ".mp4", ".mov", ".mkv", ".webp"]
        return sum(1 for f in self.output_files if f.suffix.lower() in extensions)


def run_preprocessor(
    preprocessor_class: Type,
    export_dir: Path,
    output_dir: Path,
    **kwargs,
) -> ProcessingResult:
    """Run a preprocessor and collect results.

    Args:
        preprocessor_class: The preprocessor class to instantiate
        export_dir: Path to the export directory
        output_dir: Path to the output directory
        **kwargs: Additional arguments to pass to the preprocessor

    Returns:
        ProcessingResult with success status and artifacts
    """
    # Handle iMessage which takes export_paths (list) instead of export_path
    if "IMessagePreprocessor" in preprocessor_class.__name__:
        preprocessor = preprocessor_class(
            export_paths=[export_dir],
            output_dir=output_dir,
            **kwargs,
        )
    else:
        preprocessor = preprocessor_class(
            export_path=export_dir,
            output_dir=output_dir,
            **kwargs,
        )

    success = preprocessor.run()

    return _collect_results(success, output_dir)


def run_processor(
    processor_class: Type,
    input_dir: Path,
    output_dir: Path,
    **kwargs,
) -> ProcessingResult:
    """Run a processor and collect results.

    Args:
        processor_class: The processor class to instantiate
        input_dir: Path to the preprocessed input directory
        output_dir: Path to the output directory
        **kwargs: Additional arguments to pass to the processor

    Returns:
        ProcessingResult with success status and artifacts
    """
    processor = processor_class()
    success = processor.process(
        input_dir=str(input_dir),
        output_dir=str(output_dir),
        **kwargs,
    )

    return _collect_results(success, output_dir)


def _collect_results(success: bool, output_dir: Path) -> ProcessingResult:
    """Collect processing results from output directory."""
    metadata = None
    failure_report = None
    issues_dir = None
    output_files = []

    # Load metadata.json if exists
    metadata_path = output_dir / "metadata.json"
    if metadata_path.exists():
        with open(metadata_path) as f:
            metadata = json.load(f)

    # Find issues directory (could be at root or in subdirectory)
    for issues_candidate in [
        output_dir / "issues",
        output_dir / "messages" / "issues",
        output_dir / "memories" / "issues",
    ]:
        if issues_candidate.is_dir():
            issues_dir = issues_candidate
            break

    # Load failure report if exists
    if issues_dir:
        failure_report_path = issues_dir / "failure-report.json"
        if failure_report_path.exists():
            with open(failure_report_path) as f:
                failure_report = json.load(f)

    # Collect all output files
    if output_dir.exists():
        output_files = [p for p in output_dir.rglob("*") if p.is_file()]

    return ProcessingResult(
        success=success,
        output_dir=output_dir,
        metadata=metadata,
        failure_report=failure_report,
        output_files=output_files,
        issues_dir=issues_dir,
    )


def load_failure_report(output_dir: Path) -> Optional[Dict[str, Any]]:
    """Load failure-report.json from output directory.

    Searches common locations for the failure report.
    """
    for issues_candidate in [
        output_dir / "issues",
        output_dir / "messages" / "issues",
        output_dir / "memories" / "issues",
    ]:
        failure_report_path = issues_candidate / "failure-report.json"
        if failure_report_path.exists():
            with open(failure_report_path) as f:
                return json.load(f)
    return None


def assert_no_failures(result: ProcessingResult) -> None:
    """Assert that the processing result has no failures."""
    assert result.success, "Processing should succeed"
    if result.failure_report:
        failed_processing = result.failure_report.get("failed_processing", [])
        assert len(failed_processing) == 0, (
            f"Expected no processing failures, got: {failed_processing}"
        )


def assert_has_media_files(
    result: ProcessingResult,
    min_count: int = 1,
    extensions: Optional[List[str]] = None,
) -> None:
    """Assert that the output contains at least min_count media files."""
    count = result.count_output_media(extensions)
    assert count >= min_count, (
        f"Expected at least {min_count} media files, found {count}"
    )


def assert_metadata_json_valid(result: ProcessingResult) -> None:
    """Assert that metadata.json exists and has required structure."""
    assert result.metadata is not None, "metadata.json should exist"
    assert "export_info" in result.metadata, "metadata should have export_info"


def assert_no_mkv_on_overlay_fail(result: ProcessingResult) -> None:
    """Assert there are no .mkv files when overlay failures occurred.

    This is a regression test assertion for the bug where overlay failures
    produced phantom .mkv references.
    """
    overlay_failures = result.get_failures_by_reason("overlay_embed_failed")
    if overlay_failures:
        mkv_files = [f for f in result.output_files if f.suffix.lower() == ".mkv"]
        assert len(mkv_files) == 0, (
            f"Found .mkv files with overlay failures: {mkv_files}"
        )


def count_output_files(
    output_dir: Path,
    extensions: Optional[List[str]] = None,
    recursive: bool = True,
) -> int:
    """Count output files, optionally filtered by extension.

    Args:
        output_dir: Directory to count files in
        extensions: List of extensions to filter (e.g., [".jpg", ".mp4"])
        recursive: Whether to search recursively

    Returns:
        Number of matching files
    """
    if recursive:
        files = output_dir.rglob("*")
    else:
        files = output_dir.iterdir()

    count = 0
    for f in files:
        if f.is_file():
            if extensions is None or f.suffix.lower() in extensions:
                count += 1
    return count
