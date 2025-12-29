from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from common.media.probe import (
    get_video_metadata_with_failure,
    quick_isobmff_header_check,
)


def test_quick_isobmff_header_check_detects_ftyp(tmp_path: Path) -> None:
    p = tmp_path / "validish.mov"
    # Minimal fake ISO BMFF header: size + 'ftyp' + brand
    p.write_bytes(b"\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00qt  ")

    ok, reason = quick_isobmff_header_check(p, header_bytes=1024)
    assert ok is True
    assert reason == "header_has_ftyp"


def test_quick_isobmff_header_check_flags_missing_ftyp(tmp_path: Path) -> None:
    p = tmp_path / "placeholder.mov"
    p.write_bytes(b"not a container payload" * 100)

    ok, reason = quick_isobmff_header_check(p, header_bytes=1024)
    assert ok is False
    assert reason in {"header_missing_ftyp", "read_failed:ValueError", "read_failed:OSError"}


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not available")
def test_get_video_metadata_with_failure_returns_failure_for_invalid_file(
    tmp_path: Path,
) -> None:
    p = tmp_path / "invalid.mov"
    p.write_bytes(b"not a video")

    metadata, failure = get_video_metadata_with_failure(p, timeout=2)
    assert metadata is None
    assert failure is not None
    assert failure.reason_code


