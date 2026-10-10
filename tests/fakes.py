"""Fakes and builders shared across test packages."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING

from grillmaster.core.srt import SrtBlock
from grillmaster.core.timecode import format_timecode_line

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grillmaster.media.ffmpeg import ProgressCallback

# What `FakeFfmpeg` writes as each ffmpeg output: a JPEG SOI marker and filler.
FAKE_JPEG = b"\xff\xd8jpeg"


class FakeFfmpeg:
    """An `FfmpegRunner` that records each argv; thread-safe.

    `fail_with` is raised by every run. Otherwise ffprobe returns
    `duration` (when set) or `stdout`, and ffmpeg writes `FAKE_JPEG` at its
    last argument (unless `write_output` is off) and returns `stdout`.
    """

    def __init__(
        self,
        stdout: str = "",
        *,
        duration: float | None = None,
        write_output: bool = True,
    ) -> None:
        self.stdout = stdout
        self.duration = duration
        self.write_output = write_output
        self.fail_with: Exception | None = None
        self._lock = threading.Lock()
        self._calls: list[list[str]] = []

    @property
    def calls(self) -> list[list[str]]:
        with self._lock:
            return list(self._calls)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> str:
        with self._lock:
            self._calls.append(list(argv))
        if self.fail_with is not None:
            raise self.fail_with
        if argv[0] == "ffprobe":
            return self.stdout if self.duration is None else f"{self.duration}\n"
        if self.write_output:
            Path(argv[-1]).write_bytes(FAKE_JPEG)
        return self.stdout


def make_blocks(count: int) -> list[SrtBlock]:
    """`count` blocks numbered from 1: 1.5s cues every 2s, text `line <i>`."""
    return [
        SrtBlock(i, format_timecode_line(i * 2.0, i * 2.0 + 1.5), f"line {i}")
        for i in range(1, count + 1)
    ]
