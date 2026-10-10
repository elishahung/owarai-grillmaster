"""Media facts read with ffprobe."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grillmaster.media.errors import MediaError
from grillmaster.media.ffmpeg import ffprobe

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.media.ffmpeg import FfmpegRunner

_PROBE_TIMEOUT_S = 60.0


def duration(runner: FfmpegRunner, path: Path) -> float:
    """Container duration in seconds."""
    output = runner.run(
        ffprobe(
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ),
        timeout=_PROBE_TIMEOUT_S,
    )
    value = output.strip()
    try:
        return float(value)
    except ValueError:  # empty or `N/A`
        raise MediaError(f"Media duration missing: {path}") from None
