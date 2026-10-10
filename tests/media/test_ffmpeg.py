from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from grillmaster.media.errors import MediaError
from grillmaster.media.ffmpeg import (
    SubprocessFfmpegRunner,
    ffmpeg,
    ffprobe,
    parse_progress_line,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def runner() -> SubprocessFfmpegRunner:
    return SubprocessFfmpegRunner()


def test_ffmpeg_argv_prefixes_shared_flags():
    assert ffmpeg("-i", "in.mp4", "out.wav") == [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        "in.mp4",
        "out.wav",
    ]


def test_ffprobe_argv():
    assert ffprobe("x.mp4") == ["ffprobe", "-v", "error", "x.mp4"]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("out_time_us=1500000\n", 1.5),
        # Same value as `out_time_us` in every block; reading it would report twice.
        ("out_time_ms=2000000", None),
        ("out_time_us=N/A", None),
        ("out_time=00:00:01.500000", None),
        ("progress=continue", None),
        ("garbage", None),
    ],
)
def test_parse_progress_line(line: str, expected: float | None):
    assert parse_progress_line(line) == expected


def test_run_returns_stdout(runner: SubprocessFfmpegRunner):
    assert runner.run(["ffprobe", "-version"]).startswith("ffprobe version")


def test_failure_raises_with_stderr_tail(
    runner: SubprocessFfmpegRunner, tmp_path: Path
):
    missing = tmp_path / "missing.mp4"
    with pytest.raises(MediaError, match=r"ffmpeg failed \(exit \d+\)") as info:
        runner.run(ffmpeg("-i", str(missing), str(tmp_path / "out.wav")))
    command, *stderr = str(info.value).splitlines()
    assert "missing.mp4" in command
    assert any("No such file" in line for line in stderr)


def test_missing_program_raises(runner: SubprocessFfmpegRunner):
    with pytest.raises(MediaError, match="not found on PATH"):
        runner.run(["grill-no-such-ffmpeg", "-version"])


def _endless() -> list[str]:
    """A real-time silent stream that never ends on its own."""
    return ffmpeg("-re", "-f", "lavfi", "-i", "anullsrc", "-f", "null", "-")


def test_timeout_kills_the_process(runner: SubprocessFfmpegRunner):
    started = time.monotonic()
    with pytest.raises(MediaError, match=r"timed out after 0\.5s"):
        runner.run(_endless(), timeout=0.5)
    assert time.monotonic() - started < 10


def test_progress_callback_receives_output_time(
    runner: SubprocessFfmpegRunner, media_fixture: Path
):
    seen: list[float] = []
    output = runner.run(
        ffmpeg("-i", str(media_fixture), "-f", "null", "-"),
        on_progress=seen.append,
    )
    assert output == ""
    assert seen
    assert seen[-1] == pytest.approx(2.0, abs=0.1)


def test_failing_progress_callback_kills_the_process(runner: SubprocessFfmpegRunner):
    def explode(_seconds: float) -> None:
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError, match="stop"):
        runner.run(
            _endless(),
            timeout=30,
            on_progress=explode,
        )


def test_progress_needs_an_ffmpeg_argv(runner: SubprocessFfmpegRunner):
    with pytest.raises(ValueError, match="needs an ffmpeg argv"):
        runner.run(ffprobe("-version"), on_progress=lambda _seconds: None)
