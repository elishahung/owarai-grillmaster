from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.fakes import stream_json

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


def test_stream_format_reads_the_first_video_and_audio_stream(fake_ffmpeg: FakeFfmpeg):
    fake_ffmpeg.streams = lambda _path: stream_json(codec="av1", fps="30000/1001")

    found = probe.stream_format(fake_ffmpeg, Path("video.mp4"))

    assert found == probe.StreamFormat(
        video_codec="av1",
        width=1920,
        height=1080,
        frame_rate=Fraction(30000, 1001),
        pixel_format="yuv420p",
        audio_codec="aac",
        sample_rate=48000,
        channels=2,
    )


def test_stream_format_without_a_video_stream_raises(fake_ffmpeg: FakeFfmpeg):
    fake_ffmpeg.streams = lambda _path: '{"streams": []}'
    with pytest.raises(MediaError, match="No readable video stream format"):
        probe.stream_format(fake_ffmpeg, Path("video.mp4"))


def test_stream_format_of_a_real_file(media_fixture: Path):
    found = probe.stream_format(SubprocessFfmpegRunner(), media_fixture)
    assert found.width > 0
    assert found.audio_codec is not None
