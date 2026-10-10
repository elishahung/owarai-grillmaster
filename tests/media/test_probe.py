from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from grillmaster.media import probe
from grillmaster.media.errors import MediaError
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner

if TYPE_CHECKING:
    from tests.fakes import FakeFfmpeg


def test_duration_parses_ffprobe_output(fake_ffmpeg: FakeFfmpeg):
    fake_ffmpeg.stdout = "1234.567000\r\n"
    assert probe.duration(fake_ffmpeg, Path("video.mp4")) == 1234.567
    (argv,) = fake_ffmpeg.calls
    assert argv[0] == "ffprobe"
    assert argv[-1] == "video.mp4"
    assert "format=duration" in argv


@pytest.mark.parametrize("stdout", ["", "N/A\n"])
def test_missing_duration_raises(fake_ffmpeg: FakeFfmpeg, stdout: str):
    fake_ffmpeg.stdout = stdout
    with pytest.raises(MediaError, match="duration missing"):
        probe.duration(fake_ffmpeg, Path("video.mp4"))


def test_duration_of_a_real_file(media_fixture: Path):
    assert probe.duration(SubprocessFfmpegRunner(), media_fixture) == pytest.approx(
        2.0, abs=0.1
    )
