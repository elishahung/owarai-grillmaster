"""JPEG stills at given timestamps, cached by file name.

A frame is named `frame_<seconds:010.3f>_<max_side>.jpg`; an existing file is
a hit and is never re-extracted. Each still is written to a dot-prefixed temp
name first, so an interrupted run never leaves a truncated frame behind under
the cached name. Missing stills are extracted concurrently, a few ffmpeg
runs at a time.
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from grillmaster.media.errors import MediaError
from grillmaster.media.ffmpeg import ffmpeg

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

    from grillmaster.media.ffmpeg import FfmpegRunner

_FRAME_TIMEOUT_S = 120.0
_JPEG_QUALITY = "2"  # mjpeg qscale: 2 is near-lossless
_MAX_WORKERS = 4


def frame_path(out_dir: Path, time: float, max_side: int) -> Path:
    return out_dir / f"frame_{time:010.3f}_{max_side}.jpg"


def extract_frames(
    runner: FfmpegRunner,
    video: Path,
    times: Iterable[float],
    out_dir: Path,
    max_side: int,
) -> list[Path]:
    """One still per timestamp, longest side scaled to `max_side`, in input order.

    Raises `MediaError` when a frame cannot be extracted (for example a
    timestamp past the last frame).
    """
    if max_side <= 0:
        raise ValueError(f"max_side must be positive: {max_side}")
    out_dir.mkdir(parents=True, exist_ok=True)
    timestamps = list(times)
    paths = [frame_path(out_dir, time, max_side) for time in timestamps]
    # Keyed by path: a repeated timestamp is extracted once.
    missing = {
        path: time
        for time, path in zip(timestamps, paths, strict=True)
        if not path.exists()
    }
    if missing:
        with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(missing))) as pool:
            futures = [
                pool.submit(_extract_one, runner, video, time, path, max_side)
                for path, time in missing.items()
            ]
            for future in futures:
                future.result()
    return paths


def _extract_one(
    runner: FfmpegRunner, video: Path, time: float, path: Path, max_side: int
) -> None:
    # Unique per worker: parallel tool calls may extract the same timestamp.
    partial = path.with_name(f".{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    landscape = "gte(iw,ih)"
    scale = f"scale='if({landscape},{max_side},-2)':'if({landscape},-2,{max_side})'"
    runner.run(
        ffmpeg(
            "-ss",
            f"{time:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-vf",
            scale,
            "-f",
            "image2",
            "-update",
            "1",
            "-c:v",
            "mjpeg",
            "-q:v",
            _JPEG_QUALITY,
            str(partial),
        ),
        timeout=_FRAME_TIMEOUT_S,
    )
    # A seek past the last frame exits 0 without writing anything.
    if not partial.exists() or partial.stat().st_size == 0:
        partial.unlink(missing_ok=True)
        raise MediaError(f"No frame extracted at {time:.3f}s from {video}")
    partial.replace(path)
