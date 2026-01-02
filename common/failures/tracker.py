#!/usr/bin/env python3
"""
Failure Tracker Module

Tracks preprocessing and processing failures including:
- Orphaned media (files without metadata)
- Orphaned metadata (metadata without files)
- Processing failures (files that failed during processing)

Generates detailed JSON reports and organizes failed files.
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from common.files.utils import short_path

logger = logging.getLogger(__name__)


class FailureTracker:
    """
    Tracks failures during preprocessing and processing.

    Captures orphaned media, orphaned metadata, and processing failures,
    then generates comprehensive JSON reports and organizes failed files.
    """

    def __init__(self, processor_name: str, export_directory: str):
        """
        Initialize failure tracker.

        Args:
            processor_name: Name of the processor (e.g., "Google Photos")
            export_directory: Path to the export directory being processed
        """
        self.processor_name = processor_name
        self.export_directory = export_directory
        self.timestamp = datetime.now().isoformat()

        # Track failures
        self.orphaned_media: List[Dict[str, Any]] = []
        self.orphaned_metadata: List[Dict[str, Any]] = []
        self.processing_failures: List[Dict[str, Any]] = []
        self.export_failures: List[Dict[str, Any]] = []

    def add_orphaned_media(
        self,
        media_path: Path,
        reason: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Track a media file that has no matching metadata.

        Args:
            media_path: Path to the orphaned media file
            reason: Human-readable reason for the failure
            context: Additional context information (file_size, etc.)
        """
        if context is None:
            context = {}

        # Add file size if not provided
        if "file_size" not in context and media_path.exists():
            try:
                context["file_size"] = media_path.stat().st_size
            except Exception as e:
                logger.debug(f"Size failed: {short_path(media_path)}: {e}")

        entry = {
            "file_path": str(media_path),
            "reason": reason,
            "context": context,
        }

        self.orphaned_media.append(entry)

    def add_orphaned_metadata(
        self,
        metadata_entry: Dict[str, Any],
        reason: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Track metadata that references a missing media file.

        Args:
            metadata_entry: The metadata entry (dict/JSON object)
            reason: Human-readable reason for the failure
            context: Additional context information (expected_path, etc.)
        """
        if context is None:
            context = {}

        entry = {
            "metadata_entry": metadata_entry,
            "reason": reason,
            "context": context,
        }

        self.orphaned_metadata.append(entry)

    def add_processing_failure(
        self,
        media_path: Path,
        metadata: Optional[Dict[str, Any]],
        reason: str,
        error_details: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Track a media file that failed during processing.

        Args:
            media_path: Path to the media file that failed
            metadata: Associated metadata
            reason: Human-readable reason for the failure
            error_details: Detailed error message/traceback
            context: Additional context information
        """
        if context is None:
            context = {}

        entry = {
            "file_path": str(media_path),
            "metadata": metadata,
            "reason": reason,
            "error_details": error_details,
            "context": context,
        }

        self.processing_failures.append(entry)

    def add_export_failure(
        self,
        reason: str,
        error_details: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Track an export-level failure (e.g., empty export).

        Args:
            reason: Human-readable reason for the failure
            error_details: Additional error details or stacktrace
            context: Additional context information
        """
        if context is None:
            context = {}

        entry = {
            "reason": reason,
            "error_details": error_details,
            "context": context,
        }

        self.export_failures.append(entry)

    def has_failures(self) -> bool:
        return bool(
            self.orphaned_media
            or self.orphaned_metadata
            or self.processing_failures
            or self.export_failures
        )

    def get_summary(self) -> Dict[str, int]:
        """
        Get summary statistics of tracked failures.

        Returns:
            Dict with counts of each failure type
        """
        failed_matching = len(self.orphaned_media) + len(self.orphaned_metadata)
        return {
            "total_failures": failed_matching
            + len(self.processing_failures)
            + len(self.export_failures),
            "failed_matching": failed_matching,
            "failed_processing": len(self.processing_failures),
            "export_failures": len(self.export_failures),
        }

    def _relative_path(self, full_path: str) -> str:
        """
        Convert a full path to a relative path from the export directory.

        Args:
            full_path: Full absolute path to a file

        Returns:
            Path relative to export directory, or export dir name + path
        """
        try:
            export_path = Path(self.export_directory)
            file_path = Path(full_path)
            # Try to get relative path from export directory's parent
            # so we include the export directory name itself
            if export_path.parent in file_path.parents or file_path.is_relative_to(
                export_path.parent
            ):
                return str(file_path.relative_to(export_path.parent))
            # Fallback: just use export dir name + relative path
            if file_path.is_relative_to(export_path):
                return f"{export_path.name}/{file_path.relative_to(export_path)}"
            return str(file_path)
        except (ValueError, TypeError):
            return str(full_path)

    def log_tracking_summary(self) -> None:
        """
        Log a grouped summary of all tracked failures.

        Instead of logging each failure individually, this logs counts
        and sample file paths for each category.
        """
        if not self.has_failures():
            return

        if self.orphaned_metadata:
            logger.debug(
                f"Tracked {len(self.orphaned_metadata)} orphaned metadata entries"
            )

        if self.orphaned_media:
            # Group by reason and show counts
            reasons_to_paths: Dict[str, List[str]] = {}
            for entry in self.orphaned_media:
                reason = entry.get("reason", "unknown")
                rel_path = self._relative_path(entry["file_path"])
                if reason not in reasons_to_paths:
                    reasons_to_paths[reason] = []
                reasons_to_paths[reason].append(rel_path)

            for reason, paths in reasons_to_paths.items():
                if len(paths) <= 3:
                    # Show all paths if 3 or fewer
                    paths_str = ", ".join(paths)
                    logger.debug(
                        f"Tracked {len(paths)} orphaned media ({reason}): {paths_str}"
                    )
                else:
                    # Show count and first few examples
                    samples = ", ".join(paths[:2])
                    logger.debug(
                        f"Tracked {len(paths)} orphaned media ({reason}), e.g.: {samples}"
                    )

        if self.processing_failures:
            # Group by reason and show counts
            reasons_to_paths: Dict[str, List[str]] = {}
            for entry in self.processing_failures:
                reason = entry.get("reason", "unknown")
                rel_path = self._relative_path(entry["file_path"])
                if reason not in reasons_to_paths:
                    reasons_to_paths[reason] = []
                reasons_to_paths[reason].append(rel_path)

            for reason, paths in reasons_to_paths.items():
                if len(paths) <= 3:
                    paths_str = ", ".join(paths)
                    logger.debug(
                        f"Tracked {len(paths)} processing failures ({reason}): {paths_str}"
                    )
                else:
                    samples = ", ".join(paths[:2])
                    logger.debug(
                        f"Tracked {len(paths)} processing failures ({reason}), e.g.: {samples}"
                    )

        if self.export_failures:
            reason_counts: Dict[str, int] = {}
            for entry in self.export_failures:
                reason = entry.get("reason", "unknown")
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
            for reason, count in reason_counts.items():
                logger.debug(f"Tracked {count} export-level failures ({reason})")

    def generate_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive failure report.

        Returns:
            Dict containing all failure information
        """
        return {
            "processor_name": self.processor_name,
            "export_directory": self.export_directory,
            "timestamp": self.timestamp,
            "summary": self.get_summary(),
            "failed_matching": {
                "orphaned_media": self.orphaned_media,
                "orphaned_metadata": self.orphaned_metadata,
            },
            "failed_processing": self.processing_failures,
            "export_failures": self.export_failures,
        }

    def move_processing_failures(self, output_dir: Path) -> None:
        """
        Move processing failure outputs into issues/failed-processing/.

        Ensures failed outputs are not mixed with successful outputs.

        Args:
            output_dir: Processor output directory (e.g., {base}/Google Photos/)
        """
        if not self.processing_failures:
            return

        dest_dir = output_dir / "issues" / "failed-processing"
        dest_dir.mkdir(parents=True, exist_ok=True)

        moved_count = 0
        for entry in self.processing_failures:
            source_path = Path(entry["file_path"])
            context = entry.setdefault("context", {})

            if not source_path.exists():
                context["move_error"] = "Source file not found"
                continue

            dest_path = dest_dir / source_path.name
            if dest_path.exists():
                counter = 1
                stem = dest_path.stem
                suffix = dest_path.suffix
                while dest_path.exists():
                    dest_path = dest_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            try:
                shutil.move(str(source_path), dest_path)
                context["moved_to"] = str(dest_path.relative_to(output_dir))
                moved_count += 1
            except Exception as e:
                logger.error(f"Failed to move processing failure {source_path}: {e}")
                context["move_error"] = str(e)

        logger.info(
            f"Moved {moved_count}/{len(self.processing_failures)} failed outputs to {dest_dir}"
        )

    def copy_orphaned_media(self, output_dir: Path) -> None:
        """
        Copy orphaned media files to the failed-matching directory.

        Preserves original filenames.

        Args:
            output_dir: Processor output directory (e.g., {base}/Google Photos/)
        """
        if not self.orphaned_media:
            return

        # Create destination directory
        dest_dir = output_dir / "issues" / "failed-matching" / "media"
        dest_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Copying {len(self.orphaned_media)} orphaned media files...")

        copied_count = 0
        for entry in self.orphaned_media:
            source_path = Path(entry["file_path"])

            if not source_path.exists():
                logger.warning(
                    f"Orphaned media file no longer exists: {short_path(source_path)}"
                )
                entry["context"]["copy_error"] = "Source file not found"
                continue

            # Preserve original filename
            dest_path = dest_dir / source_path.name

            # Handle filename collisions
            if dest_path.exists():
                # Add counter to filename
                counter = 1
                stem = dest_path.stem
                suffix = dest_path.suffix
                while dest_path.exists():
                    dest_path = dest_dir / f"{stem}_{counter}{suffix}"
                    counter += 1

            try:
                shutil.copy2(source_path, dest_path)
                entry["context"]["copied_to"] = str(dest_path.relative_to(output_dir))
                copied_count += 1
            except Exception as e:
                logger.error(f"Failed to copy orphaned media {source_path}: {e}")
                entry["context"]["copy_error"] = str(e)

        logger.info(
            f"Copied {copied_count}/{len(self.orphaned_media)} orphaned media files"
        )

    def save_orphaned_metadata(self, output_dir: Path) -> None:
        """
        Save orphaned metadata entries as individual JSON files.

        Args:
            output_dir: Processor output directory (e.g., {base}/Google Photos/)
        """
        if not self.orphaned_metadata:
            return

        # Create destination directory
        dest_dir = output_dir / "issues" / "failed-matching" / "metadata"
        dest_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"Saving {len(self.orphaned_metadata)} orphaned metadata entries..."
        )

        saved_count = 0
        for idx, entry in enumerate(self.orphaned_metadata):
            # Generate filename from metadata or use index
            metadata = entry["metadata_entry"]

            # Try to extract a meaningful filename from metadata
            filename = None
            if isinstance(metadata, dict):
                # Try common filename fields
                for field in [
                    "title",
                    "name",
                    "filename",
                    "media_filename",
                    "file_name",
                ]:
                    if field in metadata and metadata[field]:
                        filename = Path(metadata[field]).stem
                        break

            # Fallback to index-based naming
            if not filename:
                filename = f"orphaned_metadata_{idx:04d}"

            # Ensure filename is safe and not too long
            # Max filename length on most filesystems is 255 bytes
            # Reserve space for .json extension and potential counter suffix
            filename = "".join(c if c.isalnum() or c in "-_" else "_" for c in filename)
            if len(filename) > 200:
                filename = filename[:200]
            dest_path = dest_dir / f"{filename}.json"

            # Handle filename collisions
            if dest_path.exists():
                counter = 1
                while dest_path.exists():
                    dest_path = dest_dir / f"{filename}_{counter}.json"
                    counter += 1

            try:
                with open(dest_path, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)
                entry["context"]["metadata_saved_to"] = str(
                    dest_path.relative_to(output_dir)
                )
                saved_count += 1
            except Exception as e:
                logger.error(f"Failed to save orphaned metadata to {dest_path}: {e}")
                entry["context"]["save_error"] = str(e)

        logger.info(
            f"Saved {saved_count}/{len(self.orphaned_metadata)} orphaned metadata entries"
        )

    def save_report(
        self, output_dir: Path, report_namespace: Optional[str] = None
    ) -> None:
        """
        Save the failure report to a JSON file.

        Args:
            output_dir: Processor output directory (e.g., {base}/Google Photos/)
            report_namespace: Optional namespace for the report filename.
                If provided, the report is saved as failure-report__{namespace}.json.
                Useful for processors processing multiple exports into the same output.
        """
        if not self.has_failures():
            logger.info("No failures to report")
            return

        # Create issues directory
        issues_dir = output_dir / "issues"
        issues_dir.mkdir(parents=True, exist_ok=True)

        # Determine report filename
        if report_namespace:
            report_filename = f"failure-report__{report_namespace}.json"
        else:
            report_filename = "failure-report.json"
        report_path = issues_dir / report_filename

        try:
            report = self.generate_report()
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            logger.info(f"Failure report saved to: {report_path}")
        except Exception as e:
            logger.error(f"Failed to save failure report to {report_path}: {e}")

    def handle_failures(
        self, output_dir: Path, report_namespace: Optional[str] = None
    ) -> None:
        """
        Handle all tracked failures by copying files and generating reports.

        This is a convenience method that:
        1. Logs a grouped summary of tracked failures
        2. Copies orphaned media files
        3. Saves orphaned metadata files
        4. Saves the failure report

        Args:
            output_dir: Processor output directory (e.g., {base}/Google Photos/)
            report_namespace: Optional namespace for the failure report filename.
                If provided, the report is saved as failure-report__{namespace}.json.
                Useful for processors processing multiple exports into the same output.
        """
        if not self.has_failures():
            return

        output_path = Path(output_dir)

        # Log grouped summary of what was tracked
        self.log_tracking_summary()

        logger.info(f"Handling failures for {self.processor_name}...")

        # Copy orphaned media files
        self.copy_orphaned_media(output_path)

        # Save orphaned metadata
        self.save_orphaned_metadata(output_path)

        # Move failed processing outputs
        self.move_processing_failures(output_path)

        # Save failure report
        self.save_report(output_path, report_namespace=report_namespace)

        summary = self.get_summary()
        logger.info(
            f"Failure handling complete: {summary['failed_matching']} matching failures, "
            f"{summary['failed_processing']} processing failures, "
            f"{summary['export_failures']} export-level failures"
        )
