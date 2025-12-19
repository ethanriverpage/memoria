"""
Upload utilities for external services.

This package provides:
- Immich CLI wrapper for uploads
- Upload target building based on processor and export
"""

from common.upload.immich import verify_auth, upload
from common.upload.targets import build_upload_targets

__all__ = [
    # Immich
    "verify_auth",
    "upload",
    # Targets
    "build_upload_targets",
]

