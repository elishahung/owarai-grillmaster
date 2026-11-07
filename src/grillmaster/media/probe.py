"""Media facts read with ffprobe."""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from typing import TYPE_CHECKING, Any

from grillmaster.media.errors import MediaError
from grillmaster.media.ffmpeg import ffprobe

if TYPE_CHECKING:
    from pathlib import Path

    from grillmaster.media.ffmpeg import FfmpegRunner

_PROBE_TIMEOUT_S = 60.0
_STREAM_FIELDS = (
    "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt,"
    "sample_rate,channels"
)


@dataclass(frozen=True, slots=True)
class StreamFormat:
    """The first video and audio stream's format: two files whose formats
    are equal can be joined by stream copy. `frame_rate` is exact (`str` of
    it is a rational ffmpeg's `fps=` takes); audio fields are `None` without
    audio."""

    video_codec: str
    width: int
    height: int
    frame_rate: Fraction
    pixel_format: str
    audio_codec: str | None
    sample_rate: int | None
    channels: int | None


def stream_format(runner: FfmpegRunner, path: Path) -> StreamFormat:
    output = runner.run(
        ffprobe("-show_entries", _STREAM_FIELDS, "-of", "json", str(path)),
        timeout=_PROBE_TIMEOUT_S,
    )
    try:
        streams: list[dict[str, Any]] = json.loads(output)["streams"]
        video = next(s for s in streams if s.get("codec_type") == "video")
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        return StreamFormat(
            video_codec=str(video["codec_name"]),
            width=int(video["width"]),
            height=int(video["height"]),
            frame_rate=Fraction(str(video["r_frame_rate"])),
            pixel_format=str(video["pix_fmt"]),
            audio_codec=None if audio is None else str(audio["codec_name"]),
            sample_rate=None if audio is None else int(audio["sample_rate"]),
            channels=None if audio is None else int(audio["channels"]),
        )
    except (ValueError, KeyError, StopIteration, TypeError, ZeroDivisionError):
        raise MediaError(f"No readable video stream format: {path}") from None


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
