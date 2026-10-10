"""Joining downloaded parts and cutting a section, both without re-encoding,
and the concat demuxer run they and the package renders share.

`join_parts` and `cut` write under a temporary name and rename into place
(`core.fs.partial_path`), so an interrupted run never leaves a truncated
video under the final name.
"""

from __future__ import annotations

import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.fs import partial_path
from grillmaster.media.ffmpeg import ffmpeg

if TYPE_CHECKING:
    import threading
    from collections.abc import Iterator, Sequence

    from grillmaster.media.ffmpeg import FfmpegRunner, ProgressCallback

# Stream copy of a long show over a slow disk can take minutes; never time out.
_COPY_TIMEOUT_S = None
# The muxer, named because the temporary name hides the `.mp4` extension.
_MP4 = ("-f", "mp4")


def join_parts(runner: FfmpegRunner, parts: Sequence[Path], output: Path) -> None:
    """Join `parts` (in the given order) into `output`, consuming them.

    A single part is moved into place; several are concatenated with the
    concat demuxer and stream copy. The parts are deleted only once `output`
    is complete, so a run interrupted while deleting already has `output`.
    """
    if not parts:
        raise ValueError("No video parts to join")
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(parts) == 1:
        logger.info(f"Moving the only part {parts[0].name} to {output}")
        parts[0].replace(output)
        return
    logger.info(f"Joining {len(parts)} parts into {output}")
    with _into_place(output) as partial:
        concat_copy(
            runner,
            parts,
            partial,
            "-map",
            "0",
            "-c",
            "copy",
            "-movflags",
            "faststart",
            *_MP4,
        )
    for part in parts:
        part.unlink()


def concat_copy(
    runner: FfmpegRunner,
    inputs: Sequence[Path],
    output: Path,
    *extra: str,
    on_progress: ProgressCallback | None = None,
    abort: threading.Event | None = None,
) -> None:
    """Run the concat demuxer over `inputs` into `output`.

    `extra` is everything between the concat input and the output: further
    inputs, stream maps, codecs (`-c copy` or a partial re-encode) and
    muxer flags. The list file lives in a temporary directory removed
    afterwards. No timeout: a long show over a slow share takes minutes;
    `on_progress` and `abort` reach the runner (see `FfmpegRunner.run`).
    """
    if not inputs:
        raise ValueError("concat needs at least one input")
    with tempfile.TemporaryDirectory(prefix="grill-concat-") as scratch:
        concat_list = Path(scratch) / "inputs.txt"
        concat_list.write_text(concat_list_text(inputs), encoding="utf-8")
        runner.run(
            ffmpeg(
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                *extra,
                str(output),
            ),
            timeout=_COPY_TIMEOUT_S,
            on_progress=on_progress,
            abort=abort,
        )


def concat_list_text(parts: Sequence[Path]) -> str:
    """A concat demuxer script listing `parts`.

    Paths use forward slashes because the demuxer treats a backslash as an
    escape, which would mangle a raw Windows path inside the quoted `file`
    directive; single quotes are escaped the demuxer's way.
    """
    lines = []
    for part in parts:
        escaped = part.as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'\n")
    return "".join(lines)


def cut(
    runner: FfmpegRunner,
    source: Path,
    output: Path,
    *,
    start: float | None = None,
    end: float | None = None,
) -> None:
    """Copy the `start`..`end` seconds of `source` into `output`.

    Stream copy snaps the cut points to keyframes, so the output may hold a
    little more than the requested range. At least one bound is required.
    """
    if start is None and end is None:
        raise ValueError("A cut needs a start or an end")
    if end is not None and end <= (start or 0.0):
        raise ValueError(f"Cut end {end} is not after start {start or 0.0}")
    seek = ("-ss", str(start)) if start is not None else ()
    duration = ("-t", str(end - (start or 0.0))) if end is not None else ()
    logger.info(
        f"Cutting {start or 0.0}s-{end if end is not None else 'end'} of {source}"
    )
    _write(
        runner,
        (
            *seek,
            "-i",
            str(source),
            *duration,
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-movflags",
            "faststart",
        ),
        output,
    )


def _write(runner: FfmpegRunner, args: tuple[str, ...], output: Path) -> None:
    with _into_place(output) as partial:
        runner.run(ffmpeg(*args, *_MP4, str(partial)), timeout=_COPY_TIMEOUT_S)


@contextmanager
def _into_place(output: Path) -> Iterator[Path]:
    """The temporary name to write `output` under; renamed into place when
    the body succeeds, removed either way."""
    partial = partial_path(output)
    try:
        yield partial
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
