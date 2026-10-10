from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from tests.fakes import FAKE_JPEG

from grillmaster.core.timecode import TimeRange
from grillmaster.media import probe
from grillmaster.media.audio import (
    extract_audio,
    extract_audio_segment,
    find_audio_gaps,
)
from grillmaster.media.errors import MediaError
from grillmaster.media.ffmpeg import SubprocessFfmpegRunner, ffprobe

if TYPE_CHECKING:
    from tests.fakes import FakeFfmpeg

SPEECH_ENCODE = [
    "-vn",
    "-c:a",
    "libopus",
    "-ac",
    "1",
    "-ar",
    "16000",
    "-b:a",
    "24k",
    "-f",
    "ogg",
]


def test_extract_audio_encodes_speech_opus(fake_ffmpeg: FakeFfmpeg, tmp_path: Path):
    output = tmp_path / "work" / "audio.ogg"

    extract_audio(fake_ffmpeg, Path("video.mp4"), output)

    (argv,) = fake_ffmpeg.calls
    assert argv[0] == "ffmpeg"
    assert argv[argv.index("-i") + 1] == "video.mp4"
    assert argv[-len(SPEECH_ENCODE) - 1 : -1] == SPEECH_ENCODE
    assert output.read_bytes() == FAKE_JPEG
    # Written under a temporary name, then renamed into place.
    assert Path(argv[-1]) != output
    assert Path(argv[-1]).parent == output.parent
    assert [path.name for path in output.parent.iterdir()] == ["audio.ogg"]


def test_failed_extract_leaves_no_output(fake_ffmpeg: FakeFfmpeg, tmp_path: Path):
    fake_ffmpeg.fail_with = MediaError("ffmpeg failed")
    output = tmp_path / "audio.ogg"

    with pytest.raises(MediaError):
        extract_audio(fake_ffmpeg, Path("video.mp4"), output)
    assert list(tmp_path.iterdir()) == []


def test_extract_segment_seeks_before_the_input(
    fake_ffmpeg: FakeFfmpeg, tmp_path: Path
):
    output = tmp_path / "chunk.ogg"

    extract_audio_segment(
        fake_ffmpeg, Path("audio.ogg"), output, TimeRange(12.5, 72.25)
    )

    (argv,) = fake_ffmpeg.calls
    assert argv[argv.index("-ss") + 1] == "12.500"
    assert argv[argv.index("-t") + 1] == "59.750"
    assert argv.index("-ss") < argv.index("-i")
    assert argv[-len(SPEECH_ENCODE) - 1 : -1] == SPEECH_ENCODE
    assert output.read_bytes() == FAKE_JPEG


@pytest.mark.parametrize("span", [TimeRange(5.0, 5.0), TimeRange(5.0, 4.0)])
def test_extract_segment_rejects_an_empty_span(
    fake_ffmpeg: FakeFfmpeg, tmp_path: Path, span: TimeRange
):
    with pytest.raises(ValueError, match="positive"):
        extract_audio_segment(fake_ffmpeg, Path("a.ogg"), tmp_path / "s.ogg", span)
    assert fake_ffmpeg.calls == []


def gaps(fake_ffmpeg: FakeFfmpeg, stdout: str) -> list[tuple[float, float]]:
    fake_ffmpeg.stdout = stdout
    found = find_audio_gaps(fake_ffmpeg, Path("in.mp4"))
    (argv,) = fake_ffmpeg.calls
    assert argv[0] == "ffprobe"
    assert argv[argv.index("-select_streams") + 1] == "a:0"
    assert argv[-1] == "in.mp4"
    return [(gap.start, gap.end) for gap in found]


def test_contiguous_audio_has_no_gaps(fake_ffmpeg: FakeFfmpeg):
    assert gaps(fake_ffmpeg, "0.000000\n0.023220\n0.046440\n") == []


def test_reports_each_jump_past_the_tolerance(fake_ffmpeg: FakeFfmpeg):
    stdout = "141.758000\n141.781000\n150.140000\n150.163000\nN/A\n200.000000,\r\n"
    assert gaps(fake_ffmpeg, stdout) == [(141.781, 150.14), (150.163, 200.0)]


def test_file_without_audio_has_no_gaps(fake_ffmpeg: FakeFfmpeg):
    assert gaps(fake_ffmpeg, "") == []


def test_extract_audio_from_a_real_file(media_fixture: Path, tmp_path: Path):
    runner = SubprocessFfmpegRunner()
    output = tmp_path / "audio.ogg"

    extract_audio(runner, media_fixture, output)

    assert probe.duration(runner, output) == pytest.approx(2.0, abs=0.1)
    codec = runner.run(
        ffprobe(
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,channels,sample_rate",
            "-of",
            "default=noprint_wrappers=1",
            str(output),
        )
    )
    assert codec.split() == ["codec_name=opus", "sample_rate=48000", "channels=1"]
    assert find_audio_gaps(runner, media_fixture) == []
