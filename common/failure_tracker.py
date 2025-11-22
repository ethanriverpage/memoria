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
                logger.debug(f"Could not get file size for {media_path}: {e}")
        
        entry = {
            "file_path": str(media_path),
            "reason": reason,
            "context": context,
        }
        
        self.orphaned_media.append(entry)
        logger.debug(f"Tracked orphaned media: {media_path}")

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
        logger.debug(f"Tracked orphaned metadata: {context.get('expected_path', 'unknown')}")

    def add_processing_failure(
        self,
        media_path: Path,
        metadata: Dict[str, Any],
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
        logger.debug(f"Tracked processing failure: {media_path}")

    def has_failures(self) -> bool:
        """Check if any failures have been tracked."""
        return bool(
            self.orphaned_media or self.orphaned_metadata or self.processing_failures
        )

    def get_summary(self) -> Dict[str, int]:
        """
        Get summary statistics of tracked failures.
        
        Returns:
            Dict with counts of each failure type
        """
        failed_matching = len(self.orphaned_media) + len(self.orphaned_metadata)
        return {
            "total_failures": failed_matching + len(self.processing_failures),
            "failed_matching": failed_matching,
            "failed_processing": len(self.processing_failures),
        }

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
        }

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
                logger.warning(f"Orphaned media file no longer exists: {source_path}")
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
        
        logger.info(f"Copied {copied_count}/{len(self.orphaned_media)} orphaned media files")

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
        
        logger.info(f"Saving {len(self.orphaned_metadata)} orphaned metadata entries...")
        
        saved_count = 0
        for idx, entry in enumerate(self.orphaned_metadata):
            # Generate filename from metadata or use index
            metadata = entry["metadata_entry"]
            
            # Try to extract a meaningful filename from metadata
            filename = None
            if isinstance(metadata, dict):
                # Try common filename fields
                for field in ["title", "name", "filename", "media_filename", "file_name"]:
                    if field in metadata and metadata[field]:
                        filename = Path(metadata[field]).stem
                        break
            
            # Fallback to index-based naming
            if not filename:
                filename = f"orphaned_metadata_{idx:04d}"
            
            # Ensure filename is safe
            filename = "".join(c if c.isalnum() or c in "-_" else "_" for c in filename)
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

    def save_report(self, output_dir: Path) -> None:
        """
        Save the failure report to a JSON file.
        
        Args:
            output_dir: Processor output directory (e.g., {base}/Google Photos/)
        """
        if not self.has_failures():
            logger.info("No failures to report")
            return
        
        # Create issues directory
        issues_dir = output_dir / "issues"
        issues_dir.mkdir(parents=True, exist_ok=True)
        
        report_path = issues_dir / "failure-report.json"
        
        try:
            report = self.generate_report()
            with open(report_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
            logger.info(f"Failure report saved to: {report_path}")
        except Exception as e:
            logger.error(f"Failed to save failure report to {report_path}: {e}")

    def handle_failures(self, output_dir: Path) -> None:
        """
        Handle all tracked failures by copying files and generating reports.
        
        This is a convenience method that:
        1. Copies orphaned media files
        2. Saves orphaned metadata files
        3. Saves the failure report
        
        Args:
            output_dir: Processor output directory (e.g., {base}/Google Photos/)
        """
        if not self.has_failures():
            return
        
        output_path = Path(output_dir)
        
        logger.info(f"Handling failures for {self.processor_name}...")
        
        # Copy orphaned media files
        self.copy_orphaned_media(output_path)
        
        # Save orphaned metadata
        self.save_orphaned_metadata(output_path)
        
        # Save failure report
        self.save_report(output_path)
        
        summary = self.get_summary()
        logger.info(
            f"Failure handling complete: {summary['failed_matching']} matching failures, "
            f"{summary['failed_processing']} processing failures"
        )

