from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grillmaster.media.ffmpeg import SubprocessFfmpegRunner, ffmpeg

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(scope="session")
def media_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A 2-second 320x180 test video with a sine-tone audio track."""
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    SubprocessFfmpegRunner().run(
        ffmpeg(
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=320x180:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ),
        timeout=60,
    )
    return path
