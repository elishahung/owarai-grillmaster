from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, override

import pytest
from tests.fakes import FakeFfmpeg

if TYPE_CHECKING:
    import threading
    from collections.abc import Mapping, Sequence

    from grillmaster.media.ffmpeg import ProgressCallback


class PackageFfmpeg(FakeFfmpeg):
    """`FakeFfmpeg` probing per path and recording each run's `cwd`.

    ffprobe answers `durations[path]` (else `default_duration`); an ffmpeg
    run reports its whole `report_seconds` (when set) as progress, then
    writes its output like `FakeFfmpeg`; `fail_encodes` is raised by every
    ffmpeg run (ffprobe still answers). Each ffmpeg run's `abort` event is
    recorded in `aborts`.
    """

    def __init__(
        self,
        durations: Mapping[Path, float] | None = None,
        *,
        default_duration: float = 60.0,
        report_seconds: float | None = None,
    ) -> None:
        super().__init__()
        self.durations = dict(durations or {})
        self.default_duration = default_duration
        self.report_seconds = report_seconds
        self.cwds: list[Path | None] = []
        self.fail_encodes: Exception | None = None
        self.aborts: list[threading.Event | None] = []

    @override
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | None = None,
        on_progress: ProgressCallback | None = None,
        abort: threading.Event | None = None,
    ) -> str:
        if argv[0] == "ffmpeg" and self.fail_encodes is not None:
            raise self.fail_encodes
        output = super().run(
            argv, timeout=timeout, cwd=cwd, on_progress=on_progress, abort=abort
        )
        if argv[0] == "ffprobe":
            return f"{self.durations.get(Path(argv[-1]), self.default_duration)}\n"
        with self._lock:
            self.cwds.append(cwd)
            self.aborts.append(abort)
        if on_progress is not None and self.report_seconds is not None:
            on_progress(self.report_seconds)
        return output


def encodes(ffmpeg: FakeFfmpeg) -> list[list[str]]:
    """ffmpeg (not ffprobe) argvs, in call order."""
    return [argv for argv in ffmpeg.calls if argv[0] == "ffmpeg"]


def arg_after(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A project directory holding `video.mp4` and `subs/cht.ass`."""
    root = tmp_path / "project"
    (root / "subs").mkdir(parents=True)
    (root / "video.mp4").write_bytes(b"video")
    (root / "subs" / "cht.ass").write_text("ass", encoding="utf-8")
    return root
