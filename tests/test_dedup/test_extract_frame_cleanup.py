#!/usr/bin/env python3
"""Regression tests for ffmpeg frame extraction cleanup."""

from __future__ import annotations

import subprocess
import tempfile as std_tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from common.dedup.hashing import _extract_frame_unchecked


@pytest.mark.parametrize(
    "run_side_effect",
    [
        subprocess.TimeoutExpired(cmd=["ffmpeg"], timeout=30),
        FileNotFoundError(),
        RuntimeError("boom"),
    ],
)
def test_extract_frame_unchecked_deletes_tempfile_on_exceptions(
    tmp_path: Path, run_side_effect
):
    created_paths: list[Path] = []
    real_named_temporary_file = std_tempfile.NamedTemporaryFile

    def recording_named_temporary_file(*args, **kwargs):
        tmp = real_named_temporary_file(*args, **kwargs)
        created_paths.append(Path(tmp.name))
        return tmp

    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"not a real video")

    with patch(
        "common.dedup.hashing.tempfile.NamedTemporaryFile",
        side_effect=recording_named_temporary_file,
    ):
        with patch("common.dedup.hashing.subprocess.run", side_effect=run_side_effect):
            result = _extract_frame_unchecked(video_path)

    assert result is None
    assert len(created_paths) == 1
    assert created_paths[0].exists() is False
