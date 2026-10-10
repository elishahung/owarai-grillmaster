"""Joining downloaded parts and cutting a section, both without re-encoding.

Both write under a temporary name and rename into place (`core.fs.
partial_path`), so an interrupted run never leaves a truncated video under
the final name.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from grillmaster.core.fs import partial_path
from grillmaster.media.ffmpeg import ffmpeg

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grillmaster.media.ffmpeg import FfmpegRunner

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
    with tempfile.TemporaryDirectory(prefix="grill-concat-") as scratch:
        concat_list = Path(scratch) / "parts.txt"
        concat_list.write_text(concat_list_text(parts), encoding="utf-8")
        _write(
            runner,
            (
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_list),
                "-map",
                "0",
                "-c",
                "copy",
                "-movflags",
                "faststart",
            ),
            output,
        )
    for part in parts:
        part.unlink()


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
    partial = partial_path(output)
    try:
        runner.run(ffmpeg(*args, *_MP4, str(partial)), timeout=_COPY_TIMEOUT_S)
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
